import logging
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from legal_rag.config import settings
from legal_rag.contracts import QueryInput
from legal_rag.infrastructure import db
from legal_rag.infrastructure.http import ProviderError
from legal_rag.infrastructure.redis import RELEASE_LOCK, redis_client
from legal_rag.tasks.celery_app import celery
from legal_rag.workflow.rag_flow import OUTCOMES, run

logger = logging.getLogger(__name__)


@celery.task(name="legal_rag.tasks.answer_query", soft_time_limit=230, time_limit=240)
def answer_query(query_id):
    row = db.one(
        """UPDATE queries SET status='running' WHERE id=%s AND status='queued' RETURNING *""", (query_id,)
    )
    if not row:
        return
    try:
        snapshot = (
            db.one("SELECT * FROM corpus_snapshots WHERE id=%s", (row["snapshot_id"],))
            if row["snapshot_id"]
            else None
        )

        def progress(message):
            db.execute("UPDATE queries SET progress=%s WHERE id=%s AND status='running'", (message, query_id))

        result = run(QueryInput.model_validate(row["request"]), snapshot, progress)
        OUTCOMES.labels(result.status).inc()
        if result.error_code:
            logger.warning("query_unavailable query_id=%s code=%s", query_id, result.error_code)
        db.execute(
            """UPDATE queries SET status='completed', result=%s, error_code=%s, finished_at=now()
                   WHERE id=%s AND status='running'""",
            (Jsonb(result.model_dump(mode="json")), result.error_code, query_id),
        )
    except Exception as exc:
        # Task boundary: redact input, tokens and provider response bodies.
        code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
        logger.error("query_failed query_id=%s code=%s", query_id, code)
        db.execute(
            "UPDATE queries SET status='failed',error_code=%s,finished_at=now() WHERE id=%s AND status='running'",
            (code, query_id),
        )
    finally:
        redis_client().eval(RELEASE_LOCK, 1, "active-query:" + str(row["user_id"]), query_id)


@celery.task(name="legal_rag.tasks.ingest_source", soft_time_limit=7100, time_limit=7200)
def ingest_source(run_id):
    from legal_rag.ingestion.pipeline import ingest

    ingest(run_id)


@celery.task(name="legal_rag.tasks.daily_update")
def daily_update():
    for source in ("vanban", "congbao"):
        run_id = uuid4()
        db.execute("INSERT INTO ingestion_runs(id,source) VALUES(%s,%s)", (run_id, source))
        ingest_source.delay(str(run_id))


@celery.task(name="legal_rag.tasks.maintenance")
def maintenance():
    config = settings()
    stale = db.fetch(
        """UPDATE queries SET status='failed',error_code='timeout',finished_at=now()
                      WHERE status IN ('queued','running') AND created_at < %s RETURNING id,user_id""",
        (datetime.now(ZoneInfo("UTC")) - timedelta(seconds=config.query_timeout_seconds + 60),),
    )
    for row in stale:
        redis_client().eval(RELEASE_LOCK, 1, "active-query:" + str(row["user_id"]), str(row["id"]))
    cutoff = datetime.now(ZoneInfo("UTC")) - timedelta(days=config.history_retention_days)
    db.execute("DELETE FROM queries WHERE created_at < %s", (cutoff,))
    db.execute(
        "DELETE FROM conversations c WHERE created_at < %s AND NOT EXISTS(SELECT 1 FROM queries q WHERE q.conversation_id=c.id)",
        (cutoff,),
    )
    db.execute(
        "UPDATE ingestion_runs SET status='failed',error_code='worker_timeout',finished_at=now() WHERE status='running' AND started_at < %s",
        (datetime.now(ZoneInfo("UTC")) - timedelta(seconds=7500),),
    )
