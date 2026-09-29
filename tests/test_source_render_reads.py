import io
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from PIL import Image

from study_errorbook.models import DomainError, Region
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.mark.parametrize('format', ['PNG', 'PDF'])
@pytest.mark.parametrize('operation', ['evidence', 'redact'])
@pytest.mark.parametrize('damage', ['directory', 'link'])
def test_original_source_requires_local_regular_file(tmp_path, format, operation, damage):
    sample = tmp_path / ('sample.' + format.lower())
    Image.new('RGB', (60, 40), 'red').save(sample, format=format)
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
        doc = ws.import_document(str(sample))
        target = Path(doc['source'])
        original = target.read_bytes()
        target.unlink()
        if damage == 'directory':
            target.mkdir()
        else:
            other = tmp_path / 'outside-source'
            other.write_bytes(original)
            target.symlink_to(other)
        before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
        region = Region(page=1, box=[0, 0, .5, .5])
        with pytest.raises(DomainError, match='document_source_unavailable'):
            if operation == 'evidence':
                ws.read_evidence(doc['id'], region)
            else:
                ws.redact_document(doc['id'], [region])
        assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before
        if damage == 'directory':
            target.rmdir()
        else:
            target.unlink()
        target.write_bytes(original)
        _, binary = ws.read_evidence(doc['id'], region)
        with Image.open(io.BytesIO(binary)) as image:
            assert image.width > 0 and image.height > 0


@pytest.mark.parametrize('format', ['PNG', 'PDF'])
def test_changed_redaction_cannot_be_viewed_or_redacted_again(tmp_path, format):
    sample = tmp_path / ('sample.' + format.lower())
    Image.new('RGB', (60, 40), 'white').save(sample, format=format)
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
        doc = ws.import_document(str(sample))
        region = Region(page=1, box=[0, 0, .5, .5])
        redacted = ws.redact_document(doc['id'], [region])
        Image.new('RGB', (60, 40), 'red').save(redacted['source'], format=format)
        for operation in (
            lambda: ws.read_evidence(redacted['id'], region),
            lambda: ws.redact_document(redacted['id'], [region]),
        ):
            with pytest.raises(DomainError, match='redacted_copy_changed'):
                operation()
        assert not ws._all('evidence')


@pytest.mark.parametrize('format', ['PNG', 'PDF'])
def test_original_fifo_returns_error_without_blocking(tmp_path, format):
    sample = tmp_path / ('sample.' + format.lower())
    Image.new('RGB', (60, 40), 'white').save(sample, format=format)
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
        doc = ws.import_document(str(sample))
        target = Path(doc['source'])
        target.unlink()
        os.mkfifo(target)
    program = '''
import sys
from contextlib import closing
from pathlib import Path
from study_errorbook.models import DomainError, Region
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
root = Path(sys.argv[1])
region = Region(page=1, box=[0, 0, 1, 1])
with closing(Workspace(root / 'state', root, TextIn('', ''))) as ws:
    for action in (lambda: ws.read_evidence(sys.argv[2], region),
                   lambda: ws.redact_document(sys.argv[2], [region])):
        try:
            action()
        except DomainError as error:
            assert error.code == 'document_source_unavailable'
        else:
            raise AssertionError('FIFO accepted')
    assert not ws._all('evidence') and len(ws._all('document')) == 1
'''
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path), doc['id']],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
