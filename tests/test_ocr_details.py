"""Synthetic contract fixtures based on TextIn v1 docs; not live OCR evidence."""

import json

import pytest

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.fixture
def parsed_workspace(tmp_path):
    ws = Workspace(tmp_path / "state", None, TextIn("", ""))
    data = {
        "pages": [{"page_number": 2, "page_width": 900, "page_height": 1200, "angle": 90}],
        "elements": [
            {
                "element_id": "question_1",
                "page_number": 2,
                "text": "求值😀：" + "x+1 " * 2000,
                "coordinates": [0.1, 0.1, 0.8, 0.1, 0.8, 0.8, 0.1, 0.8],
                "metadata": {"is_continuation": True, "continuation_of": "question_0"},
                "objects": [
                    {"type": "formula", "text": f"x+{index}", "text_range": [index, index + 3]}
                    for index in range(20)
                ],
                "char_details": [
                    {
                        "index": 0,
                        "text": "求",
                        "recognition": {
                            "confidence": 0.65,
                            "candidates": [{"text": "球", "confidence": 0.3}],
                        },
                    }
                ],
                "table_structure": {"rows": 1, "cols": 1, "cells": [{"text": "答案"}]},
            }
        ],
    }
    result = ws.root / "document" / "parsed.json"
    result.parent.mkdir()
    result.write_text(json.dumps(data, ensure_ascii=False))
    ws._put(
        "job",
        "parsed",
        {
            "id": "parsed",
            "document_id": "document",
            "status": "partial",
            "result_path": str(result),
        },
    )
    try:
        yield ws, data, result
    finally:
        ws.close()


def test_summary_keeps_relationships_and_truncated_fields_are_recoverable(parsed_workspace):
    ws, data, _ = parsed_workspace
    summary = ws.read_document("parsed")["elements"][0]
    assert summary["text_truncated"] and summary["objects_truncated"]
    assert summary["metadata"]["continuation_of"] == "question_0"
    assert summary["objects"][0]["text_range"] == [0, 3]
    for section in (
        "text",
        "objects",
        "char_details",
        "table_structure",
        "metadata",
        "element",
        "page",
    ):
        parts, offset = [], 0
        while True:
            response = ws.read_element("parsed", 0, section, offset, 137)
            assert response["job_status"] == "partial" and response["present"]
            parts.append(response["content"])
            if response["next_offset"] is None:
                break
            assert response["next_offset"] > offset
            offset = response["next_offset"]
        recovered = "".join(parts)
        if section == "text":
            assert recovered == data["elements"][0]["text"]
        else:
            expected = (
                data["pages"]
                if section == "page"
                else (data["elements"][0] if section == "element" else data["elements"][0][section])
            )
            assert json.loads(recovered) == expected


@pytest.mark.parametrize("objects", [None, "malformed", [None, "wrong", {"text": "valid"}]])
def test_optional_objects_cannot_break_overview(parsed_workspace, objects):
    ws, data, result = parsed_workspace
    data["elements"][0]["objects"] = objects
    result.write_text(json.dumps(data))
    response = ws.read_document("parsed")
    assert len(response["elements"]) == 1
    assert response["next_offset"] is None
    complete = ws.read_element("parsed", 0, "objects")
    assert json.loads(complete["content"]) == objects


def test_oversized_fields_advance_cursor_and_remain_readable(parsed_workspace):
    ws, data, result = parsed_workspace
    element = data["elements"][0]
    element.update(element_id="i" * 30000, coordinates=[0] * 10000, text="\x00" * 10000)
    element["objects"][0]["coordinates"] = [0] * 10000
    data["elements"].append({"text": "next question"})
    result.write_text(json.dumps(data))
    overview = ws.read_document("parsed", limit=1)
    assert overview["next_offset"] == 1
    assert {"element_id", "coordinates", "text", "objects"} <= set(
        overview["elements"][0]["omitted_fields"]
    )
    assert len(json.dumps(overview, ensure_ascii=False)) < 20000
    assert ws.read_document("parsed", offset=1)["elements"][0]["text"] == "next question"
    response = ws.read_element("parsed", 0, "text", limit=8000)
    assert 0 < response["next_offset"] < 8000
    assert len(json.dumps(response, ensure_ascii=False)) < 18000


def test_missing_details_and_invalid_requests_are_distinct(parsed_workspace):
    ws, data, result = parsed_workspace
    del data["elements"][0]["char_details"]
    result.write_text(json.dumps(data))
    absent = ws.read_element("parsed", 0, "char_details")
    assert absent["present"] is False and absent["content"] == "null"
    for index, section, offset, limit, code in [
        (-1, "text", 0, 20, "element_out_of_range"),
        (2, "text", 0, 20, "element_out_of_range"),
        (0, "../../private", 0, 20, "invalid_section"),
        (0, "text", -1, 20, "pagination"),
        (0, "text", 0, 8001, "pagination"),
    ]:
        with pytest.raises(DomainError, match=code):
            ws.read_element("parsed", index, section, offset, limit)


def test_review_hints_locate_lowest_scores_without_changing_raw_data(parsed_workspace):
    ws, data, result = parsed_workspace
    element = data["elements"][0]
    element["char_details"] = [
        {"index": 999 - i, "text": text, "recognition": {"confidence": score}}
        for i, (text, score) in enumerate([
            ("x", 0.9), ("-", 0.15), ("P", 0.88), ("✓", 0.55),
            ("g", 0.68), ("2", 0.67), ("t", 0.79), ("x", 0.15),
        ])
    ]
    element["objects"].append({"type": "handwriting"})
    result.write_text(json.dumps(data))
    original = result.read_bytes()
    rows = ws.db.execute("SELECT * FROM objects").fetchall()
    response = ws.read_document("parsed")
    hints = response["elements"][0]["review_hints"]
    assert hints["object_counts"] == {"formula": 20, "handwriting": 1}
    assert hints["valid_confidence_count"] == 8
    assert hints["missing_confidence_count"] == hints["invalid_confidence_count"] == 0
    assert hints["lowest_confidence_truncated"] is True
    assert [c["detail_index"] for c in hints["lowest_confidence"]] == [1, 7, 3, 5, 4]
    assert [c["text"] for c in hints["lowest_confidence"]] == ["-", "x", "✓", "2", "g"]
    assert "read_element_evidence" in response["review_hint"]
    assert json.loads(ws.read_element("parsed", 0, "char_details")["content"]) == element["char_details"]
    assert result.read_bytes() == original
    assert ws.db.execute("SELECT * FROM objects").fetchall() == rows


@pytest.mark.parametrize("details,state", [(None, "invalid"), ({}, "invalid"), ("bad", "invalid"), ([], "empty")])
def test_unavailable_review_hints_are_not_zero_confidence(parsed_workspace, details, state):
    ws, data, result = parsed_workspace
    data["elements"][0]["char_details"] = details
    result.write_text(json.dumps(data))
    hints = ws.read_document("parsed")["elements"][0]["review_hints"]
    assert hints["char_details_state"] == state
    assert hints["valid_confidence_count"] == 0
    assert hints["lowest_confidence"] == []
    del data["elements"][0]["char_details"]
    result.write_text(json.dumps(data))
    assert ws.read_document("parsed")["elements"][0]["review_hints"]["char_details_state"] == "missing"


def test_invalid_scores_do_not_rank_or_crash_and_full_scores_do_not_approve(parsed_workspace):
    ws, data, result = parsed_workspace
    element = data["elements"][0]
    element["char_details"] = [
        {"text": "l", "recognition": {"confidence": score}}
        for score in [True, False, "0.2", -1, 2, float("nan"), float("inf"), [], {}, 1, 1.0, None]
    ] + [None, {"recognition": []}, {}, {"recognition": {}}]
    element["objects"] = [{"type": []}, None, {"type": "formula"}]
    result.write_text(json.dumps(data))
    response = ws.read_document("parsed")
    hints = response["elements"][0]["review_hints"]
    assert hints["valid_confidence_count"] == 2
    assert hints["invalid_confidence_count"] == 11
    assert hints["missing_confidence_count"] == 3
    assert hints["object_counts"]["formula"] == 1
    assert all(c["confidence"] == 1 for c in hints["lowest_confidence"])
    assert "分数为1" in response["review_hint"]
    assert not {"correct", "approved", "accuracy", "needs_review"} & hints.keys()
    json.dumps(response, allow_nan=False)


def test_review_hints_keep_cursor_moving_with_maximum_existing_fields(parsed_workspace):
    ws, data, result = parsed_workspace
    element = data["elements"][0]
    element.update(element_id="i" * 290, type="t" * 90, text="x" * 5000,
                   coordinates="c" * 290, metadata="m" * 1990)
    element["objects"] = [{"text": "o" * 800} for _ in range(6)]
    element["char_details"] = [
        {"text": "\x00" * 8000, "index": "i" * 8000, "recognition": {"confidence": 0.5}}
        for _ in range(1000)
    ]
    data["elements"].append({"text": "next"})
    result.write_text(json.dumps(data))
    overview = ws.read_document("parsed", limit=1)
    assert overview["next_offset"] == 1
    hints = overview["elements"][0]["review_hints"]
    assert len(hints["lowest_confidence"]) == 5
    assert hints["valid_confidence_count"] == 1000
    assert all(c["text_truncated"] and len(c["text"]) == 16 for c in hints["lowest_confidence"])
    assert len(json.dumps(overview, ensure_ascii=False)) < 20000
    assert ws.read_document("parsed", offset=1)["elements"][0]["text"] == "next"


def test_image_text_does_not_replace_visual_review_or_mutate_cached_results(parsed_workspace):
    ws, data, result = parsed_workspace
    # Mirrors the observed failure shape, without redistributing a research image:
    # both image blocks have the same extracted letters; only the image shows the answer.
    data['elements'] = [
        {'type': 'Image', 'text': 'DCAB', 'element_id': 'original', 'page_number': 2},
        {'type': 'Image', 'text': 'DCAB', 'element_id': 'response', 'page_number': 2,
         'char_details': [{'text': 'D', 'recognition': {'confidence': 1}}]},
        {'type': 'Image', 'text': '', 'element_id': 'drawing', 'page_number': 2},
        {'type': 'NarrativeText', 'text': 'Label the intersection E.'},
    ]
    result.write_text(json.dumps(data))
    original = result.read_bytes()
    rows = ws.db.execute('SELECT * FROM objects').fetchall()
    overview = ws.read_document('parsed')
    assert [row['index'] for row in overview['elements']] == [0, 1, 2, 3]
    for row in overview['elements'][:3]:
        hint = row['review_hints']['visual_review']
        assert hint['next_tool'] == 'read_element_evidence'
        assert hint['reason'] == 'image_element'
        assert '位置' in hint['note']
        assert not {'correct', 'unanswered', 'author', 'approved'} & hint.keys()
    assert overview['elements'][3]['review_hints']['visual_review'] is None
    assert result.read_bytes() == original
    assert ws.db.execute('SELECT * FROM objects').fetchall() == rows
    assert json.loads(ws.read_element('parsed', 1, 'char_details')['content']) == data['elements'][1]['char_details']


def test_image_review_hints_remain_bounded_with_large_confidence_details(parsed_workspace):
    ws, data, result = parsed_workspace
    data['elements'][0]['type'] = 'Image'
    data['elements'][0]['char_details'] = [
        {'text': '\x00' * 10000, 'recognition': {'confidence': 1}} for _ in range(1000)
    ]
    data['elements'].append({'type': 'NarrativeText', 'text': 'next'})
    result.write_text(json.dumps(data))
    overview = ws.read_document('parsed', limit=1)
    assert overview['next_offset'] == 1
    hints = overview['elements'][0]['review_hints']
    assert hints['visual_review']['reason'] == 'image_element'
    assert len(hints['lowest_confidence']) == 5
    assert len(json.dumps(hints, ensure_ascii=False)) <= 2000
    assert len(json.dumps(overview, ensure_ascii=False)) < 20000
    assert ws.read_document('parsed', offset=1)['elements'][0]['text'] == 'next'
