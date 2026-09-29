import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.deletion import preview_document_deletion
from study_errorbook.models import Region
from study_errorbook.service import Workspace
from study_errorbook.textin import ERASE_ENDPOINT, ERASE_PARAMS, TextIn


@pytest.fixture
def material(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (50, 30), "white").save(image)
    return image


@pytest.mark.parametrize("variant", ["success", "business_error", "http_error", "invalid_json", "invalid_image", "credential_echo"])
async def test_receipt_survives_errors_and_cached_reopen(tmp_path, material, variant):
    calls = []
    payload = {"code": 200, "version": "1.0.fixture", "x_request_id": "body-request",
               "duration": 12, "result": {"image": base64.b64encode(material.read_bytes()).decode()}}
    if variant == "business_error":
        payload["code"] = 40003
    if variant == "invalid_image":
        payload["result"]["image"] = None
    if variant == "credential_echo":
        payload["echo"] = "fake-secret-long-value"
    body = b"not json" if variant == "invalid_json" else json.dumps(payload, indent=2).encode()

    def provider(request):
        calls.append(request)
        assert str(request.url).startswith(ERASE_ENDPOINT)
        assert dict(request.url.params) == {k: str(v) for k, v in ERASE_PARAMS.items()}
        return httpx.Response(500 if variant == "http_error" else 200, content=body,
                              headers={"x-request-id": "header-request", "set-cookie": "excluded"})

    ws = Workspace(tmp_path / "state", tmp_path, TextIn("fake-app-id-long", "fake-secret-long-value", httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
        original = Path(evidence["path"]).read_bytes()
        result = await ws.make_clean_copy(evidence["id"])
        receipt = result["provider_receipt"]
        expected_body = body.replace(b"fake-secret-long-value", b"[REDACTED]")
        assert Path(receipt["response_path"]).read_bytes() == expected_body
        assert receipt["response_sha256"] == hashlib.sha256(expected_body).hexdigest()
        assert receipt["response_bytes"] == len(expected_body)
        assert receipt["credential_echo_redacted"] == (variant == "credential_echo")
        assert receipt["request_ids"] == {"x-request-id": "header-request"}
        assert receipt["response_complete"] and receipt["response_saved"]
        assert "excluded" not in json.dumps(result) and "fake-secret-long-value" not in json.dumps(result)
        assert "response_body" not in receipt
        assert Path(evidence["path"]).read_bytes() == original
        assert result["parameters"] == ERASE_PARAMS
        assert result["submitted_at"] <= receipt["finished_at"]
        if variant not in {"http_error", "invalid_json"}:
            assert receipt["x_request_id"] == "body-request" and receipt["version"] == "1.0.fixture"
        if variant in {"success", "credential_echo"}:
            assert result["status"] == "completed" and result["review_status"] == "needs_visual_review"
        else:
            assert result["status"] in {"failed", "submission_unknown"}
        # The archive is owned by the document deletion inventory.
        preview = preview_document_deletion(ws.db, ws.root, doc["id"])
        relative = Path(receipt["response_path"]).relative_to(ws.root).as_posix()
        assert any(item.get("path") == relative and item.get("sha256") == receipt["response_sha256"]
                   for item in preview["items"])
    finally:
        ws.close()
    reopened = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    try:
        cached = await reopened.make_clean_copy(evidence["id"])
        assert cached == {**result, "cached": True}
        assert len(calls) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["timeout", "cancel", "archive_collision"])
async def test_missing_receipt_does_not_cause_resubmission(tmp_path, material, failure):
    calls = []

    def provider(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        if failure == "cancel":
            raise asyncio.CancelledError()
        return httpx.Response(200, json={"code": 200, "result": {"image": base64.b64encode(material.read_bytes()).decode()}})

    ws = Workspace(tmp_path / "state", tmp_path, TextIn("fake-app-id-long", "fake-secret-long-value", httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
        target = ws.root / doc["id"] / (evidence["id"] + "-erase-response.bin")
        if failure == "archive_collision":
            target.write_bytes(b"existing archive")
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await ws.make_clean_copy(evidence["id"])
            result = ws._get("erase", evidence["id"])
        else:
            result = await ws.make_clean_copy(evidence["id"])
        assert result["provider_receipt"]["response_saved"] is False
        if failure == "archive_collision":
            assert result["status"] == "completed"
            assert result["provider_receipt"]["archive_error"] == "response_not_saved"
            assert target.read_bytes() == b"existing archive"
        else:
            assert result["status"] == "submission_unknown" and not target.exists()
        assert (await ws.make_clean_copy(evidence["id"]))["cached"]
        assert len(calls) == 1
    finally:
        ws.close()


@pytest.mark.parametrize("failure", ["truncated_stream", "oversized"])
async def test_incomplete_body_keeps_header_id_without_archiving_partial_data(tmp_path, material, failure):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if failure == "oversized":
                yield b"x" * (17 * 1024 * 1024)
                yield b"y" * (17 * 1024 * 1024)
            else:
                yield b'{"code":200'
                raise httpx.ReadError("fixture")

    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, headers={"x-request-id": "incomplete-request"}, stream=BrokenStream())

    ws = Workspace(tmp_path / "state", tmp_path, TextIn("fake-app-id-long", "fake-secret-long-value", httpx.MockTransport(provider)))
    try:
        doc = ws.import_document(str(material))
        evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
        result = await ws.make_clean_copy(evidence["id"])
        assert result["status"] == "submission_unknown"
        receipt = result["provider_receipt"]
        assert receipt["request_ids"] == {"x-request-id": "incomplete-request"}
        assert receipt["response_complete"] is False and receipt["response_saved"] is False
        assert not list((ws.root / doc["id"]).glob("*-erase-response.bin"))
        assert (await ws.make_clean_copy(evidence["id"]))["cached"]
        assert len(calls) == 1
    finally:
        ws.close()
