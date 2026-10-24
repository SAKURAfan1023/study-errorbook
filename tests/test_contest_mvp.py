"""Offline acceptance of progress, safe recovery and learner/answer separation."""
import asyncio
import base64
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from study_errorbook.models import CoveragePage, DomainError, PracticeFeedback, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "synthetic.pdf"
    pages = [Image.new("RGB", (120, 100), color) for color in ("white", "yellow")]
    pages[0].save(source, save_all=True, append_images=pages[1:], format="PDF")
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    doc = ws.import_document(str(source))
    yield ws, doc
    ws.close()


def inventory(ws, doc, page, **extra):
    evidence, _ = ws.read_evidence(doc["id"], Region(page=page, box=[0, 0, 1, 1]))
    return CoveragePage(page=page, evidence_id=evidence["id"], **extra)


def test_coverage_derives_current_status_and_survives_restart(workspace):
    ws, doc = workspace
    assert not ws.read_task_progress(doc["id"])["organization_complete"]
    page = inventory(ws, doc, 1, questions=[{"question_label": "1"}, {"question_label": "2", "skip_reason": "本轮只做第1题"}])
    first = ws.update_document_coverage(doc["id"], 0, [page])
    assert first["counts"] == {"processed": 0, "pending": 1, "skipped": 1}
    assert not first["inventory_complete"]
    saved = ws.save_record(StudyRecord(document_id=doc["id"], question_label="1", stem="2(x+3)", evidence_ids=[page.evidence_id]))
    assert ws.read_task_progress(doc["id"])["unlinked_record_ids"] == [saved["id"]]
    page.questions[0].record_id = saved["id"]
    second = inventory(ws, doc, 2, note="合成空白页，没有题目")
    ready = ws.update_document_coverage(doc["id"], 1, [page, second])
    assert ready["inventory_complete"] and not ready["organization_complete"]
    ws.review_record(saved["id"], 1, "include", "合成测试决定，不是真实学生意见")
    assert ws.read_task_progress(doc["id"])["organization_complete"]
    peer = Workspace(ws.root, ws.input_root, TextIn("", ""))
    try:
        assert peer.read_task_progress(doc["id"])["counts"]["processed"] == 1
        with pytest.raises(DomainError, match="coverage_conflict"):
            peer.update_document_coverage(doc["id"], 1, [second])
        current = peer.read_record(saved["id"])
        peer.save_record(StudyRecord(**current["record"]), saved["id"], 2)
        assert ws.read_task_progress(doc["id"])["counts"]["pending"] == 1
    finally:
        peer.close()


def test_same_page_record_alias_rejected_without_changing_saved_coverage(workspace):
    ws, doc = workspace
    page = inventory(ws, doc, 1, questions=[{"question_label": "1"}, {"question_label": "2"}])
    record = ws.save_record(StudyRecord(document_id=doc["id"], question_label="1", stem="2(x+3)", evidence_ids=[page.evidence_id]))
    ws.review_record(record["id"], 1, "include", "Synthetic review of question 1 only")
    page.questions[0].record_id = record["id"]
    empty = inventory(ws, doc, 2, note="Synthetic blank page")
    before = ws.update_document_coverage(doc["id"], 0, [page, empty])
    assert before["counts"] == {"processed": 1, "pending": 1, "skipped": 0}
    rows = ws.db.execute("SELECT * FROM objects ORDER BY kind,id").fetchall()
    page.questions[1].record_id = record["id"]
    with pytest.raises(DomainError, match="coverage_record_duplicate"):
        ws.update_document_coverage(doc["id"], 1, [page])
    assert ws.db.execute("SELECT * FROM objects ORDER BY kind,id").fetchall() == rows
    assert ws.read_task_progress(doc["id"]) == before
    assert not before["organization_complete"]


def test_cross_page_continuation_can_share_one_record_and_restart(workspace):
    ws, doc = workspace
    pages = [inventory(ws, doc, n, questions=[{"question_label": "1" if n == 1 else "1 (continued)"}]) for n in (1, 2)]
    record = ws.save_record(StudyRecord(document_id=doc["id"], question_label="1", stem="Synthetic question continued on page 2", evidence_ids=[p.evidence_id for p in pages]))
    ws.review_record(record["id"], 1, "include", "Synthetic review of the whole cross-page question")
    for page in pages:
        page.questions[0].record_id = record["id"]
    progress = ws.update_document_coverage(doc["id"], 0, pages)
    assert progress["counts"] == {"processed": 2, "pending": 0, "skipped": 0}
    assert progress["inventory_complete"] and progress["organization_complete"]
    peer = Workspace(ws.root, ws.input_root, TextIn("", ""))
    try:
        assert peer.read_task_progress(doc["id"]) == progress
    finally:
        peer.close()


def test_preexisting_same_page_aliases_are_pending_instead_of_false_completion(workspace):
    ws, doc = workspace
    first = inventory(ws, doc, 1, questions=[{"question_label": "1"}, {"question_label": "2"}])
    record = ws.save_record(StudyRecord(document_id=doc["id"], question_label="1", stem="Synthetic question 1 only", evidence_ids=[first.evidence_id]))
    ws.review_record(record["id"], 1, "include", "Synthetic review of question 1 only")
    for question in first.questions:
        question.record_id = record["id"]
    second = inventory(ws, doc, 2, note="Synthetic blank page")
    # Reproduce a persisted inventory created before the duplicate-mapping fix.
    saved = ws._get("document", doc["id"])
    saved.update(coverage_revision=1, coverage=[p.model_dump() for p in (first, second)])
    ws._put("document", doc["id"], saved)
    progress = ws.read_task_progress(doc["id"])
    assert progress["counts"] == {"processed": 0, "pending": 2, "skipped": 0}
    assert not progress["inventory_complete"] and not progress["organization_complete"]
    assert "重复关联" in progress["next_action"]


def test_coverage_rejects_partial_wrong_page_and_foreign_record(workspace):
    ws, doc = workspace
    crop, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, .5, .5]))
    with pytest.raises(DomainError, match="coverage_evidence"):
        ws.update_document_coverage(doc["id"], 0, [CoveragePage(page=1, evidence_id=crop["id"], note="partial")])
    with pytest.raises(DomainError, match="coverage_evidence"):
        ws.update_document_coverage(doc["id"], 0, [inventory(ws, doc, 1, note="wrong").model_copy(update={"page": 2})])
    source = ws.input_root / 'foreign.png'
    Image.new('RGB', (30, 30), 'white').save(source)
    foreign = ws.import_document(str(source))
    foreign_evidence, _ = ws.read_evidence(foreign['id'], Region(page=1, box=[0, 0, 1, 1]))
    record = ws.save_record(StudyRecord(document_id=foreign['id'], question_label='foreign', stem='x', evidence_ids=[foreign_evidence['id']]))
    with pytest.raises(DomainError, match='coverage_record'):
        ws.update_document_coverage(doc['id'], 0, [inventory(ws, doc, 1, questions=[{'question_label': '1', 'record_id': record['id']}])])
    with pytest.raises(ValidationError):
        CoveragePage(page=1, evidence_id=crop["id"])
    with pytest.raises(ValidationError):
        CoveragePage(page=1, evidence_id=crop["id"], questions=[{"question_label": "1", "record_id": "r", "skip_reason": "skip"}])
    assert ws.read_task_progress(doc["id"])["coverage_revision"] == 0


@pytest.mark.parametrize("limit", ["http_429", "textin_40306"])
async def test_completed_cache_recovery_only_get_pauses_and_resumes(workspace, monkeypatch, limit):
    ws, doc = workspace
    calls, now, replies = [], [10000.0], [limit]
    monkeypatch.setattr("study_errorbook.service.time.time", lambda: now[0])
    def provider(request):
        calls.append(request.method)
        assert request.method == "GET"
        if request.url.host == "web-api.textin.com":
            return httpx.Response(200, json={"success_count": 2, "elements": [{"text": "restored"}]})
        if replies:
            code = replies.pop()
            return httpx.Response(429) if code == "http_429" else httpx.Response(200, json={"code": 40306})
        return httpx.Response(200, json={"data": {"status": "completed", "result_url": "https://web-api.textin.com/result"}})
    ws.api = TextIn("synthetic-id", "synthetic-secret", httpx.MockTransport(provider))
    job = {"id": "saved-job", "document_id": doc["id"], "status": "completed", "mode": "paid_async", "requested_pages": 2, "provider_job_id": "original-id"}
    ws._cache_parse_result(job, doc, {"success_count": 2, "elements": []})
    ws._put("job", job["id"], job)
    assert (await ws.recover_parse_result(job["id"]))["cached"] and not calls
    Path(job["result_path"]).unlink()
    paused = await ws.recover_parse_result(job["id"])
    assert paused["poll_error"]["code"] == limit and paused["status"] == "completed"
    now[0] += 5
    assert "next_action" in await ws.get_job(job["id"])
    with pytest.raises(DomainError, match="recovery_paused"):
        await ws.recover_parse_result(job["id"])
    assert len(calls) == 1
    recovered = await ws.resume_job_observation(job["id"], 1, 1, "合成测试：已处理限流，恢复原任务GET")
    assert recovered["status"] == "completed" and recovered["provider_job_id"] == "original-id"
    assert ws.read_document(job["id"])["elements"][0]["text"] == "restored"
    assert calls == ["GET", "GET", "GET"]
    with pytest.raises(DomainError, match="observation_conflict"):
        await ws.resume_job_observation(job["id"], 1, 1, "旧决定重复送达")


@pytest.mark.parametrize("defect", ["sync", "symlink", "fifo", "expired"])
async def test_cache_recovery_refuses_unsafe_paths_and_never_posts(workspace, defect):
    ws, doc = workspace
    calls = []
    def provider(request):
        calls.append(request.method)
        assert request.method == "GET"
        return httpx.Response(404)
    ws.api = TextIn("synthetic-id", "synthetic-secret", httpx.MockTransport(provider))
    job = {"id": "original", "document_id": doc["id"], "status": "completed", "mode": "paid_async", "requested_pages": 2, "provider_job_id": "original-id"}
    ws._cache_parse_result(job, doc, {"success_count": 2, "elements": []})
    path = Path(job["result_path"])
    path.unlink()
    if defect == "sync":
        job.update(mode="free_sync", provider_job_id=None)
    if defect == "symlink":
        path.symlink_to(Path(doc["source"]))
    if defect == "fifo":
        import os
        os.mkfifo(path)
    ws._put("job", job["id"], job)
    if defect == "expired":
        result = await ws.recover_parse_result(job["id"])
        assert result["status"] == "completed" and result["poll_error"]["code"] == "http_404"
        assert calls == ["GET"]
    else:
        with pytest.raises(DomainError, match="recovery_unavailable|recovery_path"):
            await ws.recover_parse_result(job["id"])
        assert not calls


async def test_erase_unknown_requires_explicit_retry_and_retains_receipt(workspace):
    ws, doc = workspace
    evidence, binary = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    calls = []
    def provider(request):
        calls.append(request.method)
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic lost response")
        return httpx.Response(200, json={"code": 200, "result": {"image": base64.b64encode(binary).decode()}})
    ws.api = TextIn("synthetic-id", "synthetic-secret", httpx.MockTransport(provider))
    unknown = await ws.make_clean_copy(evidence["id"])
    assert unknown["status"] == "submission_unknown"
    assert (await ws.make_clean_copy(evidence["id"]))["cached"] and len(calls) == 1
    with pytest.raises(DomainError, match="retry_confirmation"):
        await ws.make_clean_copy(evidence["id"], 1, False, "")
    results = await asyncio.gather(*[ws.make_clean_copy(evidence["id"], 1, True, "合成测试：明确同意重新上传和可能重复计费") for _ in range(2)], return_exceptions=True)
    assert results[0]["status"] == "completed" and results[0]["attempt_number"] == 2
    assert results[0]["attempt_history"][0] == unknown
    assert isinstance(results[1], DomainError) and results[1].code == "attempt_conflict"
    assert len(calls) == 2 and ws._read_evidence_image(evidence["id"]) == binary


def test_feedback_export_is_private_and_escaped(workspace):
    ws, doc = workspace
    evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    record = ws.save_record(StudyRecord(document_id=doc["id"], question_label="1", stem="Expand", evidence_ids=[evidence["id"]], exercises=[{"prompt": "2(x+3)", "answer": "PRIVATE-ANSWER", "targets_error": "distribution"}]))
    attempt = ws.record_practice(record["id"], 1, 0, "PRIVATE-RESPONSE<script>bad()</script>", assistance="independent")
    ws.review_practice_attempt(attempt["id"], 0, PracticeFeedback(agent_verdict="wrong", feedback="PRIVATE-FEEDBACK", next_step="Try both terms"))
    output = ws.export_errorbook([record["id"]], include_drafts=True)
    feedback = (Path(output["folder"]) / "feedback.html").read_text()
    assert "PRIVATE-FEEDBACK" in feedback and "&lt;script&gt;" in feedback and "<script>bad" not in feedback
    with zipfile.ZipFile(output["practice_bundle_path"]) as bundle:
        assert "feedback.html" not in bundle.namelist()
        assert "answers.html" not in bundle.namelist()
        assert not any(secret in bundle.read("practice.html").decode() for secret in ["PRIVATE-ANSWER", "PRIVATE-RESPONSE", "PRIVATE-FEEDBACK"])
    assert json.loads(Path(output["manifest_path"]).read_text())["contains_drafts"]
