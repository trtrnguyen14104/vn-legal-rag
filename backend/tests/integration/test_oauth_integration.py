import hashlib
import json
import os
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from legal_rag.api import security
from legal_rag.api.app import app
from legal_rag.cli import migrate
from legal_rag.infrastructure import db

pytestmark = pytest.mark.skipif(
    os.environ.get("LEGAL_RAG_INTEGRATION") != "1", reason="Isolated database/Redis required"
)


@pytest.fixture
def oauth(monkeypatch):
    subject = "oauth-test-" + uuid4().hex
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-only")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret-only")
    monkeypatch.setenv("ADMIN_GOOGLE_SUBJECTS", subject)
    migrate()
    redis = security.redis_client()
    rate_key = "login-rate:" + hashlib.sha256(b"testclient").hexdigest()
    redis.delete(rate_key)
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/login", follow_redirects=False)
        assert response.status_code == 307
        params = parse_qs(urlparse(response.headers["location"]).query)
        state = params["state"][0]
        flow = json.loads(redis.get("oauth:" + state))
        assert params["nonce"] == [flow["nonce"]]
        assert params["code_challenge_method"] == ["S256"]
        assert params["code_challenge"][0]
        real_client = httpx.Client
        requests = []

        def token_response(request):
            requests.append(request)
            assert str(request.url) == "https://oauth2.googleapis.com/token"
            assert flow["verifier"].encode() in request.content
            return httpx.Response(200, json={"id_token": "mock-token-only"})

        monkeypatch.setattr(
            security.httpx,
            "Client",
            lambda **kwargs: real_client(transport=httpx.MockTransport(token_response), **kwargs),
        )
        claims = {"sub": subject, "nonce": flow["nonce"], "name": "Test user"}

        def verify(token, request, audience):
            assert token == "mock-token-only"
            assert audience == "test-client-only"
            return claims

        monkeypatch.setattr(security.id_token, "verify_oauth2_token", verify)
        try:
            yield client, state, claims, requests
        finally:
            token = client.cookies.get("legal_session")
            if token:
                redis.delete(security.session_key(token))
            redis.delete("oauth:" + state, rate_key)
            db.execute("DELETE FROM users WHERE google_subject=%s", (subject,))


def test_oauth_callback_session_csrf_logout_and_replay(oauth):
    client, state, _, requests = oauth
    response = client.get(
        "/api/v1/auth/callback", params={"code": "test-code", "state": state}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    me = client.get("/api/v1/auth/me").json()
    assert me["is_admin"] is True
    assert len(requests) == 1
    assert (
        client.get("/api/v1/auth/callback", params={"code": "test-code", "state": state}).status_code == 400
    )
    assert len(requests) == 1
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost:3000", "X-CSRF-Token": me["csrf_token"]},
        ).status_code
        == 204
    )
    assert client.get("/api/v1/auth/me").status_code == 401


def test_oauth_invalid_state_never_exchanges_a_code(oauth):
    client, _, _, requests = oauth
    assert (
        client.get("/api/v1/auth/callback", params={"code": "test-code", "state": "invalid"}).status_code
        == 400
    )
    assert not requests


def test_oauth_nonce_mismatch_never_creates_user(oauth):
    client, state, claims, requests = oauth
    claims["nonce"] = "invalid"
    response = client.get(
        "/api/v1/auth/callback", params={"code": "test-code", "state": state}, follow_redirects=False
    )
    assert response.status_code == 401
    assert len(requests) == 1
    assert not db.one("SELECT id FROM users WHERE google_subject=%s", (claims["sub"],))
