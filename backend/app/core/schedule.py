"""Cron scheduling helper for the DB-driven pipeline scheduler.

The Celery beat process ticks every minute and calls into ``cron_due`` to decide
whether a cron slot has just elapsed. Storing cron expressions in MongoDB (rather
than in Celery's static beat schedule) lets admins change the schedule from the UI
without restarting the workers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from croniter import croniter


def cron_due(
    expression: str | None,
    now: datetime,
    last_run: datetime | None,
    window: timedelta = timedelta(seconds=90),
) -> datetime | None:
    """Return the most recent cron slot if it is due, otherwise ``None``.

    A slot is considered due when it elapsed within ``window`` before ``now`` and
    has not already been dispatched (its time is newer than ``last_run``).
    """

    if not expression or not croniter.is_valid(expression):
        return None
    previous: datetime = croniter(expression, now).get_prev(datetime)
    if now - previous > window:
        return None
    if last_run is not None and previous <= last_run:
        return None
    return previous
