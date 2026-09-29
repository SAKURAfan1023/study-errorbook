"""Explicit live experiment: upload one generated, non-personal page to TextIn's free API.

No credentials are read, no existing files are uploaded, and there is no retry.
This is an experiment, not a production fallback for missing credentials.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from study_errorbook.textin import PARSE_CONFIG

ENDPOINT = "https://api.textin.com/api/v1/agent/parse/sync"


def main():
    folder = (
        Path(__file__).resolve().parents[1]
        / "data/probes"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    folder.mkdir(parents=True)
    image = Image.new("RGB", (1200, 750), "white")
    draw = ImageDraw.Draw(image)
    for position, text, color, size in [
        ((40, 30), "SYNTHETIC OCR TEST - NO PERSONAL DATA", "gray", 30),
        ((50, 140), "Question 1: Expand 2(x + 3).", "black", 46),
        ((50, 270), "Student answer: 2x + 3", "blue", 42),
        ((50, 410), "Correction: multiply both terms by 2.", "red", 40),
        ((50, 590), "Reference answer: 2x + 6", "black", 42),
    ]:
        draw.text(position, text, fill=color, font_size=size)
    draw.line((850, 260, 915, 325), fill="red", width=8)
    draw.line((915, 260, 850, 325), fill="red", width=8)
    source = folder / "synthetic.png"
    image.save(source)
    config = {**PARSE_CONFIG, "scope": {"page_range": "1-1"}}
    report = {
        "endpoint": ENDPOINT,
        "synthetic_only": True,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "request_config": config,
        "credentials_used": False,
        "source_header": "study-errorbook",
        "attempts": 1,
    }
    start = time.monotonic()
    try:
        with (
            source.open("rb") as stream,
            httpx.Client(timeout=90, follow_redirects=False) as client,
        ):
            with client.stream(
                "POST",
                ENDPOINT,
                headers={"X-From": "study-errorbook"},
                files={
                    "file": ("synthetic.png", stream, "image/png"),
                    "config": (None, json.dumps(config), "application/json"),
                },
            ) as response:
                report["http_status"] = response.status_code
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 32 * 1024 * 1024:
                        raise ValueError("Response exceeds 32MiB experiment limit")
                    chunks.append(chunk)
                binary = b"".join(chunks)
        (folder / "response.bin").write_bytes(binary)
        payload = json.loads(binary)
        report["business_code"] = payload.get("code")
        report["message"] = payload.get("message")
        data = payload.get("data")
        if isinstance(data, dict):
            (folder / "result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
            elements = data.get("elements", [])
            report["data_keys"] = list(data)
            report["element_count"] = len(elements)
            report["recognized_text"] = [element.get("text", "") for element in elements]
            report["character_detail_count"] = sum(len(e.get("char_details", [])) for e in elements)
            report["inline_object_count"] = sum(len(e.get("objects", [])) for e in elements)
    except (httpx.HTTPError, ValueError) as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
    report["elapsed_seconds"] = round(time.monotonic() - start, 2)
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"folder": str(folder), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
