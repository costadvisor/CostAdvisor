"""
Commodity price scrapers.
Each commodity gets a scraper that knows where to fetch data from.
Start with freely available sources; paywalled ones use manual upload.
"""
import re
import httpx
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.index_data import CommodityIndex, IndexValue, IndexOverride
from app.config import get_settings


class ScrapedDataPoint:
    def __init__(self, region: str, year: int, quarter: int, value: float):
        self.region = region
        self.year = year
        self.quarter = quarter
        self.value = value


class BaseScraper(ABC):
    """Base class for all commodity scrapers."""

    def __init__(self, commodity_name: str):
        self.commodity_name = commodity_name

    @abstractmethod
    async def fetch(self) -> list[ScrapedDataPoint]:
        """Fetch data points from the source. Override in subclass."""
        ...

    async def run(self, db: Session):
        """Fetch data and upsert into index_values."""
        commodity = db.query(CommodityIndex).filter(
            CommodityIndex.name == self.commodity_name
        ).first()
        if not commodity:
            return 0

        data = await self.fetch()
        count = 0
        for point in data:
            existing = db.query(IndexValue).filter(
                IndexValue.commodity_id == commodity.id,
                IndexValue.region == point.region,
                IndexValue.year == point.year,
                IndexValue.quarter == point.quarter,
            ).first()

            if existing:
                existing.value = point.value
                existing.source = "scraped"
                existing.scraped_at = datetime.now(timezone.utc)
            else:
                iv = IndexValue(
                    commodity_id=commodity.id,
                    region=point.region,
                    year=point.year,
                    quarter=point.quarter,
                    value=point.value,
                    source="scraped",
                    scraped_at=datetime.now(timezone.utc),
                )
                db.add(iv)
            count += 1

        db.commit()
        return count


class GenericWebScraper:
    """
    Scrapes a value from an arbitrary URL using a config dict.

    scrape_config schema:
        selector  — CSS selector to locate the element (requires selectolax)
        attribute — which part to extract: "text" (default) or an HTML attribute name
        parser    — how to interpret the extracted string: "number" (default) strips
                    non-numeric chars and converts to float
    """

    def __init__(self, url: str, config: dict | None = None):
        self.url = url
        self.config = config or {}

    async def scrape(self) -> float:
        """Fetch the page and extract a single numeric value."""
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(self.url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        text = resp.text

        # JSON path extraction
        json_path = self.config.get("json_path")
        if json_path or "json" in content_type:
            return self._extract_json(resp.json(), json_path)

        # CSS selector extraction
        selector = self.config.get("selector")
        if selector:
            return self._extract_css(text, selector)

        # Fallback: try to find the first number on the page
        return self._parse_number(text)

    def _extract_json(self, data: dict, json_path: str | None) -> float:
        """Walk a dot-separated JSON path to extract a value."""
        if not json_path:
            raise ValueError("JSON response but no json_path in scrape_config")
        parts = json_path.split(".")
        current = data
        for part in parts:
            if isinstance(current, list):
                current = current[int(part)]
            else:
                current = current[part]
        return float(current)

    def _extract_css(self, html: str, selector: str) -> float:
        """Extract value via CSS selector using selectolax."""
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        node = tree.css_first(selector)
        if not node:
            raise ValueError(f"CSS selector '{selector}' matched nothing")

        attribute = self.config.get("attribute", "text")
        if attribute == "text":
            raw = node.text(strip=True)
        else:
            raw = node.attributes.get(attribute, "")
        return self._parse_number(raw)

    @staticmethod
    def _parse_number(raw: str) -> float:
        """Extract a number from a string, stripping currency symbols etc."""
        # Remove everything except digits, dots, commas, minus
        cleaned = re.sub(r"[^\d.,-]", "", raw)
        # Normalize: remove thousand separators (commas before digits)
        cleaned = cleaned.replace(",", "")
        if not cleaned:
            raise ValueError(f"Could not parse number from: {raw!r}")
        return float(cleaned)


class INSEEScraper(BaseScraper):
    """
    Base scraper for INSEE BDM series (free, no auth).

    Uses the SDMX REST API at api.insee.fr. Monthly observations are
    averaged into quarterly data points with region="GLOBAL".

    Subclasses just set `commodity_name` and `INSEE_IDBANK`.
    """

    INSEE_IDBANK: str = ""
    INSEE_BASE_URL = "https://api.insee.fr/series/BDM/V1/data/SERIES_BDM"

    def __init__(self):
        super().__init__(self.commodity_name)

    async def fetch(self) -> list[ScrapedDataPoint]:
        url = f"{self.INSEE_BASE_URL}/{self.INSEE_IDBANK}?lastNObservations=60"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        return self._parse_sdmx(resp.text)

    @staticmethod
    def _parse_sdmx(xml_text: str) -> list[ScrapedDataPoint]:
        """Parse SDMX-ML StructureSpecific response into quarterly data points."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
        quarterly: dict[tuple[int, int], list[float]] = {}

        for elem in root.iter():
            if not elem.tag.endswith("}Obs") and elem.tag != "Obs":
                continue
            period = elem.get("TIME_PERIOD", "")
            value = elem.get("OBS_VALUE")
            if not period or value is None:
                continue
            try:
                year, month = int(period[:4]), int(period[5:7])
                val = float(value)
            except (ValueError, IndexError):
                continue
            quarter = (month - 1) // 3 + 1
            quarterly.setdefault((year, quarter), []).append(val)

        results = []
        for (year, quarter), values in sorted(quarterly.items()):
            avg = sum(values) / len(values)
            results.append(ScrapedDataPoint(
                region="GLOBAL", year=year, quarter=quarter,
                value=round(avg, 4),
            ))
        return results


# ── INSEE-backed scrapers ──────────────────────────────────────────
# Each maps a seeded CommodityIndex name to an INSEE BDM series.

class BrentOilScraper(INSEEScraper):
    """Brent crude spot price, USD/bbl (London)."""
    commodity_name = "Oil Price"
    INSEE_IDBANK = "010002077"


class AluminumScraper(INSEEScraper):
    """Aluminum high-grade spot, USD/mt (LME)."""
    commodity_name = "Aluminum"
    INSEE_IDBANK = "010002041"


class NaturalGasScraper(INSEEScraper):
    """Natural gas TTF futures, EUR/MWh (ICE Futures Europe)."""
    commodity_name = "Natural Gas"
    INSEE_IDBANK = "010767333"


class IronOreScraper(INSEEScraper):
    """Iron ore 62% Fe, USD/mt."""
    commodity_name = "Iron"
    INSEE_IDBANK = "010002059"


class CopperScraper(INSEEScraper):
    """Copper Grade A spot, USD/mt (LME)."""
    commodity_name = "Copper"
    INSEE_IDBANK = "010002052"


class NickelScraper(INSEEScraper):
    """Nickel 99.8% spot, USD/mt (LME)."""
    commodity_name = "Nickel"
    INSEE_IDBANK = "010002060"


class ZincScraper(INSEEScraper):
    """Zinc 99.995% spot, USD/mt (LME)."""
    commodity_name = "Zinc"
    INSEE_IDBANK = "010002072"


class LeadScraper(INSEEScraper):
    """Lead 99.97% spot, USD/mt (LME)."""
    commodity_name = "Lead"
    INSEE_IDBANK = "010002064"


class NaphtaScraper(INSEEScraper):
    """Naphtha spot NW Europe, USD/mt — key petrochemical feedstock."""
    commodity_name = "Naphtha"
    INSEE_IDBANK = "010002081"


class TinScraper(INSEEScraper):
    """Tin 99.85% spot, USD/mt (LME)."""
    commodity_name = "Tin"
    INSEE_IDBANK = "010002071"


# ── Scrapers from external source packages ────────────────────────────
from app.services.scrapers.eurostat import (
    EurostatScraper,
    EurostatDirectLaborScraper, EurostatPPIManufacturingScraper,
    EurostatEnergyUtilitiesScraper, EurostatLaborEuropeScraper,
    EurostatEnergieEuropeScraper, EurostatManufacturingGoodsFranceScraper,
)
from app.services.scrapers.fred import (
    FREDScraper,
    FREDECIScraper, FREDPPIChemicalsScraper, FREDIndustrialProductionScraper,
    FREDPPIChlorineScraper,
    FREDCopperScraper, FREDCornScraper, FREDLNGJKMScraper,
)
from app.services.scrapers.ecb import (
    ECBScraper,
    ECBEURUSDScraper, ECBGBPEURScraper, ECBCNYEURScraper,
    ECBJPYEURScraper, ECBIDREURScraper, ECBPHPEURScraper,
)
from app.services.scrapers.worldbank import WorldBankScraper, WorldBankUreaScraper
from app.services.scrapers.eia import EIAScraper, EIABrentOilScraper

# Registry: maps commodity name → scraper class
SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    # EIA (U.S. Energy Information Administration — public domain)
    "Brent Crude Oil (EIA)": EIABrentOilScraper,
    # INSEE (metals & energy)
    "Oil Price": BrentOilScraper,
    "Aluminum": AluminumScraper,
    "Natural Gas": NaturalGasScraper,
    "Iron": IronOreScraper,
    "Copper": CopperScraper,
    "Nickel": NickelScraper,
    "Zinc": ZincScraper,
    "Lead": LeadScraper,
    "Naphtha": NaphtaScraper,
    "Tin": TinScraper,
    # Eurostat (European statistics)
    "Direct Labor Costs": EurostatDirectLaborScraper,
    "PPI Manufacturing Europe": EurostatPPIManufacturingScraper,
    "Energy & Utilities": EurostatEnergyUtilitiesScraper,
    "Labor Europe": EurostatLaborEuropeScraper,
    "Energie Europe": EurostatEnergieEuropeScraper,
    "Manufacturing Goods France": EurostatManufacturingGoodsFranceScraper,
    # FRED (US statistics — requires API key)
    "ECI USA": FREDECIScraper,
    "PPI Chemicals USA": FREDPPIChemicalsScraper,
    "Industrial Production USA": FREDIndustrialProductionScraper,
    "PPI Chlorine USA": FREDPPIChlorineScraper,
    # FD-1 remainder — 2026-07 catalog drop short-code names (verified live
    # against fred.stlouisfed.org; see fred.py for why these are keyed
    # differently from the descriptive names above)
    "CU": FREDCopperScraper,
    "CORN": FREDCornScraper,
    "LNG-JKM": FREDLNGJKMScraper,
    # ECB (exchange rates)
    "EUR/USD": ECBEURUSDScraper,
    "GBP/EUR": ECBGBPEURScraper,
    "CNY/EUR": ECBCNYEURScraper,
    "JPY/EUR": ECBJPYEURScraper,
    "IDR/EUR": ECBIDREURScraper,
    "PHP/EUR": ECBPHPEURScraper,
    # World Bank (commodities)
    "Urea": WorldBankUreaScraper,
}

# Maps commodity name → human-readable source label (e.g. "INSEE", "Eurostat")
_BASE_CLASS_LABELS = {
    EIAScraper: "EIA",
    INSEEScraper: "INSEE",
    EurostatScraper: "Eurostat",
    FREDScraper: "FRED",
    ECBScraper: "ECB",
    WorldBankScraper: "World Bank",
}

SCRAPER_SOURCE_LABELS: dict[str, str] = {}
for _name, _cls in SCRAPER_REGISTRY.items():
    for _base, _label in _BASE_CLASS_LABELS.items():
        if issubclass(_cls, _base):
            SCRAPER_SOURCE_LABELS[_name] = _label
            break
    else:
        SCRAPER_SOURCE_LABELS[_name] = "auto"

# ── Known IDBANK codes mapped back to commodity names ─────────────
IDBANK_REGISTRY: dict[str, str] = {
    cls.INSEE_IDBANK: name
    for name, cls in SCRAPER_REGISTRY.items()
    if issubclass(cls, INSEEScraper)
}

# Regex patterns for detecting known source URLs
_INSEE_URL_RE = re.compile(
    r"(?:insee\.fr|api\.insee\.fr).*?(\d{9,12})", re.IGNORECASE
)
_IDBANK_RE = re.compile(r"^0\d{8,11}$")
_FRED_URL_RE = re.compile(r"(?:fred\.stlouisfed\.org|api\.stlouisfed\.org)", re.IGNORECASE)
_EUROSTAT_URL_RE = re.compile(r"ec\.europa\.eu/eurostat", re.IGNORECASE)
# Matches Eurostat API data URLs: .../data/{dataflow}/{filter_key}?...
_EUROSTAT_API_RE = re.compile(
    r"ec\.europa\.eu/eurostat/api/dissemination/sdmx/2\.1/data/([^/?]+)(?:/([^?]+))?",
    re.IGNORECASE,
)
# Matches Eurostat data browser URLs: .../databrowser/view/{dataflow}/...
_EUROSTAT_BROWSER_RE = re.compile(
    r"ec\.europa\.eu/eurostat/databrowser/view/([^/]+)",
    re.IGNORECASE,
)
_ECB_URL_RE = re.compile(r"(?:data-api\.ecb\.europa\.eu|data\.ecb\.europa\.eu)", re.IGNORECASE)
_WORLDBANK_URL_RE = re.compile(r"api\.worldbank\.org", re.IGNORECASE)


def detect_source_type(url: str) -> tuple[str, str | None]:
    """
    Detect if a URL or code matches a known scraper source.
    Returns (source_label, identifier_or_none).
    """
    url = url.strip()

    # Bare IDBANK code (e.g. "010002077")
    if _IDBANK_RE.match(url):
        return "INSEE", url

    # INSEE URL containing an IDBANK
    m = _INSEE_URL_RE.search(url)
    if m:
        return "INSEE", m.group(1)

    if _FRED_URL_RE.search(url):
        return "FRED", None

    # Eurostat API URL with dataflow (and optional filter key)
    m = _EUROSTAT_API_RE.search(url)
    if m:
        dataflow = m.group(1)
        filter_key = m.group(2) or ""
        return "Eurostat", f"{dataflow}/{filter_key}" if filter_key else dataflow

    # Eurostat data browser URL with dataflow
    m = _EUROSTAT_BROWSER_RE.search(url)
    if m:
        return "Eurostat", m.group(1)

    if _EUROSTAT_URL_RE.search(url):
        return "Eurostat", None

    if _ECB_URL_RE.search(url):
        return "ECB", None

    if _WORLDBANK_URL_RE.search(url):
        return "World Bank", None

    return "generic", None


class INSEEAdHocScraper:
    """
    Scrapes any INSEE BDM series by IDBANK, returning the latest quarterly value.
    Used by the smart dispatcher for user-provided INSEE URLs/codes.
    """

    INSEE_BASE_URL = "https://api.insee.fr/series/BDM/V1/data/SERIES_BDM"

    def __init__(self, idbank: str):
        self.idbank = idbank

    async def scrape(self) -> float:
        """Fetch the latest quarterly average from the SDMX API."""
        points = await self.scrape_all()
        if not points:
            raise ValueError(f"No data returned for IDBANK {self.idbank}")
        latest = sorted(points, key=lambda p: (p.year, p.quarter))[-1]
        return latest.value

    async def scrape_all(self) -> list[ScrapedDataPoint]:
        """Fetch all available quarterly averages (up to 60 months)."""
        url = f"{self.INSEE_BASE_URL}/{self.idbank}?lastNObservations=60"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        return INSEEScraper._parse_sdmx(resp.text)


class EurostatAdHocScraper:
    """
    Scrapes any Eurostat dataset by dataflow (and optional filter key),
    returning quarterly data points. Used by the smart dispatcher for
    user-provided Eurostat URLs.
    """

    def __init__(self, dataflow: str, filter_key: str = ""):
        self.dataflow = dataflow
        self.filter_key = filter_key

    async def scrape(self) -> float:
        """Fetch the latest quarterly value."""
        points = await self.scrape_all()
        if not points:
            raise ValueError(f"No data returned for Eurostat dataset {self.dataflow}")
        latest = sorted(points, key=lambda p: (p.year, p.quarter))[-1]
        return latest.value

    async def scrape_all(self) -> list[ScrapedDataPoint]:
        """Fetch all available quarterly data (up to 24 observations)."""
        from app.config import get_settings
        from app.services.scrapers.eurostat import EurostatScraper

        settings = get_settings()
        base = settings.eurostat_api_base
        path = f"{self.dataflow}/{self.filter_key}" if self.filter_key else self.dataflow
        url = f"{base}/data/{path}"
        params = {
            "format": "JSON",
            "lang": "en",
            "lastNObservations": "24",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

        return EurostatScraper._parse_sdmx_json(resp.json())


async def smart_scrape(url: str, config: dict | None = None) -> tuple[float, str]:
    """
    Dispatch to the right scraper based on URL pattern detection.
    Returns (value, detected_source).
    """
    source_type, identifier = detect_source_type(url)

    if source_type == "INSEE" and identifier:
        scraper = INSEEAdHocScraper(identifier)
        value = await scraper.scrape()
        return value, "INSEE"
    elif source_type == "Eurostat" and identifier:
        parts = identifier.split("/", 1)
        scraper = EurostatAdHocScraper(parts[0], parts[1] if len(parts) > 1 else "")
        value = await scraper.scrape()
        return value, "Eurostat"
    else:
        scraper = GenericWebScraper(url, config)
        value = await scraper.scrape()
        return value, "generic"


async def smart_scrape_all(url: str, config: dict | None = None) -> tuple[list[ScrapedDataPoint], str]:
    """
    Like smart_scrape but returns all available historical data points.
    For sources that only support a single value (generic web), returns the
    value as a single-element list for the current quarter.
    """
    source_type, identifier = detect_source_type(url)

    if source_type == "INSEE" and identifier:
        scraper = INSEEAdHocScraper(identifier)
        points = await scraper.scrape_all()
        return points, "INSEE"
    elif source_type == "Eurostat" and identifier:
        parts = identifier.split("/", 1)
        scraper = EurostatAdHocScraper(parts[0], parts[1] if len(parts) > 1 else "")
        points = await scraper.scrape_all()
        return points, "Eurostat"
    else:
        scraper = GenericWebScraper(url, config)
        value = await scraper.scrape()
        now = datetime.now(timezone.utc)
        point = ScrapedDataPoint(
            region="GLOBAL",
            year=now.year,
            quarter=(now.month - 1) // 3 + 1,
            value=value,
        )
        return [point], "generic"
