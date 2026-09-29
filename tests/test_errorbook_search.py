import json

import pytest
from PIL import Image

from study_errorbook.models import Analysis, Annotation, DomainError, Exercise, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def search_workspace(tmp_path):
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    saved = []
    for index, color in enumerate(('white', 'yellow', 'blue')):
        path = tmp_path / f'synthetic-{index}.png'
        Image.new('RGB', (40, 40), color).save(path)
        doc = ws.import_document(str(path))
        evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
        record = StudyRecord(
            document_id=doc['id'], question_label=str(index), subject='数学' if index < 2 else '英语',
            stem='合成题 分配律 Straße 100% a_b \\alpha', student_answer='旧作答', evidence_ids=[evidence['id']],
            annotations=[Annotation(text='批注文字', kind='other', evidence_ids=[evidence['id']])],
            analysis=Analysis(explanation='合成解释', error_type='漏乘', knowledge_points=['分配律', '乘法'] if index < 2 else ['词汇'],
                              evidence_ids=[evidence['id']], uncertainty='合成不确定性', diagnostic_question='合成追问'),
            exercises=[Exercise(prompt='变式题干', answer='变式答案', explanation='变式解释', targets_error='合成目标')],
        )
        item = ws.save_record(record)
        if index < 2:
            item = ws.review_record(item['id'], 1, 'include' if index == 0 else 'exclude', 'Synthetic test choice, not user opinion.')
        saved.append(item)
    yield ws, saved
    ws.close()


def test_intersection_filters_and_default_preserve_all_review_states(search_workspace):
    ws, saved = search_workspace
    assert ws.query_errorbook()['total'] == 3
    result = ws.query_errorbook('分配律', review_status='include', subject='数学', knowledge_point='乘法', document_id=saved[0]['record']['document_id'])
    assert result['total'] == 1 and result['items'][0]['id'] == saved[0]['id']
    assert result['items'][0]['document_id'] == saved[0]['record']['document_id']
    assert ws.query_errorbook(review_status='exclude')['items'][0]['id'] == saved[1]['id']
    assert ws.query_errorbook(review_status='needs_review')['items'][0]['id'] == saved[2]['id']
    assert ws.query_errorbook(subject='数学', knowledge_point='词汇')['total'] == 0
    assert ws.query_errorbook(knowledge_point='乘')['total'] == 0
    assert ws.query_errorbook(document_id='missing')['total'] == 0


@pytest.mark.parametrize('text', ['document_id', 'evidence_ids', 'check_id', 'teacher_verdict', 'null', 'unmarked'])
def test_internal_json_keys_and_metadata_do_not_match(search_workspace, text):
    ws, saved = search_workspace
    assert ws.query_errorbook(text)['total'] == 0
    assert ws.query_errorbook(saved[0]['record']['document_id'])['total'] == 0


@pytest.mark.parametrize('text', ['STRASSE', '100%', 'a_b', '\\alpha', '旧作答', '批注文字', '合成解释', '漏乘', '乘法', '合成不确定性', '合成追问', '变式题干', '变式答案', '变式解释', '合成目标'])
def test_content_fields_unicode_and_literal_characters(search_workspace, text):
    ws, _ = search_workspace
    assert ws.query_errorbook(text)['total'] > 0


def test_pagination_uses_filtered_total_and_returns_only_summaries(search_workspace):
    ws, saved = search_workspace
    first = ws.query_errorbook(subject='数学', limit=1)
    assert first['total'] == 2 and first['next_offset'] == 1
    second = ws.query_errorbook(subject='数学', offset=first['next_offset'], limit=1)
    assert second['next_offset'] is None
    assert [first['items'][0]['id'], second['items'][0]['id']] == [saved[1]['id'], saved[0]['id']]
    assert 'student_answer' not in first['items'][0] and 'analysis' not in first['items'][0]
    assert ws.query_errorbook(offset=100)['items'] == []


def test_search_tracks_current_record_not_prior_revisions(search_workspace):
    ws, saved = search_workspace
    record = StudyRecord.model_validate(saved[0]['record'])
    record.student_answer = '当前新作答'
    record.analysis = None
    updated = ws.save_record(record, saved[0]['id'], 2)
    filters = {'document_id': record.document_id}
    assert ws.query_errorbook('旧作答', **filters)['total'] == 0
    assert ws.query_errorbook('当前新作答', **filters)['items'][0]['revision'] == updated['revision']
    assert ws.query_errorbook(knowledge_point='乘法', **filters)['total'] == 0
    assert ws.query_errorbook(review_status='include', **filters)['total'] == 0


def test_judgment_filter_is_separate_from_review_and_paginates_current_records(search_workspace):
    ws, saved = search_workspace
    original = StudyRecord.model_validate(saved[0]['record'])
    cases = {}
    for verdict in ('correct', 'wrong', 'partial', 'unanswered', 'uncertain'):
        record = original.model_copy(update={'agent_verdict': verdict, 'question_label': verdict})
        cases[verdict] = ws.save_record(record)
    extra = ws.save_record(original.model_copy(update={'agent_verdict': 'wrong'}))
    included = ws.review_record(cases['wrong']['id'], 1, 'include', 'Synthetic test selection')
    filters = {'document_id': original.document_id, 'subject': original.subject, 'knowledge_point': '乘法'}
    before = ws.db.execute('SELECT rowid,* FROM objects ORDER BY rowid').fetchall()
    for verdict in ('correct', 'partial', 'unanswered'):
        page = ws.query_errorbook(agent_verdict=verdict, **filters)
        assert [item['id'] for item in page['items']] == [cases[verdict]['id']]
        assert page['filters']['agent_verdict'] == verdict
    first = ws.query_errorbook('合成题', agent_verdict='wrong', limit=1, **filters)
    assert first['total'] == 2 and first['next_offset'] == 1
    second = ws.query_errorbook('合成题', agent_verdict='wrong', limit=1, offset=1, **filters)
    assert second['next_offset'] is None
    assert [first['items'][0]['id'], second['items'][0]['id']] == [extra['id'], included['id']]
    assert ws.query_errorbook(agent_verdict='wrong', review_status='include', **filters)['total'] == 1
    assert ws.query_errorbook(agent_verdict='wrong', review_status='needs_review', **filters)['total'] == 1
    assert ws.query_errorbook(agent_verdict='uncertain', review_status='uncertain', **filters)['total'] == 0
    assert ws.query_errorbook(agent_verdict='uncertain', review_status='needs_review', **filters)['total'] == 1
    assert ws.query_errorbook(**filters)['total'] == 7
    assert ws.db.execute('SELECT rowid,* FROM objects ORDER BY rowid').fetchall() == before
    changed = StudyRecord.model_validate(included['record'])
    changed.agent_verdict = 'correct'
    ws.save_record(changed, included['id'], 2)
    assert ws.query_errorbook(agent_verdict='wrong', document_id=original.document_id)['total'] == 1
    assert ws.query_errorbook(agent_verdict='correct', document_id=original.document_id)['total'] == 2
    assert ws.read_record(included['id'], 2)['record']['agent_verdict'] == 'wrong'


@pytest.mark.parametrize('value', ['include', '', True, []])
def test_invalid_judgment_filter_is_rejected(search_workspace, value):
    ws, _ = search_workspace
    with pytest.raises(DomainError, match='invalid_search_filter'):
        ws.query_errorbook(agent_verdict=value)


@pytest.mark.parametrize('params', [dict(limit=True), dict(offset=True), dict(limit=0), dict(offset=-1), dict(limit=51), dict(review_status='wrong'), dict(subject=' '), dict(document_id='x'*129), dict(knowledge_point='x'*201), dict(search='x'*201)])
def test_invalid_filters_are_rejected(search_workspace, params):
    ws, _ = search_workspace
    with pytest.raises(DomainError):
        ws.query_errorbook(**params)


@pytest.mark.parametrize('text', ['中' * 300, '\x01' * 300], ids=['chinese', 'escaped-controls'])
def test_summary_byte_budget_keeps_contiguous_pages_and_exact_total(search_workspace, text):
    ws, saved = search_workspace
    original = StudyRecord.model_validate(saved[0]['record'])
    original.stem = text
    original.question_label = '长' * 100
    original.subject = '数' * 100
    inserted = []
    for index in range(30):
        original.stem = '短' if index == 20 else text
        inserted.append(ws.save_record(original)['id'])
    expected = list(reversed(inserted))
    before = ws.db.execute('SELECT rowid,* FROM objects ORDER BY rowid').fetchall()
    offset, seen = 0, []
    while True:
        page = ws.query_errorbook(subject=original.subject, offset=offset, limit=50)
        assert page['total'] == 30
        assert 0 < len(page['items']) < 30
        assert sum(len(json.dumps(item, ensure_ascii=False).encode()) for item in page['items']) <= 12000
        assert all(item['stem_preview'] == ('短' if item['id'] == inserted[20] else text) for item in page['items'])
        seen.extend(item['id'] for item in page['items'])
        if page['next_offset'] is None:
            break
        assert page['next_offset'] == offset + len(page['items'])
        offset = page['next_offset']
    assert seen == expected
    assert ws.db.execute('SELECT rowid,* FROM objects ORDER BY rowid').fetchall() == before
