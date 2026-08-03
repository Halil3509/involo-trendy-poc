from datetime import UTC, datetime

from app.core.schedule import cron_due


def test_due_when_slot_just_elapsed() -> None:
    now = datetime(2026, 7, 16, 5, 0, 30, tzinfo=UTC)
    due = cron_due("0 5 * * *", now, last_run=None)
    assert due == datetime(2026, 7, 16, 5, 0, tzinfo=UTC)


def test_not_due_when_slot_is_old() -> None:
    now = datetime(2026, 7, 16, 9, 0, 0, tzinfo=UTC)
    assert cron_due("0 5 * * *", now, last_run=None) is None


def test_not_due_when_already_dispatched() -> None:
    now = datetime(2026, 7, 16, 5, 0, 30, tzinfo=UTC)
    last = datetime(2026, 7, 16, 5, 0, 0, tzinfo=UTC)
    assert cron_due("0 5 * * *", now, last_run=last) is None


def test_due_again_next_day() -> None:
    now = datetime(2026, 7, 17, 5, 0, 20, tzinfo=UTC)
    last = datetime(2026, 7, 16, 5, 0, 0, tzinfo=UTC)
    due = cron_due("0 5 * * *", now, last_run=last)
    assert due == datetime(2026, 7, 17, 5, 0, tzinfo=UTC)


def test_invalid_or_empty_expression_is_never_due() -> None:
    now = datetime(2026, 7, 16, 5, 0, 30, tzinfo=UTC)
    assert cron_due(None, now, None) is None
    assert cron_due("not-a-cron", now, None) is None
