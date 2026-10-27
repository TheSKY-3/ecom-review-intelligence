"""Unit tests for the opt-in insight gateway and the model allowlist."""

from types import SimpleNamespace

import pytest

from ecom_review_intelligence.model_gateway import (
    DeepSeekInsightGateway,
    GatewayError,
    build_insight_gateway,
    model_options,
    normalize_request_model,
)

CONFIG = {"base_url": "https://api.deepseek.com", "model": "deepseek-test"}


class _ProviderError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("provider failure")
        self.status_code = status_code


class _Completions:
    def __init__(self, item):
        self.item = item

    def create(self, **kwargs):
        del kwargs
        if isinstance(self.item, Exception):
            raise self.item
        return self.item


def _gateway(item):
    client = SimpleNamespace(
        max_retries=0,
        chat=SimpleNamespace(completions=_Completions(item)),
    )
    return DeepSeekInsightGateway(client=client, model="deepseek-test")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_reported_as_key_rejected(status):
    gateway = _gateway(_ProviderError(status))

    with pytest.raises(GatewayError) as error:
        gateway.analyse({})

    assert error.value.error_code == "KEY_REJECTED"


def test_a_rejected_request_is_reported_separately():
    gateway = _gateway(_ProviderError(400))

    with pytest.raises(GatewayError) as error:
        gateway.analyse({})

    assert error.value.error_code == "MODEL_REQUEST_REJECTED"


def test_rate_limit_is_reported_separately():
    gateway = _gateway(_ProviderError(429))

    with pytest.raises(GatewayError) as error:
        gateway.analyse({})

    assert error.value.error_code == "MODEL_RATE_LIMITED"


def test_build_insight_gateway_honours_a_model_override():
    seen = {}

    def factory(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            max_retries=0,
            chat=SimpleNamespace(completions=_Completions(None)),
        )

    gateway = build_insight_gateway(
        CONFIG,
        client_factory=factory,
        environment={"DEEPSEEK_API_KEY": "sk-test"},
        model="deepseek-other",
    )

    assert gateway is not None
    assert gateway.model == "deepseek-other"
    assert seen["api_key"] == "sk-test"


def test_normalize_request_model_accepts_only_offered_ids():
    assert normalize_request_model(None) is None
    assert normalize_request_model("") is None
    assert normalize_request_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert (
        normalize_request_model("deepseek-v4-flash", options=["deepseek-v4-flash"])
        == "deepseek-v4-flash"
    )
    with pytest.raises(ValueError):
        normalize_request_model("gpt-4o", options=["deepseek-v4-flash"])
    with pytest.raises(ValueError):
        normalize_request_model("bad model", options=["bad model"])


def test_model_options_always_list_the_configured_model_first():
    ids = [model_id for model_id, _label in model_options({"model": "custom-model"})]

    assert ids[0] == "custom-model"
    assert "deepseek-v4-flash" in ids
