import json
import math
import os
import time

import httpx

headers = {"Authorization": "Bearer " + os.environ["EMBEDDING_API_KEY"]}
started = time.monotonic()
with httpx.Client(timeout=180) as client:
    health = client.get("http://127.0.0.1:8001/health", headers=headers)
    health.raise_for_status()
    query = client.post("http://127.0.0.1:8001/queries", headers=headers, json={"texts": ["Điều kiện giao kết hợp đồng lao động", "Ngày bắt đầu hiệu lực của văn bản"]})
    query.raise_for_status()
    result = query.json()
    text = "Điều 1. Phạm vi thử nghiệm\n1. Một đoạn văn bản tiếng Việt dùng để kiểm tra kỹ thuật.\n2. Đây là dữ liệu kiểm thử, không phải quy định pháp luật."
    prepared = client.post("http://127.0.0.1:8001/prepare", headers=headers, json={"title": "Kiểm thử kỹ thuật", "text": text})
    prepared.raise_for_status()
    passages = prepared.json()
vectors = result["vectors"] + [c["embedding"] for c in passages["chunks"]]
assert all(len(v) == 1024 and abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-5 for v in vectors)
assert all(c["token_count"] <= 512 for c in passages["chunks"])
assert "".join(c["raw_text"] for c in passages["chunks"]) == text
assert any("_" in text for text in result["segmented"])
print(json.dumps({"status": "passed", "vectors": len(vectors), "dimensions": 1024,
                  "unit_norm": True, "preserved_text": True, "vncorenlp": True,
                  "revision": result["revision"], "seconds": round(time.monotonic() - started, 2)}))
