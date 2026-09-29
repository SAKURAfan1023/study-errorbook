import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.deletion import preview_document_deletion
from study_errorbook.models import DomainError, Region
from study_errorbook.service import Workspace
from study_errorbook.textin import VLM_CONFIG, TextIn


@pytest.fixture
def material(tmp_path):
    path = tmp_path / 'sample.png'
    Image.new('RGB', (100, 100), 'white').save(path)
    return path


def payload():
    return {'code': 200, 'version': 'fixture.1', 'x_request_id': 'body-id',
            'result': {'image_angle': 0, 'image_width': 100, 'image_height': 100,
                       'parse_chunk_results': [{'text': r'$\frac{7}{12}$', 'chunk_type': 'text',
                                                'position': [1, 2, 3, 4], 'sub_labels': ['handwriting']}]}}


async def test_vlm_independent_wire_format_cache_evidence_and_deletion(tmp_path, material):
    calls = []
    raw = json.dumps(payload(), indent=2).encode()

    def provider(request):
        calls.append(request)
        if request.url.path.endswith('/parse/sync'):
            return httpx.Response(200, json={'code': 200, 'data': {'success_count': 1, 'elements': []}})
        assert str(request.url) == VLM_CONFIG['endpoint']
        assert request.headers['x-ti-app-id'] == 'fixture-app-long'
        assert request.headers['x-ti-secret-code'] == 'fixture-secret-long'
        assert request.headers['content-type'] == 'application/octet-stream'
        assert request.content == material.read_bytes()
        return httpx.Response(200, content=raw, headers={'x-request-id': 'header-id'})

    api = TextIn('fixture-app-long', 'fixture-secret-long', httpx.MockTransport(provider))
    ws = Workspace(tmp_path / 'state', tmp_path, api)
    try:
        doc = ws.import_document(str(material))
        first, second = await asyncio.gather(*[ws.parse_document(doc['id'], 1, 'vlm_sync') for _ in range(2)])
        assert first['status'] == 'completed' and second['cached'] and len(calls) == 1
        receipt = first['provider_receipt']
        assert Path(receipt['response_path']).read_bytes() == raw
        assert receipt['response_sha256'] == hashlib.sha256(raw).hexdigest()
        assert receipt['request_ids'] == {'x-request-id': 'header-id'}
        assert 'response_body' not in receipt
        assert json.loads(Path(first['result_path']).read_text()) == payload()
        view = ws.read_document(first['id'])
        assert view['format'] == 'textin_vlm_v4' and 'elements' not in view
        assert view['chunks'][0]['text'] == payload()['result']['parse_chunk_results'][0]['text']
        assert json.loads(ws.read_element(first['id'], 0, 'element')['content']) == payload()['result']['parse_chunk_results'][0]
        assert json.loads(ws.read_element(first['id'], 0, 'page')['content']) == {'image_angle': 0, 'image_width': 100, 'image_height': 100}
        assert not ws.read_element(first['id'], 0, 'char_details')['present']
        with pytest.raises(DomainError, match='coordinate_mapping_unavailable'):
            ws.read_element_evidence(first['id'], 0)
        evidence, binary = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
        assert evidence['document_id'] == doc['id'] and binary
        free = await ws.parse_document(doc['id'], 1, 'free_sync')
        assert free['id'] != first['id'] and len(calls) == 2
        preview = preview_document_deletion(ws.db, ws.root, doc['id'])
        assert any(item.get('sha256') == receipt['response_sha256'] for item in preview['items'])
    finally:
        ws.close()
    reopened = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    try:
        assert reopened.read_document(first['id']) == view
        assert (await reopened.get_job(first['id']))['status'] == 'completed'
        Path(first['result_path']).write_text('{}')
        with pytest.raises(DomainError, match='cached_result_unavailable'):
            reopened.read_document(first['id'])
        assert (await reopened.get_job(first['id']))['status'] == 'completed'
    finally:
        reopened.close()


@pytest.mark.parametrize('variant,status', [('schema', 'submission_unknown'), ('http', 'submission_unknown'),
                                          ('business', 'failed'), ('timeout', 'submission_unknown'),
                                          ('cancel', 'submission_unknown'), ('echo', 'completed')])
async def test_vlm_receipts_no_automatic_retry_and_explicit_history(tmp_path, material, variant, status):
    calls = []

    def provider(request):
        calls.append(request)
        data = payload()
        if len(calls) == 1:
            if variant == 'timeout':
                raise httpx.ReadTimeout('fixture', request=request)
            if variant == 'cancel':
                raise asyncio.CancelledError()
            if variant == 'schema':
                data['result']['parse_chunk_results'] = [{'text': None}]
            if variant == 'business':
                data['code'] = 40306
            if variant == 'echo':
                data['result']['parse_chunk_results'][0]['text'] = 'fixture-secret-long'
        return httpx.Response(500 if variant == 'http' and len(calls) == 1 else 200, json=data)

    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('fixture-app-long', 'fixture-secret-long', httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        if variant == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await ws.parse_document(doc['id'], 1, 'vlm_sync')
            first = json.loads(ws.db.execute("SELECT body FROM objects WHERE kind='job'").fetchone()[0])
        else:
            first = await ws.parse_document(doc['id'], 1, 'vlm_sync')
        assert first['status'] == status
        assert first['provider_receipt']['response_saved'] == (variant not in {'timeout', 'cancel'})
        assert (await ws.parse_document(doc['id'], 1, 'vlm_sync'))['cached'] and len(calls) == 1
        if variant == 'echo':
            assert ws.read_document(first['id'])['chunks'][0]['text'] == '[REDACTED]'
            for file in (ws.root / doc['id']).glob('*'):
                assert b'fixture-secret-long' not in file.read_bytes()
        else:
            with pytest.raises(DomainError, match='retry_confirmation'):
                await ws.retry_parse_document(first['id'], 1, 1, False, '')
            retry = await ws.retry_parse_document(first['id'], 1, 1, True, 'fixture explicit retry')
            assert retry['status'] == 'completed' and retry['attempt_number'] == 2 and len(calls) == 2
            history = ws.parse_attempt_history(first['id'])
            assert history['items'][0] == first
            assert retry['provider_receipt']['response_path'].endswith('attempt-2-vlm-response.bin')
    finally:
        ws.close()


@pytest.mark.parametrize('variant,code', [('pdf', 'vlm_image_only'), ('large', 'vlm_file_limit'), ('credentials', 'credentials_missing')])
async def test_vlm_preflight_never_uploads(tmp_path, material, monkeypatch, variant, code):
    api = TextIn('' if variant == 'credentials' else 'fixture-id', 'fixture-secret',
                 httpx.MockTransport(lambda _: pytest.fail('must not upload')))
    ws = Workspace(tmp_path / 'state', tmp_path, api)
    try:
        doc = ws.import_document(str(material))
        if variant == 'pdf':
            doc['source'] = str(tmp_path / 'sample.pdf')
            ws._put('document', doc['id'], doc)
            monkeypatch.setattr(ws, '_read_document_source', lambda _: b'fixture-pdf')
        elif variant == 'large':
            monkeypatch.setattr(ws, '_read_document_source', lambda _: b'x' * (10 * 1024 * 1024 + 1))
        with pytest.raises(DomainError, match=code):
            await ws.parse_document(doc['id'], 1, 'vlm_sync')
        assert ws.db.execute("SELECT count(*) FROM objects WHERE kind='job'").fetchone()[0] == 0
    finally:
        ws.close()


async def test_vlm_deadline_and_bounded_lossless_read(tmp_path, material, monkeypatch):
    original_timeout = asyncio.timeout
    monkeypatch.setattr('study_errorbook.textin.asyncio.timeout', lambda _: original_timeout(0.01))

    async def slow(request):
        await asyncio.sleep(1)

    api = TextIn('fixture-id', 'fixture-secret', httpx.MockTransport(slow))
    with pytest.raises(DomainError, match='network_uncertain'):
        await api.parse_vlm(b'image', receipt={})
    data = payload()
    text = '\x00' * 9000 + '保留末尾'
    data['result']['parse_chunk_results'] = [{'text': text, 'position': [1] * 1000}] * 5
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('fixture-id', 'fixture-secret',
                   httpx.MockTransport(lambda _: httpx.Response(200, json=data))))
    try:
        doc = ws.import_document(str(material))
        job = await ws.parse_document(doc['id'], 1, 'vlm_sync')
        overview = ws.read_document(job['id'])
        assert len(json.dumps(overview)) < 20000 and overview['chunks']
        assert 'text' in overview['chunks'][0]['omitted_fields']
        pieces, offset = [], 0
        while offset is not None:
            part = ws.read_element(job['id'], 0, 'text', offset, 8000)
            pieces.append(part['content'])
            assert len(json.dumps(part['content'])) <= 16000
            offset = part['next_offset']
        assert ''.join(pieces) == text
    finally:
        ws.close()


@pytest.mark.parametrize('metadata', [
    {'image_width': 100, 'image_height': 100, 'image_angle': 270, 'image_angle_score': 0.83},
    {},
    {'image_width': 'x' * 2000, 'image_height': 100, 'image_angle': 0},
])
async def test_empty_vlm_exposes_bounded_image_metadata_without_inventing_blankness(tmp_path, material, metadata):
    calls = []
    data = {'code': 200, 'version': 'fixture.empty',
            'result': {'parse_chunk_results': [], **metadata}}

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('fixture-app', 'fixture-secret', httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        job = await ws.parse_document(doc['id'], 1, 'vlm_sync')
        assert job['status'] == 'completed' and job['chunk_count'] == 0
        overview = ws.read_document(job['id'])
        assert overview['chunks_empty'] is True
        assert overview['chunks'] == [] and overview['total'] == 0 and overview['next_offset'] is None
        if len(str(metadata.get('image_width', ''))) > 200:
            assert overview['image_metadata']['image_width'] is None
            assert overview['omitted_image_metadata_fields'] == ['image_width']
            assert len(json.dumps(overview)) < 4000
        else:
            assert overview['image_metadata'] == metadata
            assert overview['omitted_image_metadata_fields'] == []
        assert 'is_blank' not in overview and 'unanswered' not in overview
        assert json.loads(Path(job['result_path']).read_text()) == data
        assert json.loads(Path(job['provider_receipt']['response_path']).read_bytes()) == data
        with pytest.raises(DomainError, match='element_out_of_range'):
            ws.read_element(job['id'], 0, 'page')
        assert (await ws.parse_document(doc['id'], 1, 'vlm_sync'))['cached']
        assert len(calls) == 1
    finally:
        ws.close()
    reopened = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    try:
        assert reopened.read_document(job['id']) == overview
        assert (await reopened.get_job(job['id']))['provider_receipt'] == job['provider_receipt']
    finally:
        reopened.close()


@pytest.mark.parametrize('chunk', [
    {'chunk_type': 'image', 'position': [1, 2, 3, 4]},
    {'chunk_type': 'stamp'},
    {'chunk_type': 'chart', 'text': ''},
    {'chunk_type': 'textchunk', 'text': 'E'},
])
async def test_optional_chunk_text_preserves_absence_cache_and_reopen(tmp_path, material, chunk):
    data = payload()
    data['result']['parse_chunk_results'] = [chunk, {'chunk_type': 'textchunk', 'text': 'next'}]
    raw = json.dumps(data, ensure_ascii=False).encode()
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, content=raw)

    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('fixture-app', 'fixture-secret', httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        job = await ws.parse_document(doc['id'], 1, 'vlm_sync')
        assert job['status'] == 'completed' and job['chunk_count'] == 2
        view = ws.read_document(job['id'], limit=1)
        first = view['chunks'][0]
        assert first['text_present'] is ('text' in chunk)
        assert first['text'] == chunk.get('text')
        assert first['text_truncated'] is False and first['omitted_fields'] == []
        assert view['chunks_empty'] is False and view['next_offset'] == 1
        assert ws.read_document(job['id'], offset=1)['chunks'][0]['text'] == 'next'
        detail = ws.read_element(job['id'], 0, 'text')
        assert detail['present'] is ('text' in chunk)
        assert detail['content'] == (chunk['text'] if 'text' in chunk else 'null')
        assert json.loads(ws.read_element(job['id'], 0, 'element')['content']) == chunk
        assert json.loads(Path(job['result_path']).read_text()) == data
        assert Path(job['provider_receipt']['response_path']).read_bytes() == raw
        assert (await ws.parse_document(doc['id'], 1, 'vlm_sync'))['cached']
        assert len(calls) == 1
    finally:
        ws.close()
    reopened = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    try:
        assert reopened.read_document(job['id'], limit=1) == view
        assert reopened.read_element(job['id'], 0, 'text') == detail
    finally:
        reopened.close()
