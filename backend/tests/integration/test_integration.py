import concurrent.futures
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from legal_rag.cli import migrate
from legal_rag.config import settings
from legal_rag.infrastructure import db
from legal_rag.infrastructure.redis import RELEASE_LOCK, RESERVE_QUERY, redis_client
from legal_rag.ingestion import pipeline as ingestion

pytestmark = pytest.mark.skipif(
    os.environ.get("LEGAL_RAG_INTEGRATION") != "1", reason="Isolated PostgreSQL/Redis required"
)


@pytest.fixture(scope="module", autouse=True)
def database_schema():
    if os.environ.get("LEGAL_RAG_INTEGRATION") != "1":
        return
    assert "127.0.0.1:55432" in settings().database_url.get_secret_value()
    migrate()
    migrate()  # migration is repeatable


def make_version():
    document_id, version_id, unit_id = uuid4(), uuid4(), uuid4()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO documents(id,identity_key,number,title,kind) VALUES(%s,%s,'1/2020/NĐ-CP','Test','Nghị định')",
            (document_id, str(document_id)),
        )
        conn.execute(
            """INSERT INTO document_versions(id,document_id,content_hash,raw_text,source_url,review_status)
                     VALUES(%s,%s,%s,'Điều 1. Kiểm thử','https://vbpl.vn/test','approved')""",
            (version_id, document_id, str(version_id)),
        )
        conn.execute(
            "INSERT INTO legal_units(id,version_id,locator,ordinal,raw_text,start_offset,end_offset) VALUES(%s,%s,'Điều 1',0,'Kiểm thử',0,8)",
            (unit_id, version_id),
        )
        vector = [1.0] + [0.0] * 1023
        import json

        conn.execute(
            """INSERT INTO chunks(id,unit_id,version_id,ordinal,raw_text,normalized_text,segmented_text,token_count,embedding,embedding_revision)
                     VALUES(%s,%s,%s,0,'Kiểm thử','Kiểm thử','kiểm_thử',5,%s::vector,'test')""",
            (uuid4(), unit_id, version_id, json.dumps(vector)),
        )
    return document_id, version_id


def test_conversation_pagination_returns_recent_questions_and_enforces_ownership():
    from fastapi.testclient import TestClient

    from legal_rag.api.app import app
    from legal_rag.api.security import current_user

    user_id, conversation_id = uuid4(), uuid4()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO users(id,google_subject,name) VALUES(%s,%s,'Test')", (user_id, str(user_id))
        )
        conn.execute(
            "INSERT INTO conversations(id,user_id,title) VALUES(%s,%s,'History')", (conversation_id, user_id)
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO queries(id,user_id,conversation_id,request,created_at) VALUES(%s,%s,%s,%s,'2026-01-01'::timestamptz + %s * interval '1 second')",
                [(uuid4(), user_id, conversation_id, Jsonb({"question": str(i)}), i) for i in range(201)],
            )
    app.dependency_overrides[current_user] = lambda: {"id": str(user_id)}
    try:
        with TestClient(app) as client:
            page = client.get(f"/api/v1/conversations/{conversation_id}").json()
            assert len(page) == 200
            assert page[0]["request"]["question"] == "1"
            assert page[-1]["request"]["question"] == "200"
            older = client.get(f"/api/v1/conversations/{conversation_id}?offset=200").json()
            assert [r["request"]["question"] for r in older] == ["0"]
            app.dependency_overrides[current_user] = lambda: {"id": str(uuid4())}
            assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        db.execute("DELETE FROM users WHERE id=%s", (user_id,))


def test_review_rejects_interval_conflicting_with_existing_start():
    from fastapi.testclient import TestClient

    from legal_rag.api.app import app
    from legal_rag.api.security import admin_mutation

    _, version_id = make_version()
    db.execute(
        "UPDATE document_versions SET review_status='pending',effective_from='2025-01-01' WHERE id=%s",
        (version_id,),
    )
    app.dependency_overrides[admin_mutation] = lambda: {"id": str(uuid4()), "is_admin": True}
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/admin/reviews/{version_id}",
                json={
                    "approved": True,
                    "evidence": "Nguồn ghi ngày hết hiệu lực 01/01/2024.",
                    "effective_to": "2024-01-01",
                },
            )
            assert response.status_code == 422
            assert (
                db.one("SELECT review_status FROM document_versions WHERE id=%s", (version_id,))[
                    "review_status"
                ]
                == "pending"
            )
    finally:
        app.dependency_overrides.clear()


def test_publish_snapshot_is_atomic_and_old_snapshot_is_immutable(monkeypatch):
    document_id, version_id = make_version()
    first = ingestion.publish_snapshot(None, [str(version_id)], {})
    before = db.one("SELECT snapshot_id FROM active_corpus WHERE singleton")["snapshot_id"]
    original = db.connection

    class FailingConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=()):
            if "UPDATE active_corpus" in sql:
                raise RuntimeError("injected_publish_failure")
            return self.conn.execute(sql, params)

    @contextmanager
    def failing():
        with original() as conn:
            yield FailingConnection(conn)

    monkeypatch.setattr(db, "connection", failing)
    with pytest.raises(RuntimeError, match="injected"):
        ingestion.publish_snapshot(None, [], {})
    monkeypatch.setattr(db, "connection", original)
    assert db.one("SELECT snapshot_id FROM active_corpus WHERE singleton")["snapshot_id"] == before
    second = ingestion.publish_snapshot(None, [], {})
    assert first != second
    assert (
        db.one(
            "SELECT version_id FROM snapshot_members WHERE snapshot_id=%s AND document_id=%s",
            (first, document_id),
        )["version_id"]
        == version_id
    )


def test_concurrent_quota_allows_one_inflight_query():
    client = redis_client()
    namespace = "integration:" + str(uuid4())
    lock, quota = namespace + ":lock", namespace + ":quota"

    def reserve(index):
        return client.eval(RESERVE_QUERY, 2, lock, quota, 30, str(index), 60)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))
    assert results.count(1) == 1
    assert results.count(-1) == 7
    owner = client.get(lock)
    assert client.eval(RELEASE_LOCK, 1, lock, "wrong-owner") == 0
    assert client.get(lock) == owner
    client.eval(RELEASE_LOCK, 1, lock, owner)
    client.set(quota, 30, ex=60)
    assert reserve(9) == -2
    client.delete(lock, quota)


def test_query_captures_snapshot_when_created():
    user_id, conversation_id, query_id = uuid4(), uuid4(), uuid4()
    snapshot = db.one("SELECT snapshot_id FROM active_corpus WHERE singleton")["snapshot_id"]
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO users(id,google_subject,name) VALUES(%s,%s,'Test')", (user_id, str(user_id))
        )
        conn.execute(
            "INSERT INTO conversations(id,user_id,title) VALUES(%s,%s,'Test')", (conversation_id, user_id)
        )
        conn.execute(
            "INSERT INTO queries(id,user_id,conversation_id,request,snapshot_id) VALUES(%s,%s,%s,%s,%s)",
            (query_id, user_id, conversation_id, Jsonb({"question": "Kiểm thử snapshot"}), snapshot),
        )
    ingestion.publish_snapshot(None, [], {})
    assert db.one("SELECT snapshot_id FROM queries WHERE id=%s", (query_id,))["snapshot_id"] == snapshot
    db.execute("DELETE FROM users WHERE id=%s", (user_id,))
    assert db.one("SELECT id FROM queries WHERE id=%s", (query_id,)) is None


def test_unavailable_result_persists_error_and_releases_query_lock(monkeypatch):
    from datetime import date

    from legal_rag.contracts import QueryResult
    from legal_rag.tasks import jobs

    user_id, conversation_id, query_id = uuid4(), uuid4(), uuid4()
    client = redis_client()
    lock = "active-query:" + str(user_id)
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO users(id,google_subject,name) VALUES(%s,%s,'Test')", (user_id, str(user_id))
        )
        conn.execute(
            "INSERT INTO conversations(id,user_id,title) VALUES(%s,%s,'Test')", (conversation_id, user_id)
        )
        conn.execute(
            "INSERT INTO queries(id,user_id,conversation_id,request) VALUES(%s,%s,%s,%s)",
            (query_id, user_id, conversation_id, Jsonb({"question": "Synthetic legal question"})),
        )
    result = QueryResult(
        intent="legal_lookup",
        status="unavailable",
        as_of_date=date(2026, 10, 13),
        error_code="jev:unexpected_model",
    )
    monkeypatch.setattr(jobs, "run", lambda *args: result)
    client.set(lock, str(query_id), ex=60)
    try:
        jobs.answer_query.run(str(query_id))
        row = db.one("SELECT status,error_code,result,finished_at FROM queries WHERE id=%s", (query_id,))
        assert row["status"] == "completed"
        assert row["error_code"] == "jev:unexpected_model"
        assert row["result"]["status"] == "unavailable"
        assert "error_code" not in row["result"]
        assert row["finished_at"] is not None
        assert client.get(lock) is None
    finally:
        client.delete(lock)
        db.execute("DELETE FROM users WHERE id=%s", (user_id,))
