"""Export a new local tracking snapshot without changing archived TextIn evidence."""

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

FIELDS = (
    "id", "title", "category", "priority", "classification", "review_status",
    "vendor_status", "vendor_feedback", "fixed_version", "retest_status",
    "retest_run_id", "human_reviewer",
)
IMMUTABLE = FIELDS[:5]
LABELS = {
    "review_status": "人工复核状态", "human_reviewer": "复核人（如有）",
    "vendor_status": "服务商沟通状态", "vendor_feedback": "服务商回复原文及来源",
    "fixed_version": "服务商声明修复版本", "retest_status": "本地复测结论",
    "retest_run_id": "复测证据标识（注明类型）",
}


def _read_tracking(raw: bytes) -> dict[str, dict[str, str]]:
    """Parse the existing CSV contract, rejecting silent loss or ambiguous row identity."""
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames or len(reader.fieldnames) != len(FIELDS) or set(reader.fieldnames) != set(FIELDS):
        raise ValueError("CSV列必须与原跟踪表完全一致；不要增删或重复列名")
    rows = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV行列数不匹配；多行回复应保留CSV引号")
        case_id = row["id"]
        if not re.fullmatch(r"TI-\d{3,}", case_id) or case_id in rows:
            raise ValueError("案例编号无效或重复")
        rows[case_id] = row
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-bundle", type=Path, required=True)
    parser.add_argument("--base-csv", type=Path, required=True)
    parser.add_argument("--updated-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--note", required=True, help="说明更新依据、来源和日期；初始化时明确尚无新回复")
    args = parser.parse_args()
    bundle = args.evidence_bundle.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("输出目录已存在；请选择新快照目录，不能覆盖历史")
    if output.resolve().is_relative_to(bundle):
        raise ValueError("跟踪快照必须放在证据包之外")
    if not args.note.strip():
        raise ValueError("必须说明更新依据")

    sources = {
        "cases": bundle / "cases.json", "baseline": args.base_csv.resolve(strict=True),
        "updated": args.updated_csv.resolve(strict=True),
    }
    raw = {name: path.read_bytes() for name, path in sources.items()}
    cases = json.loads(raw["cases"])
    previous, current = _read_tracking(raw["baseline"]), _read_tracking(raw["updated"])
    case_ids = [case["id"] for case in cases]
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != set(previous) or set(case_ids) != set(current):
        raise ValueError("案例集合必须与证据包相同；不得丢失或添加编号")
    changes, cards = [], []
    escape = html.escape
    for case in cases:
        case_id = case["id"]
        before, after = previous[case_id], current[case_id]
        if any(before[key] != case[key] or after[key] != case[key] for key in IMMUTABLE):
            raise ValueError(f"{case_id}的编号、标题、分类或优先级已变化；本工具只更新跟踪字段")
        for key in LABELS:
            if before[key] != after[key]:
                changes.append({"id": case_id, "field": key, "before": before[key], "after": after[key]})
        card = bundle / "cases" / f"{case_id}.md"
        if not card.is_file() or not (bundle / "index.html").is_file():
            raise ValueError(f"{case_id}缺少原始证据入口")
        card_link = quote(os.path.relpath(card, output), safe="/")
        image_link = quote(os.path.relpath(bundle / "index.html", output), safe="/") + "#" + case_id
        details = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(after[key]) if after[key] else '未填写'}</dd>"
            for key, label in LABELS.items()
        )
        cards.append(
            f'<article id="{case_id}"><h2>{case_id} · {escape(after["title"])}</h2>'
            f'<p>{escape(after["priority"])} · {escape(after["classification"])}</p><dl>{details}</dl>'
            f'<p><a href="{card_link}">原始问题卡片</a> · <a href="{image_link}">归档图文证据</a></p></article>'
        )
    ordered = [current[case_id] for case_id in case_ids]
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(ordered)
    timestamp = datetime.now(timezone.utc).isoformat()
    status_counts = dict(Counter(row["vendor_status"] or "未填写" for row in ordered))
    audit = {
        "created_at": timestamp, "note": args.note, "cases": len(ordered),
        "changed_cases": len({change["id"] for change in changes}), "changes": changes,
        "sources": {name: {"path_from_snapshot": os.path.relpath(sources[name], output),
                           "sha256": hashlib.sha256(content).hexdigest()}
                    for name, content in raw.items()},
        "vendor_status_counts": status_counts,
        "scope": "跟踪字段转录；未验证回复真伪、修复效果或人工意见，不推导关闭状态；无网络调用",
    }
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>TextIn反馈跟踪快照</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;color:#182c35;background:#f4f7f7;margin:0}}
main{{max-width:960px;margin:auto;padding:24px}}article{{background:white;padding:20px;margin:20px 0;border:1px solid #cbd7da;border-radius:10px}}
h1{{font-size:28px}}h2{{font-size:20px}}dt{{font-weight:600;margin-top:12px}}dd{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#086a80}}.note{{white-space:pre-wrap}}</style>
<main><h1>TextIn反馈跟踪快照</h1><p>{len(ordered)}项 · 本次更新{audit['changed_cases']}项 · {escape(timestamp)}</p>
<p class="note">更新依据：{escape(args.note)}</p>
<p>本页记录当前跟踪字段。原始证据页是历史快照，其旧状态不随本页改变。服务商声明修复与本地复测分别记录，未自动合并成“已解决”。</p>
<p>本页只读；后续复制本次CSV修改，并导出新的快照。原图链接依赖相邻证据包，移动文件时保留相对目录。</p>
<nav><a href="跟踪表.csv">跟踪表CSV</a> · <a href="跟踪表.json">跟踪表JSON</a> · <a href="变更记录.json">字段变更与来源哈希</a></nav>
{''.join(cards)}</main></html>'''
    # Validate all inputs before claiming a new output directory; never alter a prior snapshot.
    if any(path.read_bytes() != raw[name] for name, path in sources.items()):
        raise ValueError("导出期间输入变化，请重新读取后导出")
    output.mkdir(parents=True, exist_ok=False)
    (output / "跟踪表.csv").write_bytes(csv_buffer.getvalue().encode("utf-8-sig"))
    (output / "跟踪表.json").write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")
    (output / "变更记录.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    # Write the entry point last; a failed export without index.html is incomplete.
    (output / "index.html").write_text(page)
    print(json.dumps({"snapshot": str(output), "cases": len(ordered),
                      "changed_cases": audit["changed_cases"], "network_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"未完成导出：{error}") from error
