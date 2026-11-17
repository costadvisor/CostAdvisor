"""Minimal Claude API client (Wave 4, SCRUM-213 / SCRUM-214).

The backend's first Anthropic client. Deliberately small and synchronous:
FastAPI runs sync endpoints in a threadpool and Celery tasks are sync, so one
code path serves both. Uses ``httpx`` (already a dependency) rather than
adding the SDK.

Every response carries its own cost, because the monthly sweep (SCRUM-214)
needs a per-run spend cap and a run log with "tokens and dollars", and the
profile test run shows an editor what their click cost.

Prices: token prices are per million tokens; web search is billed per search
on top of tokens ($10 per 1,000). Check https://claude.com/pricing when
models change — an unknown model falls back to the most expensive known rate,
so cost is over- rather than under-estimated.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

API_VERSION = "2023-06-01"
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
WEB_SEARCH_USD_PER_SEARCH = 10.0 / 1000

# (input, output) USD per million tokens, standard tier. The longest matching
# model-name prefix wins, so "claude-sonnet-5-5" uses its own row and an older
# "claude-sonnet-4-6" falls back to the family row. Checked November 2026
# against published pricing (https://platform.claude.com/docs/en/about-claude/pricing).
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5": (0.10, 0.50),    # prompts up to 100k tokens; ours are far smaller
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (4.0, 20.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    # Older generations, by family.
    "claude-haiku": (1.0, 5.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (5.0, 25.0),
}
_FALLBACK_PRICE = max(MODEL_PRICES.values())

_RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
_MAX_RETRIES = 2


class AnthropicNotConfigured(RuntimeError):
    """No API key set — the feature should say so rather than fail."""


class AnthropicError(RuntimeError):
    pass


def _price(model: str) -> tuple[float, float]:
    matches = [p for p in MODEL_PRICES if model.startswith(p)]
    return MODEL_PRICES[max(matches, key=len)] if matches else _FALLBACK_PRICE


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.web_searches += other.web_searches
        self.cost_usd = round(self.cost_usd + other.cost_usd, 6)


@dataclass
class ClaudeResponse:
    content: list[dict]
    stop_reason: str | None
    model: str
    usage: Usage = field(default_factory=Usage)

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_input(self, name: str) -> dict | None:
        """The input of the first client tool call named ``name``, if any."""
        for b in self.content:
            if b.get("type") == "tool_use" and b.get("name") == name:
                return b.get("input")
        return None


def usage_from_api(model: str, raw: dict | None) -> Usage:
    raw = raw or {}
    inp = int(raw.get("input_tokens") or 0)
    inp += int(raw.get("cache_creation_input_tokens") or 0)
    inp += int(raw.get("cache_read_input_tokens") or 0)
    out = int(raw.get("output_tokens") or 0)
    searches = int((raw.get("server_tool_use") or {}).get("web_search_requests") or 0)
    in_price, out_price = _price(model)
    cost = inp / 1e6 * in_price + out / 1e6 * out_price + searches * WEB_SEARCH_USD_PER_SEARCH
    return Usage(input_tokens=inp, output_tokens=out, web_searches=searches,
                 cost_usd=round(cost, 6))


def is_configured() -> bool:
    return bool(get_settings().anthropic_api_key)


def web_search_tool(max_uses: int, blocked_domains: list[str] | None = None) -> dict:
    tool = {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_uses}
    if blocked_domains:
        tool["blocked_domains"] = blocked_domains
    return tool


def create_message(
    *,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    max_tokens: int = 4096,
    model: str | None = None,
    client: httpx.Client | None = None,
    timeout: float | None = None,
    retries: int = _MAX_RETRIES,
) -> ClaudeResponse:
    """One Messages API call. Retries transient failures; raises on the rest.

    ``timeout`` caps this call (seconds) below the configured default, so a
    caller with a time budget (the profile test run) can stay inside it.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AnthropicNotConfigured("ANTHROPIC_API_KEY is not set")

    model = model or settings.anthropic_model
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice

    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    url = f"{settings.anthropic_api_url.rstrip('/')}/v1/messages"
    own_client = client is None
    limit = min(float(settings.anthropic_timeout), timeout or float(settings.anthropic_timeout))
    http = client or httpx.Client(timeout=limit)
    try:
        for attempt in range(retries + 1):
            try:
                resp = http.post(url, json=body, headers=headers, timeout=limit)
            except httpx.TimeoutException as e:
                raise AnthropicError(f"Claude API took longer than {limit:.0f}s") from e
            except httpx.HTTPError as e:
                if attempt == retries:
                    raise AnthropicError(f"Claude API unreachable: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in _RETRY_STATUSES and attempt < retries:
                logger.info("Claude API %s, retrying", resp.status_code)
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise AnthropicError(f"Claude API error {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            return ClaudeResponse(
                content=data.get("content") or [],
                stop_reason=data.get("stop_reason"),
                model=data.get("model") or model,
                usage=usage_from_api(model, data.get("usage")),
            )
    finally:
        if own_client:
            http.close()
    raise AnthropicError("Claude API: retries exhausted")  # pragma: no cover
