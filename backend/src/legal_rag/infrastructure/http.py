import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from prometheus_client import Counter, Histogram

from legal_rag.config import settings

CALLS = Counter("legal_provider_calls", "Provider requests", ["provider", "status"])


LATENCY = Histogram("legal_provider_seconds", "Provider latency", ["provider"])


TOKENS = Counter("legal_provider_tokens", "Provider token usage", ["provider", "direction"])


MODELS = Counter("legal_provider_model_calls", "Verified provider model responses", ["provider", "model"])


class ProviderError(RuntimeError):
    def __init__(self, provider, code, retry_after_seconds=None):
        self.provider, self.code = provider, code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{provider}:{code}")


def retry_after_seconds(value):
    if not value:
        return None
    if value.isdigit():
        return int(value)
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0, int((target - datetime.now(timezone.utc)).total_seconds()) + 1)
    except (ValueError, TypeError, OverflowError):
        return None


def request_json(
    provider, url, *, headers=None, body=None, method="POST", timeout=None, max_attempts=3, response_info=None
):
    config = settings()
    if provider == "jev" and method.upper() != "GET" and not config.jev_ai_inference_enabled:
        raise ProviderError("jev", "inference_disabled")
    for attempt in range(max_attempts):
        started = time.monotonic()
        try:
            with httpx.Client(
                timeout=timeout or config.provider_timeout_seconds, follow_redirects=False
            ) as client:
                response = client.request(method, url, headers=headers, json=body)
            CALLS.labels(provider, str(response.status_code)).inc()
            delay = retry_after_seconds(response.headers.get("Retry-After"))
            if response.status_code in (429, 529, 502, 503, 504) and attempt < max_attempts - 1:
                if delay is not None and delay > 10:
                    raise ProviderError(provider, str(response.status_code), delay)
                time.sleep(delay if delay is not None else 2**attempt + random.random())
                continue
            if not response.is_success:
                raise ProviderError(provider, str(response.status_code), delay)
            if response_info is not None:
                response_info.update(
                    {
                        "http_status": response.status_code,
                        "destination": str(response.request.url),
                        "billing": {
                            "mode": response.headers.get("X-Jev-Billing"),
                            "input_tokens_charged": response.headers.get("X-Jev-Paid-Input-Tokens-Used"),
                            "credits_charged": response.headers.get("X-Jev-Credits-Charged"),
                        },
                    }
                )
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) and attempt < max_attempts - 1:
                time.sleep(2**attempt + random.random())
                continue
            raise ProviderError(provider, "transport_or_schema") from exc
        finally:
            LATENCY.labels(provider).observe(time.monotonic() - started)
    raise ProviderError(provider, "retry_exhausted")
