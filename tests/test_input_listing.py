import os
from contextlib import closing

import pytest
from PIL import Image

from study_errorbook.models import DomainError
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn


def test_input_candidates_page_then_import_without_reading_other_files(tmp_path):
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    Image.new('RGB', (20, 20), 'white').save(inbox / '数学 作业.PNG')
    (inbox / 'a.pdf').write_bytes(b'not actually a PDF')
    (inbox / 'b.jpg').touch()
    with (inbox / 'c.jpeg').open('wb') as large:
        large.truncate(20 * 1024 * 1024 + 1)
    (inbox / '.hidden.png').write_bytes(b'private')
    (inbox / 'notes.txt').write_text('not a worksheet')
    (inbox / 'nested').mkdir()
    (inbox / 'nested' / 'child.pdf').write_bytes(b'not enumerated')
    (inbox / 'alias.png').symlink_to(inbox / '数学 作业.PNG')
    (inbox / 'outside.pdf').symlink_to(tmp_path / 'outside.pdf')
    os.mkfifo(inbox / 'pipe.png')
    with closing(Workspace(tmp_path / 'state', inbox, TextIn('', ''))) as ws:
        before = ws.db.execute('SELECT rowid,* FROM objects').fetchall()
        first = ws.list_input_files(limit=2)
        second = ws.list_input_files(offset=first['next_offset'], limit=2)
        assert first['total'] == second['total'] == 4
        assert second['next_offset'] is None
        items = first['items'] + second['items']
        assert [x['name'] for x in items] == ['a.pdf', 'b.jpg', 'c.jpeg', '数学 作业.PNG']
        assert [x['size_within_limit'] for x in items] == [True, False, False, True]
        assert ws.list_input_files(offset=4)['items'] == []
        assert ws.db.execute('SELECT rowid,* FROM objects').fetchall() == before
        assert ws.list_documents()['total'] == 0
        with pytest.raises(DomainError, match='unsupported_document'):
            ws.import_document(items[0]['path'])
        document = ws.import_document(items[-1]['path'])
        assert document['name'] == '数学 作业.PNG' and document['pages'] == 1
        assert ws.list_input_files()['total'] == 4  # No content/hash scan to infer import status.


@pytest.mark.parametrize('input_state', ['unconfigured', 'missing', 'replaced_with_link'])
def test_input_listing_unavailable_root(tmp_path, input_state):
    inbox = tmp_path / 'inbox'
    if input_state == 'replaced_with_link':
        inbox.mkdir()
    with closing(Workspace(tmp_path / 'state', None if input_state == 'unconfigured' else inbox, TextIn('', ''))) as ws:
        if input_state == 'replaced_with_link':
            inbox.rmdir()
            inbox.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(DomainError, match='input_root_missing|input_root_unavailable'):
            ws.list_input_files()
        assert ws.list_documents()['total'] == 0


@pytest.mark.parametrize('offset,limit', [(-1, 20), (0, 0), (0, 51), (True, 20), (0, 1.5)])
def test_input_listing_rejects_invalid_pagination(tmp_path, offset, limit):
    with closing(Workspace(tmp_path / 'state', tmp_path, TextIn('', ''))) as ws:
        with pytest.raises(DomainError, match='pagination'):
            ws.list_input_files(offset, limit)
