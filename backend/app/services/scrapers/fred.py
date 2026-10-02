"""
FRED (Federal Reserve Economic Data) scrapers.

FRED provides free access with an API key. Get one at:
https://fred.stlouisfed.org/docs/api/api_key.html

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""
import httpx

from app.services.scraper import BaseScraper, ScrapedDataPoint
from app.config import get_settings


class FREDScraper(BaseScraper):
    """
    Base scraper for FRED time series.

    Subclasses set:
      - commodity_name: maps to CommodityIndex.name
      - SERIES_ID: FRED series identifier (e.g. "ECIWAG")
    """

    SERIES_ID: str = ""
    FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self):
        super().__init__(self.commodity_name)

    async def fetch(self) -> list[ScrapedDataPoint]:
        settings = get_settings()
        api_key = settings.fred_api_key
        if not api_key:
            return []  # Gracefully skip if no API key configured

        params = {
            "series_id": self.SERIES_ID,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": "48",  # ~4 years of monthly data
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self.FRED_BASE_URL, params=params)
            resp.raise_for_status()

        return self._parse_observations(resp.json())

    @staticmethod
    def _parse_observations(data: dict) -> list[ScrapedDataPoint]:
        """Parse FRED JSON response into quarterly data points."""
        observations = data.get("observations", [])
        if not observations:
            return []

        quarterly: dict[tuple[int, int], list[float]] = {}
        for obs in observations:
            date_str = obs.get("date", "")
            value_str = obs.get("value", "")
            if not date_str or value_str == "." or not value_str:
                continue
            try:
                year = int(date_str[:4])
                month = int(date_str[5:7])
                val = float(value_str)
            except (ValueError, IndexError):
                continue

            quarter = (month - 1) // 3 + 1
            quarterly.setdefault((year, quarter), []).append(val)

        results = []
        for (year, quarter), vals in sorted(quarterly.items()):
            avg = sum(vals) / len(vals)
            results.append(ScrapedDataPoint(
                region="GLOBAL", year=year, quarter=quarter,
                value=round(avg, 4),
            ))
        return results


# ── Concrete FRED scrapers ─────────────────────────────────────────────

class FREDECIScraper(FREDScraper):
    """Employment Cost Index: Wages and salaries, civilian workers (quarterly, index)."""
    commodity_name = "ECI USA"
    SERIES_ID = "ECIWAG"


class FREDPPIChemicalsScraper(FREDScraper):
    """PPI: Chemicals and allied products (monthly, index 1982=100)."""
    commodity_name = "PPI Chemicals USA"
    SERIES_ID = "PCU325325"


class FREDIndustrialProductionScraper(FREDScraper):
    """Industrial Production Index, total (monthly, index 2017=100)."""
    commodity_name = "Industrial Production USA"
    SERIES_ID = "INDPRO"


class FREDPPIChlorineScraper(FREDScraper):
    """PPI: Alkalies and chlorine (monthly, index 1982=100)."""
    commodity_name = "PPI Chlorine USA"
    SERIES_ID = "WPU061303"


# ── FD-1 remainder: 2026-07 catalog drop short-code names ──────────────
# The current catalog's commodities are named with short type-codes (CU,
# CORN, LNG-JKM, ...), not the descriptive names the scrapers above use —
# so these three are registered directly against the new codes.

class FREDCopperScraper(FREDScraper):
    """Global price of Copper (IMF Primary Commodity Prices, monthly, USD/metric ton)."""
    commodity_name = "CU"
    SERIES_ID = "PCOPPUSDM"


class FREDCornScraper(FREDScraper):
    """Global price of Corn (IMF Primary Commodity Prices, monthly, USD/metric ton)."""
    commodity_name = "CORN"
    SERIES_ID = "PMAIZMTUSDM"


class FREDLNGJKMScraper(FREDScraper):
    """Global price of LNG, Asia — FRED's published proxy for the JKM Asia LNG
    benchmark (IMF Primary Commodity Prices, monthly, USD/MMBtu)."""
    commodity_name = "LNG-JKM"
    SERIES_ID = "PNGASJPUSDM"
