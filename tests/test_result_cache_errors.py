"""Damaged local OCR caches must not imply a failed remote parse or a new upload."""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from study_errorbook import service
from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import PARSE_CONFIG, TextIn


@pytest.fixture
def cached_result(tmp_path):
    def forbidden(request):
        pytest.fail('Reading a damaged cache must not call TextIn')

    source = tmp_path / 'synthetic.png'
    Image.new('RGB', (20, 20), 'white').save(source)
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', '', httpx.MockTransport(forbidden)))
    doc = ws.import_document(str(source))
    data = {'elements': [{'text': 'Synthetic cache text', 'page_number': 1,
                         'coordinates': [0, 0, 1, 0, 1, 1, 0, 1]}],
            'pages': [{'page_number': 1, 'angle': 0, 'status': 'Success'}], 'success_count': 1}
    job = {'id': 'synthetic_cache', 'document_id': doc['id'], 'requested_pages': 1,
           'mode': 'free_sync', 'config': PARSE_CONFIG}
    ws._cache_parse_result(job, doc, data)
    ws._put('job', job['id'], job)
    try:
        yield ws, job, data
    finally:
        ws.close()


@pytest.mark.parametrize('state', ['completed', 'partial'])
@pytest.mark.parametrize('defect', ['missing', 'truncated', 'utf8', 'nonobject',
                                  'missing_elements', 'invalid_elements', 'nondict_element', 'directory'])
async def test_damaged_cache_preserves_job_and_recovers_after_local_restore(cached_result, state, defect):
    ws, job, data = cached_result
    job['status'] = state
    ws._put('job', job['id'], job)
    path = Path(job['result_path'])
    raw = path.read_bytes()
    if defect == 'missing':
        path.unlink()
    elif defect == 'directory':
        path.unlink()
        path.mkdir()
    else:
        payloads = {'truncated': b'{"elements":', 'utf8': b'\xff', 'nonobject': b'[]',
                    'missing_elements': b'{}', 'invalid_elements': b'{"elements":{}}',
                    'nondict_element': b'{"elements":[null]}'}
        path.write_bytes(payloads[defect])
    before = ws.db.execute('SELECT rowid,* FROM objects ORDER BY kind,id').fetchall()
    for reader in (lambda: ws.read_document(job['id']),
                   lambda: ws.read_element(job['id'], 0),
                   lambda: ws.read_element_evidence(job['id'], 0)):
        with pytest.raises(DomainError, match='cached_result_unavailable') as caught:
            reader()
        assert '不' in caught.value.message and str(path) not in caught.value.message
    assert (await ws.get_job(job['id']))['status'] == state
    assert ws.list_parse_jobs(job['document_id'])['items'][0]['status'] == state
    assert ws.db.execute('SELECT rowid,* FROM objects ORDER BY kind,id').fetchall() == before
    if path.is_dir():
        path.rmdir()
    path.write_bytes(raw)
    assert ws.read_document(job['id'])['elements'][0]['text'] == data['elements'][0]['text']
    assert ws.read_element(job['id'], 0)['content'] == data['elements'][0]['text']
    assert ws.db.execute('SELECT rowid,* FROM objects ORDER BY kind,id').fetchall() == before


async def test_cache_failure_is_actionable_tool_error_and_same_process_recovers(cached_result, tmp_path):
    ws, job, _ = cached_result
    path = Path(job['result_path'])
    original = path.read_bytes()
    path.write_bytes(b'{"elements":[null]}')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'study_errorbook.server'], env={
        'STUDY_DATA_DIR': str(ws.root), 'STUDY_INPUT_ROOT': str(tmp_path),
        'STUDY_CONFIG_FILE': str(tmp_path/'absent.env'), 'TEXTIN_APP_ID': '', 'TEXTIN_SECRET_CODE': '',
    })
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            for name, extra in [('read_document', {}), ('read_element', {'element_index': 0}),
                                ('read_element_evidence', {'element_index': 0})]:
                result = await client.call_tool(name, {'job_id': job['id'], **extra})
                assert result.isError
                message = '\n'.join(item.text for item in result.content if item.type == 'text')
                assert 'cached_result_unavailable' in message and str(path) not in message
            path.write_bytes(original)
            recovered = await client.call_tool('read_document', {'job_id': job['id']})
            assert not recovered.isError
            assert recovered.structuredContent['elements'][0]['text'] == 'Synthetic cache text'
            status = await client.call_tool('get_job', {'job_id': job['id']})
            assert status.structuredContent['status'] == 'completed'
    assert json.loads(path.read_text())['success_count'] == 1


@pytest.mark.parametrize('defect', ['file_link', 'directory_link', 'outside_path', 'wrong_name', 'oversized'])
def test_cache_file_boundary_preserves_state_and_recovers(cached_result, monkeypatch, defect):
    ws, job, data = cached_result
    path = Path(job['result_path'])
    original = path.read_bytes()
    moved = None
    if defect == 'file_link':
        other = ws.input_root/'external.json'
        other.write_bytes(original)
        path.unlink()
        path.symlink_to(other)
    elif defect == 'directory_link':
        moved = path.parent.with_name('moved-document')
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
    elif defect in {'outside_path', 'wrong_name'}:
        other = (ws.input_root if defect == 'outside_path' else path.parent)/'other.json'
        other.write_bytes(original)
        ws._put('job', job['id'], {**job, 'result_path': str(other)})
    else:
        monkeypatch.setattr(service, 'MAX_CACHE_BYTES', 1024, raising=False)
        path.write_bytes(original + b' ' * (1025-len(original)))
    before = ws.db.execute('SELECT rowid,* FROM objects ORDER BY kind,id').fetchall()
    for reader in (lambda: ws.read_document(job['id']),
                   lambda: ws.read_element(job['id'], 0),
                   lambda: ws.read_element_evidence(job['id'], 0)):
        with pytest.raises(DomainError, match='cached_result_unavailable'):
            reader()
    assert ws.db.execute('SELECT rowid,* FROM objects ORDER BY kind,id').fetchall() == before
    if moved:
        path.parent.unlink()
        moved.rename(path.parent)
    elif path.is_symlink():
        path.unlink()
    path.write_bytes(original)
    ws._put('job', job['id'], job)
    assert ws.read_document(job['id'])['elements'][0]['text'] == data['elements'][0]['text']


def test_cache_fifo_does_not_block_agent(cached_result):
    ws, job, _ = cached_result
    path = Path(job['result_path'])
    path.unlink()
    os.mkfifo(path)
    program = '''
import sys
from pathlib import Path
from contextlib import closing
from study_errorbook.service import Workspace
from study_errorbook.models import DomainError
from study_errorbook.textin import TextIn
with closing(Workspace(Path(sys.argv[1]), None, TextIn('', ''))) as ws:
    for reader in (lambda: ws.read_document(sys.argv[2]),
                   lambda: ws.read_element(sys.argv[2], 0),
                   lambda: ws.read_element_evidence(sys.argv[2], 0)):
        try:
            reader()
        except DomainError as error:
            assert error.code == 'cached_result_unavailable'
        else:
            raise AssertionError('FIFO accepted')
'''
    result = subprocess.run([sys.executable, '-c', program, str(ws.root), job['id']],
                            capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize('operation', ['fstat', 'fdopen'])
def test_cache_read_failure_releases_descriptors(cached_result, monkeypatch, operation):
    ws, job, _ = cached_result
    def failed(*args, **kwargs):
        raise OSError('Synthetic read failure')
    monkeypatch.setattr(os, operation, failed)
    before = len(os.listdir('/dev/fd'))
    for _ in range(40):
        with pytest.raises(DomainError, match='cached_result_unavailable'):
            ws.read_document(job['id'])
    assert len(os.listdir('/dev/fd')) == before


def test_cache_size_boundary_and_growth_during_read(cached_result, monkeypatch):
    ws, job, _ = cached_result
    path = Path(job['result_path'])
    original = path.read_bytes()
    monkeypatch.setattr(service, 'MAX_CACHE_BYTES', 1024, raising=False)
    path.write_bytes(original + b' ' * (1024-len(original)))
    assert ws.read_document(job['id'])['elements'][0]['text'] == 'Synthetic cache text'
    fdopen = os.fdopen
    def grow_after_stat(fd, *args, **kwargs):
        with path.open('ab') as stream:
            stream.write(b' ')
        return fdopen(fd, *args, **kwargs)
    monkeypatch.setattr(os, 'fdopen', grow_after_stat)
    with pytest.raises(DomainError, match='cached_result_unavailable'):
        ws.read_document(job['id'])
