import base64
import hashlib
import hmac
import json
import secrets
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from legal_rag.config import settings
from legal_rag.infrastructure import db
from legal_rag.infrastructure.redis import redis_client


def session_key(token):
    digest = hmac.new(
        settings().session_secret.get_secret_value().encode(), token.encode(), hashlib.sha256
    ).hexdigest()
    return "session:" + digest


def current_user(request: Request):
    token = request.cookies.get("legal_session", "")
    session = redis_client().get(session_key(token)) if token else None
    if not session:
        raise HTTPException(401, "Vui lòng đăng nhập Google để tiếp tục.")
    user = json.loads(session)
    if not db.one("SELECT id FROM users WHERE id=%s", (user["id"],)):
        raise HTTPException(401, "Phiên đăng nhập không còn hợp lệ.")
    user["is_admin"] = user["google_subject"] in settings().admin_google_subjects.split(",")
    return user


def mutation_user(request: Request, user=Depends(current_user)):
    if request.headers.get("origin") != settings().public_origin:
        raise HTTPException(403, "Origin không hợp lệ.")
    if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf"]):
        raise HTTPException(403, "CSRF token không hợp lệ.")
    return user


def admin_user(user=Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(403, "Cần quyền quản trị.")
    return user


def admin_mutation(user=Depends(mutation_user)):
    if not user["is_admin"]:
        raise HTTPException(403, "Cần quyền quản trị.")
    return user


def google_login(request):
    config = settings()
    if not config.google_client_id or not config.google_client_secret.get_secret_value():
        raise HTTPException(503, "Đăng nhập Google chưa được cấu hình.")
    client = redis_client()
    address = request.client.host if request.client else "unknown"
    key = "login-rate:" + hashlib.sha256(address.encode()).hexdigest()
    attempts = client.incr(key)
    if attempts == 1:
        client.expire(key, 3600)
    if attempts > 20:
        raise HTTPException(429, "Bạn đã thử đăng nhập quá nhiều lần.")
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    client.set("oauth:" + state, json.dumps({"nonce": nonce, "verifier": verifier}), ex=600)
    response = RedirectResponse(
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(
            {
                "client_id": config.google_client_id,
                "redirect_uri": config.google_redirect_uri,
                "response_type": "code",
                "scope": "openid profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    response.set_cookie(
        "legal_oauth_state",
        state,
        httponly=True,
        secure=config.environment == "production",
        samesite="lax",
        max_age=600,
        path="/api/v1/auth",
    )
    return response


def google_callback(request, code, state):
    config = settings()
    if not secrets.compare_digest(state, request.cookies.get("legal_oauth_state", "")):
        raise HTTPException(400, "OAuth state không hợp lệ.")
    raw = redis_client().getdel("oauth:" + state)
    if not raw:
        raise HTTPException(400, "Phiên đăng nhập đã hết hạn.")
    flow = json.loads(raw)
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret.get_secret_value(),
                    "redirect_uri": config.google_redirect_uri,
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": flow["verifier"],
                },
            )
            response.raise_for_status()
        claims = id_token.verify_oauth2_token(
            response.json()["id_token"], GoogleRequest(), config.google_client_id
        )
        if not secrets.compare_digest(claims.get("nonce", ""), flow["nonce"]):
            raise ValueError("nonce mismatch")
        subject = claims["sub"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(401, "Không thể xác minh đăng nhập Google.") from exc
    user = db.one(
        """INSERT INTO users(id,google_subject,name) VALUES(%s,%s,%s)
                  ON CONFLICT(google_subject) DO UPDATE SET name=EXCLUDED.name RETURNING *""",
        (uuid4(), subject, claims.get("name", "Người dùng")[:150]),
    )
    token = secrets.token_urlsafe(48)
    redis_client().set(
        session_key(token),
        json.dumps(
            {
                "id": str(user["id"]),
                "google_subject": subject,
                "name": user["name"],
                "csrf": secrets.token_urlsafe(32),
            }
        ),
        ex=config.session_ttl_seconds,
    )
    response = RedirectResponse(config.public_origin + "/", status_code=303)
    response.delete_cookie("legal_oauth_state", path="/api/v1/auth")
    response.set_cookie(
        "legal_session",
        token,
        httponly=True,
        secure=config.environment == "production",
        samesite="lax",
        max_age=config.session_ttl_seconds,
        path="/",
    )
    return response
