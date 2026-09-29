import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from study_errorbook.models import DomainError, Exercise, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def sample(tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    path = folder / "synthetic-paper.png"
    image = Image.new("RGB", (800, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "1. Expand: 2(x+3)", fill="black", font_size=28)
    draw.text((20, 90), "Student: 2x+3", fill="black", font_size=28)
    draw.line((300, 80, 350, 130), fill="red", width=5)
    draw.line((350, 80, 300, 130), fill="red", width=5)
    image.save(path)
    return path


@pytest.fixture
def workspace(tmp_path, sample):
    service = Workspace(tmp_path / "data", sample.parent, TextIn("", ""))
    yield service
    service.close()


def draft(workspace, sample):
    doc = workspace.import_document(str(sample))
    evidence, binary = workspace.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 0.5]))
    record = StudyRecord.model_validate(
        {
            "document_id": doc["id"],
            "question_label": "1",
            "subject": "math",
            "stem": "Expand 2(x+3)",
            "student_answer": "2x+3",
            "evidence_ids": [evidence["id"]],
            "teacher_verdict": "wrong",
            "agent_verdict": "wrong",
            "analysis": {
                "explanation": "May have omitted multiplying the constant.",
                "error_type": "distribution",
                "evidence_ids": [evidence["id"]],
                "uncertainty": "Could be a transcription slip.",
            },
        }
    )
    return doc, record, binary


def test_complete_offline_workflow_and_revision_invalidation(workspace, sample):
    doc, record, binary = draft(workspace, sample)
    assert Image.open(io.BytesIO(binary)).size == (800, 200)
    assert workspace.import_document(str(sample))["id"] == doc["id"]
    check = workspace.check_exercise("3*(x+4)", "3*x+12")
    assert check["status"] == "equivalent"
    record.exercises = [
        Exercise(
            prompt="Expand 3(x+4)",
            answer="3*x+12",
            targets_error="distribution",
            check_id=check["id"],
        )
    ]
    record = StudyRecord.model_validate(record.model_dump())
    saved = workspace.save_record(record)
    with pytest.raises(DomainError, match="needs_review"):
        workspace.export_errorbook([saved["id"]])
    reviewed = workspace.review_record(
        saved["id"], 1, "include", "The user confirmed this is wrong."
    )
    output = workspace.export_errorbook([saved["id"]])
    folder = Path(output["folder"])
    assert "3*x+12" not in (folder / "practice.html").read_text()
    assert "3*x+12" in (folder / "answers.html").read_text()
    assert "原卷证据" in (folder / "errorbook.html").read_text()
    assert list(folder.glob("ev_*.png"))
    assert workspace.query_errorbook("distribution")["total"] == 1
    attempt = workspace.record_practice(saved["id"], reviewed["revision"], 0, "3x+12")
    assert attempt["grading_status"] == "needs_review"
    record.student_answer = "2x+6"
    updated = workspace.save_record(record, saved["id"], reviewed["revision"])
    assert updated["record"]["analysis"] is None
    assert updated["record"]["exercises"] == []
    assert updated["review_status"] == "needs_review"
    with pytest.raises(DomainError, match="revision_conflict"):
        workspace.save_record(record, saved["id"], 1)


def test_file_and_evidence_boundaries(workspace, sample, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(sample.read_bytes())
    link = sample.parent / "link.png"
    link.symlink_to(outside)
    with pytest.raises(DomainError, match="path_rejected"):
        workspace.import_document(str(link))
    doc, record, _ = draft(workspace, sample)
    second = sample.parent / "second.png"
    Image.new("RGB", (200, 200), "red").save(second)
    other = workspace.import_document(str(second))
    evidence, _ = workspace.read_evidence(other["id"], Region(page=1, box=[0, 0, 1, 1]))
    record.evidence_ids = [evidence["id"]]
    with pytest.raises(DomainError, match="evidence_mismatch"):
        workspace.save_record(record)
    with pytest.raises(DomainError, match="page_out_of_range"):
        workspace.read_evidence(doc["id"], Region(page=2, box=[0, 0, 1, 1]))
    peer = Workspace(workspace.root, sample.parent, TextIn("", ""))
    try:
        assert peer.list_documents()["total"] == 2
    finally:
        peer.close()


@pytest.mark.parametrize(
    "left,right,status",
    [
        ("2*(x+3)", "2*x+6", "equivalent"),
        ("2*(x+3)", "2*x+3", "not_equivalent"),
        ("-2*(x-5)", "-2*x+10", "equivalent"),
        ("(x+1)^2", "x*x+2*x+1", "equivalent"),
        ("x/3 + x/6", "x/2", "equivalent"),
        ("x/x", "1", "needs_review"),
        ("1e999", "1", "needs_review"),
        ("0.10000000000000001", "0.1", "needs_review"),
        ("0.99999999999999999", "1", "needs_review"),
        ("1e-400", "0", "needs_review"),
        ("1e-999999999999999999999999", "0", "needs_review"),
        ("0.00000099999999999999999", "0.000001", "needs_review"),
        ("0.1+0.2", "0.3", "equivalent"),
        ("(x^2 + 0.125)/.5", "2*x*x+.25", "equivalent"),
        ("1_000.000_001", "1000+1/1000000", "equivalent"),
        ("0.1000000000000000000000000000000000000000", "1/10", "equivalent"),
        ("(x-1)/(x-1)", "1", "needs_review"),
        ("__import__('os').system('false')", "0", "needs_review"),
        ("x**99", "x", "needs_review"),
    ],
)
def test_exact_bounded_algebra(workspace, left, right, status):
    assert workspace.check_exercise(left, right)["status"] == status


def test_export_escapes_material(workspace, sample):
    _, record, _ = draft(workspace, sample)
    record.stem = '<script>alert(1)</script><img src="https://example.com">'
    saved = workspace.save_record(record)
    out = workspace.export_errorbook([saved["id"]], include_drafts=True)
    page = Path(out["files"][0]).read_text()
    assert "<script>" not in page
    assert '<img src="https:' not in page
    assert "&lt;script&gt;" in page


async def test_credentials_and_page_budget_do_not_submit(workspace, sample):
    doc = workspace.import_document(str(sample))
    with pytest.raises(DomainError, match="credentials_missing"):
        await workspace.parse_document(doc["id"], 1)
    with pytest.raises(DomainError, match="page_budget"):
        await workspace.parse_document(doc["id"], 0)
    assert workspace._all("job") == []


async def test_remote_job_survives_restart_and_no_duplicate_submission(tmp_path, sample):
    calls = []

    def provider(request):
        calls.append((request.method, request.url.path))
        assert request.headers["x-ti-secret-code"] == "fixture-secret"
        if request.method == "POST":
            body = request.read()
            assert (
                b"include_inline_objects" in body
                and b"multipart/form-data" in request.headers["content-type"].encode()
            )
            return httpx.Response(200, json={"code": 200, "data": {"job_id": "remote_1"}})
        if request.url.host == "api.textin.com":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result_url": "https://web-api.textin.com/file/xparse_result/1",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "success_count": 1,
                    "schema_version": "1.3.0",
                    "elements": [
                        {
                            "element_id": "e1",
                            "type": "NarrativeText",
                            "page_number": 1,
                            "text": "Expand 2(x+3)",
                            "coordinates": [0, 0, 1, 0, 1, 0.2, 0, 0.2],
                            "objects": [{"object_id": "o1", "type": "handwriting", "text": "2x+3"}],
                        }
                    ],
                },
            },
        )

    api = TextIn("fixture-id", "fixture-secret", httpx.MockTransport(provider))
    first = Workspace(tmp_path / "state", sample.parent, api)
    doc = first.import_document(str(sample))
    jobs = await asyncio.gather(
        first.parse_document(doc["id"], 1), first.parse_document(doc["id"], 1)
    )
    assert jobs[0]["id"] == jobs[1]["id"]
    first.close()
    second = Workspace(tmp_path / "state", sample.parent, api)
    try:
        job = await second.get_job(jobs[0]["id"])
        assert job["status"] == "completed"
        result = second.read_document(job["id"])
        assert result["elements"][0]["objects"][0]["type"] == "handwriting"
        assert (await second.parse_document(doc["id"], 1))["cached"]
        assert sum(method == "POST" for method, _ in calls) == 1
        await second.get_job(job["id"])
        assert len(calls) == 3
    finally:
        second.close()


async def test_timeout_preserves_unknown_no_automatic_resubmit(tmp_path, sample):
    calls = []

    def provider(request):
        calls.append(request)
        raise httpx.ReadTimeout("contains fixture-secret", request=request)

    ws = Workspace(
        tmp_path / "state",
        sample.parent,
        TextIn("id", "fixture-secret", httpx.MockTransport(provider)),
    )
    try:
        doc = ws.import_document(str(sample))
        first = await ws.parse_document(doc["id"], 1)
        second = await ws.parse_document(doc["id"], 1)
        assert first["status"] == "submission_unknown" and second["cached"]
        assert len(calls) == 1
        assert "fixture-secret" not in json.dumps(first)
    finally:
        ws.close()


async def test_provider_errors_and_result_url_do_not_leak_credentials():
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"code": 40306, "message": "secret can appear here"})

    api = TextIn("id", "fixture-secret", httpx.MockTransport(provider))
    with pytest.raises(DomainError, match="停止重试"):
        await api.poll("job")
    for url in (
        "https://attacker.example/result",
        "https://api.textin.com@attacker.example/x",
        "http://api.textin.com/x",
    ):
        with pytest.raises(DomainError, match="result_url_rejected"):
            await api.result(url)
    assert len(calls) == 1


async def test_clean_copy_keeps_original_evidence_and_cached(tmp_path, sample):
    calls = []
    clean = io.BytesIO()
    Image.new("RGB", (800, 200), "white").save(clean, format="PNG")

    def provider(request):
        calls.append(request)
        assert request.headers["content-type"] == "application/octet-stream"
        assert request.url.params["dewarp"] == "0"
        return httpx.Response(
            200,
            json={"code": 200, "result": {"image": base64.b64encode(clean.getvalue()).decode()}},
        )

    ws = Workspace(
        tmp_path / "state", sample.parent, TextIn("id", "secret", httpx.MockTransport(provider))
    )
    try:
        _, record, original = draft(ws, sample)
        ev = record.evidence_ids[0]
        output = await ws.make_clean_copy(ev)
        assert output["status"] == "completed"
        assert output["review_status"] == "needs_visual_review"
        assert Path(ws._get("evidence", ev)["path"]).read_bytes() == original
        assert (await ws.make_clean_copy(ev))["cached"]
        assert len(calls) == 1
    finally:
        ws.close()
