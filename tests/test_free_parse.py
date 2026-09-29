import asyncio
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


async def test_captured_live_response_survives_parse_and_detail_reading(tmp_path):
    captured = json.loads(
        (Path(__file__).parent / "fixtures/textin-free-synthetic.json").read_text()
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (1200, 750), "white").save(source)
    api = TextIn(
        "",
        "",
        httpx.MockTransport(lambda _: httpx.Response(200, json={"code": 200, "data": captured})),
    )
    ws = Workspace(tmp_path / "state", inputs, api)
    try:
        doc = ws.import_document(str(source))
        job = await ws.parse_document(doc["id"], 1, "free_sync")
        assert job["status"] == "completed"
        overview = ws.read_document(job["id"])
        assert len(overview["elements"]) == 6
        assert overview["elements"][3]["text"] == "x"
        objects = json.loads(ws.read_element(job["id"], 1, "objects")["content"])
        assert objects[0]["text"] == "2(x+3)"
        start, end = objects[0]["text_range"]
        assert captured["elements"][1]["text"][start:end] == objects[0]["text"]
        page = json.loads(ws.read_element(job["id"], 1, "page")["content"])[0]
        assert (page["page_width"], page["page_height"], page["angle"]) == (1200, 750, 0)
    finally:
        ws.close()


async def test_free_mode_is_explicit_cached_and_never_sends_credentials(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    calls = []

    def provider(request):
        calls.append(request)
        assert request.url.path == "/api/v1/agent/parse/sync"
        assert "x-ti-app-id" not in request.headers
        assert "x-ti-secret-code" not in request.headers
        assert request.headers["x-from"] == "study-errorbook"
        assert b'"page_range": "1-1"' in request.content
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {"success_count": 1, "elements": [{"text": "2x+3"}], "pages": []},
            },
        )

    ws = Workspace(
        tmp_path / "state",
        inputs,
        TextIn("private-id", "private-secret", httpx.MockTransport(provider)),
    )
    try:
        doc = ws.import_document(str(source))
        results = await asyncio.gather(
            *[ws.parse_document(doc["id"], 1, "free_sync") for _ in range(2)]
        )
        assert len(calls) == 1 and results[1]["cached"]
        assert results[0]["status"] == "completed"
        assert ws.read_document(results[0]["id"])["elements"][0]["text"] == "2x+3"
        assert (await ws.get_job(results[0]["id"]))["status"] == "completed"
        assert len(calls) == 1
    finally:
        ws.close()


async def test_free_mode_has_no_automatic_fallback_or_retry(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    calls = []

    def provider(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown remote state")

    ws = Workspace(tmp_path / "state", inputs, TextIn("", "", httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(source))
        with pytest.raises(DomainError, match="credentials_missing"):
            await ws.parse_document(doc["id"], 1)
        assert not calls
        first = await ws.parse_document(doc["id"], 1, "free_sync")
        assert first["status"] == "submission_unknown"
        assert (await ws.parse_document(doc["id"], 1, "free_sync"))["cached"]
        assert len(calls) == 1
        with pytest.raises(DomainError, match="parse_mode"):
            await ws.parse_document(doc["id"], 1, "automatic")
        assert len(calls) == 1
    finally:
        ws.close()


async def test_free_size_limit_is_checked_before_network(tmp_path):
    source = tmp_path / "large.png"
    with source.open("wb") as stream:
        stream.truncate(10 * 1024 * 1024 + 1)
    api = TextIn("", "", httpx.MockTransport(lambda _: pytest.fail("must not upload")))
    with pytest.raises(DomainError, match="free_file_limit"):
        await api.parse_free(source.read_bytes(), 1, source.suffix)


async def test_free_total_deadline_does_not_wait_for_http_inactivity_timeout(tmp_path, monkeypatch):
    path = tmp_path / "sample.png"
    path.write_bytes(b"synthetic request fixture")
    original_timeout = asyncio.timeout
    monkeypatch.setattr("study_errorbook.textin.asyncio.timeout", lambda _: original_timeout(0.01))

    async def slow_provider(request):
        await asyncio.sleep(1)
        pytest.fail("overall deadline should cancel the request")

    api = TextIn("", "", httpx.MockTransport(slow_provider))
    with pytest.raises(DomainError, match="network_uncertain"):
        await api.parse_free(path.read_bytes(), 1, path.suffix)


async def test_free_provider_error_stops_without_paid_fallback(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={"code": 40306})

    ws = Workspace(
        tmp_path / "state", inputs, TextIn("id", "secret", httpx.MockTransport(provider))
    )
    try:
        doc = ws.import_document(str(source))
        result = await ws.parse_document(doc["id"], 1, "free_sync")
        assert result["status"] == "failed" and result["error"]["code"] == "textin_40306"
        assert (await ws.parse_document(doc["id"], 1, "free_sync"))["cached"]
        assert len(calls) == 1
    finally:
        ws.close()
