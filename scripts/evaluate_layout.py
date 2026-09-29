"""Small synthetic layout diagnostic, not a benchmark score or handwriting test.

Default: generate labelled Chinese pages locally. --live explicitly uploads the
generated PDF once through the free API; subsequent runs reuse the workspace job.
"""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

ROOT = Path(__file__).resolve().parents[1] / "data/layout-evaluation-v1"
FONT = Path("/System/Library/Fonts/STHeiti Medium.ttc")


def generate():
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / "cases.json").exists():
        return json.loads((ROOT / "cases.json").read_text())
    if not FONT.is_file():
        raise SystemExit("This local fixture generator requires the documented macOS STHeiti font.")
    font = ImageFont.truetype(str(FONT), 40)
    small = ImageFont.truetype(str(FONT), 28)
    images, cases = [], []
    definitions = [
        (
            "中文单栏与红叉",
            False,
            [
                (70, 150, "第一题：展开 2(x + 3)", "black"),
                (70, 310, "学生作答：2x + 3", "blue"),
                (70, 530, "老师批注：常数项也要乘以二", "red"),
                (70, 720, "参考答案：2x + 6", "black"),
            ],
            ["第一题", "学生作答", "常数项也要乘以二", "参考答案"],
        ),
        (
            "旋转页与黑色批注",
            True,
            [
                (70, 150, "第二题：展开 -3(x - 2)", "red"),
                (70, 310, "学生作答：-3x - 6", "blue"),
                (70, 530, "老师黑笔批注：负数乘负数得到正数", "black"),
                (70, 720, "红色题干并非批改", "red"),
            ],
            ["第二题", "学生作答", "负数乘负数得到正数", "红色题干并非批改"],
        ),
        (
            "双栏与学生红笔订正",
            False,
            [
                (70, 150, "第三题：展开 3(x + 4)", "black"),
                (70, 310, "学生作答：3x + 12", "blue"),
                (70, 530, "老师批注：正确", "red"),
                (740, 150, "第四题：展开 5(x - 2)", "black"),
                (740, 310, "学生作答：5x - 2", "black"),
                (740, 530, "学生红笔订正：5x - 10", "red"),
            ],
            ["第三题", "第四题", "老师批注", "学生红笔订正"],
        ),
    ]
    for page_number, (name, rotated, lines, anchors) in enumerate(definitions, 1):
        image = Image.new("RGB", (1400, 1000), "white")
        draw = ImageDraw.Draw(image)
        draw.text(
            (60, 30),
            "合成测试材料：全为打印字体，无个人数据，不能代表真实手写效果",
            font=small,
            fill="gray",
        )
        for x, y, content, color in lines:
            draw.text((x, y), content, font=font, fill=color)
        if page_number == 1:
            draw.line((750, 300, 810, 360), fill="red", width=8)
            draw.line((810, 300, 750, 360), fill="red", width=8)
        if page_number == 3:
            draw.line((695, 130, 695, 850), fill="gray", width=2)
        if rotated:
            image = image.transpose(Image.Transpose.ROTATE_270)
        image.save(ROOT / f"page-{page_number}.png")
        images.append(image)
        cases.append(
            {
                "page": page_number,
                "case": name,
                "input_clockwise_rotation": 90 if rotated else 0,
                "anchors": anchors,
                "source_lines": [line[2] for line in lines],
            }
        )
    source = ROOT / "synthetic-layout.pdf"
    images[0].save(source, save_all=True, append_images=images[1:], resolution=120)
    manifest = {
        "synthetic_only": True,
        "font": str(FONT),
        "pdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "cases": cases,
    }
    (ROOT / "cases.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


async def evaluate(manifest):
    source = ROOT / "synthetic-layout.pdf"
    if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["pdf_sha256"]:
        raise SystemExit(
            "Fixture changed after generation; do not upload unverified replacement content."
        )
    ws = Workspace(ROOT / "workspace", ROOT, TextIn("", ""))
    start = time.monotonic()
    try:
        document = ws.import_document(str(source))
        job = await ws.parse_document(document["id"], 3, "free_sync")
        report = {
            "job": job,
            "elapsed_seconds": round(time.monotonic() - start, 2),
            "synthetic_only": True,
            "checks": [],
        }
        if job["status"] in {"completed", "partial"}:
            result = json.loads(Path(job["result_path"]).read_text())
            for case in manifest["cases"]:
                elements = [e for e in result["elements"] if e.get("page_number") == case["page"]]
                page_meta = [
                    p for p in result.get("pages", []) if p.get("page_number") == case["page"]
                ]
                text = "\n".join(e.get("text", "") for e in elements)
                report["checks"].append(
                    {
                        **case,
                        "anchor_presence": {anchor: anchor in text for anchor in case["anchors"]},
                        "recognized_elements": [
                            {
                                "type": e.get("type"),
                                "text": e.get("text"),
                                "coordinates": e.get("coordinates"),
                                "objects": e.get("objects", []),
                            }
                            for e in elements
                        ],
                        "page_metadata": [
                            {
                                k: p.get(k)
                                for k in (
                                    "page_number",
                                    "page_width",
                                    "page_height",
                                    "angle",
                                    "status",
                                )
                            }
                            for p in page_meta
                        ],
                    }
                )
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if not job.get("cached") and not (ROOT / "first-live-report.json").exists():
            (ROOT / "first-live-report.json").write_text(serialized)
        (ROOT / "report.json").write_text(serialized)
        print(
            json.dumps(
                {
                    "folder": str(ROOT),
                    "status": job["status"],
                    "cached": job.get("cached", False),
                    "elapsed_seconds": report["elapsed_seconds"],
                    "cases": [
                        {
                            "case": c["case"],
                            "anchors": c["anchor_presence"],
                            "page_metadata": c["page_metadata"],
                        }
                        for c in report["checks"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        ws.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Upload the generated PDF to TextIn free API"
    )
    args = parser.parse_args()
    manifest = generate()
    if args.live:
        asyncio.run(evaluate(manifest))
    else:
        print(f"Generated locally; no upload: {ROOT}")
