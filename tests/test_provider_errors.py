import json

import httpx
import pytest
from PIL import Image

from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.mark.parametrize("bad_status", [["completed"], {"value": "completed"}])
async def test_malformed_poll_status_preserves_remote_job_and_can_recover(tmp_path, bad_status):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    calls = []

    def provider(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"job_id": "original-job"}})
        status = bad_status if calls.count("GET") == 1 else "in_progress"
        return httpx.Response(200, json={"data": {"status": status}})

    ws = Workspace(tmp_path / "state", inputs, TextIn("id", "secret", httpx.MockTransport(provider)))
    try:
        document = ws.import_document(str(source))
        job = await ws.parse_document(document["id"], 1)
        observed = await ws.get_job(job["id"])
        assert observed["status"] == "pending"
        assert observed["provider_job_id"] == "original-job"
        assert observed["poll_error"]["code"] == "invalid_response"
        assert ws._get("job", job["id"]) == observed
        # Advance only the local poll clock; recovery must observe the same remote job.
        observed["polled_at"] = 0
        ws._put("job", job["id"], observed)
        recovered = await ws.get_job(job["id"])
        assert recovered["status"] == "in_progress"
        assert recovered["provider_job_id"] == "original-job"
        assert "poll_error" not in recovered
        assert (await ws.parse_document(document["id"], 1))["cached"]
        assert calls == ["POST", "GET", "GET"]
    finally:
        ws.close()


@pytest.mark.parametrize("mode", ["paid_async", "free_sync"])
@pytest.mark.parametrize("failure", ["timeout", "http_500", "business_error", "invalid_json"])
async def test_provider_failure_is_safe_persisted_and_never_resubmitted(tmp_path, mode, failure):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    private_marker = "SYNTHETIC-UPSTREAM-SECRET-AND-MATERIAL"
    calls = []

    def provider(request):
        calls.append(request.method)
        if failure == "timeout":
            raise httpx.ReadTimeout(private_marker, request=request)
        if failure == "http_500":
            return httpx.Response(500, text=private_marker)
        if failure == "business_error":
            return httpx.Response(200, json={"code": 40003, "message": private_marker})
        return httpx.Response(200, text=private_marker)

    ws = Workspace(tmp_path / "state", inputs, TextIn("id", private_marker, httpx.MockTransport(provider)))
    try:
        document = ws.import_document(str(source))
        result = await ws.parse_document(document["id"], 1, mode)
        expected = {"timeout": "network_uncertain", "http_500": "http_500", "business_error": "textin_40003"}.get(failure, "invalid_response")
        assert result["status"] == ("failed" if failure == "business_error" else "submission_unknown")
        assert result["error"]["code"] == expected
        assert private_marker not in json.dumps(result)
        assert ws._get("job", result["id"]) == result
        assert private_marker not in ws.db.execute("SELECT body FROM objects WHERE kind='job'").fetchone()[0]
        assert (await ws.parse_document(document["id"], 1, mode))["cached"]
        assert calls == ["POST"]
    finally:
        ws.close()
