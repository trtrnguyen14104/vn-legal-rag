import hashlib
import json
import math
import statistics
import time
from pathlib import Path

from legal_rag.config import settings
from legal_rag.contracts import QueryInput
from legal_rag.infrastructure import db
from legal_rag.infrastructure.http import ProviderError
from legal_rag.llm.jev import DecisionBudget, Jev
from legal_rag.retrieval import retriever as retrieval
from legal_rag.workflow.rag_flow import INTENTS, route, run


def ndcg(ids, relevance, k=10):
    def dcg(grades):
        return sum((2**grade - 1) / math.log2(i + 2) for i, grade in enumerate(grades))

    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    return dcg([relevance.get(str(item), 0) for item in ids[:k]]) / ideal if ideal else 0.0


def macro_f1(truth, predictions):
    scores = []
    for label in INTENTS:
        tp = sum(t == p == label for t, p in zip(truth, predictions, strict=True))
        fp = sum(p == label and t != label for t, p in zip(truth, predictions, strict=True))
        fn = sum(t == label and p != label for t, p in zip(truth, predictions, strict=True))
        scores.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    return statistics.mean(scores)


def validate_dataset(rows):
    if len(rows) < 250:
        raise ValueError("At least 250 manually reviewed questions required")
    groups = {"development": set(), "held_out": set()}
    ids = set()
    for row in rows:
        if not row.get("reviewed_by") or row.get("reviewed") is not True:
            raise ValueError("Every question must be manually reviewed")
        if row["intent"] not in INTENTS or row["split"] not in groups:
            raise ValueError("Invalid intent or split")
        if row["id"] in ids:
            raise ValueError("Duplicate benchmark id")
        ids.add(row["id"])
        groups[row["split"]].add(row["document_group"])
        if row["intent"] in ("legal_lookup", "legal_explanation", "compare_versions") and not row.get(
            "relevance"
        ):
            raise ValueError("Legal queries require manually graded source relevance")
    if not groups["development"] or not groups["held_out"] or groups["development"] & groups["held_out"]:
        raise ValueError("Development/held-out document groups must be nonempty and disjoint")
    held = [r for r in rows if r["split"] == "held_out"]
    if {r["intent"] for r in held} != set(INTENTS):
        raise ValueError("Held-out must cover all five intents")


def draft_dataset(output: Path):
    snapshot = retrieval.active_snapshot()
    if not snapshot:
        raise ValueError("A published corpus is required")
    docs = db.fetch(
        """SELECT d.*,sm.version_id FROM documents d JOIN snapshot_members sm ON sm.document_id=d.id
                      WHERE sm.snapshot_id=%s ORDER BY d.id LIMIT 50""",
        (snapshot["id"],),
    )
    if len(docs) < 50:
        raise ValueError("At least 50 published documents required to create a 250-question review queue")
    rows = []
    for doc in docs:
        group = str(doc["id"])
        split = (
            "held_out" if int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 5 == 0 else "development"
        )
        questions = {
            "legal_lookup": f"Văn bản {doc['number']} có hiệu lực từ ngày nào?",
            "legal_explanation": f"Giải thích phạm vi và điều kiện áp dụng của {doc['number']} bằng ngôn ngữ dễ hiểu.",
            "compare_versions": f"So sánh quy định trong hai phiên bản của {doc['number']}.",
            "clarification_needed": f"Văn bản số {doc['number'].split('/')[0]} quy định thế nào?",
            "out_of_scope": f"Viết một bài thơ về con số {doc['number'].split('/')[0]}.",
        }
        for intent, question in questions.items():
            rows.append(
                {
                    "id": group + ":" + intent,
                    "question": question,
                    "intent": intent,
                    "document_group": group,
                    "split": split,
                    "reviewed": False,
                    "reviewed_by": "",
                    "relevance": {},
                    "version_ids": [],
                    "as_of_date": None,
                    "expected_metadata": {},
                    "expected_claims": [],
                    "snapshot_id": str(snapshot["id"]),
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return len(rows)


def estimate(dataset):
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Benchmark rows must be JSON objects")
    validation_error = None
    try:
        validate_dataset(rows)
    except (ValueError, KeyError) as exc:
        validation_error = str(exc)
    config = settings()
    held = sum(r.get("split") == "held_out" for r in rows)
    return {
        "inference": False,
        "questions": len(rows),
        "held_out_questions": held,
        "reviewed_questions": sum(r.get("reviewed") is True and bool(r.get("reviewed_by")) for r in rows),
        "dataset_ready": validation_error is None,
        "dataset_validation_error": validation_error,
        "jev_inference_enabled": config.jev_ai_inference_enabled,
        "max_calls_per_question": config.jev_ai_max_calls_per_query,
        "configured_run_limit": config.jev_ai_max_calls_per_benchmark,
        "maximum_post_attempts": min(
            held * config.jev_ai_max_calls_per_query, config.jev_ai_max_calls_per_benchmark
        ),
        "model": config.jev_ai_model,
        "note": "Call limit counts POST attempts, not currency or provider balance. No inference performed.",
    }


def evaluate(dataset, output, baseline=False):
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_dataset(rows)
    config = settings()
    if not config.jev_ai_inference_enabled:
        raise ProviderError("jev", "inference_disabled")
    if not config.jev_ai_max_calls_per_benchmark:
        raise ProviderError("jev", "benchmark_budget_required")
    if config.jev_ai_model == "jev-latest":
        raise ValueError("Pin JEV_AI_MODEL=jev-1.13.0 before benchmarking; aliases can change")
    model = None
    if baseline:
        from FlagEmbedding import FlagReranker

        model = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=False, devices=["cpu"])
    truth, predictions = [], []
    recalls, jev_scores, rrf_scores = [], [], []
    baseline_scores, latencies = [], []
    outcomes = []
    total_budget = DecisionBudget(config.jev_ai_max_calls_per_benchmark)
    failure = None
    for row in [r for r in rows if r["split"] == "held_out"]:
        jev = None
        try:
            snapshot = db.one("SELECT * FROM corpus_snapshots WHERE id=%s", (row["snapshot_id"],))
            if not snapshot:
                raise ValueError("Frozen benchmark snapshot is missing")
            body = QueryInput(
                question=row["question"],
                as_of_date=row.get("as_of_date"),
                version_ids=row.get("version_ids", []),
            )
            started = time.monotonic()
            # One client per question: reuse identical routing/rerank decisions, never across users.
            jev = Jev(total_budget=total_budget)
            predicted = route(body.question, jev)
            result = run(body, snapshot, decision_client=jev)
            latencies.append(time.monotonic() - started)
            truth.append(row["intent"])
            predictions.append(predicted)
            if row.get("relevance"):
                from legal_rag.workflow.rag_flow import rerank

                candidates = retrieval.search(
                    body.question, snapshot["id"], [str(v) for v in body.version_ids] or None
                )
                relevant = {k for k, grade in row["relevance"].items() if grade >= 2}
                recalls.append(
                    len({str(c["id"]) for c in candidates[:20]} & relevant) / len(relevant) if relevant else 0
                )
                rrf_scores.append(ndcg([c["id"] for c in candidates], row["relevance"]))
                # Evaluate the ranked candidate list before applying the eight-passage generation budget.
                ranked = rerank(body.question, candidates, jev, limit=20)
                jev_scores.append(ndcg([c["id"] for c in ranked], row["relevance"]))
                if model and candidates:
                    scores = model.compute_score([[body.question, c["raw_text"]] for c in candidates[:20]])
                    if isinstance(scores, (int, float)):
                        scores = [scores]
                    order = sorted(zip(scores, candidates[:20], strict=True), key=lambda pair: -pair[0])
                    baseline_scores.append(ndcg([c["id"] for _, c in order], row["relevance"]))
            outcomes.append(
                {
                    "id": row["id"],
                    "result": result.model_dump(mode="json"),
                    "citation_review": None,
                    "temporal_review": None,
                    "jev_post_attempts": jev.budget.used,
                }
            )
        except ProviderError as exc:
            failure = {"id": row["id"], "code": exc.code, "jev_post_attempts": jev.budget.used if jev else 0}
            break
    metrics = {
        "routing_macro_f1": macro_f1(truth, predictions),
        "recall_at_20": statistics.mean(recalls) if recalls else 0,
        "jev_ndcg_at_10": statistics.mean(jev_scores) if jev_scores else 0,
        "rrf_ndcg_at_10": statistics.mean(rrf_scores) if rrf_scores else 0,
        "bge_ndcg_at_10": statistics.mean(baseline_scores) if baseline_scores else None,
        "latency_p50_seconds": statistics.median(latencies) if latencies else None,
        "latency_p95_seconds": sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
        "abstention_rate": sum(o["result"]["status"] != "answered" for o in outcomes) / len(outcomes)
        if outcomes
        else None,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "metrics": metrics,
        "beta_gate": "blocked",
        "reason": failure["code"] if failure else "Manual citation and temporal review required",
        "execution_status": "incomplete" if failure else "completed",
        "failed_question": failure,
        "held_out_questions": sum(r["split"] == "held_out" for r in rows),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "decision_provider": {"base_url": config.jev_ai_base_url, "model": config.jev_ai_model},
        "jev_post_attempts": total_budget.used,
        "jev_call_limit": total_budget.limit,
        "outcomes": outcomes,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return False


def check_reviewed_report(report):
    if report.get("execution_status") != "completed":
        return False
    metrics, outcomes = report.get("metrics", {}), report.get("outcomes", [])
    if not outcomes or len(outcomes) != report.get("held_out_questions"):
        return False
    for outcome in outcomes:
        citation, temporal = outcome.get("citation_review"), outcome.get("temporal_review")
        if not isinstance(citation, dict) or not isinstance(temporal, dict):
            return False
        total, supported, errors = (
            citation.get("total_claims"),
            citation.get("supported_claims"),
            temporal.get("errors"),
        )
        if any(type(value) is not int for value in (total, supported, errors)):
            return False
        if not 0 <= supported <= total or errors != 0:
            return False
        if not citation.get("reviewed_by") or not temporal.get("reviewed_by"):
            return False
    for key in ("routing_macro_f1", "recall_at_20", "jev_ndcg_at_10", "rrf_ndcg_at_10", "bge_ndcg_at_10"):
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            return False
    total = sum(o["citation_review"]["total_claims"] for o in outcomes)
    supported = sum(o["citation_review"]["supported_claims"] for o in outcomes)
    if not total:
        return False
    return (
        metrics["routing_macro_f1"] >= 0.9
        and metrics["recall_at_20"] >= 0.9
        and supported / total >= 0.95
        and metrics["jev_ndcg_at_10"] >= max(metrics["bge_ndcg_at_10"], metrics["rrf_ndcg_at_10"])
    )
