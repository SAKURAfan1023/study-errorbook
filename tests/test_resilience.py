import io
import json

import httpx
import pytest
from PIL import Image

from study_errorbook.models import Region
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


async def test_partial_result_and_observation_failure_keep_one_job(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    path = inputs / "two-pages.pdf"
    Image.new("RGB", (200, 300), "white").save(
        path, save_all=True, append_images=[Image.new("RGB", (200, 300), "red")]
    )
    count = {"post": 0, "poll": 0}

    def provider(request):
        if request.method == "POST":
            count["post"] += 1
            return httpx.Response(200, json={"code": 200, "data": {"job_id": "two_pages"}})
        if request.url.host == "api.textin.com":
            count["poll"] += 1
            if count["poll"] == 1:
                raise httpx.ReadTimeout("temporary")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "status": "completed",
                        "result_url": "https://web-api.textin.com/result",
                    },
                },
            )
        return httpx.Response(200, json={"success_count": 1, "elements": []})

    api = TextIn("id", "secret", httpx.MockTransport(provider))
    ws = Workspace(tmp_path / "state", inputs, api)
    try:
        doc = ws.import_document(str(path))
        assert doc["pages"] == 2
        _, page = ws.read_evidence(doc["id"], Region(page=2, box=[0, 0, 1, 1]))
        assert Image.open(io.BytesIO(page)).getpixel((20, 20))[0] > 240
        job = await ws.parse_document(doc["id"], 2)
        observed = await ws.get_job(job["id"])
        assert observed["status"] == "pending"
        assert observed["provider_job_id"] == "two_pages"
        assert observed["poll_error"]["code"] == "network_uncertain"
        # Advance the persisted poll time without waiting in a unit test.
        observed["polled_at"] = 0
        ws._put("job", job["id"], observed)
        observed = await ws.get_job(job["id"])
        assert observed["status"] == "partial"
        assert count["post"] == 1
    finally:
        ws.close()


def test_submission_interrupted_and_exif_rotation_are_preserved(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    path = inputs / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (100, 200), "blue").save(path, exif=exif)
    original = path.read_bytes()
    ws = Workspace(tmp_path / "state", inputs, TextIn("", ""))
    doc = ws.import_document(str(path))
    assert (ws.root / doc["id"] / "original.bin").read_bytes() == original
    evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    assert (evidence["width"], evidence["height"]) == (200, 100)
    ws._put("job", "interrupted", {"id": "interrupted", "status": "submitting"})
    ws.close()
    reopened = Workspace(tmp_path / "state", inputs, TextIn("", ""))
    try:
        assert reopened._get("job", "interrupted")["status"] == "submission_unknown"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "payload",
    [{"code": 200, "data": None}, {"code": "bad"}, {"code": 200, "data": {"job_id": "../escape"}}],
)
async def test_bad_submission_response_has_safe_error(tmp_path, payload):
    api = TextIn("id", "secret", httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    path = tmp_path / "dummy.png"
    path.write_bytes(b"fixture")
    from study_errorbook.models import DomainError

    with pytest.raises(DomainError, match="invalid_response"):
        await api.submit(path.read_bytes(), 1, path.suffix)


def test_bounded_read_does_not_drop_its_cursor(tmp_path):
    ws = Workspace(tmp_path / "state", None, TextIn("", ""))
    data = {
        "elements": [
            {
                "element_id": "e",
                "text": "x" * 6000,
                "objects": [{"text": "y" * 2000} for _ in range(30)],
            }
        ]
    }
    result = ws.root / "fixture" / "fixture.json"
    result.parent.mkdir()
    result.write_text(json.dumps(data))
    ws._put(
        "job",
        "fixture",
        {
            "id": "fixture",
            "document_id": "fixture",
            "status": "completed",
            "result_path": str(result),
        },
    )
    try:
        response = ws.read_document("fixture")
        assert response["elements"][0]["text_truncated"]
        assert response["next_offset"] is None
        assert len(json.dumps(response)) < 20000
    finally:
        ws.close()
