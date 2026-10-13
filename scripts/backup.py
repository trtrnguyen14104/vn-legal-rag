"""Backup a named Compose database. Restore verification is restricted to the test project."""
import argparse
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--project", default="vietnamese-legal-rag", choices=["vietnamese-legal-rag", "legal-rag-test"])
parser.add_argument("--verify-restore", action="store_true")
args = parser.parse_args()
if args.verify_restore and args.project != "legal-rag-test":
    raise SystemExit("Restore verification is restricted to legal-rag-test")
root = Path(__file__).resolve().parent.parent
command = ["docker", "compose", "-p", args.project, "-f", str(root / "compose.yaml")]
if args.project == "legal-rag-test":
    command += ["-f", str(root / "compose.test.yaml")]
command += ["exec", "-T", "db", "sh", "-c"]
folder = root / "data/backups"
folder.mkdir(parents=True, exist_ok=True)
output = folder / (args.project + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".dump")
with output.open("wb") as stream:
    subprocess.run(command + ['PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'], stdout=stream, check=True)
digest = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix(".dump.sha256").write_text(digest + "\n", encoding="utf-8")
if args.verify_restore:
    database = "restore_test_" + uuid4().hex
    subprocess.run(command + [f'PGPASSWORD="$POSTGRES_PASSWORD" createdb -U "$POSTGRES_USER" {database}'], check=True)
    try:
        with output.open("rb") as stream:
            subprocess.run(command + [f'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -U "$POSTGRES_USER" -d {database} --exit-on-error'], stdin=stream, check=True)
        subprocess.run(command + [f'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d {database} -c "SELECT count(*) FROM schema_migrations; SELECT count(*) FROM corpus_snapshots;"'], check=True)
        print("Restore verification passed")
    finally:
        subprocess.run(command + [f'PGPASSWORD="$POSTGRES_PASSWORD" dropdb -U "$POSTGRES_USER" {database}'], check=True)
print("Backup saved:", output.name)
