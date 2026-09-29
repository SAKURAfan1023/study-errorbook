from contextlib import closing
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


async def test_find_cached_result_after_restart_without_source_or_credentials(tmp_path):
    sample = tmp_path / 'sample.png'
    Image.new('RGB', (20, 20), 'white').save(sample)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'code': 200, 'data': {
            'success_count': 1, 'elements': [{'text': 'synthetic 2x+3'}], 'pages': []}})

    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', '', httpx.MockTransport(provider)))) as ws:
        doc = ws.import_document(str(sample))
        job = await ws.parse_document(doc['id'], 1, 'free_sync')
        assert len(calls) == 1
    Path(doc['source']).unlink()
    with closing(Workspace(tmp_path / 'state', None, TextIn('', '', httpx.MockTransport(provider)))) as ws:
        before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
        found = ws.list_parse_jobs(ws.list_documents()['items'][0]['id'])
        assert found['total'] == 1 and found['next_offset'] is None
        assert found['items'][0]['id'] == job['id']
        assert found['items'][0]['status'] == 'completed'
        assert found['items'][0]['mode'] == 'free_sync'
        assert ws.read_document(found['items'][0]['id'])['elements'][0]['text'] == 'synthetic 2x+3'
        assert len(calls) == 1
        assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before


def test_job_discovery_scopes_paginates_and_preserves_stored_states(tmp_path):
    with closing(Workspace(tmp_path / 'state', None, TextIn('', ''))) as ws:
        for doc_id in ('doc_a', 'doc_b'):
            ws._put('document', doc_id, {'id': doc_id})
        for i, status in enumerate(('failed', 'submission_unknown', 'pending', 'partial')):
            ws._put('job', f'job_{i}', {'id': f'job_{i}', 'document_id': 'doc_a', 'status': status,
                'provider_job_id': 'remote-private', 'config': {'extra': 'not in summary'},
                'error': {'message': 'not in summary'}})
        ws._put('job', 'other', {'id': 'other', 'document_id': 'doc_b', 'status': 'completed'})
        ws._put('parse_history', 'job_0:1', {'id': 'job_0', 'document_id': 'doc_a', 'status': 'failed'})
        before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
        first = ws.list_parse_jobs('doc_a', limit=2)
        second = ws.list_parse_jobs('doc_a', offset=first['next_offset'], limit=2)
        assert first['total'] == second['total'] == 4 and second['next_offset'] is None
        items = first['items'] + second['items']
        assert [x['id'] for x in items] == ['job_3', 'job_2', 'job_1', 'job_0']
        assert [x['status'] for x in items] == ['partial', 'pending', 'submission_unknown', 'failed']
        assert all(x['mode'] == 'paid_async' and x['attempt_number'] == 1 for x in items)
        assert all(set(x) == {'id', 'status', 'mode', 'attempt_number', 'submitted_at'} for x in items)
        assert ws.list_parse_jobs('doc_a', offset=4)['items'] == []
        with pytest.raises(DomainError, match='not_found'):
            ws.list_parse_jobs('unknown')
        assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before


@pytest.mark.parametrize('offset,limit', [(-1, 20), (0, 0), (0, 51), (True, 20)])
def test_job_discovery_pagination_is_validated(tmp_path, offset, limit):
    with closing(Workspace(tmp_path / 'state', None, TextIn('', ''))) as ws:
        with pytest.raises(DomainError, match='pagination'):
            ws.list_parse_jobs('unknown', offset, limit)
