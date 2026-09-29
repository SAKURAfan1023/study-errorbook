from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.models import DomainError, ReviewPlanChange, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def release_workspace(tmp_path):
    source = tmp_path / 'paper.png'
    Image.new('RGB', (100, 100), 'white').save(source)
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    from study_errorbook.models import Region
    doc = ws.import_document(str(source))
    evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
    record = StudyRecord(document_id=doc['id'], question_label='练习发布测试',
                         stem='合成题', evidence_ids=[evidence['id']], exercises=[{
                             'prompt': '解释一道几何题', 'answer': '人工核对的说明',
                             'targets_error': '说明理由',
                         }])
    yield ws, record
    ws.close()


async def test_record_inclusion_does_not_approve_unchecked_answers(release_workspace):
    ws, record = release_workspace
    saved = ws.save_record(record)
    ws.review_record(saved['id'], 1, 'include', '确认原题为错题')
    with pytest.raises(DomainError, match='exercise_not_ready'):
        ws.export_errorbook([saved['id']])
    assert not (ws.root / 'exports').exists()
    draft = ws.export_errorbook([saved['id']], include_drafts=True)
    assert draft['contains_drafts']
    assert '答案待复核' in (Path(draft['folder']) / 'practice.html').read_text()
    assert ws.read_record(saved['id'])['exercise_validation'][0]['status'] == 'needs_review'


def test_explicit_exercise_review_is_versioned_and_invalidated_on_edit(release_workspace):
    ws, record = release_workspace
    saved = ws.save_record(record)
    reviewed = ws.review_record(saved['id'], 1, 'include', '已核对练习0的题干与参考答案', [0])
    assert reviewed['review']['exercise_revision'] == reviewed['revision']
    readiness = ws.read_record(saved['id'])['exercise_validation']
    assert readiness[0]['status'] == 'user_reviewed'
    output = ws.export_errorbook([saved['id']])
    assert not output['contains_drafts']
    assert '用户已复核题干与答案（Agent转述）' in (Path(output['folder']) / 'answers.html').read_text()
    record.exercises[0].answer = '修订后的答案'
    updated = ws.save_record(record, saved['id'], reviewed['revision'])
    ws.review_record(saved['id'], updated['revision'], 'include', '只确认原题纳入')
    with pytest.raises(DomainError, match='exercise_not_ready'):
        ws.export_errorbook([saved['id']])


@pytest.mark.parametrize('expression,answer,expected', [
    ('2*(x+3)', '2*x+6', 'expression_verified'),
    ('2*(x+3)', '2*x+3', 'not_equivalent'),
    ('1/x', '1/x', 'needs_review'),
])
def test_formal_export_uses_check_outcome_not_just_check_presence(release_workspace, expression, answer, expected):
    ws, record = release_workspace
    check = ws.check_exercise(expression, answer)
    record.exercises[0].prompt = expression
    record.exercises[0].answer = answer
    record.exercises[0].check_id = check['id']
    saved = ws.save_record(record)
    ws.review_record(saved['id'], 1, 'include', '纳入原题')
    assert ws.read_record(saved['id'])['exercise_validation'][0]['status'] == expected
    if expected == 'expression_verified':
        assert not ws.export_errorbook([saved['id']])['contains_drafts']
    else:
        with pytest.raises(DomainError, match='exercise_not_ready'):
            ws.export_errorbook([saved['id']])
        assert ws.export_errorbook([saved['id']], include_drafts=True)['contains_drafts']
    if expected == 'not_equivalent':
        with pytest.raises(DomainError, match='exercise_check_failed'):
            ws.review_record(saved['id'], 2, 'include', '尝试覆盖已失败的验算', [0])
        assert ws.read_record(saved['id'])['revision'] == 2
    if expression == '1/x':
        ws.review_record(saved['id'], 2, 'include', '用户已人工核对不支持的表达式', [0])
        assert not ws.export_errorbook([saved['id']])['contains_drafts']


@pytest.mark.parametrize('indices', [[1], [-1], [0, 0], [True]])
def test_invalid_exercise_review_is_atomic(release_workspace, indices):
    ws, record = release_workspace
    saved = ws.save_record(record)
    with pytest.raises(DomainError, match='invalid_exercise_review'):
        ws.review_record(saved['id'], 1, 'include', '用户意见', indices)
    assert ws.read_record(saved['id'])['revision'] == 1
    assert ws._all('history') == []


def test_legacy_review_is_not_inferred_as_exercise_approval(release_workspace):
    ws, record = release_workspace
    saved = ws.save_record(record)
    saved.update(review_status='include', review={'user_note': '旧版整题纳入'})
    ws._put('record', saved['id'], saved)
    with pytest.raises(DomainError, match='exercise_not_ready'):
        ws.export_errorbook([saved['id']])


@pytest.mark.parametrize('expression,answer,outcome', [
    ('0.10000000000000001', '0.1', 'needs_review'),
    ('0.1+0.2', '0.3', 'equivalent'),
    ('('*9+'1000000'+')^2'*9, '('*9+'1000000'+')^2'*9, 'needs_review'),
])
def test_saved_checks_are_rechecked_without_rewriting_history(release_workspace, expression, answer, outcome):
    ws, record = release_workspace
    legacy = {'id': 'check_legacy_fixture', 'expression': expression, 'proposed_answer': answer,
              'status': 'equivalent', 'scope': 'Synthetic legacy result, not a real user record.'}
    ws._put('check', legacy['id'], legacy)
    record.exercises[0].prompt = expression
    record.exercises[0].answer = answer
    record.exercises[0].check_id = legacy['id']
    saved = ws.save_record(record)
    reviewed = ws.review_record(saved['id'], 1, 'include', 'Synthetic inclusion only.')
    before = ws.db.execute('SELECT * FROM objects').fetchall()
    current = ws.read_record(saved['id'])
    assert current['exercise_checks'] == [legacy]
    assert current['exercise_validation'][0]['check_status'] == outcome
    assert current['exercise_validation'][0]['answer_ready'] == (outcome == 'equivalent')
    assert ws.read_record(saved['id'], revision=1)['exercise_checks'] == [legacy]
    assert ws.read_record(saved['id'], revision=reviewed['revision'])['exercise_checks'] == [legacy]
    if outcome == 'needs_review':
        with pytest.raises(DomainError, match='exercise_not_ready'):
            ws.export_errorbook([saved['id']])
        with pytest.raises(DomainError, match='exercise_not_ready'):
            ws.read_practice_prompt(saved['id'], 2, 'exercise', 0)
        with pytest.raises(DomainError, match='exercise_not_ready'):
            ws.update_review_plan(saved['id'], 2, 0, ReviewPlanChange(
                action='schedule', due_at='2030-01-01T00:00:00Z', practice_kind='exercise',
                exercise_index=0, user_note='Synthetic requested schedule.'))
        draft = ws.export_errorbook([saved['id']], include_drafts=True)
        answers = (Path(draft['folder'])/'answers.html').read_text()
        assert '验算不支持此表达式' in answers and '表达式验算通过' not in answers
    else:
        assert not ws.export_errorbook([saved['id']])['contains_drafts']
    assert ws.db.execute('SELECT * FROM objects').fetchall() == before
