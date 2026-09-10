"""Fastmarkets adapter (Scrum 26).

The REST shape below is an ILLUSTRATIVE, plausible price-series contract
(Bearer-token auth, a per-series JSON endpoint returning a period/value
array) — it is NOT verified against Fastmarkets' real private API docs; no
live paid credentials exist in this environment to validate against. The
point of this adapter is the seam (auth injection, response parsing, and
credential-error classification), not a certified vendor integration. Swap
the URL/parsing in `fetch_series` for the real contract when one is
available; the ProviderAdapter interface and error semantics don't change.
"""
import httpx

from app.services.providers.base import ProviderAdapter, ProviderCredentialError, ProviderPoint

FASTMARKETS_BASE_URL = "https://api.fastmarkets.com/v1/prices"


class FastmarketsAdapter(ProviderAdapter):
    async def fetch_series(self, credential: dict, series_id: str, region: str) -> list[ProviderPoint]:
        api_key = credential.get("api_key")
        if not api_key:
            raise ProviderCredentialError("rejected", "Credential is missing an api_key field")

        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{FASTMARKETS_BASE_URL}/{series_id}", headers=headers)
        except httpx.RequestError as exc:
            raise ProviderCredentialError("error", f"Request failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise ProviderCredentialError(*_classify_auth_failure(resp))
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderCredentialError("error", f"Fastmarkets returned {resp.status_code}") from exc

        return _parse_response(resp.json(), region)


def _classify_auth_failure(resp: httpx.Response) -> tuple[str, str]:
    """401/403 could mean an invalid key (rejected) or a valid-but-expired
    one (expired) — the two need different messaging for a team admin to act
    on. Falls back to "rejected" if the body doesn't say which."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if body.get("error") == "token_expired":
        return "expired", "Fastmarkets credential has expired"
    return "rejected", "Fastmarkets rejected the credential"


def _parse_response(data: dict, region: str) -> list[ProviderPoint]:
    points: list[ProviderPoint] = []
    for row in data.get("series", []):
        period = row.get("period", "")
        value = row.get("value")
        if not period or value is None:
            continue
        try:
            year, quarter = int(period[:4]), (int(period[5:7]) - 1) // 3 + 1
            points.append(ProviderPoint(region=region, year=year, quarter=quarter, value=float(value)))
        except (ValueError, IndexError, TypeError):
            continue
    return points
