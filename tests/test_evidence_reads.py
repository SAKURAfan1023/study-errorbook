import base64
import hashlib
import io
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def evidence_workspace(tmp_path):
    source = tmp_path/'synthetic.png'
    Image.new('RGB', (40, 40), 'white').save(source)
    uploads = []
    response = source.read_bytes()

    def provider(request):
        uploads.append(request.content)
        return httpx.Response(200, json={'code': 200, 'result': {'image': base64.b64encode(response).decode()}})

    with closing(Workspace(tmp_path/'state', tmp_path, TextIn('fake-id', 'fake-secret', httpx.MockTransport(provider)))) as ws:
        doc = ws.import_document(str(source))
        evidence, binary = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
        saved = ws.save_record(StudyRecord(document_id=doc['id'], question_label='Synthetic fixture',
                                         stem='Synthetic evidence reader', evidence_ids=[evidence['id']]))
        yield ws, evidence, binary, saved, uploads


@pytest.mark.parametrize('damage', ['file_link', 'directory_link', 'outside_path', 'directory', 'not_png', 'truncated', 'oversized'])
async def test_invalid_evidence_is_not_uploaded_or_exported(evidence_workspace, damage):
    ws, evidence, original, saved, uploads = evidence_workspace
    target = Path(evidence['path'])
    if damage == 'file_link':
        other = ws.input_root/'not-authorized-for-erasure.png'
        other.write_bytes(original)
        target.unlink()
        target.symlink_to(other)
    elif damage == 'directory_link':
        moved = target.parent.with_name('moved-document')
        target.parent.rename(moved)
        target.parent.symlink_to(moved, target_is_directory=True)
    elif damage == 'outside_path':
        other = ws.input_root/'outside.png'
        other.write_bytes(original)
        ws._put('evidence', evidence['id'], {**evidence, 'path': str(other)})
    elif damage == 'directory':
        target.unlink()
        target.mkdir()
    elif damage == 'not_png':
        Image.new('RGB', (40, 40), 'red').save(target, format='JPEG')
    elif damage == 'truncated':
        target.write_bytes(original[:50])
    else:
        with target.open('wb') as stream:
            stream.truncate(20*1024*1024+1)
    expected = 'evidence_image_too_large' if damage == 'oversized' else 'evidence_unavailable'
    before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
    with pytest.raises(DomainError, match=expected):
        await ws.make_clean_copy(evidence['id'])
    assert uploads == []
    assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before
    with pytest.raises(DomainError, match=expected):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert list((ws.root/'exports').iterdir()) == []
    assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before


async def test_erase_upload_uses_validated_bytes_and_cached_result_does_not_resubmit(evidence_workspace, monkeypatch):
    ws, evidence, original, _, uploads = evidence_workspace
    target = Path(evidence['path'])
    replacement = io.BytesIO()
    Image.new('RGB', (40, 40), 'red').save(replacement, format='PNG')
    put = ws._put

    def replace_after_marker(kind, key, body):
        put(kind, key, body)
        if kind == 'erase' and body['status'] == 'submitting':
            target.write_bytes(replacement.getvalue())

    monkeypatch.setattr(ws, '_put', replace_after_marker)
    result = await ws.make_clean_copy(evidence['id'])
    assert result['status'] == 'completed' and uploads == [original]
    assert result['submitted_source_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['submitted_source_bytes'] == len(original)
    target.unlink()
    ws.api = TextIn('', '')
    assert (await ws.make_clean_copy(evidence['id']))['cached']
    assert uploads == [original]


def test_evidence_directory_failure_closes_descriptors(evidence_workspace):
    ws, evidence, _, _, _ = evidence_workspace
    target = Path(evidence['path'])
    target.unlink()
    target.mkdir()
    before = len(os.listdir('/dev/fd'))
    for _ in range(40):
        with pytest.raises(DomainError, match='evidence_unavailable'):
            ws._read_evidence_image(evidence['id'])
    assert len(os.listdir('/dev/fd')) == before


def test_evidence_fifo_returns_without_blocking(evidence_workspace):
    ws, evidence, _, saved, _ = evidence_workspace
    target = Path(evidence['path'])
    target.unlink()
    os.mkfifo(target)
    program = '''
import asyncio, sys
from contextlib import closing
from pathlib import Path
import httpx
from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
def forbidden(request):
    raise AssertionError('Unexpected upload')
with closing(Workspace(Path(sys.argv[1]), None, TextIn('fake', 'fake', httpx.MockTransport(forbidden)))) as ws:
    for operation in (
        lambda: asyncio.run(ws.make_clean_copy(sys.argv[2])),
        lambda: ws.export_errorbook([sys.argv[3]], include_drafts=True),
    ):
        try:
            operation()
        except DomainError as error:
            assert error.code == 'evidence_unavailable'
        else:
            raise AssertionError('FIFO accepted')
'''
    result = subprocess.run([sys.executable, '-c', program, str(ws.root), evidence['id'], saved['id']],
                            capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize('operation', ['fstat', 'fdopen'])
def test_evidence_read_errors_close_file_descriptors(evidence_workspace, monkeypatch, operation):
    ws, evidence, _, _, _ = evidence_workspace
    def failed(*args, **kwargs):
        raise OSError('Synthetic failure')
    monkeypatch.setattr(os, operation, failed)
    before = len(os.listdir('/dev/fd'))
    for _ in range(40):
        with pytest.raises(DomainError, match='evidence_unavailable'):
            ws._read_evidence_image(evidence['id'])
    assert len(os.listdir('/dev/fd')) == before
