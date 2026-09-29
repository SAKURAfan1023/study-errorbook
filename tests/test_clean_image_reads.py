import hashlib
import os
import subprocess
import sys

import pytest
from PIL import Image
from test_clean_export import clean_workspace as _clean_workspace

from study_errorbook.models import DomainError

clean_workspace = _clean_workspace


def test_directory_clean_image_repeated_failures_close_descriptors(clean_workspace):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    original = clean.read_bytes()
    clean.unlink()
    clean.mkdir()
    before = len(os.listdir('/dev/fd'))
    for _ in range(40):
        assert ws.read_record(saved['id'])['clean_copy_validation'][0]['status'] == 'unavailable'
    assert len(os.listdir('/dev/fd')) == before
    assert ws.read_record(saved['id'])['revision'] == 1
    clean.rmdir()
    clean.write_bytes(original)
    assert ws.read_clean_image(record.evidence_ids[0]) == original


@pytest.mark.parametrize('damage', ['file_link', 'directory_link', 'outside_path', 'not_png', 'oversized'])
def test_unavailable_clean_images_cannot_be_approved_or_exported(clean_workspace, damage):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    original = clean.read_bytes()
    evidence_id = record.evidence_ids[0]
    if damage == 'file_link':
        other = clean.with_name('other.png')
        other.write_bytes(original)
        clean.unlink()
        clean.symlink_to(other)
    elif damage == 'directory_link':
        moved = clean.parent.with_name('moved-document')
        clean.parent.rename(moved)
        clean.parent.symlink_to(moved, target_is_directory=True)
    elif damage == 'outside_path':
        outside = ws.input_root / 'outside.png'
        outside.write_bytes(original)
        action = ws._get('erase', evidence_id)
        ws._put('erase', evidence_id, {**action, 'path': str(outside)})
    elif damage == 'not_png':
        Image.new('RGB', (30, 30), 'white').save(clean, format='JPEG')
    else:
        with clean.open('wb') as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
    validation = ws.read_record(saved['id'])['clean_copy_validation'][0]
    assert validation['status'] == 'unavailable' and validation['sha256'] is None
    with pytest.raises(DomainError, match='invalid_clean_review'):
        ws.review_record(saved['id'], 1, 'include', 'Synthetic review.',
                         approved_clean_copies={evidence_id: hashlib.sha256(original).hexdigest()})
    with pytest.raises(DomainError, match='clean_unavailable'):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert not (ws.root / 'exports').exists()


def test_read_record_with_fifo_returns_instead_of_hanging(clean_workspace):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    clean.unlink()
    os.mkfifo(clean)
    ws.close()
    program = '''
import sys
from pathlib import Path
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
ws = Workspace(Path(sys.argv[1]), Path(sys.argv[2]), TextIn('', ''))
try:
    assert ws.read_record(sys.argv[3])['clean_copy_validation'][0]['status'] == 'unavailable'
finally:
    ws.close()
'''
    try:
        result = subprocess.run([sys.executable, '-c', program, str(ws.root), str(ws.input_root), saved['id']], capture_output=True, timeout=3)
    except subprocess.TimeoutExpired:
        pytest.fail('read_record blocked on a FIFO clean image')
    assert result.returncode == 0, result.stderr.decode()


async def test_cached_clean_mcp_rejects_fifo_without_resubmitting(clean_workspace):
    import asyncio
    import json

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    evidence_id = record.evidence_ids[0]
    approved = {evidence_id: hashlib.sha256(clean.read_bytes()).hexdigest()}
    ws.review_record(saved['id'], 1, 'include', 'Synthetic fixture.', approved_clean_copies=approved)
    before = ws._get('erase', evidence_id)
    clean.unlink()
    os.mkfifo(clean)
    params = StdioServerParameters(command=sys.executable, args=['-m', 'study_errorbook.server'], env={
        'STUDY_DATA_DIR': str(ws.root), 'STUDY_INPUT_ROOT': str(ws.input_root),
        'STUDY_CONFIG_FILE': str(ws.root / 'absent.env'), 'TEXTIN_APP_ID': '', 'TEXTIN_SECRET_CODE': '',
    })
    async with asyncio.timeout(5):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                current = await session.call_tool('read_study_record', {'record_id': saved['id']})
                assert not current.isError
                assert current.structuredContent['clean_copy_validation'][0]['status'] == 'unavailable'
                for name, args, error in [
                    ('make_clean_copy', {'evidence_id': evidence_id}, 'clean_unavailable'),
                    ('export_errorbook', {'record_ids': [saved['id']]}, 'clean_unavailable'),
                    ('read_practice_prompt', {'record_id': saved['id'], 'expected_revision': 2,
                      'practice_kind': 'original', 'presentation': 'clean_images'}, 'clean_not_ready'),
                ]:
                    result = await session.call_tool(name, args)
                    assert result.isError and error in json.dumps([p.model_dump() for p in result.content])
                    assert all(p.type != 'image' for p in result.content)
    assert ws._get('erase', evidence_id) == before
    assert ws.read_record(saved['id'])['revision'] == 2


def test_clean_export_rechecks_bytes_after_validation(clean_workspace, monkeypatch):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    ws.review_record(saved['id'], 1, 'include', 'Synthetic fixture.',
                     approved_clean_copies={record.evidence_ids[0]: hashlib.sha256(clean.read_bytes()).hexdigest()})
    original = ws.read_clean_image
    reads = 0
    def replace_after_check(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            Image.new('RGB', (20, 20), 'red').save(clean)
        return original(*args, **kwargs)
    monkeypatch.setattr(ws, 'read_clean_image', replace_after_check)
    with pytest.raises(DomainError, match='clean_changed'):
        ws.export_errorbook([saved['id']])
    assert list((ws.root / 'exports').iterdir()) == []
