import json
import os
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.deletion import preview_document_deletion
from study_errorbook.models import (
    DomainError,
    Exercise,
    PracticeFeedback,
    Region,
    ReviewPlanChange,
    StudyRecord,
)
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def deletion_workspace(tmp_path):
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    documents = []
    records = []
    shared = ws.check_exercise("2*x", "x+x")
    unique = ws.check_exercise("3*x", "x+x+x")
    for index, color in enumerate(("white", "yellow")):
        path = tmp_path / f"synthetic-{index}.png"
        Image.new("RGB", (80, 80), color).save(path)
        doc = ws.import_document(str(path))
        documents.append(doc)
        evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
        record = StudyRecord(document_id=doc["id"], question_label=str(index), stem="SYNTHETIC PRIVATE CONTENT",
                             evidence_ids=[evidence["id"]], exercises=[Exercise(prompt="2x", answer="x+x", targets_error="synthetic", check_id=shared["id"])])
        if index == 0:
            record.exercises.append(Exercise(prompt="3x", answer="x+x+x", targets_error="synthetic", check_id=unique["id"]))
        saved = ws.save_record(record)
        if index == 0:
            record.exercises.pop()
            ws.save_record(record, saved["id"], 1)
            saved = ws.review_record(saved["id"], 2, "include", "Synthetic fixture inclusion.")
        records.append(saved)
    target = documents[0]
    child = ws.redact_document(target["id"], [Region(page=1, box=[0, 0, .2, .2])])
    grandchild = ws.redact_document(child["id"], [Region(page=1, box=[.3, .3, .4, .4])])
    child_ev, _ = ws.read_evidence(child["id"], Region(page=1, box=[0, 0, 1, 1]))
    ws.save_record(StudyRecord(document_id=child["id"], question_label="child", stem="fixture", evidence_ids=[child_ev["id"]]))
    attempt = ws.record_practice(records[0]["id"], 3, None, "SYNTHETIC RESPONSE", "original")
    ws.review_practice_attempt(attempt["id"], 0, PracticeFeedback(agent_verdict="uncertain", feedback="Synthetic.", next_step="Check source."))
    ws.update_review_plan(records[0]["id"], 3, 0, ReviewPlanChange(action="schedule", due_at="2030-01-01T09:00:00Z", user_note="Synthetic plan."))
    ws._put("job", "job_fixture", {"id": "job_fixture", "document_id": target["id"], "status": "pending"})
    ws._put("parse_history", "job_fixture:1", {"id": "job_fixture", "document_id": target["id"], "status": "failed"})
    mixed = ws.export_errorbook([r["id"] for r in records], include_drafts=True)
    unrelated = ws.export_errorbook([records[1]["id"]], include_drafts=True)
    yield ws, target, records, shared, unique, child, grandchild, mixed, unrelated
    ws.close()


def all_items(ws, doc_id):
    page = preview_document_deletion(ws.db, ws.root, doc_id, limit=50)
    items = page["items"][:]
    digest = page["inventory_digest"]
    while page["next_offset"] is not None:
        page = preview_document_deletion(ws.db, ws.root, doc_id, page["next_offset"], 50)
        assert page["inventory_digest"] == digest
        items.extend(page["items"])
    return page, items


def test_preview_covers_descendants_histories_shared_checks_and_mixed_export(deletion_workspace):
    ws, target, records, shared, unique, child, grandchild, mixed, unrelated = deletion_workspace
    rows_before = ws.db.execute("SELECT * FROM objects ORDER BY kind,id").fetchall()
    result, items = all_items(ws, target["id"])
    assert result["inventory_complete"] and not result["deleted"] and result["operation"] == "preview_only"
    assert result["counts"]["document"] == 3 and result["counts"]["record"] == 2
    for kind in ("history", "attempt", "practice_feedback", "review_plan", "job", "parse_history"):
        assert result["counts"][kind] >= 1
    removed = {(item["kind"], item["id"]) for item in items if item["category"] == "database"}
    assert ("document", child["id"]) in removed and ("document", grandchild["id"]) in removed
    assert ("check", unique["id"]) in removed and ("check", shared["id"]) not in removed
    assert {"category": "retained_shared_check", "id": shared["id"]} in items
    exports = [item for item in items if item["category"] == "export"]
    assert len(exports) == 1 and exports[0]["other_record_ids"] == [records[1]["id"]]
    assert exports[0]["path"] == Path(mixed["folder"]).relative_to(ws.root).as_posix()
    assert all(unrelated["export_id"] not in item.get("path", "") for item in items)
    assert "SYNTHETIC PRIVATE CONTENT" not in json.dumps(items)
    assert ws.db.execute("SELECT * FROM objects ORDER BY kind,id").fetchall() == rows_before
    assert Path(target["source"]).is_file() and Path(mixed["manifest_path"]).is_file()


def test_deleting_a_copy_does_not_include_its_parent_or_siblings(deletion_workspace):
    ws, target, _, _, _, child, grandchild, *_ = deletion_workspace
    result, items = all_items(ws, child["id"])
    ids = {item.get("id") for item in items if item["category"] == "database"}
    assert result["documents_total"] == 2 and target["id"] not in ids
    assert child["id"] in ids and grandchild["id"] in ids


def test_content_changes_invalidate_the_inventory_digest(deletion_workspace):
    ws, target, *_ = deletion_workspace
    before = preview_document_deletion(ws.db, ws.root, target["id"])["inventory_digest"]
    path = ws.root / target["id"] / "orphan-sensitive-cache.txt"
    path.write_text("Synthetic cache without a database row")
    after, items = all_items(ws, target["id"])
    assert after["inventory_digest"] != before
    assert any(item.get("path", "").endswith(path.name) for item in items)


@pytest.mark.parametrize("problem", ["symlink", "linked_directory", "fifo", "unknown_kind", "broken_export", "external_path"])
def test_unresolved_dependencies_are_not_claimed_complete(deletion_workspace, tmp_path, problem):
    ws, target, *_ = deletion_workspace
    if problem == "symlink":
        outside = tmp_path / "outside-secret.txt"
        outside.write_text("MUST NOT READ OR DELETE")
        (ws.root / target["id"] / "external.txt").symlink_to(outside)
    elif problem == "linked_directory":
        outside = tmp_path / "outside-folder"
        outside.mkdir()
        (outside / "secret.txt").write_text("MUST NOT READ OR DELETE")
        (ws.root / target["id"] / "linked").symlink_to(outside, target_is_directory=True)
    elif problem == "fifo":
        os.mkfifo(ws.root / target["id"] / "pipe")
    elif problem == "unknown_kind":
        ws._put("future_annotation", "unknown", {"document_id": target["id"]})
    elif problem == "broken_export":
        folder = ws.root / "exports" / "incomplete"
        folder.mkdir()
        (folder / "errorbook.html").write_text("Unclassified synthetic export")
    else:
        ws._put("job", "external", {"document_id": target["id"], "result_path": str(tmp_path / "outside.json")})
    result, items = all_items(ws, target["id"])
    assert not result["inventory_complete"] and result["inventory_digest"] is None
    assert result["unresolved_total"] >= 1
    assert any(item["category"] == "unresolved" for item in items)


def test_snapshot_recovers_dependency_after_record_rows_are_missing(deletion_workspace):
    ws, target, records, *_ = deletion_workspace
    ws.db.execute("DELETE FROM objects WHERE kind IN ('record','history') AND json_extract(body,'$.id')=?", (records[0]["id"],))
    ws.db.commit()
    result, items = all_items(ws, target["id"])
    assert result["counts"]["attempt"] == 1 and result["counts"]["practice_feedback"] == 1
    assert any(item["category"] == "export" for item in items)


def test_unreadable_nested_directory_cannot_silently_disappear(deletion_workspace):
    if os.geteuid() == 0:
        pytest.skip("Root bypasses directory access permissions")
    ws, target, *_ = deletion_workspace
    folder = ws.root / target["id"] / "locked"
    folder.mkdir()
    (folder / "private-cache.txt").write_text("Synthetic locked cache")
    folder.chmod(0)
    try:
        result = preview_document_deletion(ws.db, ws.root, target["id"])
        assert not result["inventory_complete"] and result["unresolved_total"] >= 1
    finally:
        folder.chmod(0o700)


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 0}, {"limit": 51}, {"limit": True}])
def test_preview_rejects_invalid_pages(deletion_workspace, params):
    ws, target, *_ = deletion_workspace
    with pytest.raises(DomainError, match="invalid_page"):
        preview_document_deletion(ws.db, ws.root, target["id"], **params)
