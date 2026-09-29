from contextlib import closing
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.algebra import check_answer
from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.mark.parametrize('equation,answer,status', [
    ('2*(x+3)=10', '2', 'equivalent'),
    ('2*(x+3)=10', '7/2', 'not_equivalent'),
    ('3*x+1=0', '-1/3', 'equivalent'),
    ('0.1*x+0.2=0.3', '1', 'equivalent'),
    ('x/3=2', '6', 'equivalent'),
    (' 2*x + 1 = 1 ', ' 0 ', 'equivalent'),
    ('-2*x=3', '-3/2', 'equivalent'),
    ('x^2+x=x^2+1', '1', 'equivalent'),
    ('x=x', '3', 'needs_review'),
    ('x=x+1', '3', 'needs_review'),
    ('x^2=4', '2', 'needs_review'),
    ('(x-1)/(x-1)=1', '2', 'needs_review'),
    ('x=2', 'x', 'needs_review'),
    ('x=0', 'x-x', 'needs_review'),
    ('x=2', 'x=2', 'needs_review'),
    ('x=2=2', '2', 'needs_review'),
    ('x+1', '2', 'needs_review'),
    ('2x=4', '2', 'needs_review'),
    ('x+0.10000000000000001=0.1', '0', 'needs_review'),
])
def test_unique_linear_solution_check(tmp_path, equation, answer, status):
    with closing(Workspace(tmp_path / 'state', None, TextIn('', ''))) as ws:
        checked = ws.check_exercise(equation, answer, kind='linear_equation')
        assert checked['kind'] == 'linear_equation'
        assert checked['status'] == status
        assert ws._get('check', checked['id']) == checked


def test_equation_checks_remain_bound_to_saved_mode_and_gate_practice(tmp_path):
    sample = tmp_path / 'synthetic.png'
    Image.new('RGB', (20, 20), 'white').save(sample)
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
        doc = ws.import_document(str(sample))
        evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
        check = ws.check_exercise('2*(x+3)=10', '2', kind='linear_equation')
        record = StudyRecord(document_id=doc['id'], question_label='Synthetic equation test',
            stem='Synthetic source', evidence_ids=[evidence['id']], exercises=[{
                'prompt': '解方程 2(x+3)=10', 'answer': '2', 'targets_error': '每一项均需乘2',
                'check_id': check['id']}])
        saved = ws.save_record(record)
        reviewed = ws.review_record(saved['id'], 1, 'include', 'Synthetic inclusion fixture')
        before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
        ready = ws.read_record(saved['id'])['exercise_validation'][0]
        assert ready['status'] == 'equation_verified' and ready['answer_ready']
        assert ready['check_kind'] == 'linear_equation'
        assert ws.read_practice_prompt(saved['id'], reviewed['revision'], 'exercise', 0)[0]['prompt'] == record.exercises[0].prompt
        export = ws.export_errorbook([saved['id']])
        answers = (Path(export['folder']) / 'answers.html').read_text()
        assert '一元一次方程的唯一解验算通过' in answers
        assert '表达式验算通过' not in answers
        assert ws.read_record(saved['id'], revision=1)['exercise_checks'] == [check]
        assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before
        # A saved pass is not trusted when its actual equation has no unique root.
        legacy = ws._get('record', saved['id'])
        legacy['exercise_checks'][0]['expression'] = 'x=x'
        ws._put('record', saved['id'], legacy)
        assert not ws.read_record(saved['id'])['exercise_validation'][0]['answer_ready']
        with pytest.raises(DomainError, match='exercise_not_ready'):
            ws.read_practice_prompt(saved['id'], reviewed['revision'], 'exercise', 0)


def test_existing_mode_and_invalid_kind_do_not_silently_solve(tmp_path):
    with closing(Workspace(tmp_path / 'state', None, TextIn('', ''))) as ws:
        assert ws.check_exercise('x=2', '2')['status'] == 'needs_review'
        assert ws.check_exercise('2*(x+3)', '2*x+6')['status'] == 'equivalent'
        before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
        with pytest.raises(DomainError, match='check_kind'):
            ws.check_exercise('x=2', '2', kind='guess')
        assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before


def test_equation_combination_and_root_share_the_coefficient_budget():
    power = '65536'
    for _ in range(7):
        power = f'({power})^2'
    boundary = f'(({power})/2)^2*2'
    for equation in (f'x+({boundary})=-({boundary})', f'x/({boundary})=2'):
        checked = check_answer(equation, '0', 'linear_equation')
        assert checked['status'] == 'needs_review' and '4096' in checked['reason']


def test_saved_legacy_and_unknown_modes_are_not_guessed():
    saved = {'revision': 1, 'record': {'exercises': [{'answer': '2*x+2', 'check_id': 'test'}]},
             'exercise_checks': [{'id': 'test', 'expression': '2*(x+1)', 'proposed_answer': '2*x+2'}]}
    assert Workspace._exercise_validation(saved)[0]['status'] == 'expression_verified'
    saved['exercise_checks'][0]['kind'] = 'guess'
    assert not Workspace._exercise_validation(saved)[0]['answer_ready']
    saved['exercise_checks'][0].update(kind='linear_equation', expression='x=3', proposed_answer='2')
    saved['record']['exercises'][0]['answer'] = '2'
    saved['review'] = {'exercise_revision': 1, 'approved_exercise_indices': [0]}
    validation = Workspace._exercise_validation(saved)[0]
    assert validation['status'] == 'not_equivalent' and not validation['answer_ready']
