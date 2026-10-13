import asyncio
import json
import time
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb

from legal_rag.api.schemas import FeedbackInput
from legal_rag.api.security import (
    current_user,
    mutation_user,
)
from legal_rag.config import settings
from legal_rag.contracts import QueryInput
from legal_rag.infrastructure import db
from legal_rag.infrastructure.redis import RELEASE_LOCK, RESERVE_QUERY, redis_client

router = APIRouter()


@router.post("/api/v1/queries", status_code=202)
def create_query(body: QueryInput, user=Depends(mutation_user)):
    from legal_rag.tasks.jobs import answer_query

    if body.conversation_id and not db.one(
        "SELECT id FROM conversations WHERE id=%s AND user_id=%s", (body.conversation_id, user["id"])
    ):
        raise HTTPException(404, "Không tìm thấy hội thoại.")
    query_id, conversation_id = uuid4(), body.conversation_id or uuid4()
    client = redis_client()
    quota_key = "quota:" + user["id"] + ":" + datetime.now(ZoneInfo("Asia/Saigon")).date().isoformat()
    lock_key = "active-query:" + user["id"]
    reserved = client.eval(
        RESERVE_QUERY,
        2,
        lock_key,
        quota_key,
        settings().daily_quota,
        str(query_id),
        settings().query_timeout_seconds + 120,
    )
    if reserved == -1:
        raise HTTPException(409, "Một câu hỏi đang được xử lý. Vui lòng chờ kết quả.")
    if reserved == -2:
        raise HTTPException(429, "Bạn đã dùng hết lượt hỏi hôm nay.")
    try:
        with db.connection() as conn:
            if not body.conversation_id:
                conn.execute(
                    "INSERT INTO conversations(id,user_id,title) VALUES(%s,%s,%s)",
                    (conversation_id, user["id"], body.question[:100]),
                )
            snapshot = conn.execute("SELECT snapshot_id FROM active_corpus WHERE singleton").fetchone()
            conn.execute(
                "INSERT INTO queries(id,user_id,conversation_id,request,snapshot_id) VALUES(%s,%s,%s,%s,%s)",
                (
                    query_id,
                    user["id"],
                    conversation_id,
                    Jsonb(body.model_dump(mode="json")),
                    snapshot["snapshot_id"],
                ),
            )
        answer_query.apply_async(args=[str(query_id)], task_id=str(query_id))
    except Exception as exc:
        client.eval(RELEASE_LOCK, 1, lock_key, str(query_id))
        client.decr(quota_key)
        db.execute(
            "UPDATE queries SET status='failed',error_code='dispatch_failed',finished_at=now() WHERE id=%s",
            (query_id,),
        )
        raise HTTPException(503, "Chưa thể nhận câu hỏi. Vui lòng thử lại.") from exc
    return {"query_id": str(query_id), "conversation_id": str(conversation_id)}


def owned_query(query_id, user_id):
    row = db.one("SELECT * FROM queries WHERE id=%s AND user_id=%s", (query_id, user_id))
    if not row:
        raise HTTPException(404, "Không tìm thấy câu hỏi.")
    return row


@router.get("/api/v1/queries/{query_id}/events")
async def events(query_id: UUID, request: Request, user=Depends(current_user)):
    await asyncio.to_thread(owned_query, query_id, user["id"])

    async def stream():
        previous = None
        deadline = time.monotonic() + settings().query_timeout_seconds + 120
        while time.monotonic() < deadline and not await request.is_disconnected():
            row = await asyncio.to_thread(owned_query, query_id, user["id"])
            if row["status"] == "completed":
                yield "event: result\ndata: " + json.dumps(row["result"], ensure_ascii=False) + "\n\n"
                return
            if row["status"] == "failed":
                yield 'event: failure\ndata: {"message":"Chưa thể hoàn tất câu hỏi. Vui lòng thử lại sau."}\n\n'
                return
            if row["progress"] != previous:
                previous = row["progress"]
                yield (
                    "event: progress\ndata: " + json.dumps({"message": previous}, ensure_ascii=False) + "\n\n"
                )
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)
        yield 'event: failure\ndata: {"message":"Thời gian xử lý đã hết. Bạn có thể xem lại lịch sử."}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.get("/api/v1/conversations")
def conversations(
    limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(current_user)
):
    return db.fetch(
        "SELECT id,title,created_at FROM conversations WHERE user_id=%s ORDER BY created_at DESC,id LIMIT %s OFFSET %s",
        (user["id"], limit, offset),
    )


@router.get("/api/v1/conversations/{conversation_id}")
def conversation(
    conversation_id: UUID,
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
):
    if not db.one("SELECT id FROM conversations WHERE id=%s AND user_id=%s", (conversation_id, user["id"])):
        raise HTTPException(404)
    return db.fetch(
        """SELECT * FROM (
               SELECT id,request,status,result,created_at FROM queries
               WHERE conversation_id=%s AND user_id=%s
               ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s
           ) page ORDER BY created_at,id""",
        (conversation_id, user["id"], limit, offset),
    )


@router.delete("/api/v1/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, user=Depends(mutation_user)):
    if not db.execute("DELETE FROM conversations WHERE id=%s AND user_id=%s", (conversation_id, user["id"])):
        raise HTTPException(404)


@router.post("/api/v1/queries/{query_id}/feedback", status_code=204)
def feedback(query_id: UUID, body: FeedbackInput, user=Depends(mutation_user)):
    owned_query(query_id, user["id"])
    db.execute(
        """INSERT INTO feedback(query_id,user_id,helpful,comment) VALUES(%s,%s,%s,%s)
               ON CONFLICT(query_id) DO UPDATE SET helpful=EXCLUDED.helpful,comment=EXCLUDED.comment""",
        (query_id, user["id"], body.helpful, body.comment),
    )
