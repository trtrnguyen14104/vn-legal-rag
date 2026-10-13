import re

from legal_rag.domain.legal import NUMBER
from legal_rag.embedding.client import EmbeddingClient
from legal_rag.infrastructure import db
from legal_rag.retrieval.fusion import rrf

COLUMNS = """c.id, c.raw_text, c.version_id, d.id document_id, d.title, d.number,
 v.source_url, v.effective_from, v.effective_to, v.temporal_status, v.verified_at,
 u.locator, c.segmented_text"""
JOINS = """FROM chunks c JOIN document_versions v ON v.id=c.version_id
 JOIN documents d ON d.id=v.document_id JOIN legal_units u ON u.id=c.unit_id"""


def active_snapshot():
    return db.one(
        "SELECT s.* FROM active_corpus a JOIN corpus_snapshots s ON s.id=a.snapshot_id WHERE a.singleton"
    )


def document_candidates(question, snapshot_id):
    matches = NUMBER.findall(question)
    if matches:
        return db.fetch(
            """SELECT DISTINCT d.*, v.id version_id, v.effective_from, v.effective_to,
                         v.temporal_status, v.verified_at, v.metadata, v.source_url
                         FROM snapshot_members sm JOIN documents d ON d.id=sm.document_id
                         JOIN document_versions v ON v.id=sm.version_id
                         WHERE sm.snapshot_id=%s AND lower(d.number)=lower(%s)""",
            (snapshot_id, matches[0]),
        )
    abbreviated = re.search(r"(nghị định|luật|thông tư|quyết định)\s+(\d+)\b", question, re.I)
    if not abbreviated:
        return []
    kind, number = abbreviated.groups()
    return db.fetch(
        """SELECT d.*, v.id version_id, v.effective_from, v.effective_to,
                     v.temporal_status, v.verified_at, v.metadata, v.source_url
                     FROM snapshot_members sm JOIN documents d ON d.id=sm.document_id
                     JOIN document_versions v ON v.id=sm.version_id
                     WHERE sm.snapshot_id=%s AND d.number LIKE %s AND d.kind ILIKE %s LIMIT 10""",
        (snapshot_id, number + "/%", "%" + kind + "%"),
    )


def search(question, snapshot_id, version_ids=None):
    query = EmbeddingClient().query(question)
    vector = "[" + ",".join(str(float(v)) for v in query["vectors"][0]) + "]"
    if version_ids:
        # Explicit comparison versions must have appeared in an immutable published snapshot.
        restriction = "c.version_id = ANY(%s::uuid[]) AND EXISTS (SELECT 1 FROM snapshot_members sm WHERE sm.version_id=c.version_id)"
        params = (version_ids,)
    else:
        restriction = "c.version_id IN (SELECT version_id FROM snapshot_members WHERE snapshot_id=%s)"
        params = (snapshot_id,)
    lexical = db.fetch(
        f"""SELECT {COLUMNS}, ts_rank(c.search_vector, plainto_tsquery('simple', %s)) score
                         {JOINS} WHERE {restriction}
                         AND c.search_vector @@ plainto_tsquery('simple', %s)
                         ORDER BY score DESC, c.id LIMIT 50""",
        (query["segmented"][0], *params, query["segmented"][0]),
    )
    with db.connection() as conn:
        conn.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
        vector_rows = conn.execute(
            f"""SELECT {COLUMNS}, -(c.embedding <#> %s::vector) score
                                   {JOINS} WHERE {restriction} AND c.embedding_revision=%s
                                   ORDER BY c.embedding <#> %s::vector, c.id LIMIT 50""",
            (vector, *params, query["revision"], vector),
        ).fetchall()
    return rrf(lexical, vector_rows)
