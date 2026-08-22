import httpx
from bs4 import BeautifulSoup


class GoogleFinanceScraper:
    """Fetch the current exchange rate from a Google Finance quote page.

    scrape_url should be the full Google Finance URL, e.g.:
    https://www.google.com/finance/quote/CNY-EUR
    The pair symbol uses a dash (CNY-EUR), not a slash.
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, scrape_url: str):
        self.url = scrape_url

    async def fetch_live(self) -> float | None:
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=True, headers=self.HEADERS
            ) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Primary: data-last-price attribute — most stable across page redesigns
            el = soup.find(attrs={"data-last-price": True})
            if el:
                return float(el["data-last-price"])

            # Fallback: price text inside the main YMlKec price div
            div = soup.find("div", class_=lambda c: c and "YMlKec" in c)
            if div:
                text = div.get_text(strip=True).replace(",", "")
                return float(text)

            return None
        except Exception:
            return None
