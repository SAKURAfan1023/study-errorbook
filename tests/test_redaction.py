import hashlib
import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


def synthetic_pdf():
    """Two pages with real text, hidden text, a widget, metadata and an attachment."""
    stream = b"BT /F1 18 Tf 20 170 Td (Name: SYNTHETIC SECRET) Tj 3 Tr 0 -30 Td (HIDDEN SECRET) Tj 0 Tr 0 -70 Td (Expand 2\x28x+3\x29) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /AcroForm 12 0 R /Names << /EmbeddedFiles << /Names [(private.txt) 11 0 R] >> >> >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R /Annots [10 0 R] >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Rotate 90 /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /EmbeddedFile /Length 17 >>\nstream\nATTACHMENT SECRET\nendstream",
        b"<< /Title (TITLE SECRET) /Author (AUTHOR SECRET) >>",
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (Student) /V (FIELD SECRET) /Rect [10 10 80 25] /P 3 0 R >>",
        b"<< /Type /Filespec /F (private.txt) /EF << /F 8 0 R >> >>",
        b"<< /Fields [10 0 R] >>",
    ]
    content = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R /Info 9 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(content)


@pytest.fixture
def redaction_workspace(tmp_path):
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    source = tmp_path / "SYNTHETIC-STUDENT.png"
    im = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(im).text((10, 10), "SYNTHETIC NAME", fill="red", font_size=24)
    ImageDraw.Draw(im).text((10, 100), "2(x+3) = ?", fill="black", font_size=24)
    im.save(source, icc_profile=b"SYNTHETIC PRIVATE PROFILE")
    doc = ws.import_document(str(source))
    yield ws, doc
    ws.close()


def test_image_pixels_metadata_and_original_are_preserved_or_removed_as_intended(redaction_workspace):
    ws, doc = redaction_workspace
    original = Path(doc["source"]).read_bytes()
    copied = ws.redact_document(doc["id"], [Region(page=1, box=[0, 0, 1, .3])])
    assert copied["id"] != doc["id"] and copied["name"] == "redacted.png"
    assert copied["redaction"]["source_document_id"] == doc["id"]
    assert Path(doc["source"]).read_bytes() == original
    with Image.open(doc["source"]) as before, Image.open(copied["source"]) as after:
        assert not after.info and after.crop((0, 0, 400, 60)).getbbox() is None
        assert before.crop((0, 60, 400, 200)).tobytes() == after.crop((0, 60, 400, 200)).tobytes()
    evidence, raw = ws.read_evidence(copied["id"], Region(page=1, box=[0, 0, 1, 1]))
    assert Image.open(io.BytesIO(raw)).getpixel((10, 10)) == (0, 0, 0)
    saved = ws.save_record(StudyRecord(document_id=copied["id"], question_label="Synthetic", stem="2(x+3)", evidence_ids=[evidence["id"]]))
    assert saved["record"]["document_id"] == copied["id"]


def test_pdf_rebuild_removes_hidden_objects_and_keeps_page_geometry(redaction_workspace, tmp_path):
    ws, _ = redaction_workspace
    path = tmp_path / "synthetic-private.pdf"
    path.write_bytes(synthetic_pdf())
    doc = ws.import_document(str(path))
    with pdfium.PdfDocument(path) as original:
        assert "SECRET" in original[0].get_textpage().get_text_range()
    regions = [Region(page=1, box=[0, 0, 1, .3]), Region(page=2, box=[.7, 0, 1, 1])]
    copied = ws.redact_document(doc["id"], regions)
    assert copied["pages"] == 2
    data = Path(copied["source"]).read_bytes()
    assert b"SECRET" not in data
    for key in (b"/AcroForm", b"/EmbeddedFiles", b"/Annots", b"/Title", b"/Author"):
        assert key not in data
    with pdfium.PdfDocument(copied["source"]) as pdf:
        assert [pdf[i].get_size() for i in range(2)] == [(300, 200), (200, 300)]
        for page in pdf:
            textpage = page.get_textpage()
            assert textpage.get_text_range() == ""
            textpage.close()
            assert all(obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects())
            page.close()
    with ws._page_image(copied, 1) as page:
        assert page.getpixel((20, 20)) == (0, 0, 0)
        assert page.crop((0, 150, 600, 400)).getextrema()[0][0] == 0
    assert path.read_bytes() == synthetic_pdf()


def test_equivalent_regions_are_cached_and_tampering_is_not_reused(redaction_workspace):
    ws, doc = redaction_workspace
    regions = [Region(page=1, box=[0, 0, .5, .3]), Region(page=1, box=[.5, 0, 1, .3])]
    copied = ws.redact_document(doc["id"], regions)
    assert ws.redact_document(doc["id"], list(reversed(regions)) + regions)["cached"]
    Path(copied["source"]).write_bytes(b"modified")
    with pytest.raises(DomainError, match="redacted_copy_changed"):
        ws.redact_document(doc["id"], regions)


@pytest.mark.parametrize("regions", [[], [Region(page=2, box=[0, 0, 1, 1])], [Region(page=1, box=[0, 0, 1, 1])] * 101])
def test_invalid_redactions_do_not_create_documents(redaction_workspace, regions):
    ws, doc = redaction_workspace
    with pytest.raises(DomainError, match="invalid_redactions"):
        ws.redact_document(doc["id"], regions)
    assert ws.list_documents()["total"] == 1


def test_interrupted_database_write_rolls_back_new_artifact(redaction_workspace, monkeypatch):
    ws, doc = redaction_workspace
    original_put = ws._put

    def fail(kind, identifier, data):
        original_put(kind, identifier, data)
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(ws, "_put", fail)
    with pytest.raises(RuntimeError):
        ws.redact_document(doc["id"], [Region(page=1, box=[0, 0, 1, .3])])
    assert ws.list_documents()["total"] == 1
    assert not list(ws.root.glob("doc_redacted_*")) and not list(ws.root.glob(".redact-*"))


def test_rasterized_output_limit_leaves_no_partial_copy(redaction_workspace, tmp_path, monkeypatch):
    ws, _ = redaction_workspace
    path = tmp_path / "synthetic-limit.pdf"
    data = synthetic_pdf()
    path.write_bytes(data)
    doc = ws.import_document(str(path))
    monkeypatch.setattr("study_errorbook.service.MAX_BYTES", len(data) + 10)
    with pytest.raises(DomainError, match="redacted_copy_too_large"):
        ws.redact_document(doc["id"], [Region(page=1, box=[0, 0, 1, .3])])
    assert ws.list_documents()["total"] == 2
    assert not list(ws.root.glob("doc_redacted_*")) and not list(ws.root.glob(".redact-*"))


async def test_parse_uses_only_redacted_bytes_and_refuses_changed_copy(redaction_workspace, monkeypatch):
    ws, doc = redaction_workspace
    copied = ws.redact_document(doc["id"], [Region(page=1, box=[0, 0, 1, .3])])
    uploads = []

    async def capture(source, pages, suffix, *, receipt=None):
        uploads.append(source)
        raise DomainError("synthetic_provider_stop", "No network call in this test.")

    monkeypatch.setattr(ws.api, "parse_free", capture)
    await ws.parse_document(copied["id"], 1, "free_sync")
    assert len(uploads) == 1 and hashlib.sha256(uploads[0]).hexdigest() == copied["sha256"]
    assert uploads[0] != Path(doc["source"]).read_bytes()
    Path(copied["source"]).write_bytes(Path(doc["source"]).read_bytes())
    with pytest.raises(DomainError, match="redacted_copy_changed"):
        await ws.parse_document(copied["id"], 1, "free_sync")
    assert len(uploads) == 1
