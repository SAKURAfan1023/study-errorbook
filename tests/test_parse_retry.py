import asyncio

import httpx
import pytest
from PIL import Image

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def retry_workspace(tmp_path):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    source = inputs / 'sample.png'
    Image.new('RGB', (100, 100), 'white').save(source)
    calls = []

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout('response lost')
        return httpx.Response(200, json={
            'code': 200, 'data': {'success_count': 1, 'elements': [], 'pages': []}
        })

    ws = Workspace(tmp_path / 'state', inputs, TextIn('', '', httpx.MockTransport(provider)))
    doc = ws.import_document(str(source))
    yield ws, doc, calls
    ws.close()


async def test_unknown_retry_is_explicit_versioned_and_survives_restart(retry_workspace):
    ws, doc, calls = retry_workspace
    first = await ws.parse_document(doc['id'], 1, 'free_sync')
    assert first['status'] == 'submission_unknown'
    assert (await ws.parse_document(doc['id'], 1, 'free_sync'))['cached']
    with pytest.raises(DomainError, match='retry_confirmation'):
        await ws.retry_parse_document(first['id'], 1, 1, False, '继续')
    with pytest.raises(DomainError, match='page_budget'):
        await ws.retry_parse_document(first['id'], 1, 0, True, '同意重新上传')
    assert len(calls) == 1 and ws.parse_attempt_history(first['id'])['total'] == 0
    results = await asyncio.gather(
        *[ws.retry_parse_document(first['id'], 1, 1, True, '已核实，同意可能重复处理')
          for _ in range(2)], return_exceptions=True,
    )
    assert results[0]['status'] == 'completed' and results[0]['attempt_number'] == 2
    assert isinstance(results[1], DomainError) and results[1].code == 'attempt_conflict'
    assert len(calls) == 2
    assert all(r.url.path == '/api/v1/agent/parse/sync' for r in calls)
    peer = Workspace(ws.root, ws.input_root, ws.api)
    try:
        history = peer.parse_attempt_history(first['id'])
        assert history['total'] == 1 and history['items'][0] == first
        assert (await peer.get_job(first['id']))['attempt_number'] == 2
    finally:
        peer.close()


@pytest.mark.parametrize('status', ['pending', 'in_progress', 'completed', 'partial'])
async def test_retry_never_replaces_active_or_readable_result(retry_workspace, status):
    ws, doc, calls = retry_workspace
    job = await ws.parse_document(doc['id'], 1, 'free_sync')
    job['status'] = status
    ws._put('job', job['id'], job)
    with pytest.raises(DomainError, match='retry_state'):
        await ws.retry_parse_document(job['id'], 1, 1, True, '重试')
    assert len(calls) == 1 and ws.parse_attempt_history(job['id'])['total'] == 0


async def test_paid_failed_legacy_job_preserves_provider_id_and_checks_credentials(retry_workspace):
    ws, doc, calls = retry_workspace
    legacy = {'id': 'legacy', 'document_id': doc['id'], 'status': 'failed',
              'provider_job_id': 'old-provider-job', 'error': {'code': 'provider_failed'}}
    ws._put('job', 'legacy', legacy)
    with pytest.raises(DomainError, match='credentials_missing'):
        await ws.retry_parse_document('legacy', 1, 1, True, '修复凭据后重提')
    assert not calls and ws._get('job', 'legacy') == legacy
    paid_calls = []

    def provider(request):
        paid_calls.append(request)
        return httpx.Response(200, json={'code': 200, 'data': {'job_id': 'new-provider-job'}})

    ws.api = TextIn('fixture-id', 'fixture-secret', httpx.MockTransport(provider))
    result = await ws.retry_parse_document('legacy', 1, 1, True, '确认重提')
    assert result['status'] == 'pending' and result['provider_job_id'] == 'new-provider-job'
    assert result['mode'] == 'paid_async' and len(paid_calls) == 1
    assert ws.parse_attempt_history('legacy')['items'] == [legacy]


async def test_archive_and_new_marker_roll_back_together(retry_workspace, monkeypatch):
    ws, doc, calls = retry_workspace
    first = await ws.parse_document(doc['id'], 1, 'free_sync')
    original_put = ws._put

    def failing_put(kind, identifier, value):
        if kind == 'job':
            raise OSError('simulated disk failure')
        original_put(kind, identifier, value)

    monkeypatch.setattr(ws, '_put', failing_put)
    with pytest.raises(OSError):
        await ws.retry_parse_document(first['id'], 1, 1, True, '确认重提')
    assert ws._get('job', first['id']) == first
    assert ws.parse_attempt_history(first['id'])['total'] == 0 and len(calls) == 1


async def test_two_sessions_cannot_submit_same_retry_and_cancellation_keeps_history(retry_workspace):
    ws, doc, calls = retry_workspace
    first = await ws.parse_document(doc['id'], 1, 'free_sync')
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_provider(request):
        calls.append(request)
        started.set()
        await release.wait()
        pytest.fail('cancelled request must not finish')

    ws.api = TextIn('', '', httpx.MockTransport(slow_provider))
    peer = Workspace(ws.root, ws.input_root, ws.api)
    task = asyncio.create_task(ws.retry_parse_document(first['id'], 1, 1, True, '确认重试'))
    try:
        await asyncio.wait_for(started.wait(), 1)
        with pytest.raises(DomainError, match='action_busy'):
            await peer.retry_parse_document(first['id'], 1, 1, True, '重复送达')
        assert (await peer.get_job(first['id']))['status'] == 'submitting'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await peer.get_job(first['id'])
        assert result['status'] == 'submission_unknown' and result['attempt_number'] == 2
        assert peer.parse_attempt_history(first['id'])['items'] == [first]
        with pytest.raises(DomainError, match='attempt_conflict'):
            await peer.retry_parse_document(first['id'], 1, 1, True, '重复送达')
        assert len(calls) == 2
    finally:
        task.cancel()
        peer.close()
