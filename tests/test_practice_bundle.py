import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from test_clean_export import clean_workspace as _clean_workspace
from test_export_atomic import export_workspace as _export_workspace
from test_export_print import Links

from study_errorbook.deletion import preview_document_deletion
from study_errorbook.models import Exercise

clean_workspace = _clean_workspace
export_workspace = _export_workspace


@pytest.mark.parametrize('clean_images', [True, False])
@pytest.mark.parametrize('draft', [True, False])
def test_practice_bundle_is_complete_and_excludes_answer_material(clean_workspace, tmp_path, clean_images, draft):
    ws, record, clean = clean_workspace
    record.question_label = 'PRIVATE_DIAGNOSTIC_LABEL'
    record.student_answer = 'PRIVATE_OLD_RESPONSE'
    record.exercises = [Exercise(prompt=r'计算 \(2+3\)', answer='PRIVATE_ANSWER',
                                 explanation='PRIVATE_EXPLANATION', targets_error='PRIVATE_TARGET')]
    if not clean_images:
        record.clean_evidence_ids = []
    saved = ws.save_record(record)
    if not draft:
        saved = ws.review_record(saved['id'], 1, 'include', 'Synthetic fixture only.', approved_exercise_indices=[0],
                                 approved_clean_copies={record.evidence_ids[0]: hashlib.sha256(clean.read_bytes()).hexdigest()} if clean_images else {})
    before = ws.db.execute('SELECT * FROM objects').fetchall()
    exported = ws.export_errorbook([saved['id']], include_drafts=draft)
    bundle_path = Path(exported['practice_bundle_path'])
    manifest = json.loads(Path(exported['manifest_path']).read_text())
    assert bundle_path.parent == Path(exported['folder'])
    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == manifest['practice_bundle']['sha256']
    with zipfile.ZipFile(bundle_path) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) == manifest['practice_bundle']['file_count']
        assert archive.testzip() is None
        assert not {'answers.html', 'errorbook.html', 'manifest.json', record.evidence_ids[0]+'.png'} & set(names)
        assert all(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts for name in names)
        assert ('original-practice.html' in names) == clean_images
        assert (record.evidence_ids[0]+'-clean.png' in names) == clean_images
        assert len([name for name in names if name.startswith('assets/')]) == 67
        for name in names:
            if name.endswith(('.html', '.txt', '.json')):
                assert b'PRIVATE_' not in archive.read(name)
        assert ('待复核草稿' in archive.read('README.txt').decode()) == draft
        archive.extractall(tmp_path/'moved-practice')
    moved = tmp_path/'moved-practice'
    for page in moved.glob('*.html'):
        parser = Links()
        parser.feed(page.read_text())
        assert all('://' not in link and (moved/link).is_file() for link in parser.resources)
    css = moved/'assets/katex/katex.min.css'
    fonts = re.findall(r'url\(([^)]+)\)', css.read_text())
    assert fonts and all((css.parent/font.strip('\"\'')).is_file() for font in fonts)
    if clean_images:
        assert (moved/(record.evidence_ids[0]+'-clean.png')).read_bytes() == clean.read_bytes()
    assert ws.db.execute('SELECT * FROM objects').fetchall() == before


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt])
def test_bundle_failure_leaves_no_partial_export(export_workspace, monkeypatch, failure):
    ws, doc, _, saved, existing = export_workspace
    old_files = {p.relative_to(existing['folder']): p.read_bytes() for p in Path(existing['folder']).rglob('*') if p.is_file()}
    original = zipfile.ZipFile.write
    def fail(archive, filename, *args, **kwargs):
        original(archive, filename, *args, **kwargs)
        raise failure('synthetic bundle interruption')
    monkeypatch.setattr(zipfile.ZipFile, 'write', fail)
    with pytest.raises(failure, match='bundle interruption'):
        ws.export_errorbook([saved['id']], include_drafts=True)
    assert list((ws.root/'exports').iterdir()) == [Path(existing['folder'])]
    assert old_files == {p.relative_to(existing['folder']): p.read_bytes() for p in Path(existing['folder']).rglob('*') if p.is_file()}
    assert preview_document_deletion(ws.db, ws.root, doc['id'])['inventory_complete']
