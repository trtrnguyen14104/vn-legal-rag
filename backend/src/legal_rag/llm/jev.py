import copy
import hashlib
import json
import re
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field

from prometheus_client import Counter

from legal_rag.config import settings
from legal_rag.infrastructure.http import MODELS, TOKENS, ProviderError, request_json

CACHE_HITS = Counter("legal_jev_cache_hits", "Repeated decisions reused within one query")

# Jev's hosted API can report the upstream TypeSafe ID for this same version.
MODEL_ALIASES = {
    "typesafe/jev-1.13": "jev-1.13.0",
    "typesafe/jev-1.13-20260917": "jev-1.13.0",
}


@dataclass
class DecisionBudget:
    limit: int
    used: int = 0
    lock: object = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if self.limit < 0:
            raise ValueError("Decision budget cannot be negative")


def reserve_decision(*budgets):
    unique = sorted({id(b): b for b in budgets if b is not None}.values(), key=id)
    with ExitStack() as stack:
        for budget in unique:
            stack.enter_context(budget.lock)
        if any(budget.used >= budget.limit for budget in unique):
            raise ProviderError("jev", "call_budget_exhausted")
        for budget in unique:
            # Count attempts, including uncertain outcomes. Never refund a timeout locally.
            budget.used += 1


class Jev:
    def __init__(self, *, max_calls=None, total_budget=None):
        self.last_response_info = {}
        configured = settings().jev_ai_max_calls_per_query
        self.budget = DecisionBudget(configured if max_calls is None else min(max_calls, configured))
        self.total_budget = total_budget
        self._cache = {}
        self._lock = threading.Lock()

    def _headers(self):
        key = settings().jev_ai_api_key.get_secret_value()
        if not key:
            raise ProviderError("jev", "not_configured")
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def models(self):
        result = request_json(
            "jev", settings().jev_ai_base_url + "/v1/models", headers=self._headers(), method="GET"
        )
        if not isinstance(result, dict) or not isinstance(result.get("models"), list):
            raise ProviderError("jev", "invalid_models_schema")
        if any(not isinstance(m, dict) or not isinstance(m.get("name"), str) for m in result["models"]):
            raise ProviderError("jev", "invalid_models_schema")
        return result["models"]

    def evaluate(self, state, questions):
        with self._lock:
            return self._evaluate(state, questions)

    def _evaluate(self, state, questions):
        config = settings()
        if not config.jev_ai_inference_enabled:
            raise ProviderError("jev", "inference_disabled")
        headers = self._headers()
        if not 1 <= len(questions) <= 64 or any(
            not re.fullmatch(r"[\w.-]{1,64}", name, re.ASCII) for name in questions
        ):
            raise ProviderError("jev", "request_limits")
        body = {"model": config.jev_ai_model, "state": state, "questions": questions}
        serialized = json.dumps(body, ensure_ascii=True, sort_keys=True).encode("utf-8")
        if len(serialized) > 256000:
            raise ProviderError("jev", "request_limits")
        cache_key = hashlib.sha256(serialized).hexdigest()
        if cache_key in self._cache:
            answers, info = self._cache[cache_key]
            self.last_response_info = copy.deepcopy(info) | {"cache_hit": True}
            CACHE_HITS.inc()
            return copy.deepcopy(answers)
        reserve_decision(self.budget, self.total_budget)
        self.last_response_info = {}
        result = request_json(
            "jev",
            config.jev_ai_base_url + "/v1/systemone",
            headers=headers,
            body=body,
            max_attempts=1,
            response_info=self.last_response_info,
        )
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise ProviderError("jev", "invalid_schema")
        answers = result["answers"]
        if not result.get("model") or set(answers) != set(questions):
            raise ProviderError("jev", "invalid_schema")
        reported_model = result["model"]
        self.last_response_info["requested_model"] = config.jev_ai_model
        if isinstance(reported_model, str) and re.fullmatch(r"[\w./~-]{1,128}", reported_model, re.ASCII):
            self.last_response_info["reported_model"] = reported_model
        actual_model = (
            MODEL_ALIASES.get(reported_model, reported_model) if isinstance(reported_model, str) else None
        )
        if not isinstance(actual_model, str) or not re.fullmatch(r"jev-[\w.-]{1,64}", actual_model, re.ASCII):
            raise ProviderError("jev", "unexpected_model")
        if config.jev_ai_model != "jev-latest" and actual_model != config.jev_ai_model:
            raise ProviderError("jev", "unexpected_model")
        self.last_response_info.update(
            model=actual_model, requested_model=config.jev_ai_model, usage=result.get("usage", {})
        )
        MODELS.labels("jev", actual_model).inc()
        for name, question in questions.items():
            answer = answers[name]
            if not isinstance(answer, dict):
                raise ProviderError("jev", "invalid_schema")
            kind = question["type"]
            if answer.get("type") != kind:
                raise ProviderError("jev", "invalid_type")
            if kind == "noul":
                value = answer.get("noul")
                if not isinstance(value, (float, int)) or not 0 <= value <= 1:
                    raise ProviderError("jev", "invalid_probability")
            else:
                probabilities = answer.get("probabilities", {})
                confidence = answer.get("confidence")
                if not probabilities or not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
                    raise ProviderError("jev", "invalid_confidence")
                if any(not isinstance(p, (int, float)) or not 0 <= p <= 1 for p in probabilities.values()):
                    raise ProviderError("jev", "invalid_probability")
                if abs(sum(probabilities.values()) - 1) > 0.02:
                    raise ProviderError("jev", "invalid_distribution")
                if kind == "choice" and (
                    answer.get("choice") not in question["criteria"]
                    or set(probabilities) != set(question["criteria"])
                ):
                    raise ProviderError("jev", "invalid_choice")
                if kind == "score":
                    expected = {str(i) for i in range(len(question["criteria"]))}
                    score = answer.get("score")
                    if (
                        set(probabilities) != expected
                        or not isinstance(score, (float, int))
                        or not 0 <= score <= len(expected) - 1
                    ):
                        raise ProviderError("jev", "invalid_score")
        for direction in ("input", "output"):
            TOKENS.labels("jev", direction).inc(result.get("usage", {}).get(f"{direction}_tokens", 0))
        self._cache[cache_key] = (copy.deepcopy(answers), copy.deepcopy(self.last_response_info))
        return answers
