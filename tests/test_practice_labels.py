import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from test_clean_export import clean_workspace as _clean_workspace

from study_errorbook.models import Exercise

clean_workspace = _clean_workspace


@pytest.mark.parametrize('draft', [True, False])
def test_export_uses_neutral_numbers_and_keeps_source_mapping(clean_workspace, draft):
    ws, record, clean = clean_workspace
    records = []
    for number in (1, 2):
        record.question_label = f'SECRET LABEL {number}: answer is 42'
        record.subject = 'SECRET SUBJECT: subtract'
        record.exercises = [Exercise(prompt=f'SYNTHETIC PROMPT {number}', answer='SECRET ANSWER',
                                     explanation='SECRET EXPLANATION', targets_error='SECRET TARGET')]
        saved = ws.save_record(record)
        if not draft:
            saved = ws.review_record(saved['id'], 1, 'include', 'Synthetic review only.',
                                     approved_exercise_indices=[0], approved_clean_copies={
                                         record.evidence_ids[0]: hashlib.sha256(clean.read_bytes()).hexdigest()})
        records.append(saved)
    before = ws.db.execute('SELECT * FROM objects').fetchall()
    exported = ws.export_errorbook([item['id'] for item in reversed(records)], include_drafts=draft)
    folder = Path(exported['folder'])
    practice = (folder/'practice.html').read_text()
    original = (folder/'original-practice.html').read_text()
    notebook = (folder/'errorbook.html').read_text()
    answers = (folder/'answers.html').read_text()
    manifest = json.loads((folder/'manifest.json').read_text())
    material_label = f'资料编号：{exported["export_id"]}'
    assert manifest['export_id'] == exported['export_id']
    assert all(material_label in page for page in (practice, original, notebook, answers))
    with zipfile.ZipFile(exported['practice_bundle_path']) as bundle:
        for name in ('practice.html', 'original-practice.html', 'README.txt'):
            assert material_label in bundle.read(name).decode('utf-8')
    assert 'SECRET' not in practice and 'SECRET' not in original
    assert practice.index('SYNTHETIC PROMPT 2') < practice.index('SYNTHETIC PROMPT 1')
    for number, saved in enumerate(reversed(records), 1):
        label = saved['record']['question_label']
        assert f'第{number}题 / 练习 1' in practice
        assert f'第{number}题 ·' in original
        assert f'第{number}题 · {label}' in notebook
        assert f'第{number}题 / 练习 1 · {label}' in answers
        item = manifest['records'][number-1]
        assert (item['display_number'], item['id'], item['revision']) == (number, saved['id'], saved['revision'])
        assert (folder/item['clean_copies'][0]['file']).read_bytes() == clean.read_bytes()
    assert ws.db.execute('SELECT * FROM objects').fetchall() == before


def test_reexport_after_exercise_edit_has_distinct_material_id(clean_workspace):
    ws, record, _ = clean_workspace
    record.exercises = [Exercise(prompt='OLD PROMPT', answer='OLD ANSWER',
                                 explanation='OLD EXPLANATION', targets_error='OLD TARGET')]
    first = ws.save_record(record)
    old = ws.export_errorbook([first['id']], include_drafts=True)
    old_folder = Path(old['folder'])
    old_bytes = {p.relative_to(old_folder): p.read_bytes() for p in old_folder.rglob('*') if p.is_file()}
    record.exercises = [Exercise(prompt='NEW PROMPT', answer='NEW ANSWER',
                                 explanation='NEW EXPLANATION', targets_error='NEW TARGET')]
    second = ws.save_record(record, record_id=first['id'], expected_revision=first['revision'])
    new = ws.export_errorbook([second['id']], include_drafts=True)
    assert old['export_id'] != new['export_id']
    for exported, saved, word, other in ((old, first, 'OLD', new), (new, second, 'NEW', old)):
        folder = Path(exported['folder'])
        manifest = json.loads((folder/'manifest.json').read_text())
        assert manifest['export_id'] == exported['export_id']
        item = manifest['records'][0]
        assert (item['display_number'], item['id'], item['revision']) == (1, saved['id'], saved['revision'])
        for filename in exported['files']:
            page = Path(filename).read_text()
            assert f'资料编号：{exported["export_id"]}' in page
            assert other['export_id'] not in page
        with zipfile.ZipFile(exported['practice_bundle_path']) as bundle:
            assert f'资料编号：{exported["export_id"]}' in bundle.read('README.txt').decode('utf-8')
            assert bundle.read('practice.html') == (folder/'practice.html').read_bytes()
        practice = (folder/'practice.html').read_text()
        assert '第1题 / 练习 1' in practice and f'{word} PROMPT' in practice
        assert 'ANSWER' not in practice and 'EXPLANATION' not in practice and 'TARGET' not in practice
        assert f'{word} ANSWER' in (folder/'answers.html').read_text()
    assert {p.relative_to(old_folder): p.read_bytes() for p in old_folder.rglob('*') if p.is_file()} == old_bytes
