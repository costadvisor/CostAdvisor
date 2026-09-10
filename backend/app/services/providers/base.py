"""Provider-adapter interface for team-supplied paid index credentials
(Fastmarkets/Argus/ICIS, Scrum 26).

Distinct from services/scraper.py's BaseScraper/SCRAPER_REGISTRY, which is
for PLATFORM-level, admin-configured commodities keyed by CommodityIndex.name.
An adapter here is keyed by a team's own credential + the vendor's own
series identifier, not by our commodity catalog.
"""
from abc import ABC, abstractmethod
from typing import Literal


class ProviderCredentialError(Exception):
    """Raised by an adapter (or the credential lookup) for any failure that
    should degrade gracefully rather than crash — the caller catches this,
    updates the credential's status/last_error, and leaves existing
    IndexOverride data untouched (Scrum 26 acceptance criterion 3)."""

    def __init__(self, reason: Literal["missing", "expired", "rejected", "error"], detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


class ProviderPoint:
    """One resolved (region, year, quarter, value) point from a provider —
    mirrors services/scraper.py's ScrapedDataPoint."""

    def __init__(self, region: str, year: int, quarter: int, value: float):
        self.region = region
        self.year = year
        self.quarter = quarter
        self.value = value


class ProviderAdapter(ABC):
    """One vendor integration. `credential` is the decrypted dict a team
    registered (shape is provider-specific); `series_id` is the vendor's own
    series/ticker code for the commodity, stored in TeamIndexSource.scrape_config."""

    @abstractmethod
    async def fetch_series(self, credential: dict, series_id: str, region: str) -> list[ProviderPoint]:
        ...
