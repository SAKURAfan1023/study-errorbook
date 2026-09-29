import asyncio
import json
import sys
from contextlib import AsyncExitStack

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from study_errorbook.models import DomainError, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def practice_workspace(tmp_path):
    paper = tmp_path / "paper.png"
    Image.new("RGB", (100, 100), "white").save(paper)
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    doc = ws.import_document(str(paper))
    evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    record = StudyRecord(document_id=doc["id"], question_label="Synthetic history fixture",
                         stem="Expand 2(x+3)", student_answer="2x+3 (old incorrect answer)",
                         evidence_ids=[evidence["id"]], exercises=[{
                             "prompt": "Expand 3(x+4)", "answer": "3*x+12", "targets_error": "distribution",
                         }])
    saved = ws.save_record(record)
    yield ws, record, saved["id"]
    ws.close()


def test_original_attempt_works_without_variants_and_does_not_infer_grading(practice_workspace):
    ws, record, identifier = practice_workspace
    record.exercises = []
    ws.save_record(record, identifier, 1)
    attempt = ws.record_practice(identifier, 2, None, "2x+6", "original", "hinted", "original-1")
    assert attempt["source_snapshot"]["prompt"] == record.stem
    assert attempt["source_snapshot"]["reference_answer"] is None
    assert record.student_answer not in json.dumps(attempt["source_snapshot"])
    assert attempt["grading_status"] == "needs_review" and attempt["assistance"] == "hinted"
    assert ws.read_record(identifier)["practice_attempts_total"] == 1


def test_variant_snapshot_survives_question_rewrite(practice_workspace):
    ws, record, identifier = practice_workspace
    attempt = ws.record_practice(identifier, 1, 0, "3x+12")
    record.stem = "Changed original stem"
    ws.save_record(record, identifier, 1)
    history = ws.read_practice_history(identifier, include_details=True)["items"][0]
    assert history["source_snapshot"] == attempt["source_snapshot"]
    assert history["source_snapshot"]["prompt"] == "Expand 3(x+4)"
    assert history["source_snapshot"]["reference_answer"] == "3*x+12"
    assert history["source_snapshot"]["validation"]["status"] == "needs_review"
    assert history["source_revision"] == 1 and history["assistance"] == "unknown"


def test_submission_key_deduplicates_even_after_question_changes(practice_workspace):
    ws, record, identifier = practice_workspace
    first = ws.record_practice(identifier, 1, 0, "3x+12", attempt_key="response-1")
    ws.save_record(record, identifier, 1)
    retry = ws.record_practice(identifier, 1, 0, "3x+12", attempt_key="response-1")
    assert retry["id"] == first["id"] and retry["cached"]
    with pytest.raises(DomainError, match="attempt_key_conflict"):
        ws.record_practice(identifier, 1, 0, "different", attempt_key="response-1")
    assert ws.read_practice_history(identifier)["total"] == 1
    with pytest.raises(DomainError, match="revision_conflict"):
        ws.record_practice(identifier, 1, 0, "3x+12", attempt_key="response-2")


@pytest.mark.parametrize("kind,index,help_used,response,key", [
    ("original", 0, "unknown", "answer", None),
    ("exercise", None, "unknown", "answer", None),
    ("exercise", True, "unknown", "answer", None),
    ("exercise", -1, "unknown", "answer", None),
    ("exercise", 1, "unknown", "answer", None),
    ("exercise", 0, "mastered", "answer", None),
    ("exercise", 0, "unknown", " ", None),
    ("exercise", 0, "unknown", "answer", " "),
])
def test_invalid_attempt_does_not_write(practice_workspace, kind, index, help_used, response, key):
    ws, _, identifier = practice_workspace
    with pytest.raises(DomainError):
        ws.record_practice(identifier, 1, index, response, kind, help_used, key)
    assert ws.read_practice_history(identifier)["total"] == 0


def test_cursor_reaches_old_attempts_without_duplicates_when_new_answers_arrive(practice_workspace):
    ws, record, identifier = practice_workspace
    ids = [ws.record_practice(identifier, 1, 0, str(i))["id"] for i in range(27)]
    other = ws.save_record(record)
    ws.record_practice(other["id"], 1, 0, "other question")
    first = ws.read_practice_history(identifier)
    assert first["total"] == 27 and len(first["items"]) == 20 and first["next_before"]
    new = ws.record_practice(identifier, 1, 0, "new arrival")
    second = ws.read_practice_history(identifier, before=first["next_before"])
    collected = [i["id"] for i in first["items"] + second["items"]]
    assert collected == list(reversed(ids)) and second["next_before"] is None
    assert new["id"] not in collected and second["total"] == 28


def test_detailed_pages_are_bounded_but_lossless(practice_workspace):
    ws, record, identifier = practice_workspace
    record.stem = "题" * 12000
    ws.save_record(record, identifier, 1)
    for i in range(8):
        ws.record_practice(identifier, 2, None, str(i) + "答" * 3999, "original")
    before, items = None, []
    while True:
        page = ws.read_practice_history(identifier, before, include_details=True)
        assert len(json.dumps(page, ensure_ascii=False)) <= 80000
        assert page["items"]
        items.extend(page["items"])
        before = page["next_before"]
        if before is None:
            break
    assert len(items) == 8 and len({item["id"] for item in items}) == 8
    assert all(len(item["response"]) == 4000 and len(item["source_snapshot"]["prompt"]) == 12000 for item in items)
    overview = ws.read_record(identifier)
    assert all("source_snapshot" not in item and "response" not in item for item in overview["recent_practice_attempts"])
    assert all(item["response_truncated"] for item in overview["recent_practice_attempts"])


def test_legacy_history_is_not_backfilled_from_current_question(practice_workspace):
    ws, _, identifier = practice_workspace
    ws._put("attempt", "attempt_legacy", {
        "id": "attempt_legacy", "record_id": identifier, "source_revision": 1,
        "exercise_index": 0, "response": "legacy", "created_at": 0, "grading_status": "needs_review",
    })
    item = ws.read_practice_history(identifier, include_details=True)["items"][0]
    assert not item["snapshot_available"] and item["assistance"] == "unknown"
    assert item["practice_kind"] == "exercise" and "source_snapshot" not in item


async def test_real_mcp_two_sessions_deduplicate_and_retrieve_original_snapshot(practice_workspace, tmp_path):
    ws, _, identifier = practice_workspace
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
        payload = {"record_id": identifier, "expected_revision": 1, "practice_kind": "original",
                   "response": "2x+6", "assistance": "independent", "attempt_key": "same-logical-answer"}
        results = await asyncio.gather(*(c.call_tool("record_practice", payload) for c in clients))
        assert all(not r.isError for r in results)
        assert results[0].structuredContent["id"] == results[1].structuredContent["id"]
        assert sum(bool(r.structuredContent.get("cached")) for r in results) == 1
        history = await clients[1].call_tool("read_practice_history", {"record_id": identifier, "include_details": True})
        assert not history.isError and history.structuredContent["total"] == 1
        assert history.structuredContent["items"][0]["source_snapshot"]["prompt"] == "Expand 2(x+3)"
