import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_deletion_preview import deletion_workspace as _deletion_workspace

from study_errorbook import deletion
from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

deletion_workspace = _deletion_workspace


def preview(ws, target):
    return deletion.preview_document_deletion(ws.db, ws.root, target['id'])['inventory_digest']


def test_cascade_preserves_shared_data_input_and_unrelated_export(deletion_workspace):
    ws, target, records, shared, unique, child, grandchild, mixed, unrelated = deletion_workspace
    rows_before = ws.db.execute("SELECT kind,id,rowid FROM objects").fetchall()
    digest = preview(ws, target)
    receipt = ws.delete_document(target['id'], digest, True)
    rows_after = set(ws.db.execute("SELECT kind,id,rowid FROM objects").fetchall())
    assert rows_after <= set(rows_before)
    assert receipt['deleted'] and not receipt['connection_reopen_required']
    for doc in (target, child, grandchild):
        assert not (ws.root / doc['id']).exists()
    assert not Path(mixed['folder']).exists()
    assert Path(unrelated['folder']).exists()
    assert (ws.input_root / 'synthetic-0.png').is_file()
    assert ws._get('check', shared['id']) and ws._get('record', records[1]['id'])
    for kind, identifier in [('check', unique['id']), ('record', records[0]['id'])]:
        with pytest.raises(DomainError, match='not_found'):
            ws._get(kind, identifier)
    assert ws.db.execute('SELECT count(*) FROM deletion_pending').fetchone()[0] == 0
    assert (ws.root / 'study.db-wal').stat().st_size == 0
    assert b'SYNTHETIC RESPONSE' not in (ws.root / 'study.db').read_bytes()
    assert ws.delete_document(target['id'], digest, True)['cached']
    ws.import_document(str(ws.input_root / 'synthetic-0.png'))
    with pytest.raises(DomainError, match='inventory_changed'):
        ws.delete_document(target['id'], digest, True)


@pytest.mark.parametrize('problem', ['unacknowledged', 'stale', 'unknown', 'peer', 'remote'])
def test_refusal_before_mutation(deletion_workspace, problem):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    peer = None
    acknowledged = True
    if problem == 'unacknowledged':
        acknowledged = False
    elif problem == 'stale':
        (ws.root / target['id'] / 'new.txt').write_text('synthetic change')
    elif problem == 'unknown':
        ws._put('future', 'unknown', {'document_id': target['id']})
    elif problem == 'peer':
        peer = Workspace(ws.root, ws.input_root, TextIn('', ''))
    elif problem == 'remote':
        lock = asyncio.Lock()
        asyncio.run(lock.acquire())
        ws.remote_locks['synthetic'] = lock
    rows = ws.db.execute('SELECT * FROM objects').fetchall()
    try:
        with pytest.raises(DomainError):
            ws.delete_document(target['id'], digest, acknowledged)
        assert ws.db.execute('SELECT * FROM objects').fetchall() == rows
        assert Path(target['source']).is_file()
        assert ws.db.execute('SELECT count(*) FROM deletion_pending').fetchone()[0] == 0
    finally:
        if peer:
            peer.close()


@pytest.mark.parametrize('fail_after', [0, 3, 30])
def test_restart_resumes_partial_file_deletion(deletion_workspace, fail_after):
    ws, target, records, shared, unique, child, grandchild, mixed, unrelated = deletion_workspace
    digest = preview(ws, target)
    original = deletion.os.unlink
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        if calls == fail_after:
            raise OSError('Synthetic process interruption')
        calls += 1
        return original(*args, **kwargs)

    with patch.object(deletion.os, 'unlink', interrupted):
        with pytest.raises(DomainError, match='deletion_pending'):
            ws.delete_document(target['id'], digest, True)
    assert ws._closed
    resumed = Workspace(ws.root, ws.input_root, TextIn('', ''))
    try:
        assert resumed.delete_document(target['id'], digest, True)['cached']
        assert not (ws.root / target['id']).exists()
        assert not Path(mixed['folder']).exists() and Path(unrelated['folder']).exists()
        assert resumed._get('record', records[1]['id'])
    finally:
        resumed.close()


def test_recovery_refuses_replaced_file_and_can_resume_after_restore(deletion_workspace):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    with patch.object(deletion, '_remove_files', side_effect=OSError('Synthetic failure')):
        with pytest.raises(DomainError, match='deletion_pending'):
            ws.delete_document(target['id'], digest, True)
    source = Path(target['source'])
    original = source.read_bytes()
    source.write_bytes(b'NEW CONTENT MUST SURVIVE')
    with pytest.raises(DomainError, match='deletion_pending'):
        Workspace(ws.root, ws.input_root, TextIn('', ''))
    assert source.read_bytes() == b'NEW CONTENT MUST SURVIVE'
    source.write_bytes(original)
    resumed = Workspace(ws.root, ws.input_root, TextIn('', ''))
    assert resumed.delete_document(target['id'], digest, True)['cached']
    resumed.close()


def test_unknown_new_file_during_recovery_is_preserved(deletion_workspace):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    with patch.object(deletion, '_remove_files', side_effect=OSError('Synthetic failure')):
        with pytest.raises(DomainError, match='deletion_pending'):
            ws.delete_document(target['id'], digest, True)
    unknown = ws.root / target['id'] / 'new-unreviewed.txt'
    unknown.write_text('Preserve me')
    with pytest.raises(DomainError, match='deletion_pending'):
        Workspace(ws.root, ws.input_root, TextIn('', ''))
    assert unknown.read_text() == 'Preserve me'


def test_journal_commit_failure_rolls_back_before_any_file_deletion(deletion_workspace):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    ws.db.execute("CREATE TRIGGER synthetic_failure BEFORE DELETE ON objects BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END")
    ws.db.commit()
    with pytest.raises(Exception, match='Synthetic failure'):
        ws.delete_document(target['id'], digest, True)
    assert Path(target['source']).is_file() and ws._get('document', target['id'])
    assert ws.db.execute('SELECT count(*) FROM deletion_pending').fetchone()[0] == 0


def test_receipt_has_no_user_text_or_paths(deletion_workspace):
    ws, target, *_ = deletion_workspace
    ws.delete_document(target['id'], preview(ws, target), True)
    body = ws.db.execute('SELECT body FROM deletion_receipts').fetchone()[0]
    assert set(json.loads(body)) == {'document_hash', 'inventory_digest', 'deleted', 'counts', 'files_total', 'bytes_total', 'scope'}
    assert target['id'] not in body and str(ws.root) not in body and 'SYNTHETIC' not in body


def test_real_process_exit_after_database_commit_recovers(deletion_workspace):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    ws.close()
    program = """
import os, sys
from pathlib import Path
from study_errorbook import deletion
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
ws = Workspace(Path(sys.argv[1]), Path(sys.argv[2]), TextIn('', ''))
def crash(*args):
    os._exit(71)
deletion._remove_files = crash
ws.delete_document(sys.argv[3], sys.argv[4], True)
"""
    process = subprocess.run([sys.executable, '-c', program, str(ws.root), str(ws.input_root), target['id'], digest], timeout=20, capture_output=True)
    assert process.returncode == 71, process.stderr.decode()
    assert Path(target['source']).is_file()
    resumed = Workspace(ws.root, ws.input_root, TextIn('', ''))
    try:
        assert resumed.delete_document(target['id'], digest, True)['cached']
        assert not Path(target['source']).exists()
    finally:
        resumed.close()


def test_checkpoint_failure_never_reports_success_and_restart_recovers(deletion_workspace):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    with patch.object(deletion, '_checkpoint', side_effect=DomainError('deletion_pending', 'Synthetic busy checkpoint')):
        with pytest.raises(DomainError, match='deletion_pending'):
            ws.delete_document(target['id'], digest, True)
    assert ws._closed
    resumed = Workspace(ws.root, ws.input_root, TextIn('', ''))
    assert resumed.delete_document(target['id'], digest, True)['cached']
    resumed.close()


def test_lock_conversion_failure_closes_if_shared_lock_cannot_be_restored(deletion_workspace):
    import fcntl
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    original = fcntl.flock
    def fail_conversion(handle, mode):
        if handle is ws.lock and mode & fcntl.LOCK_NB:
            raise BlockingIOError('Synthetic competing exclusive lock')
        return original(handle, mode)
    with patch.object(fcntl, 'flock', fail_conversion):
        with pytest.raises(DomainError, match='workspace_busy'):
            ws.delete_document(target['id'], digest, True)
    assert ws._closed and Path(target['source']).is_file()
    ws.close()


def test_peer_process_prevents_deletion_then_release_allows_it(deletion_workspace):
    ws, target, *_ = deletion_workspace
    digest = preview(ws, target)
    program = """
import sys
from pathlib import Path
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
ws = Workspace(Path(sys.argv[1]), Path(sys.argv[2]), TextIn('', ''))
print('ready', flush=True)
sys.stdin.read(1)
ws.close()
"""
    process = subprocess.Popen([sys.executable, '-c', program, str(ws.root), str(ws.input_root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        with pytest.raises(DomainError, match='workspace_busy'):
            ws.delete_document(target['id'], digest, True)
        assert Path(target['source']).is_file()
        process.communicate('x', timeout=10)
        assert process.returncode == 0
        assert ws.delete_document(target['id'], digest, True)['deleted']
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
