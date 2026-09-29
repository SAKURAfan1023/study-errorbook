import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def material(tmp_path):
    source = tmp_path / 'sample.png'
    Image.new('RGB', (100, 100), 'white').save(source)
    return source


async def test_free_receipt_preserves_envelope_headers_and_result_across_reopen(tmp_path, material):
    calls = []
    data = {'success_count': 1, 'elements': [{'type': 'Image', 'text': 'EDC8'}], 'schema_version': 'fixture-schema'}
    raw = json.dumps({'code': 200, 'x_request_id': 'body-fixture-id', 'data': data}, indent=2).encode()

    def provider(request):
        calls.append(request)
        assert 'x-ti-app-id' not in request.headers and 'x-ti-secret-code' not in request.headers
        return httpx.Response(200, content=raw, headers={'x-request-id': 'header-fixture-id'})

    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('fixture-app-long', 'fixture-secret-long', httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        job = await ws.parse_document(doc['id'], 1, 'free_sync')
        receipt = job['provider_receipt']
        assert receipt['response_complete'] and receipt['response_saved']
        assert Path(receipt['response_path']).read_bytes() == raw
        assert receipt['response_sha256'] == hashlib.sha256(raw).hexdigest()
        assert receipt['request_ids'] == {'x-request-id': 'header-fixture-id'}
        assert receipt['x_request_id'] == 'body-fixture-id'
        assert 'response_body' not in receipt and 'version' not in receipt
        assert json.loads(Path(job['result_path']).read_text()) == data
        assert (await ws.parse_document(doc['id'], 1, 'free_sync'))['cached']
        assert len(calls) == 1
    finally:
        ws.close()
    reopened = Workspace(tmp_path / 'state', tmp_path, TextIn('', '', httpx.MockTransport(lambda _: pytest.fail('must use local state'))))
    try:
        assert (await reopened.get_job(job['id']))['provider_receipt'] == receipt
        assert reopened.read_document(job['id'])['elements'][0]['text'] == 'EDC8'
    finally:
        reopened.close()


@pytest.mark.parametrize('variant,status', [
    ('schema', 'submission_unknown'), ('http', 'submission_unknown'),
    ('business', 'failed'), ('timeout', 'submission_unknown'),
    ('cancel', 'submission_unknown'), ('echo', 'completed'),
    ('invalid_json', 'submission_unknown'), ('oversize', 'submission_unknown'),
])
async def test_free_error_receipt_history_and_no_automatic_resubmission(tmp_path, material, variant, status):
    calls = []

    def provider(request):
        calls.append(request)
        assert 'x-ti-app-id' not in request.headers and 'x-ti-secret-code' not in request.headers
        payload = {'code': 200, 'data': {'success_count': 1, 'elements': [{'text': 'A'}]}}
        if len(calls) == 1:
            if variant == 'timeout':
                raise httpx.ReadTimeout('fixture', request=request)
            if variant == 'cancel':
                raise asyncio.CancelledError()
            if variant == 'schema':
                payload['data']['elements'] = None
            if variant == 'business':
                payload['code'] = 40306
            if variant == 'echo':
                payload['data']['elements'][0]['text'] = 'fixture-secret-long'
            if variant == 'invalid_json':
                return httpx.Response(200, content=b'not-json')
            if variant == 'oversize':
                return httpx.Response(200, content=b'x' * (32 * 1024 * 1024 + 1))
        return httpx.Response(500 if variant == 'http' and len(calls) == 1 else 200, json=payload,
                              headers={'x-request-id': 'fixture-secret-long'})

    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('fixture-app-long', 'fixture-secret-long', httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        if variant == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await ws.parse_document(doc['id'], 1, 'free_sync')
            first = json.loads(ws.db.execute("SELECT body FROM objects WHERE kind='job'").fetchone()[0])
        else:
            first = await ws.parse_document(doc['id'], 1, 'free_sync')
        assert first['status'] == status
        receipt = first['provider_receipt']
        assert receipt['response_saved'] == (variant not in {'timeout', 'cancel', 'oversize'})
        assert 'fixture-secret-long' not in json.dumps(receipt)
        assert (await ws.parse_document(doc['id'], 1, 'free_sync'))['cached'] and len(calls) == 1
        if variant == 'echo':
            assert ws.read_document(first['id'])['elements'][0]['text'] == '[REDACTED]'
            assert receipt['credential_echo_redacted']
            for file in (ws.root / doc['id']).glob('*'):
                assert b'fixture-secret-long' not in file.read_bytes()
        else:
            retried = await ws.retry_parse_document(first['id'], 1, 1, True, 'Synthetic explicit retry; not a user action')
            assert retried['status'] == 'completed' and len(calls) == 2
            assert retried['provider_receipt']['response_path'].endswith('attempt-2-free-response.bin')
            assert ws.parse_attempt_history(first['id'])['items'][0] == first
            if receipt['response_saved']:
                assert Path(receipt['response_path']).exists()
    finally:
        ws.close()


async def test_free_archive_failure_preserves_successful_result(tmp_path, material, monkeypatch):
    original_open = Path.open

    def fail_binary_archive(path, mode='r', *args, **kwargs):
        if mode == 'xb' and path.suffix == '.bin':
            raise OSError('synthetic archival failure')
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_binary_archive)
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', '', httpx.MockTransport(
        lambda _: httpx.Response(200, json={'code': 200, 'data': {'success_count': 1, 'elements': [{'text': 'A'}]}}))))
    try:
        doc = ws.import_document(str(material))
        job = await ws.parse_document(doc['id'], 1, 'free_sync')
        assert job['status'] == 'completed'
        assert job['provider_receipt']['response_saved'] is False
        assert job['provider_receipt']['archive_error'] == 'response_not_saved'
        assert ws.read_document(job['id'])['elements'][0]['text'] == 'A'
    finally:
        ws.close()
