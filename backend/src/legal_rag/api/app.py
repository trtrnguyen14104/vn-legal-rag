import secrets
import time

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

from legal_rag.api.routes import admin, auth, chat, documents
from legal_rag.config import settings
from legal_rag.infrastructure import db
from legal_rag.infrastructure.redis import redis_client
from legal_rag.retrieval import retriever as retrieval

app = FastAPI(
    title="Vietnamese Legal RAG", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
)


HTTP_LATENCY = Histogram("legal_http_seconds", "HTTP latency", ["method", "route"])


@app.middleware("http")
async def observe(request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    route = request.scope.get("route")
    HTTP_LATENCY.labels(request.method, route.path if route else "unmatched").observe(
        time.monotonic() - started
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/v1/health")
def health():
    try:
        db.one("SELECT 1")
        redis_client().ping()
    except Exception as exc:
        raise HTTPException(503, "Hệ thống đang khởi động hoặc chưa kết nối được kho dữ liệu.") from exc
    return {"status": "ready"}


@app.get("/api/v1/corpus")
def corpus():
    snapshot = retrieval.active_snapshot()
    checks = db.fetch("SELECT source,checked_at,status FROM source_checkpoints ORDER BY source")
    count = (
        db.one("SELECT count(*) total FROM snapshot_members WHERE snapshot_id=%s", (snapshot["id"],))
        if snapshot
        else None
    )
    return {
        "snapshot_id": snapshot["id"] if snapshot else None,
        "updated_at": snapshot["created_at"] if snapshot else None,
        "scope": "Văn bản trung ương",
        "document_count": count["total"] if count else 0,
        "sources": checks,
    }


@app.get("/metrics")
def metrics(request: Request):
    if not secrets.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + settings().embedding_api_key.get_secret_value()
    ):
        raise HTTPException(401)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


for router in (auth.router, chat.router, documents.router, admin.router):
    app.include_router(router)
