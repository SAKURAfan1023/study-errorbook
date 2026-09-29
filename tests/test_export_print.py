import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.models import Region, StudyRecord
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'img'} and 'src' in attrs:
            self.resources.append(attrs['src'])
        if tag == 'link' and 'href' in attrs:
            self.resources.append(attrs['href'])


def test_export_bundles_verified_offline_math_and_keeps_answers_separate(tmp_path):
    image = tmp_path / 'synthetic.png'
    Image.new('RGB', (100, 100), 'white').save(image)
    ws = Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))
    try:
        doc = ws.import_document(str(image))
        evidence, _ = ws.read_evidence(doc['id'], Region(page=1, box=[0, 0, 1, 1]))
        record = StudyRecord(
            document_id=doc['id'], question_label='公式导出测试',
            stem=r'计算 \(\frac{1}{2}+\sqrt{4}\)。价格$5保持原文。<script>alert(1)</script>',
            evidence_ids=[evidence['id']], exercises=[{
                'prompt': r'计算 \[\frac{1}{3}+\frac{1}{6}\]',
                'answer': r'唯一答案标记：\(\frac{1}{2}\)',
                'explanation': '独立答案说明', 'targets_error': '通分',
            }],
        )
        saved = ws.save_record(record)
        result = ws.export_errorbook([saved['id']], include_drafts=True)
        folder = Path(result['folder'])
        for page in map(Path, result['files']):
            content = page.read_text()
            links = Links()
            links.feed(content)
            assert links.resources and all('://' not in link for link in links.resources)
            assert all((folder / link).is_file() for link in links.resources)
            assert '待复核草稿' in content
        notebook = (folder / 'errorbook.html').read_text()
        assert r'\frac{1}{2}' in notebook and '$5' in notebook
        assert '<script>alert(1)</script>' not in notebook
        practice = (folder / 'practice.html').read_text()
        assert '唯一答案标记' not in practice and '独立答案说明' not in practice
        assert '答题区域' in practice
        assert '唯一答案标记' in (folder / 'answers.html').read_text()
        provenance = json.loads((folder / 'assets/katex/PROVENANCE.json').read_text())
        assert provenance['version'] == '0.18.9'
        for name, digest in provenance['sha256'].items():
            assert hashlib.sha256((folder / 'assets/katex' / name).read_bytes()).hexdigest() == digest
        assert (folder / 'assets/katex/LICENSE').is_file()
        assert ws.read_record(saved['id'])['revision'] == saved['revision']
    finally:
        ws.close()


@pytest.mark.parametrize("expression,answer,label", [
    ("2*(x+3)", "2*x+6", "表达式验算通过"),
    ("2*(x+3)", "2*x+3", "表达式验算未通过"),
    ("1/x", "1/x", "验算不支持此表达式"),
])
def test_export_reports_actual_check_outcome(tmp_path, expression, answer, label):
    source = tmp_path / "sample.png"
    Image.new("RGB", (100, 100), "white").save(source)
    ws = Workspace(tmp_path / "state", tmp_path, TextIn("", ""))
    try:
        doc = ws.import_document(str(source))
        evidence, _ = ws.read_evidence(doc["id"], Region(page=1, box=[0, 0, 1, 1]))
        check = ws.check_exercise(expression, answer)
        record = StudyRecord(
            document_id=doc["id"], question_label="验算标签", stem="合成样例",
            evidence_ids=[evidence["id"]], exercises=[{
                "prompt": expression, "answer": answer, "targets_error": "test",
                "check_id": check["id"],
            }],
        )
        saved = ws.save_record(record)
        output = ws.export_errorbook([saved["id"]], include_drafts=True)
        page = (Path(output["folder"]) / "answers.html").read_text()
        assert label in page
        if check["status"] != "equivalent":
            assert "表达式验算通过" not in page
    finally:
        ws.close()
