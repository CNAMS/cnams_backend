"""Structured JSON logging — no PII. Logs record UUIDs, never names."""
import json
import logging
import sys
import time
from starlette.middleware.base import BaseHTTPMiddleware

SKIP_PATHS = {"/health", "/docs", "/openapi.json"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("method", "path", "status", "duration_ms", "worker_id", "device_id", "batch_size"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload)


def setup() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("cnams")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
    return root


logger = logging.getLogger("cnams")


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.monotonic()
        resp = await call_next(request)
        if request.url.path not in SKIP_PATHS:
            dur = int((time.monotonic() - start) * 1000)
            logger.info("request", extra={"method": request.method, "path": request.url.path,
                                          "status": resp.status_code, "duration_ms": dur})
        return resp
