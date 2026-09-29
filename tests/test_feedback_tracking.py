"""CLI regressions use synthetic cases and replies, never real review assertions."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export_feedback_tracking.py"
FIELDS = ["id", "title", "category", "priority", "classification", "review_status",
          "vendor_status", "vendor_feedback", "fixed_version", "retest_status",
          "retest_run_id", "human_reviewer"]


@pytest.fixture
def tracking(tmp_path):
    bundle = tmp_path / "evidence"
    (bundle / "cases").mkdir(parents=True)
    rows = [{key: "" for key in FIELDS} for _ in range(2)]
    for index, row in enumerate(rows, 1):
        row.update(id=f"TI-{index:03}", title="合成案例", category="合成分类", priority="P1",
                   classification="合成观察", review_status="待人工复核", vendor_status="未发送",
                   retest_status="未复测")
        (bundle / "cases" / (row["id"] + ".md")).write_text("合成证据")
    (bundle / "cases.json").write_text(json.dumps(rows, ensure_ascii=False))
    (bundle / "index.html").write_text("合成证据页")
    for name in ["base.csv", "updated.csv"]:
        with (tmp_path / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    return tmp_path, bundle, rows


def _export(root, bundle, output="snapshot", base="base.csv"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence-bundle", str(bundle),
         "--base-csv", str(root / base), "--updated-csv", str(root / "updated.csv"),
         "--output", str(root / output), "--note", "合成测试；不是服务商或用户的真实意见"],
        capture_output=True, text=True, check=False,
    )


def _write_updates(root, rows, fields=FIELDS):
    with (root / "updated.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_reply_and_retest_remain_separate_and_next_snapshot_preserves_history(tracking):
    root, bundle, rows = tracking
    originals = {path: path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    rows[0].update(vendor_feedback='合成回复：声称已修复,请重测\n<script>alert("fixture")</script>',
                   vendor_status="合成已回复", fixed_version="fixture-v2")
    _write_updates(root, rows)
    first = _export(root, bundle)
    assert first.returncode == 0, first.stderr
    snapshot = root / "snapshot"
    saved = json.loads((snapshot / "跟踪表.json").read_text())
    assert saved[0]["retest_status"] == "未复测"
    assert saved[0]["human_reviewer"] == "" and saved[0]["review_status"] == "待人工复核"
    assert saved == rows
    audit = json.loads((snapshot / "变更记录.json").read_text())
    assert audit["changed_cases"] == 1 and len(audit["changes"]) == 3
    page = (snapshot / "index.html").read_text()
    assert '<script>alert(' not in page and '&lt;script&gt;' in page
    assert 'href="../evidence/cases/TI-001.md"' in page
    assert all(path.read_bytes() == content for path, content in originals.items())
    snapshot_bytes = {p.name: p.read_bytes() for p in snapshot.iterdir()}
    rows[0].update(retest_status="合成复测仍失败", retest_run_id="fixture-run:002")
    _write_updates(root, rows)
    second = _export(root, bundle, output="second", base="snapshot/跟踪表.csv")
    assert second.returncode == 0, second.stderr
    audit = json.loads((root / "second/变更记录.json").read_text())
    assert {change["field"] for change in audit["changes"]} == {"retest_status", "retest_run_id"}
    assert all((snapshot / name).read_bytes() == content for name, content in snapshot_bytes.items())
    third = _export(root, bundle)
    assert third.returncode != 0 and "已存在" in third.stderr
    assert all((snapshot / name).read_bytes() == content for name, content in snapshot_bytes.items())


@pytest.mark.parametrize("variant", ["missing", "extra", "duplicate", "changed_title", "changed_priority",
                                     "missing_field", "extra_cell", "duplicate_header", "traversal_id"])
def test_invalid_import_leaves_evidence_unchanged_and_no_snapshot(tracking, variant):
    root, bundle, rows = tracking
    original = (bundle / "cases.json").read_bytes()
    if variant == "missing":
        rows.pop()
    elif variant == "extra":
        rows.append({**rows[0], "id": "TI-003"})
    elif variant == "duplicate":
        rows[1]["id"] = rows[0]["id"]
    elif variant == "changed_title":
        rows[0]["title"] = "改变证据说明"
    elif variant == "changed_priority":
        rows[0]["priority"] = "P2"
    elif variant == "traversal_id":
        rows[0]["id"] = "../../escape"
    _write_updates(root, rows)
    if variant == "missing_field":
        text = (root / "updated.csv").read_text(encoding="utf-8-sig")
        (root / "updated.csv").write_text(text.replace("human_reviewer", "unknown"))
    elif variant == "extra_cell":
        with (root / "updated.csv").open("a") as stream:
            stream.write(",".join(["extra"] * (len(FIELDS) + 1)) + "\n")
    elif variant == "duplicate_header":
        text = (root / "updated.csv").read_text(encoding="utf-8-sig")
        (root / "updated.csv").write_text(text.replace("human_reviewer", "vendor_status"))
    result = _export(root, bundle)
    assert result.returncode != 0
    assert not (root / "snapshot").exists()
    assert (bundle / "cases.json").read_bytes() == original


def test_initial_snapshot_has_no_invented_update_and_rejects_output_in_evidence(tracking):
    root, bundle, _ = tracking
    result = _export(root, bundle)
    assert result.returncode == 0, result.stderr
    audit = json.loads((root / "snapshot/变更记录.json").read_text())
    assert audit["changes"] == [] and audit["vendor_status_counts"] == {"未发送": 2}
    rejected = _export(root, bundle, output="evidence/snapshot")
    assert rejected.returncode != 0 and not (bundle / "snapshot").exists()
