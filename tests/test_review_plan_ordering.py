from unittest.mock import patch

import pytest
from test_review_plans import planned_workspace as _planned_workspace

from study_errorbook.models import DomainError, PracticeFeedback, ReviewPlanChange
from study_errorbook.service import Workspace
from study_errorbook.textin import TextIn

planned_workspace = _planned_workspace


def complete(ws, record, revision, attempt):
    return ws.update_review_plan(record['id'], 2, revision, ReviewPlanChange(
        action='complete', attempt_id=attempt['id'], user_note='Synthetic ordering check.'))


@pytest.mark.parametrize('old_time,scheduled_time', [(1000.0, 1000.0), (2000.0, 1000.0)])
def test_old_attempt_cannot_complete_plan_when_wall_clock_is_equal_or_moves_back(planned_workspace, old_time, scheduled_time):
    ws, _, record, change = planned_workspace
    with patch('study_errorbook.service.time.time', return_value=old_time):
        old = ws.record_practice(record['id'], 2, None, 'Synthetic old response', 'original')
    with patch('study_errorbook.service.time.time', return_value=scheduled_time):
        ws.update_review_plan(record['id'], 2, 0, change)
    with pytest.raises(DomainError, match='attempt_plan_mismatch'):
        complete(ws, record, 1, old)
    assert ws.read_review_plan(record['id'])['current_plan_revision'] == 1


def test_new_attempt_can_complete_plan_after_wall_clock_moves_back(planned_workspace):
    ws, _, record, change = planned_workspace
    with patch('study_errorbook.service.time.time', return_value=2000.0):
        ws.update_review_plan(record['id'], 2, 0, change)
    with patch('study_errorbook.service.time.time', return_value=1000.0):
        new = ws.record_practice(record['id'], 2, None, 'Synthetic later response', 'original')
    assert complete(ws, record, 1, new)['status'] == 'completed'


def test_feedback_update_does_not_make_old_attempt_new(planned_workspace):
    ws, _, record, change = planned_workspace
    with patch('study_errorbook.service.time.time', return_value=1000.0):
        old = ws.record_practice(record['id'], 2, None, 'Synthetic old response', 'original')
        ws.update_review_plan(record['id'], 2, 0, change)
        ws.review_practice_attempt(old['id'], 0, PracticeFeedback(agent_verdict='uncertain', feedback='Synthetic feedback.', next_step='Check evidence.'))
    with pytest.raises(DomainError, match='attempt_plan_mismatch'):
        complete(ws, record, 1, old)


def test_reschedule_requires_another_new_attempt_even_at_same_timestamp(planned_workspace):
    ws, _, record, change = planned_workspace
    with patch('study_errorbook.service.time.time', return_value=1000.0):
        ws.update_review_plan(record['id'], 2, 0, change)
        first = ws.record_practice(record['id'], 2, None, 'Synthetic first response', 'original')
        assert complete(ws, record, 1, first)['status'] == 'completed'
        ws.update_review_plan(record['id'], 2, 2, change)
        with pytest.raises(DomainError, match='attempt_plan_mismatch'):
            complete(ws, record, 3, first)
        second = ws.record_practice(record['id'], 2, None, 'Synthetic second response', 'original')
        assert complete(ws, record, 3, second)['completion_attempt_id'] == second['id']


def test_ordering_persists_across_connections_and_restart(planned_workspace):
    ws, _, record, change = planned_workspace
    with patch('study_errorbook.service.time.time', return_value=3000.0):
        ws.update_review_plan(record['id'], 2, 0, change)
    peer = Workspace(ws.root, ws.input_root, TextIn('', ''))
    try:
        with patch('study_errorbook.service.time.time', return_value=2000.0):
            attempt = peer.record_practice(record['id'], 2, None, 'Synthetic peer response', 'original')
    finally:
        peer.close()
    ws.close()
    restarted = Workspace(ws.root, ws.input_root, TextIn('', ''))
    try:
        assert complete(restarted, record, 1, attempt)['completion_attempt_id'] == attempt['id']
    finally:
        restarted.close()


def test_deleting_higher_unrelated_rows_keeps_new_attempt_after_plan(planned_workspace):
    ws, _, record, change = planned_workspace
    with patch('study_errorbook.service.time.time', return_value=1000.0):
        ws.update_review_plan(record['id'], 2, 0, change)
        ws.check_exercise('x+x', '2*x')
        # Simulate deletion of unrelated higher rows; the still-live plan anchors ordering.
        with ws.db:
            ws.db.execute("DELETE FROM objects WHERE kind='check'")
        attempt = ws.record_practice(record['id'], 2, None, 'Synthetic subsequent response', 'original')
        assert complete(ws, record, 1, attempt)['status'] == 'completed'
