"""Generate a labelled synthetic workflow demo without any network/API call."""

from pathlib import Path

from PIL import Image, ImageDraw

from study_errorbook.models import Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

root = Path(__file__).resolve().parents[1] / "data/demo"
inputs = root / "inputs"
inputs.mkdir(parents=True, exist_ok=True)
paper = inputs / "synthetic-paper.png"
image = Image.new("RGB", (1000, 600), "white")
draw = ImageDraw.Draw(image)
draw.text((40, 30), "SYNTHETIC DEMO - NOT A REAL STUDENT PAPER", font_size=28, fill="gray")
draw.text((50, 130), "1. Expand 2(x + 3)", font_size=40, fill="black")
draw.text((50, 240), "Student answer: 2x + 3", font_size=40, fill="black")
draw.line((650, 230, 720, 300), fill="red", width=7)
draw.line((720, 230, 650, 300), fill="red", width=7)
draw.text((50, 390), "Teacher note: multiply BOTH terms", font_size=36, fill="red")
image.save(paper)
workspace = Workspace(root / "workspace", inputs, TextIn("", ""))
try:
    doc = workspace.import_document(str(paper))
    evidence, _ = workspace.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
    marking, _ = workspace.read_evidence(doc["id"], Region(page=1, box=[0.62, 0.34, 0.75, 0.53]))
    answer_evidence, _ = workspace.read_evidence(doc["id"], Region(page=1, box=[0.03, 0.34, 0.61, 0.52]))
    check = workspace.check_exercise("-2*(x-5)", "-2*x+10")
    record = StudyRecord.model_validate(
        {
            "document_id": doc["id"],
            "question_label": "合成示例 · 第1题",
            "subject": "数学",
            "stem": "展开 2(x + 3)",
            "student_answer": "2x + 3",
            "evidence_ids": [evidence["id"]],
            "teacher_verdict": "wrong",
            "agent_verdict": "wrong",
            "annotations": [
                {
                    "text": "合成红色叉号（书写人待确认）",
                    "kind": "cross",
                    "color": "red",
                    "author": "unknown",
                    "evidence_ids": [marking["id"]],
                }
            ],
            "analysis": {
                "explanation": "【合成数据的预置分析，未经OCR识别】答案的常数项没有乘以2。可能漏用了分配律，也可能是笔误。",
                "error_type": "分配律漏乘",
                "knowledge_points": ["分配律"],
                "evidence_ids": [answer_evidence["id"]],
                "uncertainty": "需要追问才能区分概念问题与偶发笔误。",
                "diagnostic_question": "2应该分别乘括号里的哪些项？",
            },
            "exercises": [
                {
                    "prompt": "展开 -2(x - 5)",
                    "answer": "-2*x+10",
                    "explanation": "-2分别乘x和-5。",
                    "targets_error": "检查分配律与负号处理",
                    "check_id": check["id"],
                }
            ],
        }
    )
    saved = workspace.save_record(record)
    result = workspace.export_errorbook([saved["id"]], include_drafts=True)
    (root / "latest-export.txt").write_text(result["folder"] + "\n")
    print("Synthetic, offline demo. No TextIn or model API used.")
    for path in result["files"]:
        print(path)
finally:
    workspace.close()
