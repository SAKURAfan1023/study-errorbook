"""Create labelled synthetic export cases for offline browser/print QA; no API calls."""
from pathlib import Path

from PIL import Image, ImageDraw

from study_errorbook.models import Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

root = Path(__file__).resolve().parents[1] / 'data/print-qa'
inputs = root / 'inputs'
inputs.mkdir(parents=True, exist_ok=True)
source = inputs / 'synthetic.png'
image = Image.new('RGB', (900, 300), 'white')
ImageDraw.Draw(image).text((30, 90), 'SYNTHETIC PRINT QA - NOT A STUDENT PAPER', fill='black', font_size=30)
image.save(source)
ws = Workspace(root / 'workspace', inputs, TextIn('', ''))
try:
    doc = ws.import_document(str(source))
    evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
    ids = []
    for index in range(1, 4):
        record = StudyRecord(
            document_id=doc['id'], question_label=f'合成排版样例 {index}',
            stem=r'本页仅验证排版。化简 \(\frac{x^2-1}{x-1}\)，其中 \(x\ne1\)。价格 $5 与 $10 应保持原样。'
                 + ('\n第二行中文说明。' if index == 1 else ''),
            student_answer=r'原作答示例：\(x-1\)', evidence_ids=[evidence['id']],
            exercises=[
                {'prompt': rf'合成题 {index}-A：计算 \[\frac{{1}}{{3}}+\frac{{1}}{{6}}\] 写出通分过程。',
                 'answer': r'答案专用标记 \(\frac{1}{2}\)', 'targets_error': '通分',
                 'explanation': r'\[\frac{2}{6}+\frac{1}{6}=\frac{3}{6}=\frac{1}{2}\]'},
                {'prompt': rf'合成题 {index}-B：解方程 \(x^2-5x+6=0\)，写出步骤。',
                 'answer': r'答案专用标记 \(x=2\) 或 \(x=3\)', 'targets_error': '因式分解',
                 'explanation': r'\[(x-2)(x-3)=0\]'},
            ],
        )
        ids.append(ws.save_record(record)['id'])
    output = ws.export_errorbook(ids, include_drafts=True)
    (root / 'latest-export.txt').write_text(output['folder']+'\n')
    print(output['folder'])
finally:
    ws.close()
