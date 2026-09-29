import json
import os
import subprocess
import sys
from contextlib import closing

import pytest
from PIL import Image

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


@pytest.mark.parametrize('kind', ['directory', 'missing', 'socket'])
def test_unavailable_import_does_not_change_records(tmp_path, monkeypatch, kind):
    import socket

    source = tmp_path / 'worksheet.png'
    listener = None
    if kind == 'directory':
        source.mkdir()
    elif kind == 'socket':
        listener = socket.socket(socket.AF_UNIX)
        monkeypatch.chdir(tmp_path)
        listener.bind(source.name)
    try:
        with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
            before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
            with pytest.raises(DomainError, match='file_unavailable'):
                ws.import_document(str(source))
            assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before
            assert not list(ws.root.glob('doc_*'))
            assert not ws.status()['workspace_closed']
    finally:
        if listener:
            listener.close()


def test_fifo_import_returns_without_waiting_for_writer(tmp_path):
    fifo = tmp_path / 'worksheet.png'
    os.mkfifo(fifo)
    sample = tmp_path / 'real.png'
    Image.new('RGB', (20, 20), 'white').save(sample)
    program = '''
import json, sys
from contextlib import closing
from pathlib import Path
from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn
root = Path(sys.argv[1])
with closing(Workspace(root / 'state', root, TextIn('', ''))) as ws:
    try:
        ws.import_document(str(root / 'worksheet.png'))
    except DomainError as error:
        assert error.code == 'file_unavailable'
    else:
        raise AssertionError('FIFO accepted')
    assert not ws.db.execute('SELECT 1 FROM objects').fetchone()
    doc = ws.import_document(str(root / 'real.png'))
    assert doc['pages'] == 1
    print(json.dumps({'rejected_fifo': True, 'next_import_succeeded': True}))
'''
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['next_import_succeeded']


def test_in_root_symlink_keeps_existing_import_behavior(tmp_path):
    sample = tmp_path / 'real.png'
    Image.new('RGB', (20, 20), 'white').save(sample)
    link = tmp_path / 'alias.png'
    link.symlink_to(sample)
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
        first = ws.import_document(str(link))
        cached = ws.import_document(str(sample))
        assert cached['id'] == first['id'] and cached['cached']
