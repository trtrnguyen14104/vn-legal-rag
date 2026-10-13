"""Use real configured providers, with a tiny non-sensitive state and redacted results."""

import json
from pathlib import Path

from legal_rag.llm.gemini import Gemini
from legal_rag.llm.jev import Jev
from legal_rag.workflow.validation import validate_generation

state = {
    "id": "smoke",
    "document_id": "smoke",
    "version_id": "smoke",
    "number": "1/2020/NĐ-CP",
    "title": "Dữ liệu kiểm thử kỹ thuật",
    "locator": "Điều 1",
    "raw_text": "Điều 1. Quy định thử nghiệm chỉ áp dụng cho tổ chức.",
    "effective_from": None,
    "effective_to": None,
    "temporal_status": "unknown",
}
report = {}
try:
    result = Jev().evaluate(
        {"text": "Yêu cầu tra cứu pháp luật Việt Nam"},
        {
            "check": {
                "type": "noul",
                "instructions": "Does the text describe a Vietnamese legal lookup request?",
            }
        },
    )
    report["jev"] = {"status": "passed" if result["check"]["noul"] >= 0.5 else "failed"}
except Exception as exc:
    report["jev"] = {"status": "failed", "code": getattr(exc, "code", type(exc).__name__)}
try:
    result = Gemini().generate(
        "Quy định thử nghiệm áp dụng cho ai?",
        [{"source_id": "smoke", "text": state["raw_text"]}],
        "2026-10-12",
    )
    report["gemini"] = {"status": "passed", "claims": len(result.claims)}
    errors = validate_generation(result, [state], Jev())
    report["validation"] = {"status": "passed" if not errors else "failed", "errors": errors}
except Exception as exc:
    report["gemini"] = {"status": "failed", "code": getattr(exc, "code", type(exc).__name__)}
output = Path("data/reports/provider-smoke.json")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(0 if report.get("validation", {}).get("status") == "passed" else 1)
