"""Run a labelled two-page synthetic workflow offline; never call TextIn."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from study_errorbook.models import Analysis, CoveragePage, PracticeFeedback, Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / 'inputs'
    inputs.mkdir()
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 34)
    pages = []
    for title, lines in (
        ('ALGEBRA / SYNTHETIC WORKSHEET', ['Name: Synthetic Student', '1. Expand 2(x + 3).', 'Student answer: 2x + 3', 'Correction: multiply BOTH terms by 2.']),
        ('ALGEBRA / PAGE 2', ['2. Simplify 4(x + 1).', 'This question is outside the selected scope.']),
    ):
        image = Image.new('RGB', (1200, 900), 'white')
        draw = ImageDraw.Draw(image)
        draw.text((60, 55), title, font=font, fill='#173f46')
        for index, line in enumerate(lines):
            draw.text((60, 150 + index * 130), line, font=font, fill='#a83a3a' if 'Correction' in line else '#24323b')
        draw.text((60, 810), 'SYNTHETIC DEMO - NO REAL STUDENT DATA', font=font, fill='#617276')
        pages.append(image)
    source = inputs / 'synthetic-algebra.pdf'
    pages[0].save(source, save_all=True, append_images=pages[1:], format='PDF')
    ws = Workspace(root / 'workspace', inputs, TextIn('', ''))
    try:
        original = ws.import_document(str(source))
        doc = ws.redact_document(original['id'], [Region(page=1, box=[0.04, .15, .65, .25])])
        whole = [ws.read_evidence(doc['id'], Region(page=n, box=[0, 0, 1, 1]))[0] for n in (1, 2)]
        evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[.03, .29, .96, .76]))
        # Deliberately seeded offline fixture; not a provider result or OCR-quality claim.
        job = {'id': 'synthetic-cache', 'document_id': doc['id'], 'mode': 'free_sync', 'requested_pages': 2,
               'fixture_source': 'offline synthetic text, not OCR'}
        ws._cache_parse_result(job, doc, {'success_count': 2, 'elements': [
            {'text': '1. Expand 2(x + 3). Student answer: 2x + 3', 'page_number': 1},
            {'text': '2. Simplify 4(x + 1).', 'page_number': 2}],
            'pages': [{'page_number': n, 'status': 'Success'} for n in (1, 2)]})
        ws._put('job', job['id'], job)
        assert ws.read_document(job['id'])['total'] == 2
        check = ws.check_exercise('3*(x+2)=15', '3', 'linear_equation')
        saved = ws.save_record(StudyRecord(
            document_id=doc['id'], question_label='去括号', subject='代数',
            stem=r'展开并化简 \(2(x+3)\)，写出关键步骤。', student_answer=r'\(2x+3\)',
            evidence_ids=[evidence['id']], teacher_verdict='uncertain', agent_verdict='wrong',
            analysis=Analysis(explanation='可见作答把括号前的2只乘给x，没有乘给常数3。依据是保留的原图和原作答转录；不能据此推断学生一贯粗心。',
                evidence_ids=[evidence['id']], error_type='分配律漏乘常数项', knowledge_points=['乘法分配律', '去括号'],
                uncertainty='合成样例；红色订正的书写者未知，未作为教师批改依据。', diagnostic_question='括号里的两项分别要乘几？'),
            exercises=[{'prompt': r'解方程 \(3(x+2)=15\)，写出去括号和移项的步骤。', 'answer': '3',
                'explanation': r'\[3x+6=15\]\[3x=9\]\[x=3\] 代回原方程，两侧均为15。',
                'targets_error': '将括号前的3分配给括号内的每一项。', 'check_id': check['id']}]))
        progress = ws.update_document_coverage(doc['id'], 0, [
            CoveragePage(page=1, evidence_id=whole[0]['id'], questions=[{'question_label': '1', 'record_id': saved['id']}]),
            CoveragePage(page=2, evidence_id=whole[1]['id'], questions=[{'question_label': '2', 'skip_reason': '本轮演示仅处理第1题；第2题保留待后续选择。'}])])
        assert progress['counts']['pending'] == 1
        reviewed = ws.review_record(saved['id'], 1, 'include', '合成测试模拟用户明确纳入；并非真实学生复核或OCR质量验收。')
        prompt, images = ws.read_practice_prompt(saved['id'], reviewed['revision'], exercise_index=0)
        assert not images and 'answer' not in prompt
        attempt = ws.record_practice(saved['id'], reviewed['revision'], 0, '3x+6=15；3x=9；x=3。', assistance='independent', attempt_key='synthetic-attempt-1')
        ws.review_practice_attempt(attempt['id'], 0, PracticeFeedback(agent_verdict='correct',
            feedback='本次合成作答给括号内两项都乘了3，并保留了移项步骤。代入x=3，等式两侧均为15。此反馈只覆盖这一次作答。',
            next_step='换一题含负号的去括号练习，再检查是否每一项都乘到；本次答对不等于已经掌握。'))
        exported = ws.export_errorbook([saved['id']])
        result = {'fixture': 'synthetic only; no OCR, real paper or real user review', 'network_calls': 0,
                  'document_id': doc['id'], 'record_id': saved['id'], 'export': exported,
                  'progress': ws.read_task_progress(doc['id'])}
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    finally:
        ws.close()
    reopened = Workspace(root / 'workspace', inputs, TextIn('', ''))
    try:
        assert reopened.read_task_progress(doc['id'])['counts'] == {'processed': 1, 'pending': 0, 'skipped': 1}
        assert reopened.read_practice_history(saved['id'])['total'] == 1
    finally:
        reopened.close()
    print(json.dumps({'export_folder': exported['folder'], 'result': str(root / 'result.json'), 'network_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
