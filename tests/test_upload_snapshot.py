import hashlib
from contextlib import closing
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx
import pytest
from PIL import Image

from study_errorbook.models import Region
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.mark.parametrize('mode', ['free_sync', 'paid_async'])
@pytest.mark.parametrize('retry', [False, True])
async def test_upload_uses_checked_snapshot_after_source_replaced(tmp_path, monkeypatch, mode, retry):
    uploads = []

    async def provider(request):
        message = BytesParser(policy=policy.default).parsebytes(
            b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + await request.aread())
        file = next(part for part in message.iter_parts() if part.get_param('name', header='content-disposition') == 'file')
        uploads.append(file.get_payload(decode=True))
        if retry and len(uploads) == 1:
            return httpx.Response(200, json={'code': 40003})
        data = {'job_id': 'synthetic-snapshot'} if mode == 'paid_async' else {'success_count': 1, 'elements': []}
        return httpx.Response(200, json={'code': 200, 'data': data})

    sample = tmp_path / 'synthetic.png'
    Image.new('RGB', (60, 40), 'white').save(sample)
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('fake-id', 'fake-secret', httpx.MockTransport(provider)))) as ws:
        doc = ws.import_document(str(sample))
        redacted = ws.redact_document(doc['id'], [Region(page=1, box=[0, 0, .5, .5])])
        target = Path(redacted['source'])
        expected = target.read_bytes()
        replacement = Path(doc['source']).read_bytes()
        assert expected != replacement
        first = await ws.parse_document(redacted['id'], 1, mode) if retry else None
        put = ws._put

        def replace_after_marker(kind, key, body):
            put(kind, key, body)
            if kind == 'job' and body['status'] == 'submitting':
                target.write_bytes(replacement)

        monkeypatch.setattr(ws, '_put', replace_after_marker)
        if retry:
            result = await ws.retry_parse_document(first['id'], 1, 1, True, 'Synthetic explicit retry.')
        else:
            result = await ws.parse_document(redacted['id'], 1, mode)
        assert uploads[-1] == expected
        assert result['submitted_source_sha256'] == hashlib.sha256(expected).hexdigest()
        assert result['submitted_source_bytes'] == len(expected)
        assert target.read_bytes() == replacement
        assert len(uploads) == (2 if retry else 1)
        target.write_bytes(expected)
        assert (await ws.parse_document(redacted['id'], 1, mode))['cached']
        assert len(uploads) == (2 if retry else 1)
        if retry:
            assert ws.parse_attempt_history(first['id'])['items'][0] == first
