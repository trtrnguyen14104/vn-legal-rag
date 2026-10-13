"""Capture small live connector fixtures and report schema/parser readiness."""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from legal_rag.ingestion.sources import OfficialConnector

folder = Path("data/fixtures/official")
folder.mkdir(parents=True, exist_ok=True)
report = []
parser = argparse.ArgumentParser()
parser.add_argument("--source", choices=["vanban", "congbao", "both"], default="both")
args = parser.parse_args()
for source in ("vanban", "congbao") if args.source == "both" else (args.source,):
    connector = OfficialConnector(source)
    try:
        urls = connector.listings(datetime.now(ZoneInfo("Asia/Saigon")).date() - timedelta(days=7))
        url = urls[0]
        (folder / (source + ".html")).write_bytes(connector.fetch(url))
        record, artifacts = connector.detail(url)
        report.append(
            {
                "source": source,
                "status": "parsed",
                "number": record.number,
                "characters": len(record.text),
                "listing_documents": len(urls),
                "coverage_warnings": connector.failures,
                "artifact_count": len(artifacts),
            }
        )
    except Exception as exc:
        report.append(
            {
                "source": source,
                "status": "blocked",
                "code": str(exc) if type(exc).__name__ == "SourceError" else type(exc).__name__,
            }
        )
(folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True, indent=2))
