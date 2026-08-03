from typing import Any

from bson import ObjectId

from app.core.token_crypto import TokenCipher
from app.infrastructure.resources import Resources
from app.providers.embedding import build_embedding_provider
from app.providers.instagram_profile import build_instagram_profile_provider
from app.providers.media import build_media_provider
from app.providers.profile_summary import build_profile_summary_provider
from app.providers.transcription import build_transcription_provider
from app.providers.vision import build_vision_provider
from app.services.multimodal import MultimodalService
from app.services.profiling import ProfilingService
from app.workers.celery_app import RETRY_KWARGS, celery_app, settings
from app.workers.runtime import PROFILE_ALL_LOCK, execute_job, run_locked


def _profiling_service(resources: Resources) -> ProfilingService:
    assert resources.db is not None
    assert resources.qdrant is not None
    return ProfilingService(
        resources.db,
        resources.qdrant,
        settings,
        build_instagram_profile_provider(settings, redis=resources.redis),
        build_transcription_provider(settings),
        MultimodalService(
            resources.db,
            resources.qdrant,
            settings,
            build_media_provider(settings),
            build_vision_provider(settings),
            build_embedding_provider(settings),
        ),
        build_profile_summary_provider(settings),
        TokenCipher(settings.instagram_token_encryption_key.get_secret_value()),
    )


async def _profile_user(resources: Resources, user_id: str) -> dict[str, int]:
    return await _profiling_service(resources).run(ObjectId(user_id))


@celery_app.task(bind=True, name="app.tasks.profile_user", **RETRY_KWARGS)  # type: ignore[untyped-decorator]
def profile_user(self: Any, user_id: str) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "profile_user",
        f"involo:profiling:user:{user_id}",
        {"processed": 0},
        execute_job(task_id, "profile_user", lambda r: _profile_user(r, user_id)),
    )


async def _profile_all(resources: Resources) -> dict[str, int]:
    assert resources.db is not None
    assert resources.redis is not None
    counters = {"users": 0, "succeeded": 0, "failed": 0, "processed": 0}
    service = _profiling_service(resources)
    async for connection in resources.db.instagram_connections.find(
        {"status": {"$ne": "needs_reauth"}}
    ):
        counters["users"] += 1
        user_id = connection["user_id"]
        lock = resources.redis.lock(
            f"involo:profiling:user:{user_id}", timeout=60 * 60, blocking_timeout=0
        )
        acquired = await lock.acquire(blocking=False)
        if not acquired:
            counters["failed"] += 1
            continue
        try:
            result = await service.run(user_id)
            counters["processed"] += result.get("processed", 0)
            counters["succeeded"] += 1
        except Exception:  # noqa: BLE001 - per-user failure is persisted by service
            counters["failed"] += 1
        finally:
            await lock.release()
    return counters


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, name="app.tasks.profile_all_users"
)
def profile_all_users(self: Any) -> dict[str, int]:
    task_id = self.request.id
    return run_locked(
        task_id,
        "profile_all",
        PROFILE_ALL_LOCK,
        {"users": 0},
        execute_job(task_id, "profile_all", _profile_all),
    )
