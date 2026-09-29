import asyncio
import json
import sys
from contextlib import AsyncExitStack
from datetime import datetime

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from pydantic import ValidationError

from study_errorbook.models import DomainError, Exercise, Region, ReviewPlanChange, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def planned_workspace(tmp_path):
    paper = tmp_path / "synthetic.png"
    Image.new("RGB", (80, 80), "white").save(paper)
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    doc = ws.import_document(str(paper))
    evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    check = ws.check_exercise("3*(x+4)", "3*x+12")
    record = StudyRecord(document_id=doc["id"], question_label="SECRET LABEL: answer 2x+6",
                         subject="SECRET SUBJECT: distribution", stem="2(x+3)",
                         student_answer="SECRET OLD ANSWER", evidence_ids=[evidence["id"]],
                         exercises=[Exercise(prompt="3(x+4)", answer="3*x+12", targets_error="distribution", check_id=check["id"])])
    draft = ws.save_record(record)
    included = ws.review_record(draft["id"], 1, "include", "Synthetic inclusion.")
    change = ReviewPlanChange(action="schedule", due_at="2030-01-02T09:00:00+08:00", user_note="Synthetic chosen date.")
    yield ws, record, included, change
    ws.close()


def test_due_queue_uses_timezones_and_does_not_expose_answers(planned_workspace):
    ws, _, record, change = planned_workspace
    assert ws.read_review_plan(record["id"])["current_plan_revision"] == 0
    plan = ws.update_review_plan(record["id"], 2, 0, change)
    assert plan["plan_revision"] == 1 and plan["source_revision"] == 2
    assert ws.read_review_queue(as_of=datetime.fromisoformat("2030-01-02T00:59:59+00:00"))["total"] == 0
    queue = ws.read_review_queue(as_of=datetime.fromisoformat("2030-01-02T01:00:00+00:00"))
    assert queue["total"] == 1 and queue["items"][0]["is_due"]
    assert "SECRET" not in json.dumps(queue) and "3*x+12" not in json.dumps(queue)
    assert "user_note" not in queue["items"][0] and "stem" not in queue["items"][0]
    assert "question_label" not in queue["items"][0] and "subject" not in queue["items"][0]
    assert ws.read_review_queue("scheduled")["total"] == 1


@pytest.mark.parametrize("kind,index,label", [("original", None, "原题重练"), ("exercise", 0, "变式练习 1")])
def test_queue_labels_are_opt_in_and_neutral_queue_can_start_practice(planned_workspace, kind, index, label):
    ws, _, record, change = planned_workspace
    ws.update_review_plan(record['id'], 2, 0, change.model_copy(update={'practice_kind': kind, 'exercise_index': index}))
    before = ws.db.execute('SELECT * FROM objects').fetchall()
    queue = ws.read_review_queue('scheduled')
    assert 'SECRET' not in json.dumps(queue)
    item = queue['items'][0]
    assert item['display_label'] == label
    prompt, images = ws.read_practice_prompt(item['record_id'], item['current_record_revision'],
                                            item['practice_kind'], item['exercise_index'])
    assert prompt['display_label'] == label and not images
    assert 'SECRET' not in json.dumps(prompt)
    labelled = ws.read_review_queue('scheduled', include_labels=True)
    managed = labelled['items'][0]
    assert managed['question_label'] == record['record']['question_label']
    assert managed['subject'] == record['record']['subject']
    assert {k: v for k, v in managed.items() if k not in {'question_label', 'subject'}} == item
    assert ws.db.execute('SELECT * FROM objects').fetchall() == before


def test_completion_requires_new_matching_attempt_and_does_not_claim_mastery(planned_workspace):
    ws, _, record, change = planned_workspace
    old = ws.record_practice(record["id"], 2, None, "old response", "original")
    ws.update_review_plan(record["id"], 2, 0, change)
    with pytest.raises(DomainError, match="attempt_plan_mismatch"):
        ws.update_review_plan(record["id"], 2, 1, ReviewPlanChange(action="complete", attempt_id=old["id"], user_note="Synthetic completion."))
    attempt = ws.record_practice(record["id"], 2, None, "new response", "original", "hinted")
    complete = ws.update_review_plan(record["id"], 2, 1, ReviewPlanChange(action="complete", attempt_id=attempt["id"], user_note="Synthetic completion."))
    assert complete["status"] == "completed" and complete["completion_assistance"] == "hinted"
    assert ws._get("attempt", attempt["id"])["grading_status"] == "needs_review"
    assert ws.read_review_queue("scheduled")["total"] == 0
    assert ws.read_review_queue("completed")["total"] == 1
    with pytest.raises(DomainError, match="plan_not_scheduled"):
        ws.update_review_plan(record["id"], 2, 2, ReviewPlanChange(action="cancel", user_note="Synthetic cancellation."))


def test_changed_question_becomes_outdated_and_can_be_cancelled(planned_workspace):
    ws, source, record, change = planned_workspace
    ws.update_review_plan(record["id"], 2, 0, change)
    source.stem = "9(x+1)"
    ws.save_record(source, record["id"], 2)
    assert ws.read_review_queue("scheduled")["total"] == 0
    assert ws.read_review_queue("outdated")["items"][0]["source_revision"] == 2
    attempt = ws.record_practice(record["id"], 3, None, "new version", "original")
    with pytest.raises(DomainError, match="plan_outdated"):
        ws.update_review_plan(record["id"], 3, 1, ReviewPlanChange(action="complete", attempt_id=attempt["id"], user_note="Synthetic."))
    cancelled = ws.update_review_plan(record["id"], 3, 1, ReviewPlanChange(action="cancel", user_note="Synthetic cancellation."))
    assert cancelled["status"] == "cancelled" and cancelled["source_revision"] == 2


def test_schedule_requires_inclusion_and_ready_exercise(planned_workspace):
    ws, source, record, change = planned_workspace
    draft = ws.save_record(source)
    with pytest.raises(DomainError, match="needs_review"):
        ws.update_review_plan(draft["id"], 1, 0, change)
    exercise = change.model_copy(update={"practice_kind": "exercise", "exercise_index": 0})
    ws.update_review_plan(record["id"], 2, 0, exercise)
    wrong = ws.record_practice(record["id"], 2, None, "original response", "original")
    with pytest.raises(DomainError, match="attempt_plan_mismatch"):
        ws.update_review_plan(record["id"], 2, 1, ReviewPlanChange(action="complete", attempt_id=wrong["id"], user_note="Synthetic."))
    matching = ws.record_practice(record["id"], 2, 0, "variant response", "exercise", "independent")
    result = ws.update_review_plan(record["id"], 2, 1, ReviewPlanChange(action="complete", attempt_id=matching["id"], user_note="Synthetic."))
    assert result["completion_assistance"] == "independent"
    with pytest.raises(DomainError, match="exercise_not_ready"):
        ws.update_review_plan(record["id"], 2, 2, exercise.model_copy(update={"exercise_index": 1}))
    source.exercises[0].check_id = None
    ws.save_record(source, record["id"], 2)
    ws.review_record(record["id"], 3, "include", "Synthetic inclusion without exercise approval.")
    with pytest.raises(DomainError, match="exercise_not_ready"):
        ws.update_review_plan(record["id"], 4, 2, exercise)


def test_plan_history_reschedule_conflicts_and_pagination(planned_workspace):
    ws, _, record, change = planned_workspace
    for revision in range(24):
        ws.update_review_plan(record["id"], 2, revision, change)
    with pytest.raises(DomainError, match="plan_conflict"):
        ws.update_review_plan(record["id"], 2, 0, change)
    with pytest.raises(DomainError, match="revision_conflict"):
        ws.update_review_plan(record["id"], 1, 24, change)
    first = ws.read_review_plan(record["id"], limit=20)
    assert len(first["items"]) == 20 and len(json.dumps(first, ensure_ascii=False)) < 80000
    ws.update_review_plan(record["id"], 2, 24, change)
    second = ws.read_review_plan(record["id"], first["next_before_revision"], 20)
    assert [p["plan_revision"] for p in first["items"] + second["items"]] == list(range(24, 0, -1))
    assert second["next_before_revision"] is None
    assert ws.read_review_queue("all")["total"] == 1


def test_queue_orders_and_pages_across_questions_and_keeps_only_latest(planned_workspace):
    ws, source, record, change = planned_workspace
    ws.update_review_plan(record["id"], 2, 0, change)
    second = ws.save_record(source)
    ws.review_record(second["id"], 1, "include", "Synthetic.")
    earlier = ReviewPlanChange(action="schedule", due_at="2030-01-01T00:00:00Z", user_note="Synthetic earlier date.")
    ws.update_review_plan(second["id"], 2, 0, earlier)
    ws.update_review_plan(second["id"], 2, 1, earlier)
    first_page = ws.read_review_queue("scheduled", limit=1)
    assert first_page["total"] == 2 and first_page["items"][0]["record_id"] == second["id"]
    next_page = ws.read_review_queue("scheduled", offset=first_page["next_offset"], limit=1)
    assert next_page["items"][0]["record_id"] == record["id"] and next_page["next_offset"] is None


def test_failed_write_rolls_back_plan_and_leaves_question_untouched(planned_workspace, monkeypatch):
    ws, _, record, change = planned_workspace
    original_put = ws._put

    def fail_after_write(kind, identifier, value):
        original_put(kind, identifier, value)
        raise RuntimeError("synthetic interrupted write")

    monkeypatch.setattr(ws, "_put", fail_after_write)
    with pytest.raises(RuntimeError, match="interrupted write"):
        ws.update_review_plan(record["id"], 2, 0, change)
    assert ws.read_review_plan(record["id"])["items"] == []
    assert ws.read_record(record["id"])["revision"] == 2


@pytest.mark.parametrize("params", [{"limit": 21}, {"offset": -1}, {"offset": True},
                                    {"status": "mastered"}, {"as_of": datetime(2030, 1, 1)}, {"include_labels": 1}])
def test_queue_rejects_ambiguous_or_unbounded_queries(planned_workspace, params):
    ws, *_ = planned_workspace
    with pytest.raises(DomainError):
        ws.read_review_queue(**params)


@pytest.mark.parametrize("update", [
    {"user_note": " "}, {"due_at": "2030-01-01T09:00:00"}, {"due_at": None},
    {"exercise_index": 0}, {"practice_kind": "exercise"}, {"exercise_index": True},
    {"attempt_id": "unexpected"}, {"action": "complete", "due_at": None},
    {"action": "cancel", "due_at": None, "attempt_id": "unexpected"},
])
def test_invalid_plan_inputs(planned_workspace, update):
    *_, change = planned_workspace
    with pytest.raises(ValidationError):
        ReviewPlanChange.model_validate({**change.model_dump(), **update})


async def test_two_mcp_processes_compete_for_one_plan_and_restart_reads_it(planned_workspace, tmp_path):
    ws, _, record, change = planned_workspace
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
        payload = {"record_id": record["id"], "expected_record_revision": 2,
                   "expected_plan_revision": 0, "change": change.model_dump(mode="json")}
        results = await asyncio.gather(*(c.call_tool("update_review_plan", payload) for c in clients))
        assert sum(not r.isError for r in results) == 1
        assert "plan_conflict" in next(r for r in results if r.isError).content[0].text
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool("read_review_queue", {"as_of": "2030-01-02T01:00:00Z"})
            assert not result.isError and result.structuredContent["total"] == 1
            history = await client.call_tool("read_review_plan", {"record_id": record["id"]})
            assert not history.isError and history.structuredContent["current_plan_revision"] == 1
