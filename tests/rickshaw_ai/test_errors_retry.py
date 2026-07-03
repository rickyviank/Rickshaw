"""Error mapping and retry policy."""

from __future__ import annotations

import httpx
import pytest
import respx

from rickshaw_ai import (
    AuthError,
    GenerateRequest,
    InvalidRequestError,
    Message,
    NotFoundError,
    OverloadedError,
    ProviderError,
    RateLimitError,
)
from rickshaw_ai.registry import RetryPolicy
from tests.rickshaw_ai.conftest import make_models

URL = "https://oai.test/chat/completions"
OK = {"model": "test-model",
      "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _models(retry=None):
    return make_models(protocol="openai", provider_id="oai", base_url="https://oai.test",
                       retry=retry or RetryPolicy(max_retries=0))


async def _run(models):
    return await models.get("oai/test-model").generate(
        GenerateRequest(messages=[Message.user("hi")])
    )


@pytest.mark.parametrize("status,exc", [
    (401, AuthError),
    (403, AuthError),
    (404, NotFoundError),
    (429, RateLimitError),
    (529, OverloadedError),
    (400, InvalidRequestError),
    (500, ProviderError),
])
@respx.mock
async def test_status_maps_to_error(status, exc):
    respx.post(URL).mock(return_value=httpx.Response(status, json={"error": {"message": "boom"}}))
    with pytest.raises(exc) as info:
        await _run(_models())
    assert info.value.provider_id == "oai"
    assert info.value.status_code == status


@respx.mock
async def test_rate_limit_carries_retry_after():
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"retry-after": "2"},
                                                     json={"error": {"message": "slow down"}}))
    with pytest.raises(RateLimitError) as info:
        await _run(_models())
    assert info.value.retry_after == 2.0


@respx.mock
async def test_retryable_error_is_retried_then_succeeds():
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(503, json={"error": {"message": "temporarily down"}}),
        httpx.Response(200, json=OK),
    ])
    models = _models(RetryPolicy(max_retries=2, initial_backoff=0.0, jitter=0.0))
    result = await _run(models)
    assert result.text == "ok"
    assert route.call_count == 2


@respx.mock
async def test_non_retryable_error_not_retried():
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
    models = _models(RetryPolicy(max_retries=5, initial_backoff=0.0, jitter=0.0))
    with pytest.raises(InvalidRequestError):
        await _run(models)
    assert route.call_count == 1


@respx.mock
async def test_retries_exhausted_raises_last_error():
    route = respx.post(URL).mock(return_value=httpx.Response(500, json={"error": {"message": "down"}}))
    models = _models(RetryPolicy(max_retries=2, initial_backoff=0.0, jitter=0.0))
    with pytest.raises(ProviderError):
        await _run(models)
    assert route.call_count == 3  # initial + 2 retries
