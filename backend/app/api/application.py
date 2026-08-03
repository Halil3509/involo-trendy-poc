from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.admin_brand_analysis import router as brand_analysis_router
from app.api.routes.admin_pipeline import router as pipeline_router
from app.api.routes.admin_profiling import router as profiling_admin_router
from app.api.routes.admin_scraper import router as admin_router
from app.api.routes.admin_stats import router as stats_router
from app.api.routes.admin_trend_content import router as trend_content_router
from app.api.routes.auth import router as auth_router
from app.api.routes.creator_logs import router as creator_logs_router
from app.api.routes.creators import router as creators_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.profiling import router as profiling_router
from app.api.routes.recommendations import router as recommendation_router
from app.api.routes.scraper_logs import router as scraper_logs_router
from app.core.config import get_settings
from app.core.security import hash_password
from app.infrastructure.resources import Resources, utcnow


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    resources = Resources(settings)
    await resources.connect()
    app.state.settings = settings
    app.state.resources = resources
    assert resources.db is not None
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        now = utcnow()
        await resources.db.users.update_one(
            {"email": settings.bootstrap_admin_email.lower()},
            {
                "$setOnInsert": {
                    "email": settings.bootstrap_admin_email.lower(),
                    "password_hash": hash_password(
                        settings.bootstrap_admin_password.get_secret_value()
                    ),
                    "role": "admin",
                    "created_at": now,
                    "updated_at": now,
                    "disabled": False,
                }
            },
            upsert=True,
        )
    yield
    await resources.close()


settings = get_settings()
app = FastAPI(title="Involo API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.middleware("http")
async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-XSS-Protection", "0")
    return response


app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
app.include_router(pipeline_router, prefix=settings.api_prefix)
app.include_router(profiling_router, prefix=settings.api_prefix)
app.include_router(profiling_admin_router, prefix=settings.api_prefix)
app.include_router(recommendation_router, prefix=settings.api_prefix)
app.include_router(stats_router, prefix=settings.api_prefix)
app.include_router(trend_content_router, prefix=settings.api_prefix)
app.include_router(scraper_logs_router, prefix=settings.api_prefix)
app.include_router(intelligence_router, prefix=settings.api_prefix)
app.include_router(brand_analysis_router, prefix=settings.api_prefix)
app.include_router(creator_logs_router, prefix=settings.api_prefix)
app.include_router(creators_router, prefix=settings.api_prefix)


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready(request: Request) -> dict[str, object]:
    checks = await request.app.state.resources.ready()
    if any(
        bool(check.get("required", True)) and not bool(check.get("ok"))
        for check in checks.values()
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}
