import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from test_redaction import synthetic_pdf

from study_errorbook.models import Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def provenance_workspace(tmp_path):
    path = tmp_path / 'PRIVATE-STUDENT-NAME.png'
    Image.new('RGB', (101, 79), 'red').save(path)
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    doc = ws.import_document(str(path))
    yield ws, doc
    ws.close()


def export_record(ws, doc):
    evidence, raw = ws.read_evidence(doc['id'], Region(page=1, box=[.1, .2, .9, .8]))
    record = ws.save_record(StudyRecord(document_id=doc['id'], question_label='Synthetic',
                                       stem='Synthetic draft', evidence_ids=[evidence['id']]))
    output = ws.export_errorbook([record['id']], include_drafts=True)
    manifest = json.loads(Path(output['manifest_path']).read_text())
    html = (Path(output['folder']) / 'errorbook.html').read_text()
    return output, manifest, html, raw


def test_export_plain_document_has_explicit_coordinate_owner_and_exact_file_hash(provenance_workspace):
    ws, doc = provenance_workspace
    output, manifest, html, raw = export_record(ws, doc)
    item = manifest['records'][0]
    evidence = item['evidence'][0]
    assert item['document_id'] == evidence['document_id'] == doc['id']
    assert evidence['coordinate_space'] == 'document-page-normalized'
    assert evidence['file_sha256'] == hashlib.sha256(raw).hexdigest()
    assert (Path(output['folder']) / evidence['file']).read_bytes() == raw
    provenance = item['document_provenance']
    assert provenance['status'] == 'complete_saved_chain'
    assert provenance['chain'][0]['sha256_scope'] == 'imported_bytes'
    assert provenance['chain'][0]['stored_sha256'] == doc['sha256']
    assert '材料页 1' in html and '原页 1' not in html
    assert 'document_provenance' not in output['records'][0]


def test_nested_crop_and_redaction_lineage_survives_export_without_parent_files(provenance_workspace):
    ws, doc = provenance_workspace
    redacted = ws.redact_document(doc['id'], [Region(page=1, box=[0, 0, .1, .1])])
    first = ws.crop_document(redacted['id'], Region(page=1, box=[.123, .222, .833, .999]))
    second = ws.crop_document(first['id'], Region(page=1, box=[.2, .3, .8, .9]))
    output, manifest, html, _ = export_record(ws, second)
    chain = manifest['records'][0]['document_provenance']['chain']
    assert [item['document_id'] for item in chain] == [second['id'], first['id'], redacted['id'], doc['id']]
    assert [item['kind'] for item in chain] == ['crop', 'crop', 'redaction', 'import']
    assert chain[0]['derivation']['pixel_box'] == second['crop']['pixel_box']
    assert chain[1]['derivation']['source_render_size'] == first['crop']['source_render_size']
    assert chain[2]['derivation']['source_sha256'] == redacted['redaction']['source_sha256']
    assert 'regions' not in chain[2]['derivation']
    assert '裁图页 1' in html and '直接父材料第 1 页' in html
    serialized = json.dumps(manifest) + html
    assert 'PRIVATE-STUDENT-NAME' not in serialized and str(ws.root) not in serialized
    assert all('source.png' not in file.name and 'original.bin' not in file.name for file in Path(output['folder']).rglob('*'))
    for filename in ('practice.html', 'answers.html'):
        page = (Path(output['folder']) / filename).read_text()
        assert second['id'] not in page and '材料来源' not in page


def test_saved_lineage_does_not_read_or_claim_current_parent_content(provenance_workspace):
    ws, doc = provenance_workspace
    cropped = ws.crop_document(doc['id'], Region(page=1, box=[.1, .2, .9, .8]))
    source_hash = cropped['crop']['source_sha256']
    Path(doc['source']).unlink()
    _, manifest, html, _ = export_record(ws, cropped)
    provenance = manifest['records'][0]['document_provenance']
    assert provenance['status'] == 'complete_saved_chain'
    assert provenance['chain'][0]['derivation']['source_sha256'] == source_hash
    assert '未校验父文件当前内容' in html


def test_crop_page_one_keeps_page_two_of_its_actual_parent(provenance_workspace, tmp_path):
    ws, _ = provenance_workspace
    path = tmp_path / 'pages.pdf'
    path.write_bytes(synthetic_pdf())
    parent = ws.import_document(str(path))
    crop = ws.crop_document(parent['id'], Region(page=2, box=[.2, .1, .8, .9]))
    _, manifest, html, _ = export_record(ws, crop)
    item = manifest['records'][0]
    assert item['evidence'][0]['page'] == 1
    assert item['document_provenance']['chain'][0]['derivation']['region']['page'] == 2
    assert '裁图页 1' in html and '直接父材料第 2 页' in html


@pytest.mark.parametrize('problem', ['missing', 'cycle', 'depth'])
def test_incomplete_metadata_chain_is_explicit_and_bounded(provenance_workspace, problem):
    ws, doc = provenance_workspace
    cropped = ws.crop_document(doc['id'], Region(page=1, box=[.1, .2, .9, .8]))
    expected = {'missing': 'missing_document', 'cycle': 'cycle', 'depth': 'depth_limit'}[problem]
    if problem == 'missing':
        ws.db.execute("DELETE FROM objects WHERE kind='document' AND id=?", (doc['id'],))
        ws.db.commit()
    elif problem == 'cycle':
        doc['crop'] = {**cropped['crop'], 'source_document_id': cropped['id']}
        ws._put('document', doc['id'], doc)
    else:
        for _ in range(16):
            cropped = ws.crop_document(cropped['id'], Region(page=1, box=[0, 0, 1, 1]))
    _, manifest, html, _ = export_record(ws, cropped)
    provenance = manifest['records'][0]['document_provenance']
    assert provenance['status'] == expected
    assert len(provenance['chain']) <= 16 and provenance['unresolved_document_id']
    assert '来源链不完整' in html
