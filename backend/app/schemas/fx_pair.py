from datetime import datetime
from pydantic import BaseModel


class FxPairOut(BaseModel):
    id: int
    from_currency: str
    to_currency: str
    name: str
    source_type: str
    scrape_url: str | None
    scrape_enabled: bool
    live_rate: float | None
    live_scraped_at: datetime | None

    model_config = {"from_attributes": True}


class FxPairCreate(BaseModel):
    from_currency: str
    to_currency: str
    name: str
    source_type: str = "manual"
    scrape_url: str | None = None
    scrape_enabled: bool = True


class FxPairUpdate(BaseModel):
    name: str | None = None
    source_type: str | None = None
    scrape_url: str | None = None
    scrape_enabled: bool | None = None
