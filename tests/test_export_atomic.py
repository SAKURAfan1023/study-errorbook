import json
from contextlib import closing
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.deletion import preview_document_deletion
from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def export_workspace(tmp_path):
    source = tmp_path / 'sample.png'
    Image.new('RGB', (30, 30), 'blue').save(source)
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    doc = ws.import_document(str(source))
    evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
    saved = ws.save_record(StudyRecord(document_id=doc['id'], question_label='Synthetic',
                                      stem='Synthetic source', evidence_ids=[evidence['id']]))
    existing = ws.export_errorbook([saved['id']], include_drafts=True)
    yield ws, doc, evidence, saved, existing
    ws.close()


def test_missing_evidence_does_not_leave_broken_export_or_block_later_deletion(export_workspace):
    ws, doc, evidence, saved, existing = export_workspace
    path = Path(evidence['path'])
    raw = path.read_bytes()
    path.unlink()
    with pytest.raises(DomainError, match='evidence_unavailable'):
        ws.export_errorbook([saved['id']], include_drafts=True)
    path.write_bytes(raw)
    assert list((ws.root / 'exports').iterdir()) == [Path(existing['folder'])]
    assert preview_document_deletion(ws.db, ws.root, doc['id'])['inventory_complete']
    retry = ws.export_errorbook([saved['id']], include_drafts=True)
    assert Path(retry['manifest_path']).is_file()


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt])
def test_html_failure_removes_only_new_attempt(export_workspace, monkeypatch, failure):
    ws, doc, _, saved, existing = export_workspace
    old_files = {p.relative_to(existing['folder']): p.read_bytes() for p in Path(existing['folder']).rglob('*') if p.is_file()}
    original = Path.write_text
    def fail(path, text, *args, **kwargs):
        if path.name == 'practice.html':
            raise failure('synthetic write interruption')
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(failure):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert list((ws.root / 'exports').iterdir()) == [Path(existing['folder'])]
    assert old_files == {p.relative_to(existing['folder']): p.read_bytes() for p in Path(existing['folder']).rglob('*') if p.is_file()}
    assert preview_document_deletion(ws.db, ws.root, doc['id'])['inventory_complete']


def test_publish_failure_cleans_staging(export_workspace, monkeypatch):
    ws, doc, _, saved, existing = export_workspace
    original = Path.rename
    def fail(path, target):
        if path.parent == ws.root / 'exports':
            assert json.loads((path / 'manifest.json').read_text())['records'][0]['id'] == saved['id']
            assert not Path(target).exists()
            raise OSError('synthetic publish failure')
        return original(path, target)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(OSError, match='publish failure'):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert list((ws.root / 'exports').iterdir()) == [Path(existing['folder'])]
    assert preview_document_deletion(ws.db, ws.root, doc['id'])['inventory_complete']


@pytest.mark.parametrize('phase', ['practice.html', 'manifest.json.tmp'])
def test_process_exit_leaves_classifiable_staging(export_workspace, phase):
    import subprocess
    import sys

    ws, doc, _, saved, existing = export_workspace
    ws.close()
    program = '''
import os, sys
from pathlib import Path
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
ws = Workspace(Path(sys.argv[1]), Path(sys.argv[2]), TextIn('', ''))
original = Path.write_text
def crash(path, text, *args, **kwargs):
    if path.name == sys.argv[4] and (path.parent / 'errorbook.html').exists():
        path.write_bytes(b'partial synthetic output')
        os._exit(71)
    return original(path, text, *args, **kwargs)
Path.write_text = crash
ws.export_errorbook([sys.argv[3]], include_drafts=True)
'''
    process = subprocess.run([sys.executable, '-c', program, str(ws.root), str(ws.input_root), saved['id'], phase], capture_output=True, timeout=20)
    assert process.returncode == 71, process.stderr.decode()
    folders = list((ws.root / 'exports').iterdir())
    staging, = [p for p in folders if p != Path(existing['folder'])]
    assert staging.name.startswith('.export_')
    marker = json.loads((staging / 'manifest.json').read_text())
    assert marker['status'] == 'building'
    assert marker['records'] == [{'id': saved['id'], 'revision': saved['revision']}]
    with closing(Workspace(ws.root, ws.input_root, TextIn('', ''))) as resumed:
        preview = preview_document_deletion(resumed.db, resumed.root, doc['id'], limit=50)
        assert preview['inventory_complete']
        exports = [item['path'] for item in preview['items'] if item['category'] == 'export']
        assert staging.relative_to(ws.root).as_posix() in exports
        retry = resumed.export_errorbook([saved['id']], include_drafts=True)
        assert Path(retry['manifest_path']).is_file()
        preview = preview_document_deletion(resumed.db, resumed.root, doc['id'])
        assert resumed.delete_document(doc['id'], preview['inventory_digest'], True)['deleted']
    assert not staging.exists() and not Path(existing['folder']).exists()
    assert (ws.input_root / 'sample.png').is_file()


def test_existing_destination_is_not_replaced(export_workspace, monkeypatch):
    from types import SimpleNamespace

    import study_errorbook.service as service

    ws, _, _, saved, existing = export_workspace
    identifier = Path(existing['folder']).name.removeprefix('export_')
    monkeypatch.setattr(service.uuid, 'uuid4', lambda: SimpleNamespace(hex=identifier))
    before = Path(existing['manifest_path']).read_bytes()
    with pytest.raises(FileExistsError):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert Path(existing['manifest_path']).read_bytes() == before
    assert list((ws.root / 'exports').iterdir()) == [Path(existing['folder'])]


def test_export_root_symlink_is_not_followed(export_workspace):
    from study_errorbook.models import DomainError

    ws, _, _, saved, _ = export_workspace
    outside = ws.root.parent / 'moved-exports'
    (ws.root / 'exports').rename(outside)
    (ws.root / 'exports').symlink_to(outside, target_is_directory=True)
    before = list(outside.iterdir())
    with pytest.raises(DomainError, match='符号链接'):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert list(outside.iterdir()) == before
