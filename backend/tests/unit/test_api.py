from uuid import uuid4

from fastapi.testclient import TestClient

from legal_rag.api import app as module
from legal_rag.api.app import app
from legal_rag.api.security import admin_mutation, current_user, mutation_user

USER = {"id": str(uuid4()), "name": "Test", "google_subject": "test", "csrf": "csrf-only", "is_admin": False}


def test_authentication_required():
    with TestClient(app) as client:
        assert client.get("/api/v1/conversations").status_code == 401


def test_cross_account_query_and_history_are_hidden(monkeypatch):
    app.dependency_overrides[current_user] = lambda: USER
    monkeypatch.setattr(module.db, "one", lambda *args: None)
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/v1/conversations/{uuid4()}").status_code == 404
            assert client.get(f"/api/v1/queries/{uuid4()}/events").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_mutations_require_origin_and_csrf(monkeypatch):
    app.dependency_overrides[current_user] = lambda: USER
    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/auth/logout").status_code == 403
            assert (
                client.post(
                    "/api/v1/auth/logout",
                    headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "wrong"},
                ).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()


def test_admin_mutation_requires_admin_role():
    app.dependency_overrides[mutation_user] = lambda: USER
    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/admin/ingestion", json={"source": "hf"}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_review_cannot_assign_effective_date_absent_from_evidence():
    app.dependency_overrides[admin_mutation] = lambda: USER | {"is_admin": True}
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/admin/reviews/{uuid4()}",
                json={
                    "approved": True,
                    "evidence": "Nguồn đã được đối chiếu bằng tay.",
                    "effective_from": "2021-01-01",
                },
            )
            assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_unpublished_version_cannot_be_exposed(monkeypatch):
    app.dependency_overrides[current_user] = lambda: USER
    monkeypatch.setattr(module.db, "one", lambda *args: None)
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/v1/versions/{uuid4()}").status_code == 404
    finally:
        app.dependency_overrides.clear()
