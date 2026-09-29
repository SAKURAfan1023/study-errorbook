import hashlib
import io
import math
import os
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw
from test_redaction import synthetic_pdf

from study_errorbook.deletion import preview_document_deletion
from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def crop_workspace(tmp_path):
    def reject(request):
        raise AssertionError('This test must not use the network')
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', '', transport=httpx.MockTransport(reject)))
    path = tmp_path / 'source.png'
    image = Image.new('RGB', (201, 103), 'white')
    ImageDraw.Draw(image).rectangle((60, 20, 145, 70), fill='red')
    image.save(path, icc_profile=b'PRIVATE METADATA')
    doc = ws.import_document(str(path))
    yield ws, doc
    ws.close()


def test_pixels_provenance_cache_restart_and_same_pixels_distinct_parents(crop_workspace, tmp_path):
    ws, doc = crop_workspace
    before = Path(doc['source']).read_bytes()
    box = Region(page=1, box=[.201, .123, .812, .876])
    copied = ws.crop_document(doc['id'], box)
    info = copied['crop']
    assert info['source_document_id'] == doc['id']
    assert info['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert info['pixel_box'] == [40, 12, 164, 91]
    assert info['source_render_size'] == [201, 103]
    assert info['output_size'] == [124, 79] and copied['pages'] == 1
    with Image.open(io.BytesIO(before)) as original, Image.open(copied['source']) as crop:
        assert crop.tobytes() == original.crop((40, 12, 164, 91)).tobytes()
        assert not crop.info
    assert Path(doc['source']).read_bytes() == before
    assert ws.crop_document(doc['id'], box)['cached']
    other_path = tmp_path / 'same-pixels.png'
    with Image.open(io.BytesIO(before)) as original:
        original.save(other_path, compress_level=1)
    other = ws.import_document(str(other_path))
    other_crop = ws.crop_document(other['id'], box)
    assert copied['id'] != other_crop['id']
    assert copied['sha256'] == other_crop['sha256']
    peer = Workspace(ws.root, tmp_path, TextIn('', ''))
    try:
        again = peer.crop_document(doc['id'], box)
        assert again['id'] == copied['id'] and again['cached']
    finally:
        peer.close()
    assert ws._all('job') == [] and ws._all('evidence') == []


def test_rotated_pdf_page_uses_render_geometry_not_unrotated_pdf_coordinates(crop_workspace, tmp_path):
    ws, _ = crop_workspace
    path = tmp_path / 'two-pages.pdf'
    path.write_bytes(synthetic_pdf())
    parent = ws.import_document(str(path))
    region = Region(page=2, box=[.21, .13, .79, .93])
    copied = ws.crop_document(parent['id'], region)
    with ws._page_image(parent, 2) as original, Image.open(copied['source']) as cropped:
        assert original.size == (400, 600)
        pixels = [math.floor(.21 * 400), math.floor(.13 * 600), math.ceil(.79 * 400), math.ceil(.93 * 600)]
        assert copied['crop']['pixel_box'] == pixels
        assert copied['crop']['source_render_size'] == [400, 600]
        assert cropped.tobytes() == original.crop(pixels).tobytes()
    assert copied['source'].endswith('.png') and copied['pages'] == 1
    assert b'SECRET' not in Path(copied['source']).read_bytes()
    evidence, raw = ws.read_evidence(copied['id'], Region(page=1, box=[0, 0, 1, 1]))
    assert evidence['document_id'] == copied['id']
    assert Image.open(io.BytesIO(raw)).size == tuple(copied['crop']['output_size'])


def test_directory_crop_repeated_failures_close_descriptors(crop_workspace):
    ws, doc = crop_workspace
    region = Region(page=1, box=[0, 0, 1, 1])
    cropped = ws.crop_document(doc['id'], region)
    target = Path(cropped['source'])
    original = target.read_bytes()
    target.unlink()
    target.mkdir()
    before = len(os.listdir('/dev/fd'))
    for _ in range(40):
        with pytest.raises(DomainError, match='document_source_unavailable'):
            ws.read_evidence(cropped['id'], region)
    assert len(os.listdir('/dev/fd')) == before
    assert ws._all('evidence') == []
    target.rmdir()
    target.write_bytes(original)
    evidence, binary = ws.read_evidence(cropped['id'], region)
    assert evidence['document_id'] == cropped['id'] and binary


@pytest.mark.parametrize('damage', ['changed', 'missing', 'symlink', 'fifo'])
async def test_damaged_crop_blocks_reuse_evidence_derivation_and_upload(crop_workspace, tmp_path, damage):
    ws, doc = crop_workspace
    region = Region(page=1, box=[.2, .1, .8, .9])
    copied = ws.crop_document(doc['id'], region)
    target = Path(copied['source'])
    data = target.read_bytes()
    target.unlink()
    if damage == 'changed':
        target.write_bytes(b'changed')
    elif damage == 'symlink':
        external = tmp_path / 'identical.png'
        external.write_bytes(data)
        target.symlink_to(external)
    elif damage == 'fifo':
        import os
        os.mkfifo(target)
    for operation in (
        lambda: ws.crop_document(doc['id'], region),
        lambda: ws.crop_document(copied['id'], region),
        lambda: ws.read_evidence(copied['id'], region),
        lambda: ws.redact_document(copied['id'], [region]),
    ):
        with pytest.raises(DomainError):
            operation()
    with pytest.raises(DomainError):
        await ws.parse_document(copied['id'], 1, 'free_sync')
    assert ws._all('job') == [] and ws.list_documents()['total'] == 2


def test_large_image_crop_keeps_pixels_without_evidence_thumbnail_scaling(crop_workspace, tmp_path):
    ws, _ = crop_workspace
    path = tmp_path / 'wide.png'
    Image.new('RGB', (3000, 100), 'blue').save(path)
    parent = ws.import_document(str(path))
    copied = ws.crop_document(parent['id'], Region(page=1, box=[.1, 0, .9, 1]))
    assert copied['crop']['source_render_size'] == [3000, 100]
    with Image.open(copied['source']) as image:
        assert image.size == (2400, 100)
    _, preview = ws.read_evidence(copied['id'], Region(page=1, box=[0, 0, 1, 1]))
    assert Image.open(io.BytesIO(preview)).size == (1800, 75)


def test_large_pdf_mapping_records_actual_limited_render_size(crop_workspace, tmp_path):
    ws, _ = crop_workspace
    path = tmp_path / 'large.pdf'
    Image.new('RGB', (2500, 2000), 'white').save(path, format='PDF', resolution=72)
    parent = ws.import_document(str(path))
    copied = ws.crop_document(parent['id'], Region(page=1, box=[.1, .2, .9, .8]))
    assert copied['crop']['source_render_size'] == [2400, 1920]
    assert copied['crop']['pixel_box'] == [240, 384, 2160, 1536]
    assert copied['crop']['output_size'] == [1920, 1152]


def test_parent_snapshot_and_changed_parent_do_not_relabel_existing_crop(crop_workspace, monkeypatch):
    ws, doc = crop_workspace
    original = Path(doc['source']).read_bytes()
    render = ws._page_image
    def mutate_source_after_snapshot(snapshot, page, *, source=None):
        Image.new('RGB', (201, 103), 'blue').save(doc['source'])
        return render(snapshot, page, source=source)
    monkeypatch.setattr(ws, '_page_image', mutate_source_after_snapshot)
    region = Region(page=1, box=[0, 0, 1, 1])
    first = ws.crop_document(doc['id'], region)
    assert first['crop']['source_sha256'] == hashlib.sha256(original).hexdigest()
    with Image.open(first['source']) as crop, Image.open(io.BytesIO(original)) as source:
        assert crop.tobytes() == source.tobytes()
    second = ws.crop_document(doc['id'], region)
    assert second['id'] != first['id']
    assert ws._get('document', first['id']) == first


def test_creation_failure_rolls_back_files_and_database(crop_workspace, monkeypatch):
    ws, doc = crop_workspace
    put = ws._put
    def fail(kind, key, body):
        put(kind, key, body)
        raise RuntimeError('simulated database interruption')
    monkeypatch.setattr(ws, '_put', fail)
    with pytest.raises(RuntimeError):
        ws.crop_document(doc['id'], Region(page=1, box=[0, 0, .8, .8]))
    assert ws.list_documents()['total'] == 1
    assert not list(ws.root.glob('doc_crop_*')) and not list(ws.root.glob('.crop-*'))


def test_invalid_page_and_large_output_leave_no_document(crop_workspace, monkeypatch):
    ws, doc = crop_workspace
    with pytest.raises(DomainError, match='page_out_of_range'):
        ws.crop_document(doc['id'], Region(page=2, box=[0, 0, 1, 1]))
    source_bytes = Path(doc['source']).read_bytes()
    read = ws._read_document_source
    def limited_after_snapshot(parent):
        result = read(parent)
        monkeypatch.setattr('study_errorbook.service.MAX_BYTES', 1)
        return result
    monkeypatch.setattr(ws, '_read_document_source', limited_after_snapshot)
    with pytest.raises(DomainError, match='crop_too_large'):
        ws.crop_document(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
    assert Path(doc['source']).read_bytes() == source_bytes
    assert ws.list_documents()['total'] == 1
    assert not list(ws.root.glob('doc_crop_*')) and not list(ws.root.glob('.crop-*'))


async def test_parse_sends_crop_bytes_once_and_preserves_parent_job(crop_workspace, monkeypatch):
    ws, doc = crop_workspace
    ws._put('job', 'parent_job', {'document_id': doc['id'], 'status': 'completed', 'fixture': 'unchanged'})
    before = ws._get('job', 'parent_job')
    crop = ws.crop_document(doc['id'], Region(page=1, box=[.2, .1, .8, .9]))
    submitted = []
    async def fake_parse(source, pages, suffix, *, receipt=None):
        submitted.append((source, pages))
        raise DomainError('synthetic_stop', 'No network in this test')
    monkeypatch.setattr(ws.api, 'parse_free', fake_parse)
    job = await ws.parse_document(crop['id'], 1, 'free_sync')
    repeated = await ws.parse_document(crop['id'], 1, 'free_sync')
    assert repeated['cached'] and repeated['id'] == job['id']
    assert len(submitted) == 1 and submitted[0][1] == 1
    assert hashlib.sha256(submitted[0][0]).hexdigest() == crop['sha256']
    assert ws._get('job', 'parent_job') == before


@pytest.mark.parametrize('delete_parent', [False, True])
def test_mixed_crop_redaction_descendants_are_deleted_with_records_and_exports(crop_workspace, delete_parent):
    ws, doc = crop_workspace
    full = Region(page=1, box=[0, 0, 1, 1])
    crop = ws.crop_document(doc['id'], full)
    redacted = ws.redact_document(crop['id'], [Region(page=1, box=[0, 0, .1, .1])])
    nested = ws.crop_document(redacted['id'], full)
    evidence, _ = ws.read_evidence(nested['id'], full)
    record = ws.save_record(StudyRecord(document_id=nested['id'], question_label='synthetic', stem='2x', evidence_ids=[evidence['id']]))
    exported = ws.export_errorbook([record['id']], include_drafts=True)
    selected = doc if delete_parent else crop
    preview = preview_document_deletion(ws.db, ws.root, selected['id'])
    assert preview['inventory_complete'] and preview['counts']['document'] == (4 if delete_parent else 3)
    assert preview['counts']['record'] == 1
    receipt = ws.delete_document(selected['id'], preview['inventory_digest'], True)
    assert receipt['deleted'] and not Path(exported['folder']).exists()
    assert not Path(crop['source']).exists() and not Path(nested['source']).exists()
    assert Path(doc['source']).exists() is (not delete_parent)
    assert ws.list_documents()['total'] == (0 if delete_parent else 1)
    assert (ws.input_root / 'source.png').exists()
