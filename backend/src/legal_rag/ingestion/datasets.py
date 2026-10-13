"""Adapters for the two inspected bucket schemas, with disk-backed joins."""

import json
import sqlite3
from urllib.parse import quote

import pyarrow.parquet as pq
from bs4 import BeautifulSoup

from legal_rag.domain.legal import NUMBER
from legal_rag.domain.models import DocumentRecord
from legal_rag.ingestion.sources import SourceError

VBPL_GATEWAY = "https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/"


def html_text(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    blocks = [
        node.get_text(" ", strip=True)
        for node in soup.find_all(["p", "li", "h1", "h2", "h3", "h4"])
        if not node.find(["p", "li", "h1", "h2", "h3", "h4"])
    ]
    return "\n".join(blocks) if blocks else soup.get_text("\n", strip=True)


def convert_vbpl(metadata, html, relationships):
    key = str(metadata["id"])
    start, end = metadata.get("ngay_co_hieu_luc"), metadata.get("ngay_het_hieu_luc")
    scope = "central" if metadata.get("pham_vi") == "Trung ương" else "local_or_unknown"
    return DocumentRecord(
        title=(metadata.get("title") or "")[:300],
        number=metadata.get("so_ky_hieu") or "",
        kind=metadata.get("loai_van_ban") or "unknown",
        text=html_text(html),
        source_url=VBPL_GATEWAY + quote(key, safe=""),
        source_kind="consolidated" if "VBHN" in (metadata.get("so_ky_hieu") or "") else "legislation",
        authority=metadata.get("co_quan_ban_hanh") or "",
        scope=scope,
        effective_from=start,
        effective_to=end,
        issued_at=metadata.get("ngay_ban_hanh"),
        effective_from_evidence=f"Snapshot VBPL metadata id={key}: ngay_co_hieu_luc={start}"
        if start
        else None,
        effective_to_evidence=f"Snapshot VBPL metadata id={key}: ngay_het_hieu_luc={end}" if end else None,
        license_evidence="CC BY 4.0; curated by Thịnh Ngô; bucket README.md; source vbpl.vn",
        reported_status=metadata.get("tinh_trang_hieu_luc") or "unknown",
        source_document_id=key,
        relations=[
            {
                "relation": row["relationship"],
                "target_source_id": str(row["other_doc_id"]),
                "evidence": {
                    "doc_id": key,
                    "other_doc_id": str(row["other_doc_id"]),
                    "relationship": row["relationship"],
                },
            }
            for row in relationships[:100]
        ],
    )


def vbpl_records(folder):
    metadata_path, content_path = folder / "data/metadata.parquet", folder / "data/content.parquet"
    if not metadata_path.exists() or not content_path.exists():
        raise SourceError("vbpl_metadata_and_content_required")
    stage = folder / "join.sqlite"
    with sqlite3.connect(stage) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS metadata(id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS relations(source_id TEXT, body TEXT NOT NULL)")
        conn.execute("DELETE FROM metadata")
        conn.execute("DELETE FROM relations")
        for batch in pq.ParquetFile(metadata_path).iter_batches(batch_size=512):
            conn.executemany(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                [(str(row["id"]), json.dumps(row, ensure_ascii=False)) for row in batch.to_pylist()],
            )
        relationships_path = folder / "data/relationships.parquet"
        if relationships_path.exists():
            for batch in pq.ParquetFile(relationships_path).iter_batches(batch_size=1024):
                conn.executemany(
                    "INSERT INTO relations VALUES(?,?)",
                    [(str(row["doc_id"]), json.dumps(row, ensure_ascii=False)) for row in batch.to_pylist()],
                )
        conn.execute("CREATE INDEX IF NOT EXISTS relations_source ON relations(source_id)")
        conn.commit()
        for batch in pq.ParquetFile(content_path).iter_batches(batch_size=8):
            for row in batch.to_pylist():
                metadata = conn.execute("SELECT body FROM metadata WHERE id=?", (str(row["id"]),)).fetchone()
                if not metadata:
                    continue
                meta = json.loads(metadata[0])
                if meta.get("pham_vi") != "Trung ương":
                    continue
                relations = [
                    json.loads(r[0])
                    for r in conn.execute(
                        "SELECT body FROM relations WHERE source_id=? LIMIT 100", (str(row["id"]),)
                    )
                ]
                try:
                    record = convert_vbpl(meta, row["content_html"], relations)
                except ValueError:
                    record = {"error_code": "invalid_vbpl_record"}
                yield record, content_path, str(row["id"])


def convert_uts(row, sources):
    # Only the corrected 2026 split is ingested. Other splits have known quality issues.
    source = sources.get(str(row["id"]))
    if not source:
        raise SourceError("uts_provenance_missing")
    number = source.get("doc_num") or str(row["id"])
    if not NUMBER.fullmatch(number) and row.get("type") != "constitution":
        raise SourceError("uts_document_number_invalid")
    text = row["content"]
    # Strip Markdown heading syntax while preserving the citation text itself.
    import re

    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    return DocumentRecord(
        title=source.get("title") or row["title"],
        number=number,
        kind={"law": "Luật", "code": "Bộ luật", "constitution": "Hiến pháp"}.get(row["type"], "unknown"),
        text=text,
        source_url=source.get("url_vbpl") or source.get("url_vanban_chinhphu") or "",
        source_kind="legislation",
        authority="Quốc hội",
        source_document_id=str(row["id"]),
        reported_status="snapshot_2026_unverified_at_query_time",
        license_evidence="MIT; curated by Underthesea NLP; bucket README.md",
    )


def uts_records(folder):
    sources_path = folder / "metadata/2026_sources.json"
    if not sources_path.exists():
        raise SourceError("uts_sources_required")
    sources = {str(row["id"]): row for row in json.loads(sources_path.read_text(encoding="utf-8"))}
    files = sorted((folder / "data").glob("2026-*.parquet"))
    if not files:
        raise SourceError("uts_corrected_split_required")
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=8):
            for row in batch.to_pylist():
                try:
                    record = convert_uts(row, sources)
                except (ValueError, SourceError):
                    record = {"error_code": "invalid_uts_record_or_provenance"}
                yield record, path, str(row["id"])
