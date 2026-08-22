"""Migrate FX pairs to Frankfurter API + seed all 31 ECB-backed currency pairs

Revision ID: fxf2b3c4d5e6
Revises: dem0_1a2b3c4d5e
Create Date: 2026-06-21
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'fxf2b3c4d5e6'
down_revision: Union[str, None] = 'dem0_1a2b3c4d5e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update the 6 existing ECB pairs to use Frankfurter JSON API
    existing_pairs = [
        ("EUR/USD", "EUR", "USD"),
        ("GBP/EUR", "GBP", "EUR"),
        ("CNY/EUR", "CNY", "EUR"),
        ("JPY/EUR", "JPY", "EUR"),
        ("IDR/EUR", "IDR", "EUR"),
        ("PHP/EUR", "PHP", "EUR"),
    ]
    for name, from_c, to_c in existing_pairs:
        op.execute(
            sa.text(
                "UPDATE fx_pairs SET source_type = 'frankfurter', "
                "scrape_url = :url "
                "WHERE name = :name"
            ).bindparams(
                url=f"https://api.frankfurter.app/latest?from={from_c}&to={to_c}",
                name=name,
            )
        )

    # Insert the remaining 25 pairs to cover all Frankfurter-supported currencies
    new_pairs = [
        ("CHF/EUR", "CHF", "EUR"),
        ("SEK/EUR", "SEK", "EUR"),
        ("NOK/EUR", "NOK", "EUR"),
        ("DKK/EUR", "DKK", "EUR"),
        ("AUD/EUR", "AUD", "EUR"),
        ("CAD/EUR", "CAD", "EUR"),
        ("NZD/EUR", "NZD", "EUR"),
        ("INR/EUR", "INR", "EUR"),
        ("KRW/EUR", "KRW", "EUR"),
        ("SGD/EUR", "SGD", "EUR"),
        ("MYR/EUR", "MYR", "EUR"),
        ("THB/EUR", "THB", "EUR"),
        ("HKD/EUR", "HKD", "EUR"),
        ("BRL/EUR", "BRL", "EUR"),
        ("MXN/EUR", "MXN", "EUR"),
        ("ZAR/EUR", "ZAR", "EUR"),
        ("TRY/EUR", "TRY", "EUR"),
        ("PLN/EUR", "PLN", "EUR"),
        ("CZK/EUR", "CZK", "EUR"),
        ("HUF/EUR", "HUF", "EUR"),
        ("RON/EUR", "RON", "EUR"),
        ("ILS/EUR", "ILS", "EUR"),
        ("BGN/EUR", "BGN", "EUR"),
        ("ISK/EUR", "ISK", "EUR"),
        ("USD/EUR", "USD", "EUR"),
    ]
    for name, from_c, to_c in new_pairs:
        op.execute(
            sa.text(
                "INSERT INTO fx_pairs (name, from_currency, to_currency, source_type, scrape_url, scrape_enabled) "
                "VALUES (:name, :from_c, :to_c, 'frankfurter', :url, true) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(
                name=name,
                from_c=from_c,
                to_c=to_c,
                url=f"https://api.frankfurter.app/latest?from={from_c}&to={to_c}",
            )
        )


def downgrade() -> None:
    # Remove the 25 newly added pairs
    new_pair_names = [
        "CHF/EUR", "SEK/EUR", "NOK/EUR", "DKK/EUR", "AUD/EUR", "CAD/EUR",
        "NZD/EUR", "INR/EUR", "KRW/EUR", "SGD/EUR", "MYR/EUR", "THB/EUR",
        "HKD/EUR", "BRL/EUR", "MXN/EUR", "ZAR/EUR", "TRY/EUR", "PLN/EUR",
        "CZK/EUR", "HUF/EUR", "RON/EUR", "ILS/EUR", "BGN/EUR", "ISK/EUR",
        "USD/EUR",
    ]
    for name in new_pair_names:
        op.execute(sa.text("DELETE FROM fx_pairs WHERE name = :name").bindparams(name=name))

    # Revert the 6 existing pairs back to ECB source type
    ecb_pairs = [
        ("EUR/USD", "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"),
        ("GBP/EUR", "https://data-api.ecb.europa.eu/service/data/EXR/D.GBP.EUR.SP00.A"),
        ("CNY/EUR", "https://data-api.ecb.europa.eu/service/data/EXR/D.CNY.EUR.SP00.A"),
        ("JPY/EUR", "https://data-api.ecb.europa.eu/service/data/EXR/D.JPY.EUR.SP00.A"),
        ("IDR/EUR", "https://data-api.ecb.europa.eu/service/data/EXR/D.IDR.EUR.SP00.A"),
        ("PHP/EUR", "https://data-api.ecb.europa.eu/service/data/EXR/D.PHP.EUR.SP00.A"),
    ]
    for name, url in ecb_pairs:
        op.execute(
            sa.text(
                "UPDATE fx_pairs SET source_type = 'ecb', scrape_url = :url WHERE name = :name"
            ).bindparams(url=url, name=name)
        )
