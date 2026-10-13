"""Import a bounded sample from already downloaded UTS files using local E5 only."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
from legal_rag.config import settings
from legal_rag.evals.benchmark import draft_dataset
from legal_rag.infrastructure import db as db
from legal_rag.ingestion.datasets import convert_uts
from legal_rag.ingestion.pipeline import ingest_record, publish_snapshot, save_artifact
from legal_rag.ingestion.sources import record_review_errors
from legal_rag.retrieval import retriever as retrieval
from psycopg.types.json import Jsonb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, default=Path("/profiles"))
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("limit must be between 1 and 100")
    config = settings()
    sources_path = args.folder / "metadata__2026_sources.json"
    path = args.folder / "data__2026-00000-of-00001.parquet"
    sources = {str(r["id"]): r for r in json.loads(sources_path.read_text(encoding="utf-8"))}
    rows = sorted(pq.read_table(path).to_pylist(), key=lambda row: len(row["content"]))
    bucket = "hf:trtrnguyen14104/UTS_VLC-bucket"
    artifact = save_artifact(bucket, "data/2026-00000-of-00001.parquet", path)
    save_artifact(bucket, "metadata/2026_sources.json", sources_path)
    readme = args.folder / "README.md"
    if readme.exists():
        save_artifact(bucket, "README.md", readme)
    run_id = uuid4()
    db.execute(
        "INSERT INTO ingestion_runs(id,source,status) VALUES(%s,'hf','running')",
        (run_id,),
    )
    versions = []
    counts = {"approved": 0, "unchanged": 0, "pending": 0, "excluded": 0}
    started = time.monotonic()
    report = {
        "run_id": str(run_id),
        "scope": "bounded UTS sample; not full bucket coverage",
        "paid_provider_calls": 0,
    }
    try:
        for row in rows:
            try:
                record = convert_uts(row, sources)
            except ValueError:
                counts["excluded"] += 1
                continue
            if record_review_errors(record):
                counts["excluded"] += 1
                continue
            version_id, status = ingest_record(record, artifact)
            counts[status] += 1
            if status != "pending":
                versions.append(str(version_id))
            print(
                json.dumps(
                    {
                        "processed": len(versions),
                        "target": args.limit,
                        "number": record.number,
                        "status": status,
                    }
                ),
                flush=True,
            )
            if len(versions) >= args.limit:
                break
        if not versions:
            raise RuntimeError("No eligible sample documents")
        snapshot = publish_snapshot(
            run_id,
            versions,
            {bucket: {"status": "sample_only", "documents": len(versions)}},
        )
        count = db.one(
            "SELECT count(*) total FROM snapshot_members WHERE snapshot_id=%s",
            (snapshot,),
        )["total"]
        retrieved = retrieval.search("Phạm vi áp dụng của văn bản pháp luật", snapshot)
        if not retrieved:
            raise RuntimeError("Sample hybrid retrieval returned no evidence")
        report.update(
            status="passed",
            snapshot_id=snapshot,
            active_documents=count,
            retrieval_candidates=len(retrieved),
            counts=counts,
            seconds=round(time.monotonic() - started, 2),
        )
        if count >= 50:
            draft = config.data_dir / "benchmark" / "draft.jsonl"
            report["unreviewed_benchmark_questions"] = draft_dataset(draft)
        # Partial is deliberate: a sample must never signal full corpus freshness.
        db.execute(
            "UPDATE ingestion_runs SET status='partial',counts=%s,error_code='sample_only',finished_at=now() WHERE id=%s",
            (Jsonb(counts), run_id),
        )
    except Exception as exc:
        report.update(status="failed", code=type(exc).__name__, counts=counts)
        db.execute(
            "UPDATE ingestion_runs SET status='failed',counts=%s,error_code=%s,finished_at=now() WHERE id=%s",
            (Jsonb(counts), type(exc).__name__, run_id),
        )
        raise
    finally:
        output = config.data_dir / "reports" / "profile-sample.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
