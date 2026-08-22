"""
ECB (European Central Bank) exchange rate scrapers.

Uses the ECB Statistical Data Warehouse SDMX REST API (free, no auth).
Returns quarterly average exchange rates.

Docs: https://data.ecb.europa.eu/help/api/data
"""
import xml.etree.ElementTree as ET

import httpx

from app.services.scraper import BaseScraper, ScrapedDataPoint
from app.config import get_settings


class ECBScraper(BaseScraper):
    """
    Base scraper for ECB exchange rate series via SDMX-ML.

    The ECB publishes rates as units-of-foreign-currency per 1 EUR.
    For example, EUR/USD = 1.08 means 1 EUR = 1.08 USD.

    Subclasses set:
      - commodity_name: maps to CommodityIndex.name (e.g. "EUR/USD")
      - CURRENCY_CODE: ISO 4217 code (e.g. "USD")
      - FREQ: frequency filter ("Q" for quarterly, "M" for monthly)
    """

    CURRENCY_CODE: str = ""
    FREQ: str = "Q"

    def __init__(self):
        super().__init__(self.commodity_name)

    async def fetch(self) -> list[ScrapedDataPoint]:
        settings = get_settings()
        base = settings.ecb_api_base
        # ECB SDMX data flow: EXR (Exchange Rates)
        # Key structure: FREQ.CURRENCY.EUR.SP00.A
        key = f"{self.FREQ}.{self.CURRENCY_CODE}.EUR.SP00.A"
        url = f"{base}/data/EXR/{key}"
        params = {"lastNObservations": "24"}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

        return self._parse_sdmx_ml(resp.text)

    @staticmethod
    def _parse_sdmx_ml(xml_text: str) -> list[ScrapedDataPoint]:
        """Parse ECB SDMX-ML response into quarterly data points."""
        root = ET.fromstring(xml_text)
        quarterly: dict[tuple[int, int], list[float]] = {}

        for elem in root.iter():
            tag = elem.tag
            # Match Obs elements in any namespace
            if not (tag.endswith("}Obs") or tag == "Obs"):
                continue
            # ECB SDMX-ML uses child elements ObsDimension/ObsValue
            period = ""
            value = None
            for child in elem:
                local = child.tag.split("}")[-1]
                if local == "ObsDimension":
                    period = child.get("value", "")
                elif local == "ObsValue":
                    value = child.get("value")
            # Fallback: some SDMX flavours put period/value as attributes
            if not period:
                period = elem.get("TIME_PERIOD", "")
            if value is None:
                value = elem.get("OBS_VALUE")
            if not period or value is None:
                continue
            try:
                val = float(value)
            except ValueError:
                continue

            try:
                if "Q" in period:
                    # "2024-Q1" format
                    parts = period.split("-Q")
                    year, quarter = int(parts[0]), int(parts[1])
                elif len(period) == 7 and period[4] == "-":
                    # Monthly "2024-03"
                    year = int(period[:4])
                    month = int(period[5:7])
                    quarter = (month - 1) // 3 + 1
                else:
                    continue
            except (ValueError, IndexError):
                continue

            quarterly.setdefault((year, quarter), []).append(val)

        results = []
        for (year, quarter), vals in sorted(quarterly.items()):
            avg = sum(vals) / len(vals)
            results.append(ScrapedDataPoint(
                region="GLOBAL", year=year, quarter=quarter,
                value=round(avg, 4),
            ))
        return results


# ── Concrete ECB scrapers ──────────────────────────────────────────────
# All rates are quoted as foreign currency per 1 EUR.

class ECBEURUSDScraper(ECBScraper):
    """EUR/USD exchange rate (quarterly average)."""
    commodity_name = "EUR/USD"
    CURRENCY_CODE = "USD"


class ECBGBPEURScraper(ECBScraper):
    """GBP/EUR exchange rate (quarterly average)."""
    commodity_name = "GBP/EUR"
    CURRENCY_CODE = "GBP"


class ECBCNYEURScraper(ECBScraper):
    """CNY/EUR exchange rate (quarterly average)."""
    commodity_name = "CNY/EUR"
    CURRENCY_CODE = "CNY"


class ECBJPYEURScraper(ECBScraper):
    """JPY/EUR exchange rate (quarterly average)."""
    commodity_name = "JPY/EUR"
    CURRENCY_CODE = "JPY"


class ECBIDREURScraper(ECBScraper):
    """IDR/EUR exchange rate (quarterly average)."""
    commodity_name = "IDR/EUR"
    CURRENCY_CODE = "IDR"


class ECBPHPEURScraper(ECBScraper):
    """PHP/EUR exchange rate (quarterly average)."""
    commodity_name = "PHP/EUR"
    CURRENCY_CODE = "PHP"


class ECBUrlScraper(ECBScraper):
    """ECB scraper that accepts a full quarterly URL from fx_pairs.scrape_url."""

    def __init__(self, commodity_name: str, scrape_url: str):
        # Skip super().__init__() signature that calls commodity_name attribute
        self.commodity_name = commodity_name
        self.scrape_url = scrape_url

    async def fetch(self) -> list[ScrapedDataPoint]:
        params = {"lastNObservations": "24"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self.scrape_url, params=params)
            resp.raise_for_status()
        return self._parse_sdmx_ml(resp.text)


class ECBLiveScraper:
    """Fetch a single daily (live) rate from ECB by swapping Q→D in the pair's scrape_url."""

    def __init__(self, pair_name: str, quarterly_url: str):
        self.pair_name = pair_name
        # Replace Q frequency with D to get daily data
        self.daily_url = quarterly_url.replace("/Q.", "/D.")

    async def fetch_live(self) -> float | None:
        params = {"lastNObservations": "1"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(self.daily_url, params=params)
                resp.raise_for_status()
            points = ECBScraper._parse_sdmx_ml(resp.text)
            return float(points[-1].value) if points else None
        except Exception:
            return None
