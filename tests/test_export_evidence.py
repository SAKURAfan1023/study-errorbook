import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.models import Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


class EvidenceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links = [], []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and attrs.get('href', '').startswith('#'):
            self.links.append(attrs['href'][1:])


@pytest.fixture(params=['single-page', 'cross-page'])
def evidence_export(tmp_path, request):
    source = tmp_path / 'sample.png'
    image = Image.new('RGB', (300, 300), 'white')
    image.paste('red', (0, 100, 300, 200))
    image.paste('blue', (0, 200, 300, 300))
    image.save(source)
    regions = [Region(page=1, box=[0, i/3, 1, (i+1)/3]) for i in range(3)]
    if request.param == 'cross-page':
        source = tmp_path / 'sample.pdf'
        first = Image.new('RGB', (300, 300), 'white')
        second = Image.new('RGB', (300, 300), 'red')
        second.paste('blue', (0, 150, 300, 300))
        first.save(source, save_all=True, append_images=[second])
        regions = [Region(page=1, box=[0, 0, 1, 1]),
                   Region(page=2, box=[0, 0, 1, .5]),
                   Region(page=2, box=[0, .5, 1, 1])]
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    doc = ws.import_document(str(source))
    evidence = [ws.read_evidence(doc['id'], region)[0] for region in regions]
    record = StudyRecord(
        document_id=doc['id'], question_label='第1题', stem='测试题干',
        student_answer='测试原作答', evidence_ids=[evidence[0]['id']],
        teacher_verdict='wrong', agent_verdict='correct',
        annotations=[{'kind': 'cross', 'author': 'unknown', 'color': 'red',
                      'text': '<script>唯一批注</script>', 'evidence_ids': [evidence[1]['id']]}],
        analysis={'explanation': '两侧判断不同，需要核对', 'error_type': '待确认',
                  'knowledge_points': ['分配律', '<b>负号处理</b>'],
                  'uncertainty': '笔迹归属不明', 'diagnostic_question': '说明这一步的理由？',
                  'evidence_ids': [evidence[2]['id']]},
        exercises=[{'prompt': '练习题干', 'answer': '唯一答案',
                    'targets_error': '唯一练习目的：区分漏乘与笔误'}],
    )
    yield ws, record, evidence
    ws.close()


def test_export_includes_all_evidence_sources_and_separate_judgments(evidence_export):
    ws, record, evidence = evidence_export
    saved = ws.save_record(record)
    reviewed = ws.review_record(saved['id'], 1, 'include', '用户意见：先保留，待核对批改；练习题干与答案已核对', [0])
    output = ws.export_errorbook([saved['id']])
    folder = Path(output['folder'])
    assert 'evidence' not in output['records'][0]
    assert Path(output['manifest_path']).is_file()
    notebook = (folder / 'errorbook.html').read_text()
    assert all((folder / (entry['id'] + '.png')).is_file() for entry in evidence)
    assert '卷面批改（录入）：错误' in notebook
    assert 'Agent判断：正确' in notebook
    assert '书写人待确认' in notebook and '红色' in notebook and '叉号' in notebook
    assert '&lt;script&gt;唯一批注&lt;/script&gt;' in notebook
    assert '<script>唯一批注</script>' not in notebook
    assert '分配律' in notebook and '&lt;b&gt;负号处理&lt;/b&gt;' in notebook
    assert '用户意见：先保留，待核对批改' in notebook
    assert 'Agent转述' in notebook
    parser = EvidenceLinks()
    parser.feed(notebook)
    assert len(parser.links) >= 3 and all(link in parser.ids for link in parser.links)
    manifest = json.loads((folder / 'manifest.json').read_text())
    exported = manifest['records'][0]
    assert exported['revision'] == reviewed['revision']
    assert [e['id'] for e in exported['evidence']] == [e['id'] for e in evidence]
    for actual, expected, color in zip(exported['evidence'], evidence,
                                       [(255, 255, 255), (255, 0, 0), (0, 0, 255)]):
        assert actual['page'] == expected['region']['page']
        assert actual['box'] == expected['region']['box']
        assert f"材料页 {actual['page']}" in notebook
        with Image.open(folder / actual['file']) as crop:
            pixel = crop.convert('RGB').getpixel((crop.width // 2, crop.height // 2))
            assert all(abs(a - b) <= 3 for a, b in zip(pixel, color))
    assert '唯一练习目的' in (folder / 'answers.html').read_text()
    student = (folder / 'practice.html').read_text()
    assert '唯一练习目的' not in student and '唯一答案' not in student and '唯一批注' not in student


def test_reused_evidence_has_unique_per_question_links(evidence_export):
    ws, record, evidence = evidence_export
    first = ws.save_record(record)
    record.question_label = '第2题'
    second = ws.save_record(record)
    output = ws.export_errorbook([first['id'], second['id']], include_drafts=True)
    parser = EvidenceLinks()
    parser.feed((Path(output['folder']) / 'errorbook.html').read_text())
    assert len(parser.ids) == 6 and len(set(parser.ids)) == 6
    assert all(target in parser.ids for target in parser.links)
    assert len(list(Path(output['folder']).glob('ev_*.png'))) == 3


def test_absent_student_answer_and_judgments_are_not_inferred(evidence_export):
    ws, record, evidence = evidence_export
    record.student_answer = ''
    record.teacher_verdict = 'unmarked'
    record.agent_verdict = 'uncertain'
    saved = ws.save_record(record)
    output = ws.export_errorbook([saved['id']], include_drafts=True)
    page = (Path(output['folder']) / 'errorbook.html').read_text()
    assert '未记录作答' in page and '不能据此认定未作答' in page
    assert '卷面批改（录入）：未见批改' in page
    assert 'Agent判断：待确认' in page


@pytest.mark.parametrize('teacher,conflict', [
    ('correct', True), ('partial', True), ('wrong', False), ('unmarked', False), ('uncertain', False),
])
def test_unanswered_conflicts_with_recorded_credit_not_every_teacher_state(evidence_export, teacher, conflict):
    ws, record, _ = evidence_export
    record.student_answer = ''
    record.teacher_verdict = teacher
    record.agent_verdict = 'unanswered'
    record.analysis = None
    record.exercises = []
    saved = ws.save_record(record)
    before = ws.read_record(saved['id'])
    output = ws.export_errorbook([saved['id']], include_drafts=True)
    folder = Path(output['folder'])
    notebook = (folder / 'errorbook.html').read_text()
    assert ('两侧判断不一致，需结合证据核对。' in notebook) is conflict
    assert 'Agent判断：未作答' in notebook
    assert '未记录作答；不能据此认定未作答。' in notebook
    assert ws.read_record(saved['id']) == before
    exported = json.loads((folder / 'manifest.json').read_text())['records'][0]
    assert exported['id'] == saved['id'] and exported['revision'] == saved['revision']
    for filename in ('practice.html', 'answers.html'):
        assert '两侧判断不一致' not in (folder / filename).read_text()
