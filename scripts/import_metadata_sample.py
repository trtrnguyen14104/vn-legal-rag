"""Import one real VBPL sample and verify metadata lookup with paid providers disabled."""

import json
from pathlib import Path
from uuid import uuid4

from legal_rag.config import settings
from legal_rag.contracts import QueryInput
from legal_rag.domain.models import DocumentRecord
from legal_rag.infrastructure import db as db
from legal_rag.ingestion.pipeline import ingest_record, publish_snapshot, save_artifact
from legal_rag.workflow.rag_flow import run
from psycopg.types.json import Jsonb


def main():
    config = settings()
    if config.jev_ai_inference_enabled:
        raise RuntimeError("Disable Jev inference before this metadata-only verification")
    folder = Path("/sample")
    path = folder / "metadata-lookup-sample.json"
    origin = folder / "metadata-lookup-origin.json"
    record = DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))
    save_artifact(
        "hf:trtrnguyen14104/vietnamese-legal-documents-bucket",
        "selected_record/metadata-and-content.json",
        origin,
    )
    artifact = save_artifact(
        "hf:trtrnguyen14104/vietnamese-legal-documents-bucket",
        "selected_record/canonical.json",
        path,
    )
    run_id = uuid4()
    db.execute(
        "INSERT INTO ingestion_runs(id,source,status) VALUES(%s,'hf','running')",
        (run_id,),
    )
    try:
        version, status = ingest_record(record, artifact)
        if status == "pending":
            raise RuntimeError("Sample needs manual review")
        snapshot_id = publish_snapshot(
            run_id,
            [str(version)],
            {"VBPL_sample": {"status": "sample_only", "documents": 1}},
        )
        snapshot = db.one("SELECT * FROM corpus_snapshots WHERE id=%s", (snapshot_id,))
        result = run(QueryInput(question=record.number + " có hiệu lực từ ngày nào?"), snapshot)
        assert result.status == "answered" and result.citations
        assert result.citations[0].quote == record.effective_from_evidence
        report = {
            "status": "passed",
            "run_id": str(run_id),
            "snapshot_id": snapshot_id,
            "document_number": record.number,
            "metadata_lookup": result.status,
            "jev_inference_enabled": False,
            "paid_provider_calls": 0,
            "scope": "one VBPL dataset record; not full corpus or live legal-validity verification",
        }
        db.execute(
            "UPDATE ingestion_runs SET status='partial',counts=%s,error_code='sample_only',finished_at=now() WHERE id=%s",
            (Jsonb({status: 1}), run_id),
        )
    except Exception as exc:
        db.execute(
            "UPDATE ingestion_runs SET status='failed',error_code=%s,finished_at=now() WHERE id=%s",
            (type(exc).__name__, run_id),
        )
        raise
    output = config.data_dir / "reports" / "metadata-sample.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
