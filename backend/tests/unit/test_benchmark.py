import json
from collections import Counter
from datetime import date, datetime, timezone

import pytest

from legal_rag.evals.benchmark import check_reviewed_report, ndcg, validate_dataset


def reviewed_dataset(tmp_path):
    from legal_rag.workflow.rag_flow import INTENTS

    rows = []
    for i in range(250):
        intent = list(INTENTS)[i % 5]
        rows.append(
            {
                "id": str(i),
                "question": f"Giải thích quy định thứ {i}",
                "intent": intent,
                "split": "development" if i < 245 else "held_out",
                "document_group": "development-doc" if i < 245 else "held-doc",
                "reviewed": True,
                "reviewed_by": "test-reviewer",
                "relevance": {"a": 3},
                "snapshot_id": "frozen",
            }
        )
    path = tmp_path / "reviewed.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def test_dry_run_never_calls_provider(tmp_path, monkeypatch):
    from legal_rag.evals.benchmark import estimate
    from legal_rag.llm import jev as providers

    monkeypatch.setenv("JEV_AI_INFERENCE_ENABLED", "false")
    monkeypatch.setattr(
        providers, "request_json", lambda *a, **k: pytest.fail("dry-run must not call providers")
    )
    result = estimate(reviewed_dataset(tmp_path))
    assert result["held_out_questions"] == 5
    assert result["maximum_post_attempts"] == 0
    assert result["inference"] is False
    rows = [json.loads(line) for line in reviewed_dataset(tmp_path).read_text(encoding="utf-8").splitlines()]
    rows[0]["reviewed"] = False
    draft = tmp_path / "draft.jsonl"
    draft.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    result = estimate(draft)
    assert result["dataset_ready"] is False
    assert result["reviewed_questions"] == 249


def mock_benchmark_context(monkeypatch):
    from legal_rag.contracts import Claim, GeneratedAnswer
    from legal_rag.evals import benchmark as benchmark
    from legal_rag.llm import jev as providers
    from legal_rag.workflow import rag_flow as workflow

    monkeypatch.setenv("JEV_AI_INFERENCE_ENABLED", "true")
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only")
    monkeypatch.setenv("JEV_AI_MODEL", "jev-1.13.0")
    monkeypatch.setenv("JEV_AI_MAX_CALLS_PER_BENCHMARK", "50")
    snapshot = {"id": "frozen", "created_at": datetime.now(timezone.utc), "source_checks": {}}
    passage = {
        "id": "a",
        "version_id": "v",
        "document_id": "d",
        "title": "Văn bản",
        "number": "1/2020/NĐ-CP",
        "locator": "Điều 1",
        "raw_text": "Điều 1. Quy định áp dụng với tổ chức.",
        "source_url": "https://vbpl.vn/test",
        "effective_from": date(2020, 1, 1),
        "effective_to": None,
        "temporal_status": "unknown",
        "verified_at": None,
    }
    monkeypatch.setattr(benchmark.db, "one", lambda *args: snapshot)
    monkeypatch.setattr(workflow.retrieval, "document_candidates", lambda *args: [])
    monkeypatch.setattr(workflow.retrieval, "search", lambda *args: [passage])
    monkeypatch.setattr(
        workflow.Gemini,
        "generate",
        lambda *args: GeneratedAnswer(claims=[Claim(text="Quy định áp dụng với tổ chức.", source_ids=["a"])]),
    )
    calls = Counter()

    def response(*args, **kwargs):
        answers = {}
        for key, q in kwargs["body"]["questions"].items():
            calls[key] += 1
            if q["type"] == "choice":
                answers[key] = {
                    "type": "choice",
                    "choice": "legal_explanation",
                    "confidence": 1,
                    "probabilities": {k: int(k == "legal_explanation") for k in q["criteria"]},
                }
            elif q["type"] == "score":
                score = 3 if key == "relevance" else 0
                answers[key] = {
                    "type": "score",
                    "score": score,
                    "confidence": 1,
                    "probabilities": {str(i): int(i == score) for i in range(4)},
                }
            else:
                answers[key] = {"type": "noul", "noul": 0.99}
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 3}}

    monkeypatch.setattr(providers, "request_json", response)
    return calls


def test_benchmark_reuses_route_and_rerank_without_extra_post(tmp_path, monkeypatch):
    from legal_rag.evals.benchmark import evaluate

    calls = mock_benchmark_context(monkeypatch)
    output = tmp_path / "report.json"
    assert evaluate(reviewed_dataset(tmp_path), output) is False
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["execution_status"] == "completed"
    assert report["jev_post_attempts"] == 15
    assert calls["intent"] == calls["relevance"] == calls["supported"] == 5


def test_benchmark_saves_incomplete_report_when_budget_exhausted(tmp_path, monkeypatch):
    from legal_rag.evals.benchmark import evaluate

    calls = mock_benchmark_context(monkeypatch)
    monkeypatch.setenv("JEV_AI_MAX_CALLS_PER_BENCHMARK", "1")
    output = tmp_path / "report.json"
    assert evaluate(reviewed_dataset(tmp_path), output) is False
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["execution_status"] == "incomplete"
    assert report["jev_post_attempts"] == 1
    assert report["failed_question"]["code"] == "call_budget_exhausted"
    assert calls["intent"] == 1
    assert calls["relevance"] == 0
    assert check_reviewed_report(report) is False


def test_unreviewed_drafts_cannot_pass_beta_gate():
    rows = [{"reviewed": False}] * 250
    with pytest.raises(ValueError, match="manually reviewed"):
        validate_dataset(rows)
    assert not check_reviewed_report({"metrics": {}, "outcomes": []})


def test_ranking_metric_respects_relevance_and_order():
    assert ndcg(["best", "other"], {"best": 3, "other": 0}) == 1
    assert ndcg(["other", "best"], {"best": 3, "other": 0}) < 1


def test_gate_requires_complete_outcomes_and_valid_support_counts():
    report = {
        "execution_status": "completed",
        "held_out_questions": 1,
        "metrics": {
            "routing_macro_f1": 0.95,
            "recall_at_20": 0.95,
            "jev_ndcg_at_10": 0.9,
            "rrf_ndcg_at_10": 0.8,
            "bge_ndcg_at_10": 0.85,
        },
        "outcomes": [
            {
                "citation_review": {"total_claims": 10, "supported_claims": 10, "reviewed_by": "reviewer"},
                "temporal_review": {"errors": 0, "reviewed_by": "reviewer"},
            }
        ],
    }
    assert check_reviewed_report(report) is True
    report["outcomes"][0]["citation_review"]["supported_claims"] = 11
    assert check_reviewed_report(report) is False
    report["outcomes"][0]["citation_review"]["supported_claims"] = 10
    report["held_out_questions"] = 2
    assert check_reviewed_report(report) is False
