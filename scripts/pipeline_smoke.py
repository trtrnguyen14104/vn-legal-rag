"""Publish one short real UTS document; never substitute synthetic evidence."""

import json
from pathlib import Path

import pyarrow.parquet as pq
from legal_rag.config import settings
from legal_rag.infrastructure import db as db
from legal_rag.ingestion.datasets import convert_uts
from legal_rag.ingestion.pipeline import ingest_record, publish_snapshot, save_artifact
from legal_rag.ingestion.sources import record_review_errors
from legal_rag.retrieval import retriever as retrieval

folder = Path("/profiles")
sources = {
    str(r["id"]): r for r in json.loads((folder / "metadata__2026_sources.json").read_text(encoding="utf-8"))
}
path = folder / "data__2026-00000-of-00001.parquet"
rows = sorted(pq.read_table(path).to_pylist(), key=lambda r: len(r["content"]))
record = None
for row in rows:
    try:
        candidate = convert_uts(row, sources)
        if not record_review_errors(candidate):
            record = candidate
            break
    except ValueError:
        continue
if not record:
    raise RuntimeError("No eligible real source record")
artifact = save_artifact("hf:trtrnguyen14104/UTS_VLC-bucket", "data/2026-00000-of-00001.parquet", path)
version_id, status = ingest_record(record, artifact)
if status == "pending":
    raise RuntimeError("Sample requires manual review")
snapshot_id = publish_snapshot(
    None, [str(version_id)], {"UTS_VLC": {"status": "sample_only", "documents": 1}}
)
chunks = db.one(
    "SELECT count(*) total,max(token_count) max_tokens FROM chunks WHERE version_id=%s", (version_id,)
)
query = "Giải thích phạm vi áp dụng của " + record.number
candidates = retrieval.search(query, snapshot_id)
assert any(str(c["version_id"]) == str(version_id) for c in candidates)
report = {
    "status": "passed",
    "source_number": record.number,
    "characters": len(record.text),
    "snapshot_id": snapshot_id,
    "chunks": chunks["total"],
    "max_tokens": chunks["max_tokens"],
    "scope": "one real source document; full corpus not yet indexed",
}
output = settings().data_dir / "reports" / "pipeline-smoke.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
