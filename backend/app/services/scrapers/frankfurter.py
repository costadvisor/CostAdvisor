import httpx

_BASE = "https://api.frankfurter.app"


class FrankfurterScraper:
    """Fetch live exchange rate from Frankfurter JSON API (ECB-backed, no auth required).

    scrape_url format: https://api.frankfurter.app/latest?from=CNY&to=EUR
    """

    def __init__(self, scrape_url: str):
        self.url = scrape_url

    async def fetch_live(self) -> float | None:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()
            rates = resp.json().get("rates", {})
            return float(next(iter(rates.values()))) if rates else None
        except Exception:
            return None


async def fetch_daily_series(
    from_currency: str, to_currency: str, start_year: int = 2020
) -> dict:
    """Fetch the full daily rate series for a pair from Frankfurter.

    Keys are datetime.date, values are that day's rate (float).

    Returns a dict keyed by datetime.date with that day's rate. Single source
    of truth for both the daily history table and quarterly averaging — one
    HTTP call covers both.
    """
    from datetime import date as date_cls

    today = date_cls.today()
    start = date_cls(start_year, 1, 1)
    url = f"{_BASE}/{start}..{today}?from={from_currency}&to={to_currency}"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}

    out: dict[date_cls, float] = {}
    for date_str, rates in data.get("rates", {}).items():
        rate = rates.get(to_currency)
        if rate is None:
            continue
        out[date_cls.fromisoformat(date_str)] = float(rate)
    return out


async def fetch_quarterly_rates(
    from_currency: str, to_currency: str, start_year: int = 2020
) -> dict[tuple[int, int], float]:
    """Quarterly average rates for a pair, derived from the daily series."""
    daily = await fetch_daily_series(from_currency, to_currency, start_year)
    buckets: dict[tuple[int, int], list[float]] = {}
    for d, rate in daily.items():
        q = (d.month - 1) // 3 + 1
        buckets.setdefault((d.year, q), []).append(rate)
    return {k: sum(v) / len(v) for k, v in buckets.items()}
