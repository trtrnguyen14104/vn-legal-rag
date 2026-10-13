"""Download small schema samples and documentation; never execute bucket code."""

import json
from pathlib import Path

from huggingface_hub import HfApi
from legal_rag.config import settings

root = Path(__file__).resolve().parent.parent
config = settings()
api = HfApi(token=config.hf_token.get_secret_value() or False)
samples = {
    "trtrnguyen14104/vietnamese-legal-documents-bucket": [
        "README.md",
        "data/metadata.parquet",
        "crawler/vbpl/spiders/vbpl.py",
        "crawler/vbpl/api.py",
        "data/content.parquet",
        "data/relationships.parquet",
    ],
    "trtrnguyen14104/UTS_VLC-bucket": [
        "README.md",
        "data/2021-00000-of-00001.parquet",
        "data/2026-00000-of-00001.parquet",
        "metadata/2026_sources.json",
    ],
}
report = []
for bucket, paths in samples.items():
    folder = root / "data" / "profiles" / bucket.split("/")[1]
    folder.mkdir(parents=True, exist_ok=True)
    for name in paths:
        target = folder / name.replace("/", "__")
        try:
            api.download_bucket_files(bucket, files=[(name, str(target))])
            item = {"bucket": bucket, "path": name, "bytes": target.stat().st_size}
            if target.suffix == ".parquet":
                import pyarrow.parquet as pq

                parquet = pq.ParquetFile(target)
                item["rows"] = parquet.metadata.num_rows
                item["schema"] = str(parquet.schema_arrow)
                item["sample"] = next(parquet.iter_batches(batch_size=1)).to_pylist()
            report.append(item)
        except Exception as exc:
            report.append({"bucket": bucket, "path": name, "error_code": type(exc).__name__})
output = root / "data" / "reports" / "source-profile.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
print(
    json.dumps(
        [{key: value for key, value in row.items() if key != "sample"} for row in report],
        ensure_ascii=True,
        indent=2,
    )
)
