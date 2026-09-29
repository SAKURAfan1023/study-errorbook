import asyncio
import json
import sys
from contextlib import AsyncExitStack

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from pydantic import ValidationError

from study_errorbook.models import DomainError, PracticeFeedback, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def feedback_workspace(tmp_path):
    paper = tmp_path / "synthetic.png"
    Image.new("RGB", (100, 100), "white").save(paper)
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    doc = ws.import_document(str(paper))
    evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    record = StudyRecord(document_id=doc["id"], question_label="Feedback fixture", stem="Expand 2(x+3)",
                         evidence_ids=[evidence["id"]])
    saved = ws.save_record(record)
    attempt = ws.record_practice(saved["id"], 1, None, "2x+3", "original", "unknown", "answer-1")
    feedback = PracticeFeedback(agent_verdict="wrong", feedback="The constant term was not multiplied by 2.",
                                next_step="Write both products before combining them.")
    yield ws, record, saved, attempt, feedback
    ws.close()


def test_feedback_is_versioned_and_does_not_change_original_response(feedback_workspace):
    ws, _, saved, attempt, feedback = feedback_workspace
    first = ws.read_practice_feedback(attempt["id"])
    assert first["current_feedback_revision"] == 0 and first["items"] == []
    reviewed = ws.review_practice_attempt(attempt["id"], 0, feedback)
    assert reviewed["feedback_revision"] == 1 and reviewed["user_verdict"] == "unreported"
    after = ws._get("attempt", attempt["id"])
    for field in ("response", "source_revision", "source_snapshot", "assistance", "created_at", "request_fingerprint"):
        assert after[field] == attempt[field]
    assert after["grading_status"] == "feedback_recorded" and "mastered" not in after
    assert ws.read_record(saved["id"])["recent_practice_attempts"][0]["feedback_summary"] == {
        "agent_verdict": "wrong", "user_verdict": "unreported",
    }


def test_conflicting_user_opinion_is_preserved_without_overwriting_agent_assessment(feedback_workspace):
    ws, _, _, attempt, feedback = feedback_workspace
    ws.review_practice_attempt(attempt["id"], 0, feedback)
    revision = feedback.model_copy(update={"user_verdict": "correct", "user_note": "Fixture: user reports a transcription issue."})
    ws.review_practice_attempt(attempt["id"], 1, revision)
    history = ws.read_practice_feedback(attempt["id"])
    assert [item["feedback_revision"] for item in history["items"]] == [2, 1]
    assert history["items"][0]["user_verdict"] == "correct"
    assert history["items"][0]["agent_verdict"] == "wrong"
    assert history["items"][1]["user_verdict"] == "unreported"


def test_old_feedback_revision_refuses_overwrite(feedback_workspace):
    ws, _, _, attempt, feedback = feedback_workspace
    ws.review_practice_attempt(attempt["id"], 0, feedback)
    with pytest.raises(DomainError, match="feedback_conflict"):
        ws.review_practice_attempt(attempt["id"], 0, feedback)
    assert len(ws.read_practice_feedback(attempt["id"])["items"]) == 1


def test_agent_only_revision_preserves_user_disagreement_and_its_source(feedback_workspace):
    ws, _, saved, attempt, feedback = feedback_workspace
    opinion = feedback.model_copy(update={"user_verdict": "correct", "user_note": "Synthetic user disagrees."})
    ws.review_practice_attempt(attempt["id"], 0, opinion)
    updated = ws.review_practice_attempt(attempt["id"], 1, feedback)
    assert updated["user_verdict"] == "correct"
    assert updated["user_note"] == opinion.user_note
    assert updated["user_opinion_source_revision"] == 1
    assert updated["user_opinion_updated"] is False
    assert ws.read_practice_history(saved["id"])["items"][0]["feedback_summary"]["user_verdict"] == "correct"


def test_explicit_withdrawal_is_retained_and_a_new_opinion_can_replace_it(feedback_workspace):
    ws, _, _, attempt, feedback = feedback_workspace
    opinion = feedback.model_copy(update={"user_verdict": "correct", "user_note": "Synthetic disagreement."})
    ws.review_practice_attempt(attempt["id"], 0, opinion)
    withdrawal = PracticeFeedback.model_validate({**feedback.model_dump(), "withdraw_user_opinion": True,
                                                 "user_note": "Synthetic user explicitly withdraws the judgment."})
    cleared = ws.review_practice_attempt(attempt["id"], 1, withdrawal)
    assert cleared["user_verdict"] == "unreported" and cleared["user_opinion_updated"]
    assert cleared["user_opinion_source_revision"] == 2
    carried = ws.review_practice_attempt(attempt["id"], 2, feedback)
    assert carried["user_verdict"] == "unreported" and carried["user_note"] == withdrawal.user_note
    assert carried["user_opinion_source_revision"] == 2
    assert not carried["withdraw_user_opinion"] and not carried["user_opinion_updated"]
    renewed = ws.review_practice_attempt(attempt["id"], 3, opinion)
    assert renewed["user_verdict"] == "correct" and renewed["user_opinion_source_revision"] == 4
    history = ws.read_practice_feedback(attempt["id"])["items"]
    assert [item["feedback_revision"] for item in history] == [4, 3, 2, 1]
    assert history[-1]["user_note"] == opinion.user_note


def test_old_feedback_without_provenance_preserves_only_the_last_known_opinion(feedback_workspace):
    ws, _, _, attempt, feedback = feedback_workspace
    opinion = feedback.model_copy(update={"user_verdict": "correct", "user_note": "Synthetic legacy opinion."})
    legacy = ws.review_practice_attempt(attempt["id"], 0, opinion)
    for field in ("user_opinion_source_revision", "user_opinion_updated", "withdraw_user_opinion"):
        legacy.pop(field)
    ws._put("practice_feedback", f"{attempt['id']}:1", legacy)
    carried = ws.review_practice_attempt(attempt["id"], 1, feedback)
    assert carried["user_verdict"] == "correct" and carried["user_opinion_source_revision"] == 1
    # An old release may already have cleared an opinion. Do not invent a historical repair.
    for field in ("user_opinion_source_revision", "user_opinion_updated", "withdraw_user_opinion"):
        carried.pop(field)
    carried.update(user_verdict="unreported", user_note="")
    ws._put("practice_feedback", f"{attempt['id']}:2", carried)
    untouched = ws.review_practice_attempt(attempt["id"], 2, feedback)
    assert untouched["user_verdict"] == "unreported" and untouched["user_opinion_source_revision"] is None


def test_old_question_feedback_uses_the_frozen_attempt_not_new_question(feedback_workspace):
    ws, record, saved, attempt, feedback = feedback_workspace
    record.stem = "Expand 9(x+1)"
    ws.save_record(record, saved["id"], 1)
    reviewed = ws.review_practice_attempt(attempt["id"], 0, feedback)
    assert reviewed["source_revision"] == 1
    assert ws.read_practice_history(saved["id"], include_details=True)["items"][0]["source_snapshot"]["prompt"] == "Expand 2(x+3)"
    assert ws.read_record(saved["id"])["revision"] == 2


@pytest.mark.parametrize("change", [
    {"feedback": " "}, {"next_step": " "}, {"agent_verdict": "mastered"},
    {"user_verdict": "correct", "user_note": ""}, {"user_note": "unexpected unreported verdict"},
    {"withdraw_user_opinion": True}, {"withdraw_user_opinion": True, "user_note": " "},
    {"withdraw_user_opinion": True, "user_verdict": "correct", "user_note": "Contradictory."},
    {"withdraw_user_opinion": "false"},
])
def test_feedback_contract_requires_explanation_and_real_user_opinion(feedback_workspace, change):
    _, _, _, _, feedback = feedback_workspace
    with pytest.raises(ValidationError):
        PracticeFeedback.model_validate({**feedback.model_dump(), **change})


def test_missing_legacy_snapshot_is_not_automatically_assessed(feedback_workspace):
    ws, _, _, attempt, feedback = feedback_workspace
    legacy = {key: value for key, value in attempt.items() if key not in {"source_snapshot", "feedback_revision"}}
    ws._put("attempt", attempt["id"], legacy)
    assert not ws.read_practice_feedback(attempt["id"])["snapshot_available"]
    with pytest.raises(DomainError, match="attempt_snapshot_missing"):
        ws.review_practice_attempt(attempt["id"], 0, feedback)
    assert not ws._all("practice_feedback")


def test_feedback_and_attempt_update_roll_back_together(feedback_workspace, monkeypatch):
    ws, _, _, attempt, feedback = feedback_workspace
    original_put = ws._put

    def fail_attempt_write(kind, identifier, value):
        if kind == "attempt":
            raise RuntimeError("simulated storage failure")
        return original_put(kind, identifier, value)

    monkeypatch.setattr(ws, "_put", fail_attempt_write)
    with pytest.raises(RuntimeError, match="simulated storage failure"):
        ws.review_practice_attempt(attempt["id"], 0, feedback)
    assert not ws._all("practice_feedback")
    assert ws._get("attempt", attempt["id"])["feedback_revision"] == 0


def test_feedback_pagination_preserves_all_revisions_under_size_limit(feedback_workspace):
    ws, _, saved, attempt, feedback = feedback_workspace
    long_feedback = feedback.model_copy(update={"feedback": "说" * 4000, "next_step": "做" * 2000})
    for revision in range(24):
        ws.review_practice_attempt(attempt["id"], revision, long_feedback)
    other = ws.record_practice(saved["id"], 1, None, "another answer", "original")
    ws.review_practice_attempt(other["id"], 0, feedback)
    first = ws.read_practice_feedback(attempt["id"], limit=20)
    assert len(first["items"]) < 20 and len(json.dumps(first, ensure_ascii=False)) <= 80000
    ws.review_practice_attempt(attempt["id"], 24, feedback)
    revisions = [item["feedback_revision"] for item in first["items"]]
    before = first["next_before_revision"]
    while before is not None:
        page = ws.read_practice_feedback(attempt["id"], before, 20)
        assert len(json.dumps(page, ensure_ascii=False)) <= 80000
        revisions.extend(item["feedback_revision"] for item in page["items"])
        before = page["next_before_revision"]
    assert revisions == list(range(24, 0, -1))
    assert ws.read_practice_feedback(attempt["id"])["current_feedback_revision"] == 25


def test_feedback_updates_do_not_reorder_attempt_history_or_duplicate_submission(feedback_workspace):
    ws, _, saved, attempt, feedback = feedback_workspace
    second = ws.record_practice(saved["id"], 1, None, "second", "original")
    ws.review_practice_attempt(attempt["id"], 0, feedback)
    ids = [item["id"] for item in ws.read_practice_history(saved["id"])["items"]]
    assert ids == [second["id"], attempt["id"]]
    retry = ws.record_practice(saved["id"], 1, None, "2x+3", "original", "unknown", "answer-1")
    assert retry["cached"] and retry["feedback_revision"] == 1
    assert ws.read_practice_history(saved["id"])["total"] == 2


async def test_two_real_mcp_sessions_cannot_overwrite_the_same_feedback_revision(feedback_workspace, tmp_path):
    ws, _, _, attempt, feedback = feedback_workspace
    params = StdioServerParameters(command=sys.executable, args=["-m", "study_errorbook.server"], env={
        "STUDY_DATA_DIR": str(ws.root), "STUDY_INPUT_ROOT": str(tmp_path),
        "STUDY_CONFIG_FILE": str(tmp_path / "absent.env"), "TEXTIN_APP_ID": "", "TEXTIN_SECRET_CODE": "",
    })
    async with AsyncExitStack() as stack:
        clients = []
        for _ in range(2):
            read, write = await stack.enter_async_context(stdio_client(params))
            client = await stack.enter_async_context(ClientSession(read, write))
            await client.initialize()
            clients.append(client)
        payload = {"attempt_id": attempt["id"], "expected_feedback_revision": 0, "feedback": feedback.model_dump()}
        results = await asyncio.gather(*(c.call_tool("review_practice_attempt", payload) for c in clients))
        assert sum(not r.isError for r in results) == 1
        rejected = next(r for r in results if r.isError)
        assert "feedback_conflict" in rejected.content[0].text
        history = await clients[1].call_tool("read_practice_feedback", {"attempt_id": attempt["id"]})
        assert not history.isError and history.structuredContent["current_feedback_revision"] == 1
        assert len(history.structuredContent["items"]) == 1
