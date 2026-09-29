import asyncio

import httpx
import pytest
from PIL import Image

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def observation_workspace(tmp_path, monkeypatch):
    now = [10000.0]
    monkeypatch.setattr('study_errorbook.service.time.time', lambda: now[0])
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    source = inputs / 'sample.png'
    Image.new('RGB', (100, 100), 'white').save(source)
    calls, replies = [], [40306]

    def provider(request):
        calls.append((request.method, str(request.url)))
        if request.method == 'POST':
            return httpx.Response(200, json={'code': 200, 'data': {'job_id': 'original-job'}})
        if request.url.host == 'web-api.textin.com':
            return httpx.Response(200, json={'success_count': 1, 'elements': [{'text': 'original'}]})
        outcome = replies.pop(0) if replies else 'completed'
        if isinstance(outcome, int):
            return httpx.Response(200, json={'code': outcome})
        return httpx.Response(200, json={'data': {
            'status': 'completed', 'result_url': 'https://web-api.textin.com/result',
        }})

    ws = Workspace(tmp_path / 'state', inputs, TextIn('id', 'secret', httpx.MockTransport(provider)))
    doc = ws.import_document(str(source))
    yield ws, doc, calls, replies, now
    ws.close()


async def pause_job(ws, doc):
    job = await ws.parse_document(doc['id'], 1)
    paused = await ws.get_job(job['id'])
    assert paused['poll_error']['code'] == 'textin_40306'
    assert paused['observation_revision'] == 1
    return paused


async def test_resume_observes_original_job_only_after_explicit_decision(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    now[0] += 5
    for _ in range(3):
        assert 'next_action' in await ws.get_job(paused['id'])
    assert len(calls) == 2
    with pytest.raises(DomainError, match='resume_confirmation'):
        await ws.resume_job_observation(paused['id'], 1, 1, ' ')
    result = await ws.resume_job_observation(paused['id'], 1, 1, '已处理限流，继续查询原任务')
    assert result['status'] == 'completed' and result['attempt_number'] == 1
    assert result['provider_job_id'] == 'original-job' and 'poll_error' not in result
    assert ws.read_document(result['id'])['elements'][0]['text'] == 'original'
    assert [method for method, _ in calls] == ['POST', 'GET', 'GET', 'GET']
    assert calls[1][1] == calls[2][1]
    peer = Workspace(ws.root, ws.input_root, ws.api)
    try:
        saved = await peer.get_job(result['id'])
        assert saved['last_observation_resume']['previous_error']['code'] == 'textin_40306'
        assert saved['last_observation_resume']['source'] == 'host_agent_reported'
        assert saved['observation_revision'] == 3
    finally:
        peer.close()


async def test_repeated_limit_stops_and_old_decision_cannot_resume_again(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    now[0] += 5
    replies.append(40306)
    result = await ws.resume_job_observation(paused['id'], 1, 1, '服务商通知恢复')
    assert result['poll_error']['code'] == 'textin_40306'
    with pytest.raises(DomainError, match='observation_conflict'):
        await ws.resume_job_observation(paused['id'], 1, 1, '同一条通知重复送达')
    now[0] += 5
    assert 'next_action' in await ws.get_job(paused['id'])
    assert len(calls) == 3


async def test_resume_keeps_cooldown_and_requires_credentials(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    ws.api.configured = False
    with pytest.raises(DomainError, match='credentials_missing'):
        await ws.resume_job_observation(paused['id'], 1, 1, '继续查询')
    assert ws._get('job', paused['id']) == paused
    ws.api.configured = True
    ready = await ws.resume_job_observation(paused['id'], 1, 1, '继续查询')
    assert ready['poll_after_seconds'] == 3 and len(calls) == 2
    assert ready['observation_revision'] == 2
    assert (await ws.get_job(paused['id']))['poll_after_seconds'] == 3
    now[0] += 3
    assert (await ws.get_job(paused['id']))['status'] == 'completed'


@pytest.mark.parametrize('status', ['failed', 'submission_unknown', 'completed', 'partial'])
async def test_resume_rejects_non_running_jobs(observation_workspace, status):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    paused['status'] = status
    ws._put('job', paused['id'], paused)
    with pytest.raises(DomainError, match='resume_state'):
        await ws.resume_job_observation(paused['id'], 1, 1, '继续查询')
    assert len(calls) == 2


async def test_two_sessions_and_cancelled_observation_preserve_identity(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    now[0] += 5
    started = asyncio.Event()

    async def slow_provider(request):
        calls.append((request.method, str(request.url)))
        started.set()
        await asyncio.Event().wait()

    ws.api = TextIn('id', 'secret', httpx.MockTransport(slow_provider))
    peer = Workspace(ws.root, ws.input_root, ws.api)
    task = asyncio.create_task(ws.resume_job_observation(paused['id'], 1, 1, '继续查询'))
    try:
        await asyncio.wait_for(started.wait(), 1)
        with pytest.raises(DomainError, match='action_busy'):
            await peer.resume_job_observation(paused['id'], 1, 1, '重复送达')
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = await peer.get_job(paused['id'])
        assert saved['status'] == 'pending' and saved['provider_job_id'] == 'original-job'
        assert saved['observation_revision'] == 3 and saved['poll_after_seconds'] == 3
        with pytest.raises(DomainError, match='observation_conflict'):
            await peer.resume_job_observation(paused['id'], 1, 1, '重复送达')
        assert [method for method, _ in calls] == ['POST', 'GET', 'GET']
    finally:
        task.cancel()
        peer.close()


async def test_invalid_result_url_is_recorded_as_observation_error(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    now[0] += 5
    def malformed_result(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={'data': {'status': 'completed', 'result_url': 'https://['}})
    ws.api = TextIn('id', 'secret', httpx.MockTransport(malformed_result))
    result = await ws.resume_job_observation(paused['id'], 1, 1, '继续查询')
    assert result['status'] == 'pending'
    assert result['poll_error']['code'] == 'result_url_rejected'
    assert result['provider_job_id'] == 'original-job'
    assert [method for method, _ in calls] == ['POST', 'GET', 'GET']


async def test_previous_attempt_cannot_resume_a_new_attempt(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    paused['attempt_number'] = 2
    ws._put('job', paused['id'], paused)
    with pytest.raises(DomainError, match='observation_conflict'):
        await ws.resume_job_observation(paused['id'], 1, 1, '来自旧任务的用户意见')
    assert len(calls) == 2 and ws._get('job', paused['id']) == paused


async def test_legacy_paused_job_can_resume_with_default_versions(observation_workspace):
    ws, doc, calls, replies, now = observation_workspace
    paused = await pause_job(ws, doc)
    paused.pop('attempt_number')
    paused.pop('observation_revision')
    ws._put('job', paused['id'], paused)
    now[0] += 5
    result = await ws.resume_job_observation(paused['id'], 1, 0, '恢复旧任务查询')
    assert result['status'] == 'completed'
    assert result['observation_revision'] == 2
    assert [method for method, _ in calls] == ['POST', 'GET', 'GET', 'GET']
