"""NineRouter: the ONLY real-model path (AGENTS.md §6a). OpenAI-compatible router on the owner's machine.

Verified 2026-10-22 against the owner's local instance: `GET /v1/models` lists models; `av/*` models return an empty
`finish_reason="in_progress"` body for non-streaming chat completions but work when streamed, so the agent uses
streaming requests. Credentials: `NINEROUTER_API_KEY`, or `NINEROUTER_API_KEY_FILE` pointing OUTSIDE the repository.
The key is never printed, logged, journaled, exported or placed in a prompt.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_BASE_URL = "http://localhost:20128/v1"
REPO_ROOT = Path(__file__).resolve().parents[3]


class NineRouterNotConfigured(RuntimeError):
    """No usable credential. Blocks only real-model evaluation/runs, never deterministic work."""


class NineRouterSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    model_id: str
    timeout_s: float = Field(gt=0, allow_inf_nan=False)
    stream: bool = True

    @field_validator("base_url")
    @classmethod
    def router_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("NineRouter needs an HTTP endpoint without URL credentials or query")
        host = parsed.hostname.lower()
        if any(host == domain or host.endswith("." + domain) for domain in ("openai.com", "anthropic.com", "googleapis.com")):
            raise ValueError("direct provider endpoints are not permitted; configure the owner's NineRouter")
        return value.rstrip("/")

    @property
    def provider_label(self) -> str:
        return "ninerouter"


def load_settings(*, model_id: str | None, timeout_s: float, env: Mapping[str, str] | None = None) -> NineRouterSettings:
    e = os.environ if env is None else env
    model = e.get("NINEROUTER_MODEL") or model_id
    if not model:
        raise NineRouterNotConfigured("no model id: set agent.model_id or NINEROUTER_MODEL")
    return NineRouterSettings(base_url=e.get("NINEROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/"), model_id=model,
                              timeout_s=float(e.get("NINEROUTER_TIMEOUT_S", timeout_s)))


def resolve_api_key(env: Mapping[str, str] | None = None, *, repo_root: Path = REPO_ROOT) -> str:
    e = os.environ if env is None else env
    direct = e.get("NINEROUTER_API_KEY", "").strip()
    if direct:
        return direct
    path = e.get("NINEROUTER_API_KEY_FILE", "").strip()
    if not path:
        raise NineRouterNotConfigured("set NINEROUTER_API_KEY or NINEROUTER_API_KEY_FILE (outside the repo)")
    p = Path(path).expanduser().resolve()
    if p.is_relative_to(repo_root.resolve()):
        raise NineRouterNotConfigured("refusing a NineRouter key file inside the repository; move it outside")
    if not p.is_file():
        raise NineRouterNotConfigured("NINEROUTER_API_KEY_FILE does not point to a readable file")
    key = p.read_text().strip()
    if not key:
        raise NineRouterNotConfigured("NineRouter key file is empty")
    return key


def build_model(settings: NineRouterSettings, api_key: str):  # type: ignore[no-untyped-def]
    """PydanticAI OpenAI-compatible model pointed at NineRouter (no custom client)."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    # Let PydanticAI own the HTTP client: it refreshes loop-bound connections when native evaluation
    # runs predictions on different worker loops. Passing our own AsyncOpenAI disables that lifecycle.
    provider = OpenAIProvider(base_url=settings.base_url, api_key=api_key)
    provider.client.timeout = settings.timeout_s
    provider.client.max_retries = 0
    return OpenAIChatModel(settings.model_id, provider=provider)
