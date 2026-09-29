import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def page_workspace(tmp_path):
    ws = Workspace(tmp_path / "state", None, TextIn("", ""))
    path = ws.root / "synthetic-document" / "synthetic.json"
    path.parent.mkdir()
    ws._put("job", "synthetic", {"id": "synthetic", "document_id": "synthetic-document",
                               "status": "partial", "requested_pages": 3, "result_path": str(path)})
    try:
        yield ws, path
    finally:
        ws.close()


def test_failed_page_without_elements_is_visible_in_every_overview(page_workspace):
    ws, path = page_workspace
    data = {"success_count": 1, "pages": [{"page_number": 1, "status": "Success"},
                                        {"page_number": 2, "status": "Failed"}],
            "elements": [{"page_number": 1, "text": "synthetic"}]}
    path.write_text(json.dumps(data))
    before = ws.db.execute("SELECT * FROM objects").fetchall(), path.read_bytes()
    summary = ws.read_document("synthetic")["page_summary"]
    assert summary["requested_pages"] == 3 and summary["reported_success_count"] == 1
    assert summary["missing_metadata_pages"] == [3]
    assert summary["non_success_pages"] == [2]
    assert summary["pages_without_elements"] == [2, 3]
    assert summary["pages"] == [
        {"page_number": 1, "reported_status": "Success", "metadata_entries": 1, "element_count": 1},
        {"page_number": 2, "reported_status": "Failed", "metadata_entries": 1, "element_count": 0},
        {"page_number": 3, "reported_status": None, "metadata_entries": 0, "element_count": 0},
    ]
    assert ws.read_document("synthetic", offset=1)["page_summary"] == summary
    assert (ws.db.execute("SELECT * FROM objects").fetchall(), path.read_bytes()) == before


def test_empty_result_still_shows_all_requested_pages(page_workspace):
    ws, path = page_workspace
    path.write_text(json.dumps({"elements": [], "success_count": 3, "pages": [
        {"page_number": number, "status": "Success", "page_image_url": "PRIVATE-URL"}
        for number in range(1, 4)]}))
    overview = ws.read_document("synthetic")
    summary = overview["page_summary"]
    assert overview["elements"] == [] and overview["next_offset"] is None
    assert summary["count_matches_requested"] is True
    assert summary["pages_without_elements"] == [1, 2, 3]
    assert summary["non_success_pages"] == []
    assert "PRIVATE-URL" not in json.dumps(summary)
    assert "不代表空白或漏答" in summary["note"]


@pytest.mark.parametrize("pages,state", [(None, "missing"), ({}, "invalid"), ([], "available")])
def test_missing_or_invalid_metadata_is_not_guessed_from_elements(page_workspace, pages, state):
    ws, path = page_workspace
    path.write_text(json.dumps({"pages": pages, "elements": [{"page_number": 2}], "success_count": True}))
    summary = ws.read_document("synthetic")["page_summary"]
    assert summary["metadata_state"] == state
    assert summary["reported_success_count"] is None
    assert summary["count_matches_requested"] is None
    assert summary["missing_metadata_pages"] == [1, 2, 3]
    assert summary["pages_without_elements"] == [1, 3]


def test_duplicate_unknown_and_out_of_range_pages_are_bounded(page_workspace):
    ws, path = page_workspace
    path.write_text(json.dumps({"success_count": 3, "pages": [
        {"page_number": 1, "status": "Success"}, {"page_number": 1, "status": "Failed"},
        {"page_number": 2, "status": "x" * 10000}, {"page_number": 3},
        {"page_number": True}, {"page_number": 4}, "bad"],
        "elements": [{"page_number": True}, {"page_number": 4}, {}, {"page_number": 1}]}))
    summary = ws.read_document("synthetic")["page_summary"]
    assert summary["duplicate_metadata_pages"] == [1]
    assert summary["unknown_status_pages"] == [1, 2, 3]
    assert summary["invalid_metadata_entries"] == 3
    assert summary["unassigned_elements"] == 3
    assert summary["pages"][0]["reported_status"] is None
    assert len(json.dumps(summary)) < 4000


def test_legacy_job_without_requested_count_does_not_infer_whole_document(page_workspace):
    ws, path = page_workspace
    job = ws._get("job", "synthetic")
    job.pop("requested_pages")
    ws._put("job", "synthetic", job)
    path.write_text(json.dumps({"elements": [{"page_number": 2}], "pages": [{"page_number": 2, "status": "Success"}]}))
    summary = ws.read_document("synthetic")["page_summary"]
    assert summary["requested_pages"] is None and summary["count_matches_requested"] is None
    assert [page["page_number"] for page in summary["pages"]] == [2]
    assert summary["missing_metadata_pages"] == []


async def test_page_summary_is_visible_over_stdio_with_zero_elements(page_workspace):
    ws, path = page_workspace
    path.write_text(json.dumps({"elements": [], "success_count": 0,
                               "pages": [{"page_number": 2, "status": "Failed"}]}))
    params = StdioServerParameters(command=sys.executable, args=["-m", "study_errorbook.server"],
                                  env={"STUDY_DATA_DIR": str(ws.root), "STUDY_INPUT_ROOT": str(ws.root),
                                       "STUDY_CONFIG_FILE": str(ws.root / "absent.env"),
                                       "TEXTIN_APP_ID": "", "TEXTIN_SECRET_CODE": ""})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("read_document", {"job_id": "synthetic"})
            assert not result.isError
            assert result.structuredContent["page_summary"]["non_success_pages"] == [2]
            assert result.structuredContent["page_summary"]["missing_metadata_pages"] == [1, 3]
            assert result.structuredContent["total"] == 0
            assert json.loads(result.content[0].text) == result.structuredContent
