import asyncio

from wallex_agent.agent.ninerouter import NineRouterSettings, build_model
import pytest


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "https://generativelanguage.googleapis.com/v1", "http://user:password@localhost:20128/v1", "http://localhost:20128/v1?api_key=bad"])
def test_direct_provider_or_credential_bearing_router_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        NineRouterSettings(base_url=url, model_id="qa-model", timeout_s=1)


def test_router_client_uses_configured_timeout_without_hidden_retries() -> None:
    settings = NineRouterSettings(base_url="http://127.0.0.1:20128/v1", model_id="qa-model", timeout_s=7.5)
    model = build_model(settings, "synthetic-router-placeholder")
    assert model.client.timeout == 7.5
    assert model.client.max_retries == 0
    assert str(model.client.base_url) == "http://127.0.0.1:20128/v1/"


def test_native_provider_manages_client_lifecycle_across_evaluation_loops() -> None:
    model = build_model(NineRouterSettings(base_url="http://127.0.0.1:20128/v1", model_id="qa-model", timeout_s=3),
                        "synthetic-router-placeholder")

    async def use() -> None:
        async with model:
            assert not model.client.is_closed()

    for _ in range(2):
        asyncio.run(use())
        assert model.client.is_closed()  # native provider closes and refreshes its own HTTP client
