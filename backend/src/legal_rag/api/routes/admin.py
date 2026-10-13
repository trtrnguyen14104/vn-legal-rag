from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb

from legal_rag.api.schemas import ReviewInput, RunInput
from legal_rag.api.security import (
    admin_mutation,
    admin_user,
)
from legal_rag.infrastructure import db

router = APIRouter()


@router.get("/api/v1/admin/ingestion")
def runs(user=Depends(admin_user)):
    return db.fetch("SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT 100")


@router.post("/api/v1/admin/ingestion", status_code=202)
def enqueue_ingestion(body: RunInput, user=Depends(admin_mutation)):
    from legal_rag.tasks.jobs import ingest_source

    run_id = uuid4()
    db.execute("INSERT INTO ingestion_runs(id,source) VALUES(%s,%s)", (run_id, body.source))
    try:
        ingest_source.delay(str(run_id))
    except Exception as exc:
        db.execute(
            "UPDATE ingestion_runs SET status='failed',error_code='dispatch_failed',finished_at=now() WHERE id=%s",
            (run_id,),
        )
        raise HTTPException(503, "Không thể xếp lịch nhập dữ liệu.") from exc
    return {"run_id": str(run_id)}


@router.get("/api/v1/admin/reviews")
def reviews(user=Depends(admin_user)):
    return db.fetch("""SELECT v.id,d.title,d.number,v.metadata,v.source_url,v.temporal_status
                      FROM document_versions v JOIN documents d ON d.id=v.document_id
                      WHERE v.review_status='pending' ORDER BY v.created_at LIMIT 100""")


@router.post("/api/v1/admin/reviews/{version_id}")
def review(version_id: UUID, body: ReviewInput, user=Depends(admin_mutation)):
    from legal_rag.domain.legal import date_strings, parse_date
    from legal_rag.ingestion.pipeline import publish_snapshot

    if body.approved:
        try:
            dates = {parse_date(value) for value in date_strings(body.evidence)}
        except ValueError as exc:
            raise HTTPException(422, "Ngày trong bằng chứng không hợp lệ.") from exc
        if any(value and value not in dates for value in (body.effective_from, body.effective_to)):
            raise HTTPException(422, "Bằng chứng phải chứa ngày hiệu lực đã khai báo.")
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM document_versions WHERE id=%s FOR UPDATE", (version_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["review_status"] != "pending":
            raise HTTPException(409, "Phiên bản đã xử lý; không sửa dữ liệu của snapshot đã lưu.")
        start = body.effective_from or row["effective_from"]
        end = body.effective_to or row["effective_to"]
        if start and end and end <= start:
            raise HTTPException(422, "Khoảng hiệu lực không hợp lệ.")
        if body.approved and (
            row["metadata"].get("source_kind") not in ("legislation", "consolidated")
            or row["metadata"].get("scope") != "central"
        ):
            raise HTTPException(422, "Chỉ duyệt văn bản pháp luật trung ương có nguồn xuất xứ phù hợp.")
        metadata = row["metadata"] | {"review_evidence": body.evidence, "reviewer_id": user["id"]}
        if body.effective_from:
            metadata["effective_from_evidence"] = body.evidence
        if body.effective_to:
            metadata["effective_to_evidence"] = body.evidence
        conn.execute(
            """UPDATE document_versions SET review_status=%s,metadata=%s,effective_from=%s,
                     effective_to=%s,temporal_status=CASE WHEN temporal_status='conflict' THEN 'unknown' ELSE temporal_status END,
                     verified_at=now() WHERE id=%s""",
            (
                "approved" if body.approved else "rejected",
                Jsonb(metadata),
                body.effective_from or row["effective_from"],
                body.effective_to or row["effective_to"],
                version_id,
            ),
        )
    if body.approved:
        return {"snapshot_id": publish_snapshot(None, [str(version_id)], {})}
    return {"status": "rejected"}


@router.get("/api/v1/admin/snapshots")
def snapshots(user=Depends(admin_user)):
    return db.fetch("SELECT * FROM corpus_snapshots ORDER BY created_at DESC LIMIT 100")


@router.post("/api/v1/admin/snapshots/{snapshot_id}/activate")
def activate_snapshot(snapshot_id: UUID, user=Depends(admin_mutation)):
    with db.connection() as conn:
        if not conn.execute("SELECT id FROM corpus_snapshots WHERE id=%s", (snapshot_id,)).fetchone():
            raise HTTPException(404)
        conn.execute("UPDATE active_corpus SET snapshot_id=%s WHERE singleton", (snapshot_id,))
    return {"snapshot_id": str(snapshot_id)}
