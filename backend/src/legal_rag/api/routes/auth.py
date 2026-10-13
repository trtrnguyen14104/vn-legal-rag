from fastapi import APIRouter, Depends, Query, Request, Response

from legal_rag.api.security import (
    current_user,
    google_callback,
    google_login,
    mutation_user,
    session_key,
)
from legal_rag.infrastructure.redis import redis_client

router = APIRouter()


@router.get("/api/v1/auth/login")
def login(request: Request):
    return google_login(request)


@router.get("/api/v1/auth/callback")
def callback(request: Request, code: str = Query(max_length=2048), state: str = Query(max_length=100)):
    return google_callback(request, code, state)


@router.get("/api/v1/auth/me")
def me(user=Depends(current_user)):
    return {
        "id": user["id"],
        "name": user["name"],
        "google_subject": user["google_subject"],
        "csrf_token": user["csrf"],
        "is_admin": user["is_admin"],
    }


@router.post("/api/v1/auth/logout", status_code=204)
def logout(request: Request, response: Response, user=Depends(mutation_user)):
    redis_client().delete(session_key(request.cookies["legal_session"]))
    response.delete_cookie("legal_session", path="/")
