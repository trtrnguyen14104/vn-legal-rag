"""Check Jev AI authentication without inference; opt in to exactly one decision POST."""

import argparse
import json
from pathlib import Path

from legal_rag.config import settings
from legal_rag.infrastructure.http import ProviderError
from legal_rag.llm.jev import Jev


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--decision",
        action="store_true",
        help="Spend balance on one small decision call; no POST retries",
    )
    parser.add_argument("--output", type=Path, default=Path("data/reports/jev-probe.json"))
    args = parser.parse_args()
    config = settings()
    report = {
        "base_url": config.jev_ai_base_url,
        "destination": config.jev_ai_base_url + "/v1/systemone",
        "requested_model": config.jev_ai_model,
        "decision_calls_attempted": 0,
    }
    client = Jev(max_calls=1)
    try:
        names = [m["name"] for m in client.models()]
        report["connection"] = {"status": "passed", "inference": False, "models": names}
        if config.jev_ai_model not in names:
            raise ProviderError("jev", "model_not_available")
        if args.decision:
            answers = client.evaluate(
                "Nghị định 123/2020/NĐ-CP có hiệu lực từ ngày nào?",
                {
                    "intent": {
                        "type": "choice",
                        "instructions": "Classify this Vietnamese legal question.",
                        "criteria": {
                            "legal_lookup": None,
                            "legal_explanation": None,
                            "compare_versions": None,
                            "out_of_scope": None,
                            "clarification_needed": None,
                        },
                    }
                },
            )
            report["decision"] = {
                "status": "passed",
                "answers": answers,
                **client.last_response_info,
            }
    except ProviderError as exc:
        report["error"] = {"code": exc.code, "automatic_post_retry": False}
        if exc.retry_after_seconds is not None:
            report["error"]["retry_after_seconds"] = exc.retry_after_seconds
    report["decision_calls_attempted"] = client.budget.used
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if "error" in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
