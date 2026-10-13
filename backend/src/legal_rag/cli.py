import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4


def migrate():
    from legal_rag.infrastructure.db import connection
    from legal_rag.infrastructure.migrations import migration_files

    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(938214)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in migration_files():
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.stem,)).fetchone():
                conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                conn.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (path.stem,))
                print("Applied " + path.stem)


def doctor():
    from legal_rag.config import settings
    from legal_rag.embedding.client import EmbeddingClient
    from legal_rag.infrastructure import db
    from legal_rag.infrastructure.redis import redis_client
    from legal_rag.llm.gemini import Gemini
    from legal_rag.llm.jev import Jev

    config = settings()
    report = {}
    checks = {
        "postgres": lambda: (
            db.one("SELECT extversion FROM pg_extension WHERE extname='vector'")
            or (_ for _ in ()).throw(RuntimeError("pgvector_missing"))
        ),
        "redis": lambda: redis_client().ping(),
        "embedding": lambda: EmbeddingClient().query("Thời điểm có hiệu lực của văn bản là gì?"),
        "jev": lambda: Jev().evaluate(
            {"text": "Câu hỏi về pháp luật Việt Nam"},
            {"legal": {"type": "noul", "instructions": "Văn bản nói về pháp luật Việt Nam?"}},
        ),
        "gemini": lambda: Gemini().generate(
            "Nguồn ghi ngày nào?",
            [{"source_id": "smoke", "text": "Ngày kiểm tra hệ thống là 01/01/2026."}],
            "2026-01-01",
        ),
    }
    for name, check in checks.items():
        try:
            check()
            report[name] = {"status": "passed"}
        except Exception as exc:
            report[name] = {"status": "failed", "code": getattr(exc, "code", type(exc).__name__)}
    output = config.data_dir / "reports" / "doctor.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return all(item["status"] == "passed" for item in report.values())


def main():
    parser = argparse.ArgumentParser(description="Vietnamese Legal RAG operations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("doctor")
    commands.add_parser("download-vncorenlp")
    discover = commands.add_parser("discover")
    discover.add_argument("--output", type=Path, default=Path("data/reports/bucket-manifest.json"))
    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("source", choices=["hf", "vanban", "congbao", "local"])
    ingest_parser.add_argument("--run-id")
    ingest_parser.add_argument("--enqueue", action="store_true")
    draft = commands.add_parser("benchmark-draft")
    draft.add_argument("--output", type=Path, default=Path("data/benchmark/draft.jsonl"))
    bench = commands.add_parser("benchmark")
    bench.add_argument("dataset", type=Path)
    bench.add_argument("--output", type=Path, default=Path("data/reports/benchmark.json"))
    bench.add_argument("--baseline", action="store_true", help="Run BGE offline reranker baseline")
    bench.add_argument(
        "--dry-run", action="store_true", help="Inspect dataset/call limits without provider calls"
    )
    gate = commands.add_parser("benchmark-gate")
    gate.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
    elif args.command == "doctor":
        sys.exit(0 if doctor() else 1)
    elif args.command == "discover":
        from legal_rag.ingestion.sources import bucket_manifest

        report = bucket_manifest(args.output)
        for bucket in report["buckets"]:
            print(bucket["id"], bucket["status"], "files=" + str(len(bucket.get("files", []))))
        sys.exit(0 if all(b["status"] == "accessible" for b in report["buckets"]) else 1)
    elif args.command == "download-vncorenlp":
        from legal_rag.config import settings
        from legal_rag.embedding.model_setup import download_vncorenlp

        folder = settings().vncorenlp_dir.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        download_vncorenlp(folder)
        print("VnCoreNLP downloaded")
    elif args.command == "ingest":
        from legal_rag.infrastructure import db
        from legal_rag.ingestion.pipeline import ingest

        run_id = args.run_id or str(uuid4())
        if not args.run_id:
            db.execute("INSERT INTO ingestion_runs(id,source) VALUES(%s,%s)", (run_id, args.source))
        if args.enqueue:
            from legal_rag.tasks.jobs import ingest_source

            ingest_source.delay(run_id)
        else:
            ingest(run_id)
        print("run_id=" + run_id)
    elif args.command == "benchmark-draft":
        from legal_rag.evals.benchmark import draft_dataset

        print("Unreviewed draft questions:", draft_dataset(args.output))
    elif args.command == "benchmark":
        from legal_rag.evals.benchmark import estimate, evaluate

        if args.dry_run:
            print(json.dumps(estimate(args.dataset), ensure_ascii=False, indent=2))
            return

        passed = evaluate(args.dataset, args.output, args.baseline)
        print("Beta gate:", "passed" if passed else "blocked")
        sys.exit(0 if passed else 1)
    elif args.command == "benchmark-gate":
        from legal_rag.evals.benchmark import check_reviewed_report

        report = json.loads(args.report.read_text(encoding="utf-8"))
        passed = check_reviewed_report(report)
        report["beta_gate"] = "passed" if passed else "blocked"
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Beta gate:", report["beta_gate"])
        sys.exit(0 if passed else 1)


if __name__ == "__main__":
    from pydantic import ValidationError

    from legal_rag.infrastructure.http import ProviderError

    try:
        main()
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "error": "invalid_configuration_or_data",
                    "fields": [e["loc"] for e in exc.errors(include_input=False, include_url=False)],
                }
            )
        )
        sys.exit(1)
    except ProviderError as exc:
        print(
            json.dumps(
                {"error": exc.code, "provider": exc.provider, "retry_after_seconds": exc.retry_after_seconds}
            )
        )
        sys.exit(1)
