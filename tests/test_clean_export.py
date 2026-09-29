import hashlib
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageDraw

from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def clean_workspace(tmp_path):
    source = tmp_path / "synthetic.png"
    picture = Image.new("RGB", (700, 200), "white")
    drawing = ImageDraw.Draw(picture)
    drawing.text((20, 30), "Synthetic question: 2(x+3) = ____", fill="black")
    picture.save(source)
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    doc = ws.import_document(str(source))
    evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    clean = ws.root / doc["id"] / "synthetic-clean.png"
    picture.save(clean)
    ws._put("erase", evidence["id"], {
        "id": evidence["id"], "source_evidence_id": evidence["id"],
        "status": "completed", "path": str(clean), "fixture": "not an actual erasure",
    })
    record = StudyRecord(document_id=doc["id"], question_label="Synthetic original",
                         stem="TRANSCRIBED_STEM_NOT_FOR_REPLAY", student_answer="SECRET_STUDENT_ANSWER",
                         evidence_ids=[evidence["id"]], clean_evidence_ids=[evidence["id"]])
    yield ws, record, clean
    ws.close()


def test_clean_selection_requires_unique_main_evidence_and_completed_artifact(clean_workspace):
    ws, record, _ = clean_workspace
    for ids in (["not-evidence"], record.evidence_ids * 2):
        with pytest.raises(DomainError, match="clean_evidence_mismatch"):
            ws.save_record(record.model_copy(update={"clean_evidence_ids": ids}))
    ws._put("erase", record.evidence_ids[0], {"status": "submission_unknown"})
    with pytest.raises(DomainError, match="clean_unavailable"):
        ws.save_record(record)
    assert not ws._all("record")


def test_only_reviewed_clean_copies_enter_formal_export(clean_workspace):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    ws.review_record(saved["id"], 1, "include", "only the original question")
    with pytest.raises(DomainError, match="clean_not_ready"):
        ws.export_errorbook([saved["id"]])
    assert not (ws.root / "exports").exists()
    draft = ws.export_errorbook([saved["id"]], include_drafts=True)
    assert draft["contains_drafts"] and len(draft["files"]) == 4
    page = (Path(draft["folder"]) / "original-practice.html").read_text()
    assert "净题图待视觉复核" in page
    assert record.stem not in page and record.student_answer not in page
    assert f'src="{record.evidence_ids[0]}.png"' not in page
    current = ws.read_record(saved["id"])["clean_copy_validation"][0]
    approvals = {current["source_evidence_id"]: current["sha256"]}
    ws.review_record(saved["id"], 2, "include", "compared print, formulas, diagram and answers", approved_clean_copies=approvals)
    result = ws.export_errorbook([saved["id"]])
    assert not result["contains_drafts"]
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    item = manifest["records"][0]["clean_copies"][0]
    assert item["sha256"] == hashlib.sha256(clean.read_bytes()).hexdigest()
    assert (Path(result["folder"]) / item["file"]).read_bytes() == clean.read_bytes()
    assert "净题图已由用户复核" in (Path(result["folder"]) / "original-practice.html").read_text()


@pytest.mark.parametrize("decision,approvals", [("exclude", "valid"), ("include", "wrong"), ("include", "foreign")])
def test_invalid_clean_approval_does_not_change_revision(clean_workspace, decision, approvals):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    mapping = {record.evidence_ids[0]: hashlib.sha256(clean.read_bytes()).hexdigest()}
    if approvals == "wrong":
        mapping[record.evidence_ids[0]] = "0" * 64
    elif approvals == "foreign":
        mapping = {"foreign": "0" * 64}
    with pytest.raises(DomainError, match="invalid_clean_review"):
        ws.review_record(saved["id"], 1, decision, "fixture", approved_clean_copies=mapping)
    assert ws.read_record(saved["id"])["revision"] == 1
    assert not ws._all("history")


def test_edits_re_review_and_image_changes_invalidate_clean_approval(clean_workspace):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    mapping = {record.evidence_ids[0]: hashlib.sha256(clean.read_bytes()).hexdigest()}
    ws.review_record(saved["id"], 1, "include", "fixture", approved_clean_copies=mapping)
    assert ws.read_record(saved["id"])["clean_copy_validation"][0]["ready"]
    ws.save_record(record, saved["id"], 2)
    assert not ws.read_record(saved["id"])["clean_copy_validation"][0]["ready"]
    ws.review_record(saved["id"], 3, "include", "fixture", approved_clean_copies=mapping)
    ws.review_record(saved["id"], 4, "include", "new review without clean approval")
    assert not ws.read_record(saved["id"])["clean_copy_validation"][0]["ready"]
    ws.review_record(saved["id"], 5, "include", "fixture", approved_clean_copies=mapping)
    Image.new("RGB", (100, 100), "red").save(clean)
    assert not ws.read_record(saved["id"])["clean_copy_validation"][0]["ready"]
    with pytest.raises(DomainError, match="clean_not_ready"):
        ws.export_errorbook([saved["id"]])


async def test_cached_erasure_remains_readable_without_credentials(clean_workspace):
    ws, record, _ = clean_workspace
    cached = await ws.make_clean_copy(record.evidence_ids[0])
    assert cached["cached"] and cached["status"] == "completed"


def test_missing_clean_file_is_reported_and_cannot_be_approved_or_exported(clean_workspace):
    ws, record, clean = clean_workspace
    saved = ws.save_record(record)
    digest = hashlib.sha256(clean.read_bytes()).hexdigest()
    clean.unlink()
    assert ws.read_record(saved["id"])["clean_copy_validation"][0]["status"] == "unavailable"
    with pytest.raises(DomainError, match="invalid_clean_review"):
        ws.review_record(saved["id"], 1, "include", "fixture", approved_clean_copies={record.evidence_ids[0]: digest})
    with pytest.raises(DomainError, match="clean_unavailable"):
        ws.export_errorbook([saved["id"]], include_drafts=True)
    assert not (ws.root / "exports").exists()


async def test_clean_review_and_fourth_export_over_real_stdio(clean_workspace, tmp_path):
    ws, record, _ = clean_workspace
    params = StdioServerParameters(command=sys.executable, args=["-m", "study_errorbook.server"], env={
        "STUDY_DATA_DIR": str(ws.root), "STUDY_INPUT_ROOT": str(tmp_path),
        "STUDY_CONFIG_FILE": str(tmp_path / "absent.env"), "TEXTIN_APP_ID": "", "TEXTIN_SECRET_CODE": "",
    })
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            viewed = await session.call_tool("make_clean_copy", {"evidence_id": record.evidence_ids[0]})
            assert not viewed.isError and {p.type for p in viewed.content} == {"text", "image"}
            saved = await session.call_tool("save_study_record", {"record": record.model_dump()})
            assert not saved.isError
            identifier = saved.structuredContent["id"]
            current = await session.call_tool("read_study_record", {"record_id": identifier})
            clean = current.structuredContent["clean_copy_validation"][0]
            reviewed = await session.call_tool("review_study_record", {
                "record_id": identifier, "expected_revision": 1, "decision": "include", "user_note": "synthetic fixture review",
                "approved_clean_copies": {clean["source_evidence_id"]: clean["sha256"]},
            })
            assert not reviewed.isError
            exported = await session.call_tool("export_errorbook", {"record_ids": [identifier]})
            assert not exported.isError and len(exported.structuredContent["files"]) == 4
