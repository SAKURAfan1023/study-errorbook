import base64
import hashlib
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_clean_export import clean_workspace as _clean_workspace

from study_errorbook.models import DomainError, Exercise, PracticeFeedback

clean_workspace = _clean_workspace


@pytest.mark.parametrize('kind,index,presentation', [('exercise', 0, 'text'), ('original', None, 'text'), ('original', None, 'clean_images')])
def test_practice_metadata_cannot_leak_answer_labels(clean_workspace, kind, index, presentation):
    ws, record, clean = clean_workspace
    record.question_label = 'SECRET LABEL: answer is 42'
    record.subject = 'SECRET SUBJECT: use subtraction'
    saved = prepare(ws, record, clean)
    before = ws.db.execute('SELECT * FROM objects').fetchall()
    prompt, _ = ws.read_practice_prompt(saved['id'], 2, kind, index, presentation)
    assert 'SECRET' not in json.dumps(prompt)
    assert 'question_label' not in prompt and 'subject' not in prompt
    assert prompt['display_label'] == ('变式练习 1' if kind == 'exercise' else '原题重练')
    assert (prompt['record_id'], prompt['source_revision'], prompt['exercise_index']) == (saved['id'], 2, index)
    assert ws.read_record(saved['id'])['record']['question_label'] == record.question_label
    assert ws.db.execute('SELECT * FROM objects').fetchall() == before


def prepare(ws, record, clean, *, approve_images=True, approve_exercise=True):
    record.exercises = [Exercise(prompt='SYNTHETIC QUESTION ONLY', answer='SECRET REFERENCE ANSWER', explanation='SECRET EXPLANATION', targets_error='SECRET HINT')]
    saved = ws.save_record(record)
    approved = {record.evidence_ids[0]: hashlib.sha256(clean.read_bytes()).hexdigest()} if approve_images and record.clean_evidence_ids else {}
    return ws.review_record(saved['id'], 1, 'include', 'Synthetic fixture review.',
                            approved_clean_copies=approved, approved_exercise_indices=[0] if approve_exercise else [])


def test_exercise_prompt_excludes_answers_feedback_hints_and_history(clean_workspace):
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean)
    attempt = ws.record_practice(saved['id'], 2, 0, 'SECRET PRACTICE RESPONSE')
    ws.review_practice_attempt(attempt['id'], 0, PracticeFeedback(agent_verdict='uncertain', feedback='SECRET FEEDBACK', next_step='SECRET NEXT STEP'))
    before = ws.db.execute('SELECT * FROM objects').fetchall()
    prompt, images = ws.read_practice_prompt(saved['id'], 2, 'exercise', 0)
    assert prompt['prompt'] == 'SYNTHETIC QUESTION ONLY' and images == []
    assert 'SECRET' not in json.dumps(prompt)
    assert prompt['source_revision'] == 2
    assert ws.db.execute('SELECT * FROM objects').fetchall() == before


def test_original_text_is_explicitly_transcribed_without_original_image(clean_workspace):
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean)
    prompt, images = ws.read_practice_prompt(saved['id'], 2, 'original')
    assert prompt['prompt'] == record.stem and not images
    assert 'SECRET' not in json.dumps(prompt) and '仅转录文本' in prompt['material_note']


def test_clean_images_are_exact_approved_bytes_and_omit_transcription(clean_workspace):
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean)
    prompt, images = ws.read_practice_prompt(saved['id'], 2, 'original', presentation='clean_images')
    assert images == [clean.read_bytes()]
    assert prompt['images'][0]['sha256'] == hashlib.sha256(images[0]).hexdigest()
    assert prompt['prompt'] is None and record.stem not in json.dumps(prompt) and 'SECRET' not in json.dumps(prompt)
    assert str(clean) not in json.dumps(prompt)


@pytest.mark.parametrize('failure', ['unapproved', 'changed', 'missing', 'empty_selection', 'non_png', 'oversized'])
def test_clean_failures_never_fallback_to_answered_original(clean_workspace, failure):
    ws, record, clean = clean_workspace
    if failure == 'empty_selection':
        record.clean_evidence_ids = []
    saved = prepare(ws, record, clean, approve_images=failure != 'unapproved')
    if failure == 'non_png':
        clean.write_bytes(b'SYNTHETIC NOT AN IMAGE')
    if failure == 'oversized':
        clean.write_bytes(b'x' * (12 * 1024 * 1024 + 1))
    if failure == 'changed':
        clean.write_bytes(b'CHANGED')
    if failure == 'missing':
        clean.unlink()
    with pytest.raises(DomainError, match='clean_not_ready|practice_images_too_large'):
        ws.read_practice_prompt(saved['id'], 2, 'original', presentation='clean_images')


@pytest.mark.parametrize('params', [dict(expected_revision=1), dict(expected_revision=True), dict(practice_kind='original', exercise_index=0), dict(exercise_index=True), dict(exercise_index=-1), dict(exercise_index=5), dict(presentation='clean_images')])
def test_wrong_version_and_target_are_rejected(clean_workspace, params):
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean)
    kwargs = {'expected_revision': 2, 'practice_kind': 'exercise', 'exercise_index': 0} | params
    with pytest.raises(DomainError):
        ws.read_practice_prompt(saved['id'], **kwargs)


def test_exercise_and_record_reviews_are_required(clean_workspace):
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean, approve_exercise=False)
    with pytest.raises(DomainError, match='exercise_not_ready'):
        ws.read_practice_prompt(saved['id'], 2, 'exercise', 0)
    ws.review_record(saved['id'], 2, 'exclude', 'Synthetic exclusion.')
    with pytest.raises(DomainError, match='needs_review'):
        ws.read_practice_prompt(saved['id'], 3, 'original')


def test_question_changed_after_prompt_cannot_accept_stale_response(clean_workspace):
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean)
    prompt, _ = ws.read_practice_prompt(saved['id'], 2, 'original')
    record.stem = 'NEW QUESTION'
    ws.save_record(record, saved['id'], 2)
    with pytest.raises(DomainError, match='revision_conflict'):
        ws.record_practice(saved['id'], prompt['source_revision'], None, 'Synthetic response', 'original')


async def test_stdio_prompt_returns_real_image_content_and_no_answer_fields(clean_workspace, tmp_path):
    ws, record, clean = clean_workspace
    record.question_label = 'SECRET LABEL ANSWER'
    record.subject = 'SECRET SUBJECT HINT'
    saved = prepare(ws, record, clean)
    params = StdioServerParameters(command=sys.executable, args=['-m', 'study_errorbook.server'], env={
        'STUDY_DATA_DIR': str(ws.root), 'STUDY_INPUT_ROOT': str(tmp_path), 'STUDY_CONFIG_FILE': str(tmp_path / 'absent.env'),
        'TEXTIN_APP_ID': '', 'TEXTIN_SECRET_CODE': '',
    })
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            tool = next(t for t in tools.tools if t.name == 'read_practice_prompt')
            assert tool.annotations.readOnlyHint
            result = await client.call_tool('read_practice_prompt', {'record_id': saved['id'], 'expected_revision': 2, 'practice_kind': 'original', 'presentation': 'clean_images'})
            assert not result.isError
            assert [item.type for item in result.content] == ['text', 'image']
            assert base64.b64decode(result.content[1].data) == clean.read_bytes()
            assert 'SECRET' not in result.content[0].text and record.stem not in result.content[0].text


def test_multiple_clean_images_keep_selected_order(clean_workspace):
    from PIL import Image

    from study_errorbook.models import Region
    ws, record, clean = clean_workspace
    second, _ = ws.read_evidence(record.document_id, Region(page=1, box=[0, 0, .5, 1]))
    other = clean.with_name('synthetic-other.png')
    Image.new('RGB', (30, 30), 'yellow').save(other)
    ws._put('erase', second['id'], {'id':second['id'], 'status':'completed', 'path':str(other)})
    record.evidence_ids.append(second['id'])
    record.clean_evidence_ids = [second['id'], record.evidence_ids[0]]
    saved = ws.save_record(record)
    ws.review_record(saved['id'], 1, 'include', 'Synthetic ordered image review.', approved_clean_copies={
        second['id']:hashlib.sha256(other.read_bytes()).hexdigest(), record.evidence_ids[0]:hashlib.sha256(clean.read_bytes()).hexdigest()})
    prompt, images = ws.read_practice_prompt(saved['id'], 2, 'original', presentation='clean_images')
    assert images == [other.read_bytes(), clean.read_bytes()]
    assert [item['source_evidence_id'] for item in prompt['images']] == record.clean_evidence_ids


def test_fifo_image_is_rejected_without_blocking(clean_workspace):
    import os
    ws, record, clean = clean_workspace
    saved = prepare(ws, record, clean)
    clean.unlink()
    os.mkfifo(clean)
    with pytest.raises(DomainError, match='clean_not_ready'):
        ws.read_practice_prompt(saved['id'], 2, 'original', presentation='clean_images')
