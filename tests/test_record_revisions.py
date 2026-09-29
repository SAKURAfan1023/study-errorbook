"""Revision reads preserve correction lineage without mixing in current state."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from test_workflow import draft
from test_workflow import sample as _sample
from test_workflow import workspace as _workspace

from study_errorbook.models import DomainError, Exercise, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

sample = _sample
workspace = _workspace


@pytest.mark.parametrize('verdict', ['correct', 'partial', 'uncertain', 'unanswered'])
def test_revised_agent_judgment_retires_old_analysis_and_practice(workspace, sample, verdict):
    ws = workspace
    _, record, _ = draft(ws, sample)
    check = ws.check_exercise('3*(x+4)', '3*x+12')
    record.exercises = [Exercise(prompt='OLD REMEDIAL PROMPT', answer='3*x+12',
                                 targets_error='old diagnosis', check_id=check['id'])]
    original = ws.save_record(record)
    reviewed = ws.review_record(original['id'], 1, 'include', 'Synthetic initial review')
    attempt = ws.record_practice(original['id'], 2, 0, '3*x+12')
    record.agent_verdict = verdict
    revised = ws.save_record(record, original['id'], 2)
    assert revised['record']['analysis'] is None
    assert revised['record']['exercises'] == [] and revised['exercise_checks'] == []
    assert revised['record']['agent_verdict'] == verdict
    assert revised['record']['teacher_verdict'] == reviewed['record']['teacher_verdict']
    assert revised['record']['student_answer'] == reviewed['record']['student_answer']
    assert revised['review_status'] == 'needs_review' and revised['warning']
    old = ws.read_record(original['id'], 2)
    assert all(old[key] == value for key, value in reviewed.items())
    history = ws.read_practice_history(original['id'], include_details=True)
    assert history['items'][0]['id'] == attempt['id']
    assert history['items'][0]['source_snapshot']['prompt'] == 'OLD REMEDIAL PROMPT'
    with pytest.raises(DomainError, match='needs_review'):
        ws.read_practice_prompt(original['id'], 3, 'original')
    output = ws.export_errorbook([original['id']], include_drafts=True)
    assert 'OLD REMEDIAL PROMPT' not in (Path(output['folder']) / 'practice.html').read_text()
    fresh = StudyRecord.model_validate(revised['record'])
    fresh.analysis = record.analysis.model_copy(update={'explanation': 'New assessment under the revised verdict.'})
    saved = ws.save_record(fresh, original['id'], 3)
    assert saved['record']['analysis']['explanation'] == fresh.analysis.explanation


def test_corrected_prompt_and_old_attempt_keep_their_own_versions(workspace, sample):
    ws = workspace
    _, record, _ = draft(ws, sample)
    record.stem = "Synthetic OCR error: 2(x-3)"
    record.exercises = [Exercise(prompt="OLD GENERATED PROMPT", answer="OLD ANSWER", targets_error="old interpretation")]
    original = ws.save_record(record)
    identifier = original["id"]
    reviewed = ws.review_record(identifier, 1, "include", "Synthetic initial review")
    attempt = ws.record_practice(identifier, 2, None, "old response", "original")
    record.stem = "Image-checked correction: 2(x+3)"
    corrected = ws.save_record(record, identifier, 2)
    assert corrected["record"]["analysis"] is None and corrected["record"]["exercises"] == []
    with pytest.raises(DomainError, match="needs_review"):
        ws.read_practice_prompt(identifier, 3, "original")
    ws.review_record(identifier, 3, "include", "Synthetic corrected transcription review")
    prompt, _ = ws.read_practice_prompt(identifier, 4, "original")
    assert prompt["prompt"] == record.stem
    old = ws.read_record(identifier, 2)
    for key, value in reviewed.items():
        assert old[key] == value
    assert old["revision"] == 2 and old["current_revision"] == 4
    assert old["revision_view"] == "saved_snapshot"
    assert ws.read_record(identifier, 1)["record"]["stem"] == original["record"]["stem"]
    history = ws.read_practice_history(identifier, include_details=True)
    assert history["items"][0]["id"] == attempt["id"]
    assert history["items"][0]["source_snapshot"]["prompt"] == original["record"]["stem"]
    with pytest.raises(DomainError, match="revision_conflict"):
        ws.save_record(record, identifier, old["revision"])


def test_explicit_snapshot_has_no_live_derivations_and_survives_restart(workspace, sample):
    ws = workspace
    _, record, _ = draft(ws, sample)
    saved = ws.save_record(record)
    ws.review_record(saved["id"], 1, "exclude", "Synthetic excluded revision")
    rows = ws.db.execute("SELECT * FROM objects").fetchall()
    with patch.object(ws, "_clean_copy_validation", side_effect=AssertionError("live image read")), patch.object(ws, "read_practice_history", side_effect=AssertionError("live attempts")), patch.object(ws, "read_review_plan", side_effect=AssertionError("live plan")):
        past = ws.read_record(saved["id"], 1)
        current_snapshot = ws.read_record(saved["id"], 2)
    assert current_snapshot["review_status"] == "exclude"
    for response in (past, current_snapshot):
        assert not {"clean_copy_validation", "exercise_validation", "recent_practice_attempts", "review_plan"} & response.keys()
    assert ws.db.execute("SELECT * FROM objects").fetchall() == rows
    past["record"]["stem"] = "local mutation"
    with_peer = Workspace(ws.root, ws.input_root, TextIn("", ""))
    try:
        assert with_peer.read_record(saved["id"], 1)["record"]["stem"] == record.stem
        with_peer.review_record(saved["id"], 2, "uncertain", "Synthetic peer revision")
        assert ws.read_record(saved["id"], 2)["review_status"] == "exclude"
        current = ws.read_record(saved["id"])
        assert current["revision_view"] == "current" and current["current_revision"] == 3
        assert "clean_copy_validation" in current
    finally:
        with_peer.close()
    ws.close()
    reopened = Workspace(ws.root, ws.input_root, TextIn("", ""))
    try:
        assert reopened.read_record(saved["id"], 1)["record"]["stem"] == record.stem
        assert reopened.read_record(saved["id"], 2)["review_status"] == "exclude"
    finally:
        reopened.close()


@pytest.mark.parametrize("revision", [0, -1, True, False, 1.0, "1", [], {}])
def test_revision_selector_is_strict(workspace, sample, revision):
    _, record, _ = draft(workspace, sample)
    saved = workspace.save_record(record)
    with pytest.raises(DomainError, match="invalid_revision"):
        workspace.read_record(saved["id"], revision)


def test_absent_revision_never_falls_back_or_crosses_record(workspace, sample):
    _, record, _ = draft(workspace, sample)
    first = workspace.save_record(record)
    second = workspace.save_record(record.model_copy(update={"stem": "OTHER QUESTION"}))
    workspace.review_record(second["id"], 1, "include", "Synthetic other record")
    with pytest.raises(DomainError, match="revision_not_found"):
        workspace.read_record(first["id"], 2)
    workspace.review_record(first["id"], 1, "include", "Synthetic first record")
    workspace.db.execute("DELETE FROM objects WHERE kind='history' AND id=?", (first["id"] + ":1",))
    workspace.db.commit()
    with pytest.raises(DomainError, match="revision_not_found"):
        workspace.read_record(first["id"], 1)
    assert "OTHER QUESTION" not in json.dumps(workspace.read_record(first["id"]))
