"""Prevent the observed successful-but-short student export, without editing live data."""
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.models import DomainError, Exercise, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'synthetic.pdf'
    pages = [Image.new('RGB', (180, 240), color) for color in ('white', 'ivory')]
    pages[0].save(source, save_all=True, append_images=pages[1:], resolution=72)
    ws = Workspace(tmp_path / 'data', tmp_path, TextIn('', ''))
    doc = ws.import_document(str(source))
    evidence = [ws.read_evidence(doc['id'], Region(page=n, box=[0, 0, 1, 1]))[0]['id'] for n in (1, 2)]
    yield ws, doc, evidence
    ws.close()


def save(ws, doc, evidence, label, with_exercise=False, original=False):
    stem = '化简 2(x+1)+3x。' if original else '展开3(x-2)。'
    exercises = []
    if with_exercise:
        expression, answer = ('2*(x+1)+3*x', '5*x+2') if original else ('4*(x-3)', '4*x-12')
        check = ws.check_exercise(expression, answer)
        exercises = [Exercise(kind='original_text' if original else 'variant',
                             prompt=stem if original else '展开4(x-3)。', answer=answer,
                             targets_error='核对分配律', check_id=check['id'])]
    record = ws.save_record(StudyRecord(document_id=doc['id'], question_label=label, stem=stem,
                                       student_answer='PRIVATE_OLD_RESPONSE', evidence_ids=evidence,
                                       agent_verdict='wrong', exercises=exercises))
    return ws.review_record(record['id'], record['revision'], 'include', 'Synthetic fixture: explicitly include current version.')


@pytest.mark.parametrize('case', ['empty_single', 'only_middle_of_three', 'edit_clears_exercises'])
def test_formal_export_rejects_every_observed_missing_question_case(workspace, case):
    ws, doc, evidence = workspace
    if case == 'only_middle_of_three':
        records = [save(ws, doc, evidence[:1], str(i), with_exercise=i == 2) for i in (1, 2, 3)]
    else:
        record = save(ws, doc, evidence, '4', with_exercise=case == 'edit_clears_exercises')
        if case == 'edit_clears_exercises':
            body = StudyRecord(**ws.read_record(record['id'])['record'])
            body.stem = '修订后的题干：展开3(x-2)。'
            record = ws.save_record(body, record['id'], record['revision'])
            record = ws.review_record(record['id'], record['revision'], 'include', 'Synthetic explicit review after edit.')
            assert record['record']['exercises'] == []
        records = [record]
    before = ws.db.execute('SELECT * FROM objects ORDER BY kind,id').fetchall()
    with pytest.raises(DomainError, match='student_material_missing'):
        ws.export_errorbook([r['id'] for r in records])
    assert not (ws.root / 'exports').exists()
    assert ws.db.execute('SELECT * FROM objects ORDER BY kind,id').fetchall() == before


def test_explicit_missing_draft_is_not_a_formal_complete_student_package(workspace):
    ws, doc, evidence = workspace
    records = [save(ws, doc, evidence, str(i), with_exercise=i == 2) for i in (1, 2, 3)]
    result = ws.export_errorbook([r['id'] for r in records], include_drafts=True)
    coverage = result['student_materials']
    assert result['contains_drafts'] and not coverage['complete']
    assert coverage['selected_record_count'] == 3 and coverage['covered_record_count'] == 1
    assert coverage['text_question_count'] == 1
    assert len(coverage['missing_record_ids']) == 2
    assert '缺题草稿' in (Path(result['folder']) / 'practice.html').read_text()


def test_supplement_cross_page_original_text_then_reopen_and_export_both(workspace):
    ws, doc, evidence = workspace
    first = save(ws, doc, evidence[:1], '2', with_exercise=True)
    fourth = save(ws, doc, evidence, '4', original=True)
    with pytest.raises(DomainError, match='student_material_missing'):
        ws.export_errorbook([first['id'], fourth['id']])
    original = StudyRecord(**ws.read_record(fourth['id'])['record'])
    check = ws.check_exercise('2*(x+1)+3*x', '5*x+2')
    original.exercises = [Exercise(kind='original_text', prompt=original.stem, answer='5*x+2',
                                   targets_error='分配律', check_id=check['id'])]
    updated = ws.save_record(original, fourth['id'], fourth['revision'])
    assert updated['review_status'] == 'needs_review'
    ws.review_record(updated['id'], updated['revision'], 'include', 'Synthetic explicit review of completed original-text material.')
    peer = Workspace(ws.root, ws.input_root, TextIn('', ''))
    try:
        result = peer.export_errorbook([first['id'], fourth['id']])
        assert result['student_materials']['complete']
        assert result['student_materials']['selected_record_count'] == 2
        assert result['student_materials']['covered_record_count'] == 2
        assert result['student_materials']['text_question_count'] == 2
        manifest = json.loads(Path(result['manifest_path']).read_text())
        assert [ev['page'] for ev in manifest['records'][1]['evidence']] == [1, 2]
        with zipfile.ZipFile(result['practice_bundle_path']) as archive:
            page = archive.read('practice.html').decode()
            assert page.count("class='exercise'") == 2
            assert '展开4(x-3)' in page and '化简 2(x+1)+3x' in page
            assert not any(marker in page for marker in ('PRIVATE_', '4*x-12', '5*x+2'))
            assert 'answers.html' not in archive.namelist()
            assert not any(name.endswith('.png') for name in archive.namelist())
        answers = (Path(result['folder']) / 'answers.html').read_text()
        assert answers.count("class='answer'") == 2
        assert '4*x-12' in answers and '5*x+2' in answers
    finally:
        peer.close()


@pytest.mark.parametrize('requested,original', [('originals', False), ('variants', True)])
def test_export_does_not_substitute_other_question_intent(workspace, requested, original):
    ws, doc, evidence = workspace
    record = save(ws, doc, evidence, '1', with_exercise=True, original=original)
    with pytest.raises(DomainError, match='student_material_missing'):
        ws.export_errorbook([record['id']], practice_scope=requested)
    matching = 'originals' if original else 'variants'
    result = ws.export_errorbook([record['id']], practice_scope=matching)
    assert result['student_materials']['complete']
    assert result['student_materials']['text_question_count'] == 1


def test_ready_exercise_filter_skips_included_record_without_exercise(workspace):
    ws, doc, evidence = workspace
    ready = save(ws, doc, evidence, '2', with_exercise=True)
    save(ws, doc, evidence, '4')
    result = ws.query_errorbook(review_status='include', ready_exercises_only=True)
    assert [item['id'] for item in result['items']] == [ready['id']]
    assert result['total'] == 1


def test_original_text_cannot_silently_replace_source_with_a_variant(workspace):
    ws, doc, evidence = workspace
    record = StudyRecord(document_id=doc['id'], question_label='4', stem='原题文本', evidence_ids=evidence,
                         exercises=[Exercise(kind='original_text', prompt='另一道变式', answer='3', targets_error='不允许偷换')])
    with pytest.raises(DomainError, match='original_prompt_mismatch'):
        ws.save_record(record)


def test_ready_filter_preserves_requested_original_or_variant_intent(workspace):
    ws, doc, evidence = workspace
    variant = save(ws, doc, evidence, '2', with_exercise=True)
    original = save(ws, doc, evidence, '4', with_exercise=True, original=True)
    for kind, expected in [('variant', variant), ('original_text', original)]:
        result = ws.query_errorbook(ready_exercises_only=True, exercise_kind=kind)
        assert [item['id'] for item in result['items']] == [expected['id']]
        assert result['items'][0]['ready_exercises'] == [{'exercise_index': 0, 'kind': kind}]


def test_explicit_archive_without_student_material_is_not_an_empty_student_package(workspace):
    ws, doc, evidence = workspace
    record = save(ws, doc, evidence, '4')
    result = ws.export_errorbook([record['id']], practice_scope='archive')
    assert result['student_materials']['requested'] is False
    assert result['student_materials']['complete'] is None
    assert result['student_materials']['covered_record_count'] == 0
    assert result['practice_bundle_path'] is None and result['practice_bundle'] is None
    assert not result['contains_drafts']
    folder = Path(result['folder'])
    assert (folder / 'errorbook.html').is_file()
    assert not any((folder / name).exists() for name in ['practice.html', 'answers.html', 'practice-only.zip'])
