from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from legal_rag.api.security import (
    current_user,
)
from legal_rag.infrastructure import db

router = APIRouter()


@router.get("/api/v1/documents")
def documents(q: str = Query("", max_length=200), user=Depends(current_user)):
    return db.fetch(
        """SELECT d.id,d.title,d.number FROM documents d JOIN snapshot_members sm ON sm.document_id=d.id
                      JOIN active_corpus a ON a.snapshot_id=sm.snapshot_id WHERE d.title ILIKE %s OR d.number ILIKE %s
                      ORDER BY d.number,d.id LIMIT 30""",
        ("%" + q + "%", "%" + q + "%"),
    )


@router.get("/api/v1/documents/{document_id}/versions")
def versions(document_id: UUID, user=Depends(current_user)):
    return db.fetch(
        """SELECT v.id,v.created_at,v.effective_from,v.effective_to,v.temporal_status,v.source_url
                      FROM document_versions v WHERE v.document_id=%s
                      AND EXISTS(SELECT 1 FROM snapshot_members sm WHERE sm.version_id=v.id)
                      ORDER BY v.created_at LIMIT 100""",
        (document_id,),
    )


@router.get("/api/v1/versions/{version_id}")
def version(version_id: UUID, user=Depends(current_user)):
    row = db.one(
        """SELECT v.id,v.raw_text,v.source_url,v.effective_from,v.effective_to,v.temporal_status,d.title,d.number
                  FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.id=%s
                  AND EXISTS(SELECT 1 FROM snapshot_members sm WHERE sm.version_id=v.id)""",
        (version_id,),
    )
    if not row:
        raise HTTPException(404)
    return row
