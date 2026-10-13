import hashlib
import json
import logging
import shutil
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from legal_rag.config import settings
from legal_rag.domain.legal import PIPELINE_VERSION, parse_date
from legal_rag.domain.models import DocumentRecord
from legal_rag.domain.text import normalize
from legal_rag.embedding.client import EmbeddingClient
from legal_rag.infrastructure import db
from legal_rag.infrastructure.http import ProviderError
from legal_rag.infrastructure.redis import RELEASE_LOCK, redis_client
from legal_rag.ingestion.sources import (
    OfficialConnector,
    SourceError,
    read_records,
    record_review_errors,
    sha256_file,
    update_window,
)

logger = logging.getLogger(__name__)


def save_artifact(source, path, local, metadata=None):
    digest = sha256_file(local)
    immutable = settings().data_dir / "artifacts" / (digest + local.suffix)
    immutable.parent.mkdir(parents=True, exist_ok=True)
    if not immutable.exists():
        shutil.copyfile(local, immutable)
    row = db.one(
        """INSERT INTO source_artifacts(id,source,path,sha256,pipeline_version,local_path,metadata)
                  VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source,path,sha256)
                  DO UPDATE SET path=EXCLUDED.path RETURNING id""",
        (uuid4(), source, path, digest, PIPELINE_VERSION, str(immutable), Jsonb(metadata or {})),
    )
    return row["id"]


def ingest_record(record, artifact_id):
    embedding = EmbeddingClient()
    revision = embedding.metadata()["revision"]
    errors = record_review_errors(record)
    metadata = record.model_dump(mode="json", exclude={"text", "title", "number", "kind", "source_url"})
    metadata["review_errors"] = errors
    # Include legal metadata in the version fingerprint, not only text.
    canonical = json.dumps(
        record.model_dump(mode="json", exclude={"verified_at"})
        | {
            "text": normalize(record.text),
            "pipeline_version": PIPELINE_VERSION,
            "embedding_revision": revision,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    identity = normalize(record.number).casefold() + ":" + record.scope
    existing = db.one(
        """SELECT v.id,v.review_status FROM document_versions v JOIN documents d ON d.id=v.document_id
                       WHERE d.identity_key=%s AND v.content_hash=%s""",
        (identity, fingerprint),
    )
    if existing:
        # Recover versions persisted before a previous job crashed during publication.
        active = db.one(
            "SELECT 1 FROM snapshot_members sm JOIN active_corpus a ON a.snapshot_id=sm.snapshot_id WHERE sm.version_id=%s",
            (existing["id"],),
        )
        return existing["id"], "approved" if existing[
            "review_status"
        ] == "approved" and not active else "unchanged"
    previous = db.one(
        """SELECT v.* FROM document_versions v JOIN documents d ON d.id=v.document_id
                       JOIN snapshot_members sm ON sm.version_id=v.id JOIN active_corpus a ON a.snapshot_id=sm.snapshot_id
                       WHERE d.identity_key=%s""",
        (identity,),
    )
    start = end = None
    try:
        start, end = parse_date(record.effective_from), parse_date(record.effective_to)
    except ValueError:
        pass
    conflict = previous and any(
        previous[field] and new and previous[field] != new
        for field, new in (("effective_from", start), ("effective_to", end))
    )
    if conflict:
        errors.append("conflicting_effective_metadata")
        metadata["previous_version_id"] = str(previous["id"])
    if previous and record.verified_at is None and normalize(previous["raw_text"]) != normalize(record.text):
        errors.append("snapshot_content_difference_requires_review")
    prepared = embedding.prepare(record.title, record.text)
    if prepared["revision"] != revision:
        raise ProviderError("embedding", "model_changed_during_ingestion")
    version_id = uuid4()
    unit_ids = [uuid4() for _ in prepared["units"]]
    approved = not errors
    with db.connection() as conn:
        document = conn.execute(
            """INSERT INTO documents(id,identity_key,number,title,kind,scope)
                                  VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(identity_key)
                                  DO UPDATE SET identity_key=EXCLUDED.identity_key RETURNING id""",
            (uuid4(), identity, record.number, record.title, record.kind, record.scope),
        ).fetchone()
        conn.execute(
            """INSERT INTO document_versions(id,document_id,artifact_id,content_hash,raw_text,source_url,
                     effective_from,effective_to,temporal_status,verified_at,metadata,review_status)
                     VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                version_id,
                document["id"],
                artifact_id,
                fingerprint,
                record.text,
                record.source_url,
                start if record.effective_from_evidence else None,
                end if record.effective_to_evidence else None,
                "conflict"
                if conflict
                else "partially_expired"
                if "một phần" in record.reported_status.lower()
                else "unknown",
                record.verified_at,
                Jsonb(metadata),
                "approved" if approved else "pending",
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO legal_units(id,version_id,locator,ordinal,raw_text,start_offset,end_offset) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        unit_ids[i],
                        version_id,
                        u["locator"],
                        i,
                        u["raw_text"],
                        u["start_offset"],
                        u["end_offset"],
                    )
                    for i, u in enumerate(prepared["units"])
                ],
            )
            cur.executemany(
                """INSERT INTO chunks(id,unit_id,version_id,ordinal,raw_text,normalized_text,segmented_text,
                             token_count,embedding,embedding_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)""",
                [
                    (
                        uuid4(),
                        unit_ids[c["unit_ordinal"]],
                        version_id,
                        c["ordinal"],
                        c["raw_text"],
                        c["normalized_text"],
                        c["segmented_text"],
                        c["token_count"],
                        json.dumps(c["embedding"]),
                        prepared["revision"],
                    )
                    for c in prepared["chunks"]
                ],
            )
        for relation in record.relations:
            if not relation.get("evidence") or not relation.get("relation"):
                continue
            conn.execute(
                "INSERT INTO document_relations(id,from_version,relation,locator,evidence) VALUES(%s,%s,%s,%s,%s)",
                (uuid4(), version_id, relation["relation"], relation.get("locator"), Jsonb(relation)),
            )
    return version_id, "approved" if approved else "pending"


def publish_snapshot(run_id, version_ids, checks):
    snapshot_id = uuid4()
    with db.connection() as conn:
        active = conn.execute("SELECT snapshot_id FROM active_corpus WHERE singleton FOR UPDATE").fetchone()
        previous = (
            conn.execute(
                "SELECT source_checks FROM corpus_snapshots WHERE id=%s", (active["snapshot_id"],)
            ).fetchone()
            if active["snapshot_id"]
            else None
        )
        merged = (previous["source_checks"] if previous else {}) | checks
        conn.execute(
            "INSERT INTO corpus_snapshots(id,run_id,source_checks) VALUES(%s,%s,%s)",
            (snapshot_id, run_id, Jsonb(merged)),
        )
        if active["snapshot_id"]:
            conn.execute(
                "INSERT INTO snapshot_members SELECT %s,document_id,version_id FROM snapshot_members WHERE snapshot_id=%s",
                (snapshot_id, active["snapshot_id"]),
            )
        if version_ids:
            conn.execute(
                """INSERT INTO snapshot_members(snapshot_id,document_id,version_id)
                         SELECT DISTINCT ON (document_id) %s,document_id,id FROM document_versions
                         WHERE id=ANY(%s::uuid[]) AND review_status='approved'
                         ORDER BY document_id,created_at DESC,id
                         ON CONFLICT(snapshot_id,document_id) DO UPDATE SET version_id=EXCLUDED.version_id""",
                (snapshot_id, version_ids),
            )
        conn.execute("UPDATE active_corpus SET snapshot_id=%s WHERE singleton", (snapshot_id,))
    return str(snapshot_id)


def hf_records():
    from huggingface_hub import HfApi

    from legal_rag.ingestion.datasets import uts_records, vbpl_records

    config = settings()
    api = HfApi(token=config.hf_token.get_secret_value() or False)
    for bucket in sorted(config.hf_buckets.split(",")):
        folder = config.data_dir / "raw" / "hf" / bucket.replace("/", "__")
        entries = {e.path: e for e in api.list_bucket_tree(bucket, recursive=True) if e.type == "file"}
        if bucket.endswith("UTS_VLC-bucket"):
            paths = [
                "README.md",
                "metadata/2026_sources.json",
                *sorted(p for p in entries if p.startswith("data/2026-") and p.endswith(".parquet")),
            ]
            iterator = uts_records
        elif bucket.endswith("vietnamese-legal-documents-bucket"):
            paths = [
                "README.md",
                "data/metadata.parquet",
                "data/content.parquet",
                "data/relationships.parquet",
            ]
            iterator = vbpl_records
        else:
            raise SourceError("bucket_schema_not_supported")
        artifacts = {}
        for path in paths:
            if path not in entries:
                raise SourceError("required_bucket_file_missing")
            local = folder / path
            local.parent.mkdir(parents=True, exist_ok=True)
            api.download_bucket_files(bucket, files=[(entries[path], str(local))])
            artifacts[str(local)] = save_artifact(
                "hf:" + bucket, path, local, {"bucket_hash": getattr(entries[path], "xet_hash", None)}
            )
        signature = ":".join(str(getattr(entries[p], "xet_hash", "")) for p in paths)
        checkpoint_source = "hf:" + bucket
        checkpoint = db.one("SELECT cursor FROM source_checkpoints WHERE source=%s", (checkpoint_source,))
        cursor = checkpoint["cursor"] if checkpoint else {}
        start = cursor.get("index", 0) if cursor.get("signature") == signature else 0
        processed = 0
        for index, (record, path, source_id) in enumerate(iterator(folder)):
            if index < start:
                continue
            yield record, artifacts[str(path)], source_id
            processed += 1
            db.execute(
                """INSERT INTO source_checkpoints(source,status,cursor) VALUES(%s,'partial',%s)
                       ON CONFLICT(source) DO UPDATE SET status='partial',cursor=EXCLUDED.cursor""",
                (checkpoint_source, Jsonb({"signature": signature, "index": index + 1})),
            )
            if processed >= config.hf_document_limit:
                raise SourceError("hf_batch_limit_resume_required")
        db.execute(
            """INSERT INTO source_checkpoints(source,status,checked_at,cursor) VALUES(%s,'completed',now(),%s)
                   ON CONFLICT(source) DO UPDATE SET status='completed',checked_at=now(),cursor=EXCLUDED.cursor""",
            (checkpoint_source, Jsonb({"signature": signature, "index": start + processed})),
        )


def local_files(source):
    if source != "local":
        raise SourceError("unsupported_local_source")
    folder = settings().data_dir / "import"
    if not folder.exists():
        raise SourceError("local_import_directory_missing")
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.name.endswith(".metadata.json"):
            yield path, save_artifact("local", str(path.relative_to(folder)), path)


def ingest(run_id):
    row = db.one("SELECT * FROM ingestion_runs WHERE id=%s", (run_id,))
    if not row or row["status"] not in ("queued", "failed", "partial"):
        return
    source = row["source"]
    lock_key = "ingestion-lock:" + source
    client = redis_client()
    if not client.set(lock_key, run_id, nx=True, ex=7500):
        db.execute(
            "UPDATE ingestion_runs SET status='skipped',error_code='already_running',finished_at=now() WHERE id=%s",
            (run_id,),
        )
        return
    db.execute(
        "UPDATE ingestion_runs SET status='running',error_code=NULL,started_at=now() WHERE id=%s", (run_id,)
    )
    counts = {"approved": 0, "pending": 0, "unchanged": 0, "quarantined": 0}
    approved, errors = [], []
    config = settings()
    report_dir = config.data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    def consume(raw, artifact_id, location):
        try:
            record = raw if isinstance(raw, DocumentRecord) else DocumentRecord.model_validate(raw)
            version_id, status = ingest_record(record, artifact_id)
            counts[status] += 1
            if status == "approved":
                approved.append(str(version_id))
            return True
        except (ValueError, SourceError) as exc:
            counts["quarantined"] += 1
            errors.append({"location": location, "code": type(exc).__name__})
            return False

    try:
        checkpoint = db.one("SELECT * FROM source_checkpoints WHERE source=%s", (source,))
        if source in ("vanban", "congbao"):
            connector = OfficialConnector(source)
            details = set(connector.listings(update_window(checkpoint)))
            # Weekly revalidation includes every previously imported URL, even old documents.
            if datetime.now(ZoneInfo("Asia/Saigon")).weekday() == 6:
                details.update(
                    r["source_url"]
                    for r in db.fetch(
                        "SELECT DISTINCT source_url FROM document_versions WHERE source_url LIKE %s",
                        ("https://" + connector.host + "/%",),
                    )
                )
            completed = (
                set((checkpoint or {}).get("cursor", {}).get("completed", []))
                if (checkpoint or {}).get("status") == "partial"
                else set()
            )
            for url in sorted(details - completed):
                try:
                    record, artifacts = connector.detail(url)
                    primary = None
                    for artifact_url, content, suffix in artifacts:
                        digest = hashlib.sha256(content).hexdigest()
                        local = config.data_dir / "raw" / source / (digest + suffix)
                        local.parent.mkdir(parents=True, exist_ok=True)
                        local.write_bytes(content)
                        artifact_id = save_artifact(source, artifact_url, local)
                        primary = primary or artifact_id
                    if consume(record, primary, url):
                        completed.add(url)
                    db.execute(
                        """INSERT INTO source_checkpoints(source,status,cursor) VALUES(%s,'partial',%s)
                               ON CONFLICT(source) DO UPDATE SET status='partial',cursor=EXCLUDED.cursor""",
                        (source, Jsonb({"completed": sorted(completed)})),
                    )
                except ProviderError:
                    raise
                except Exception as exc:
                    counts["quarantined"] += 1
                    errors.append({"location": url, "code": type(exc).__name__})
            errors.extend({"location": source, "code": error} for error in connector.failures)
        elif source == "hf":
            try:
                for raw, artifact_id, location in hf_records():
                    consume(raw, artifact_id, location)
            except SourceError as exc:
                if str(exc) != "hf_batch_limit_resume_required":
                    raise
                errors.append({"location": "hf", "code": str(exc)})
        else:
            for path, artifact_id in local_files(source):
                try:
                    for index, raw in enumerate(read_records(path)):
                        consume(raw, artifact_id, str(path) + ":" + str(index))
                except Exception as exc:
                    counts["quarantined"] += 1
                    errors.append({"location": str(path), "code": type(exc).__name__})
        status = "partial" if errors else "completed"
        now = datetime.now(ZoneInfo("UTC")).isoformat()
        if approved or not errors:
            publish_snapshot(run_id, approved, {source: {"checked_at": now, "status": status}})
        db.execute(
            """INSERT INTO source_checkpoints(source,checked_at,status,cursor) VALUES(%s,now(),%s,'{}')
                   ON CONFLICT(source) DO UPDATE SET checked_at=CASE WHEN EXCLUDED.status='completed' THEN EXCLUDED.checked_at ELSE source_checkpoints.checked_at END,
                   status=EXCLUDED.status,cursor=CASE WHEN EXCLUDED.status='completed' THEN '{}'::jsonb ELSE source_checkpoints.cursor END""",
            (source, status),
        )
        db.execute(
            "UPDATE ingestion_runs SET status=%s,counts=%s,finished_at=now() WHERE id=%s",
            (status, Jsonb(counts), run_id),
        )
    except Exception as exc:
        errors.append({"location": source, "code": type(exc).__name__})
        db.execute(
            "UPDATE ingestion_runs SET status='failed',error_code=%s,counts=%s,finished_at=now() WHERE id=%s",
            (type(exc).__name__, Jsonb(counts), run_id),
        )
        logger.error("ingestion_failed run_id=%s code=%s", run_id, type(exc).__name__)
        raise
    finally:
        (report_dir / (str(run_id) + ".json")).write_text(
            json.dumps({"counts": counts, "errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        client.eval(RELEASE_LOCK, 1, lock_key, run_id)
