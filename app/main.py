""" growth-monitor-api — FastAPI entry. Base /v1, JSON, RFC3339 UTC."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import Base, engine
from app.logging_setup import AccessLogMiddleware, setup as setup_logging
from app.ratelimit import RateLimitMiddleware
from app.routers_auth import router as auth_router
from app.routers_core import router as core_router
from app.routers_health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_required()
    setup_logging()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AccessLogMiddleware)


@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError):
    # taxonomy: validation -> rejected-style 422 with first error, no body echo (no PII)
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse({"detail": "validation_error", "code": 422,
                         "loc": list(first.get("loc", [])), "msg": first.get("msg", "")}, status_code=422)

# dev/test convenience: create tables if absent (Alembic owns prod)
Base.metadata.create_all(bind=engine)

app.include_router(health_router)
app.include_router(auth_router, prefix="/v1")
app.include_router(core_router, prefix="/v1")
