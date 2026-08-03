from typing import Any

from app.infrastructure.resources import utcnow
from app.schemas.jobs import JobIntervention, JobProgress, JobResponse


def _integer_counters(value: Any) -> dict[str, int]:
    """Drop any counter entries that cannot be validated as integers."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(v, int)}


def _progress(value: Any) -> JobProgress | None:
    if not isinstance(value, dict):
        return None
    try:
        return JobProgress.model_validate(value)
    except ValueError:
        return None


def _intervention(value: Any) -> JobIntervention | None:
    if not isinstance(value, dict):
        return None
    try:
        return JobIntervention.model_validate(value)
    except ValueError:
        return None


def job_response(document: dict[str, Any]) -> JobResponse:
    return JobResponse(
        id=document["task_id"],
        kind=document.get("kind", "scrape"),
        state=document["state"],
        counters=_integer_counters(document.get("counters")),
        progress=_progress(document.get("progress")),
        intervention=_intervention(document.get("intervention")),
        error=document.get("error"),
        created_at=(
            document.get("created_at")
            or document.get("started_at")
            or document.get("finished_at")
            or utcnow()
        ),
        started_at=document.get("started_at"),
        finished_at=document.get("finished_at"),
        logs=document.get("logs", []),
        target_username=document.get("target_username"),
        requested_url=document.get("requested_url"),
    )
