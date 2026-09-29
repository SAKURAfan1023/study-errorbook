"""Render three licensed public human InkML samples; --live uploads one page once.

Requires the reviewed local MathWriting excerpt/provenance in data/mathwriting-evaluation-v1.
No credentials are read. Repeated live runs reuse the persisted parse job, including failures.
This is a small diagnostic, not a handwriting accuracy benchmark or worksheet acceptance.
"""

import argparse
import asyncio
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

ROOT = Path(__file__).resolve().parents[1] / 'data/mathwriting-evaluation-v1'


def render():
    provenance = json.loads((ROOT / 'provenance.json').read_text())
    samples = provenance['samples']
    if len(samples) != 3:
        raise ValueError('The reviewed diagnostic contains exactly three samples')
    output = ROOT / 'inputs'
    output.mkdir(exist_ok=True)
    scale_factor = 3
    canvas = Image.new('RGB', (1400 * scale_factor, 1050 * scale_factor), 'white')
    draw = ImageDraw.Draw(canvas)
    boxes = []
    for index, sample in enumerate(samples):
        raw = (ROOT / Path(sample['archive_member']).name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sample['sha256']
        ink = ET.fromstring(raw)
        metadata = {node.attrib['type']: node.text for node in ink if node.tag.endswith('annotation')}
        assert metadata['inkCreationMethod'] == 'human' and metadata['splitTagOriginal'] == 'test'
        strokes = [[tuple(map(float, point.split()[:2])) for point in node.text.split(',')]
                   for node in ink if node.tag.endswith('trace')]
        points = [point for stroke in strokes for point in stroke]
        x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
        x1, y1 = max(p[0] for p in points), max(p[1] for p in points)
        scale = min(1200 / (x1-x0), 220 / (y1-y0))
        left, top = (1400 - (x1-x0)*scale)/2, index*350 + (350-(y1-y0)*scale)/2
        width = max(2, round(3*scale*scale_factor))
        for stroke in strokes:
            transformed = [((x-x0)*scale*scale_factor+left*scale_factor,
                            (y-y0)*scale*scale_factor+top*scale_factor) for x, y in stroke]
            draw.line(transformed, fill='black', width=width, joint='curve')
            radius = width/2
            for x, y in (transformed[0], transformed[-1]):
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill='black')
        boxes.append({'sample_id': metadata['sampleId'], 'normalized_label': metadata['normalizedLabel'],
                      'row':index, 'box':[0, index/3, 1, (index+1)/3]})
    canvas = canvas.resize((1400, 1050), Image.Resampling.LANCZOS)
    path = output / 'human-ink-three-expressions.png'
    canvas.save(path)
    manifest = {'rendered_source':str(path), 'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'samples':boxes, 'rendering':'Recorded human stroke XY coordinates, uniform fit per row, round black strokes on white; time ignored; no labels/answers rendered; 3x supersampling.',
                'license':provenance['license']}
    (ROOT / 'render.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest


async def evaluate(live):
    manifest = render()
    if not live:
        print(json.dumps({'rendered':manifest['rendered_source'], 'uploaded':False},ensure_ascii=False))
        return
    ws = Workspace(ROOT / 'workspace', ROOT / 'inputs', TextIn('', ''))
    try:
        doc = ws.import_document(manifest['rendered_source'])
        start = time.monotonic()
        job = await ws.parse_document(doc['id'], 1, 'free_sync')
        report = {'source_sha256':manifest['source_sha256'], 'document_id':doc['id'], 'job':job,
                  'elapsed_seconds':round(time.monotonic()-start,3), 'credentials_used':False,
                  'paid_calls':0, 'pages':1, 'human_samples':3,
                  'note':'Public human ink rendered into one diagnostic page; not a real graded worksheet; no automatic correctness score.'}
        if job['status'] in {'completed','partial'}:
            result = json.loads(Path(job['result_path']).read_text())
            report['elements'] = result.get('elements',[])
            report['page_metadata'] = result.get('pages',[])
        destination = ROOT / ('cached-report.json' if job.get('cached') else 'live-report.json')
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'report':str(destination),'status':job['status'],'cached':job.get('cached',False),
                          'elements':[{'index':i,'text':item.get('text'),'type':item.get('type'),'tags':item.get('tags')} for i,item in enumerate(report.get('elements',[]))]}, ensure_ascii=False,indent=2))
    finally:
        ws.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Upload the reviewed public handwriting page once using the free API; persisted jobs prevent resubmission.')
    asyncio.run(evaluate(parser.parse_args().live))
