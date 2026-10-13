import httpx
import pytest

from legal_rag.infrastructure import http as transport
from legal_rag.infrastructure.http import ProviderError
from legal_rag.llm import jev as providers
from legal_rag.llm.jev import Jev


@pytest.fixture(autouse=True)
def enable_mock_decisions(monkeypatch):
    # All provider transports in this module are mocked; real env remains disabled.
    monkeypatch.setenv("JEV_AI_INFERENCE_ENABLED", "true")


@pytest.mark.parametrize("status,expected_calls", [(401, 1), (429, 3), (503, 3)])
def test_provider_retry_is_bounded_and_auth_failure_is_not_retried(monkeypatch, status, expected_calls):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, *args, **kwargs):
            calls.append(1)
            return httpx.Response(status, request=httpx.Request("POST", "https://example.test"))

    monkeypatch.setattr(transport.httpx, "Client", Client)
    monkeypatch.setattr(transport.time, "sleep", lambda _: None)
    with pytest.raises(ProviderError, match=str(status)):
        transport.request_json("jev", "https://example.test")
    assert len(calls) == expected_calls


def test_jev_rejects_an_unexpected_model(monkeypatch):
    monkeypatch.setenv("JEV_AI_MODEL", "jev-1.13.0")
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only")
    monkeypatch.setattr(
        providers,
        "request_json",
        lambda *a, **k: {
            "model": "jev-latest",
            "answers": {"check": {"type": "noul", "noul": 0.9}},
        },
    )
    with pytest.raises(ProviderError, match="unexpected_model"):
        Jev().evaluate("state", {"check": {"type": "noul", "instructions": "Check"}})


@pytest.mark.parametrize("requested_model", ["jev-latest", "jev-1.13.0"])
@pytest.mark.parametrize("reported_model", ["typesafe/jev-1.13", "typesafe/jev-1.13-20260917"])
def test_jev_accepts_equivalent_typesafe_model_ids(monkeypatch, requested_model, reported_model):
    monkeypatch.setenv("JEV_AI_MODEL", requested_model)

    def handler(request):
        return httpx.Response(
            200,
            json={"model": reported_model, "answers": {"check": {"type": "noul", "noul": 0.9}}},
        )

    transport_client(monkeypatch, handler)
    client = Jev(max_calls=1)
    assert (
        client.evaluate("Synthetic evidence", {"check": {"type": "noul", "instructions": "Check"}})["check"][
            "noul"
        ]
        == 0.9
    )
    assert client.last_response_info["model"] == "jev-1.13.0"
    assert client.last_response_info["reported_model"] == reported_model
    assert client.last_response_info["requested_model"] == requested_model
    assert client.budget.used == 1


@pytest.mark.parametrize(
    "model", ["gpt-6-luna", "other/jev-1.13", "typesafe/jev-1.14-20261013", ["jev-1.13.0"]]
)
def test_jev_alias_support_does_not_accept_other_models(monkeypatch, model):
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only")
    monkeypatch.setattr(
        providers,
        "request_json",
        lambda *a, **k: {"model": model, "answers": {"check": {"type": "noul", "noul": 0.9}}},
    )
    with pytest.raises(ProviderError, match="unexpected_model"):
        Jev().evaluate("Synthetic evidence", {"check": {"type": "noul", "instructions": "Check"}})


def test_jev_rejects_missing_answer_and_invalid_probability(monkeypatch):
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only")
    monkeypatch.setattr(providers, "request_json", lambda *a, **k: {"model": "jev-1.13.0", "answers": {}})
    question = {"check": {"type": "noul", "instructions": "Does evidence support the claim?"}}
    with pytest.raises(ProviderError, match="invalid_schema"):
        Jev().evaluate("state", question)
    monkeypatch.setattr(
        providers,
        "request_json",
        lambda *a, **k: {"model": "jev-1.13.0", "answers": {"check": {"type": "noul", "noul": 1.3}}},
    )
    with pytest.raises(ProviderError, match="invalid_probability"):
        Jev().evaluate("state", question)


def test_jev_noul_is_not_required_to_have_confidence(monkeypatch):
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only")
    monkeypatch.setattr(
        providers,
        "request_json",
        lambda *a, **k: {"model": "jev-1.13.0", "answers": {"check": {"type": "noul", "noul": 0.9}}},
    )
    assert (
        Jev().evaluate("state", {"check": {"type": "noul", "instructions": "Check"}})["check"]["noul"] == 0.9
    )


def transport_client(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(
        transport.httpx,
        "Client",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only-jev-key")


def test_jev_uses_correct_destination_bearer_and_resolved_model(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "intent": {
                        "type": "choice",
                        "choice": "lookup",
                        "probabilities": {"lookup": 0.95, "other": 0.05},
                        "confidence": 0.95,
                    }
                },
                "usage": {"input_tokens": 30, "output_tokens": 10},
            },
            headers={"X-Jev-Billing": "tokens"},
        )

    transport_client(monkeypatch, handler)
    client = Jev()
    answer = client.evaluate(
        "Legal question",
        {"intent": {"type": "choice", "instructions": "Route", "criteria": {"lookup": None, "other": None}}},
    )
    assert answer["intent"]["choice"] == "lookup"
    assert str(requests[0].url) == "https://jev-ai.pro/api/v1/systemone"
    assert requests[0].headers["Authorization"] == "Bearer test-only-jev-key"
    assert client.last_response_info["model"] == "jev-1.13.0"
    assert "test-only-jev-key" not in str(client.last_response_info)


@pytest.mark.parametrize("status", [401, 402, 404, 422, 429, 502, 503, 504])
def test_jev_decision_failure_never_replays_post(monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"code": status, "message": "redacted"}}, headers={"Retry-After": "30"}
        )

    transport_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match=str(status)) as failure:
        Jev().evaluate("test", {"check": {"type": "noul", "instructions": "Check"}})
    assert failure.value.retry_after_seconds == 30
    assert len(calls) == 1


def test_jev_timeout_never_replays_uncertain_decision(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Timeout", request=request)

    transport_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="transport_or_schema"):
        Jev().evaluate("test", {"check": {"type": "noul", "instructions": "Check"}})
    assert len(calls) == 1


def test_jev_models_check_is_get_without_inference(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"models": [{"name": "jev-latest", "description": "Stable alias"}]})

    transport_client(monkeypatch, handler)
    assert Jev().models()[0]["name"] == "jev-latest"
    assert [(r.method, str(r.url)) for r in requests] == [("GET", "https://jev-ai.pro/api/v1/models")]


def test_jev_rejects_wrong_host_before_sending_key(monkeypatch):
    from legal_rag.config import settings

    monkeypatch.setenv("JEV_AI_BASE_URL", "https://api.typesafe.ai/v1")
    with pytest.raises(ValueError, match="JEV_AI_BASE_URL"):
        settings()


def test_jev_rejects_oversized_request_before_inference(monkeypatch):
    monkeypatch.setenv("JEV_AI_API_KEY", "test-only")
    monkeypatch.setattr(
        providers, "request_json", lambda *a, **kw: pytest.fail("must not send oversized request")
    )
    with pytest.raises(ProviderError, match="request_limits"):
        Jev().evaluate("x" * 256000, {"check": {"type": "noul", "instructions": "Check"}})


def test_disabled_inference_blocks_post_but_models_get_remains_available(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"models": [{"name": "jev-latest"}]})

    transport_client(monkeypatch, handler)
    monkeypatch.setenv("JEV_AI_INFERENCE_ENABLED", "false")
    client = Jev()
    assert client.models()[0]["name"] == "jev-latest"
    with pytest.raises(ProviderError, match="inference_disabled"):
        client.evaluate("test", {"check": {"type": "noul", "instructions": "Check"}})
    assert client.budget.used == 0
    assert calls == ["GET"]


def test_repeated_decision_is_cached_without_spending_or_mutation_leak(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"check": {"type": "noul", "noul": 0.9}},
                "usage": {"input_tokens": 12, "output_tokens": 2},
            },
        )

    transport_client(monkeypatch, handler)
    client = Jev(max_calls=1)
    questions = {"check": {"type": "noul", "instructions": "Check"}}
    first = client.evaluate("same evidence", questions)
    first["check"]["noul"] = 0
    assert client.evaluate("same evidence", questions)["check"]["noul"] == 0.9
    assert client.last_response_info["cache_hit"] is True
    assert len(calls) == client.budget.used == 1
    with pytest.raises(ProviderError, match="call_budget_exhausted"):
        client.evaluate("different evidence", questions)
    assert len(calls) == 1


def test_shared_benchmark_budget_cannot_be_reset_by_new_client(monkeypatch):
    from legal_rag.llm.jev import DecisionBudget

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"model": "jev-1.13.0", "answers": {"check": {"type": "noul", "noul": 0.9}}}
        )

    transport_client(monkeypatch, handler)
    budget = DecisionBudget(1)
    questions = {"check": {"type": "noul", "instructions": "Check"}}
    Jev(total_budget=budget).evaluate("first", questions)
    second = Jev(total_budget=budget)
    with pytest.raises(ProviderError, match="call_budget_exhausted"):
        second.evaluate("second", questions)
    assert budget.used == len(calls) == 1
    assert second.budget.used == 0


def test_uncertain_timeout_still_consumes_call_budget(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("uncertain", request=request)

    transport_client(monkeypatch, handler)
    client = Jev(max_calls=1)
    questions = {"check": {"type": "noul", "instructions": "Check"}}
    with pytest.raises(ProviderError, match="transport_or_schema"):
        client.evaluate("test", questions)
    with pytest.raises(ProviderError, match="call_budget_exhausted"):
        client.evaluate("test", questions)
    assert len(calls) == 1
