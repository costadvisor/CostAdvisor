"""
Seed the database with initial data.
Run with: python -m app.seed
"""
from app.database import SessionLocal, engine, Base
from app.models import (
    CommodityIndex, IndexValue, CostScenario, ChemicalFamily, Region,
)

# Region reference data (Scrum 56): 7 top-level regions + GLOBAL sentinel, plus a
# few subregions reconciling feed grain finer than the top level.
REGIONS_SEED = [
    ("GLOBAL", "Global", None),
    ("Europe", "Europe", None),
    ("NA", "North America", None),
    ("Latam", "Latin America", None),
    ("Asia", "Asia", None),
    ("ME", "Middle East", None),
    ("Africa", "Africa", None),
    ("Oceania", "Oceania", None),
    ("NWE", "Northwest Europe", "Europe"),
    ("France", "France", "Europe"),
    ("USA", "United States", "NA"),
    ("China", "China", "Asia"),
]

# ── Commodity indices ────────────────────────────────────────────────────────
# Only real data from verified public sources.
# Empty 'values' dicts mean no seed data — populate via scraping or upload.

INDEXES_DATA = {
    # ── Metals (INSEE SDMX API — free, no auth) ─────────────────────────────
    'Aluminum': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002041',
        'values': {
            'Europe': [2199.1, 2199.1, 2199.1, 2199.1, 2199.1, 2518.53, 2384.47, 2572.6, 2626.77, 2444.37, 2616.4, 2580.0, 2620.0, 2650.0],
        }
    },
    'Iron': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002059',
        'values': {
            'Europe': [94.57, 94.57, 94.57, 94.57, 92.17, 92.1, 90.57, 91.27, 92.97, 91.67, 90.7, 90.0, 89.5, 89.0],
        }
    },
    'Copper': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002052',
        'values': {
            'GLOBAL': [8500, 8500, 8500, 8500, 9500, 9500, 9200, 9200, 9800, 10800, 11800, 11500, 11900, 12100],
        }
    },
    'Nickel': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002060',
        'values': {
            'GLOBAL': [23000, 23000, 23000, 23000, 18000, 16500, 16000, 15500, 15500, 14700, 14900, 15100, 15300, 15400],
        }
    },
    'Zinc': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002072',
        'values': {
            'GLOBAL': [2600, 2600, 2600, 2600, 2800, 2800, 2900, 2700, 2800, 3200, 3200, 3100, 3050, 3000],
        }
    },
    'Lead': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002064',
        'values': {
            'GLOBAL': [2100, 2100, 2100, 2100, 2200, 2100, 2000, 1950, 2000, 2000, 1950, 1950, 1920, 1900],
        }
    },
    'Tin': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Metal',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002071',
        'values': {},
    },

    # ── Energy & Petrochemicals ──────────────────────────────────────────────
    'Oil Price': {
        'unit': '$/bbl', 'currency': 'USD', 'category': 'Energy',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002077',
        'values': {
            'Europe': [82.97, 82.97, 82.97, 82.97, 84.63, 79.87, 74.5, 75.8, 67.87, 68.77, 63.9, 64.5, 62.0, 61.0],
        }
    },
    'Brent Crude Oil (EIA)': {
        'unit': '$/bbl', 'currency': 'USD', 'category': 'Energy',
        'source_url': 'https://www.eia.gov/dnav/pet/hist/rbrtem.htm',
        'values': {}
    },
    'Natural Gas': {
        'unit': 'EUR/MWh', 'currency': 'EUR', 'category': 'Energy',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010767333',
        'values': {
            'Europe': [49.7, 49.7, 49.7, 49.7, 49.7, 51.2, 51.2, 51.0, 51.0, 51.0, 51.0, 50.5, 50.0, 49.5],
        }
    },
    'Naphtha': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Energy',
        'source_url': 'https://www.insee.fr/fr/statistiques/serie/010002081',
        'values': {
            'GLOBAL': [630, 630, 630, 630, 680, 640, 600, 590, 560, 535, 510, 495, 480, 475],
        }
    },
    'Energy & Utilities': {
        'unit': 'EUR/kWh', 'currency': 'EUR', 'category': 'Energy',
        'source_url': 'https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_203/default/table',
        'values': {
            'Europe': [0.157, 0.157, 0.157, 0.157, 0.157, 0.1469, 0.1469, 0.1337, 0.1337, 0.1337, 0.1337, 0.133, 0.132, 0.131],
        }
    },

    # ── Chemical feedstocks & products ───────────────────────────────────────
    'Chlorine': {
        'unit': '$/kg', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://www.imarcgroup.com/chlorine-pricing-report',
        'values': {
            'Europe': [0.4, 0.4, 0.4, 0.4, 0.39, 0.39, 0.39, 0.38, 0.44, 0.42, 0.39, 0.38, 0.37, 0.36],
            'Asia': [0.35, 0.35, 0.35, 0.35, 0.34, 0.34, 0.33, 0.33, 0.33, 0.33, 0.33, 0.32, 0.32, 0.31],
        }
    },
    'Ammonia': {
        'unit': '$/kg', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/ammonia-price-index/',
        'values': {
            'Europe': [0.68, 0.68, 0.68, 0.68, 0.59, 0.59, 0.67, 0.69, 0.65, 0.54, 0.6, 0.58, 0.55, 0.53],
        }
    },
    'Caustic Soda': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/caustic-soda-price-index/',
        'values': {},
    },
    'Sulfuric Acid': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/sulphuric-acid-price-index/',
        'values': {},
    },
    'Hydrochloric Acid': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/hydrochloric-acid-price-index/',
        'values': {},
    },
    'Phosphoric Acid': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/phosphoric-acid-price-index/',
        'values': {},
    },
    'Ethylene': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/ethylene-price-index/',
        'values': {},
    },
    'Propylene': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/propylene-price-index/',
        'values': {},
    },
    'Benzene': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/benzene-price-index/',
        'values': {},
    },
    'Toluene': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/toluene-price-index/',
        'values': {},
    },
    'Methanol': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/methanol-price-index/',
        'values': {},
    },
    'Ethanol': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/ethanol-price-index/',
        'values': {},
    },
    'Urea': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://api.worldbank.org/v2/country/all/indicator/CMDT.UREA.USD',
        'values': {},
    },
    'Polyethylene HDPE': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/hdpe-high-density-polyethylene-price-index/',
        'values': {},
    },
    'PVC': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/pvc-price-index/',
        'values': {},
    },
    'Polypropylene': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/polypropylene-price-index/',
        'values': {},
    },
    'Acetic Acid': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/acetic-acid-price-index/',
        'values': {},
    },
    'Hydrogen Peroxide': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/hydrogen-peroxide-price-index/',
        'values': {},
    },
    'Sodium Carbonate': {
        'unit': '$/mt', 'currency': 'USD', 'category': 'Chemical',
        'source_url': 'https://businessanalytiq.com/procurementanalytics/index/soda-ash-price-index/',
        'values': {},
    },

    # ── Labor cost indices ───────────────────────────────────────────────────
    'Direct Labor Costs': {
        'unit': 'EUR/h', 'currency': 'EUR', 'category': 'Labor',
        'source_url': 'https://ec.europa.eu/eurostat/databrowser/view/lc_lci_r2_a/default/table',
        'values': {
            'Europe': [43.7, 43.7, 43.7, 43.7, 43.7, 43.7, 43.7, 45.011, 45.011, 45.011, 46.361, 46.5, 47.0, 47.5],
        }
    },
    'Labor China': {
        'unit': 'index', 'currency': 'CNY', 'category': 'Labor',
        'source_url': 'https://tradingeconomics.com/china/labour-costs',
        'values': {},
    },
    'ECI USA': {
        'unit': 'index', 'currency': 'USD', 'category': 'Labor',
        'source_url': 'https://fred.stlouisfed.org/series/ECIWAG',
        'values': {},
    },

    # ── Producer Price Indices & Manufacturing ───────────────────────────────
    'PPI Manufacturing Europe': {
        'unit': 'index', 'currency': 'EUR', 'category': 'PPI',
        'source_url': 'https://ec.europa.eu/eurostat/databrowser/view/sts_inpp_m/default/table',
        'values': {},
    },
    'PPI Chemicals USA': {
        'unit': 'index', 'currency': 'USD', 'category': 'PPI',
        'source_url': 'https://fred.stlouisfed.org/series/PCU325325',
        'values': {},
    },
    'Industrial Production USA': {
        'unit': 'index', 'currency': 'USD', 'category': 'PPI',
        'source_url': 'https://fred.stlouisfed.org/series/INDPRO',
        'values': {},
    },
    'PPI Chlorine USA': {
        'unit': 'index', 'currency': 'USD', 'category': 'PPI',
        'source_url': 'https://fred.stlouisfed.org/series/WPU061303',
        'values': {},
    },

    # ── Logistics & Freight ──────────────────────────────────────────────────
    'Container Freight WCI': {
        'unit': '$/40ft', 'currency': 'USD', 'category': 'Freight',
        'source_url': 'https://www.drewry.co.uk/supply-chain-advisors/supply-chain-expertise/world-container-index-assessed-by-drewry',
        'values': {},
    },

    # ── Exchange Rates (ECB — free, no auth) ─────────────────────────────────
    'EUR/USD': {
        'unit': 'EUR/USD', 'currency': None, 'category': 'FX',
        'source_url': 'https://data-api.ecb.europa.eu/service/data/EXR/Q.USD.EUR.SP00.A',
        'values': {},
    },
    'GBP/EUR': {
        'unit': 'GBP/EUR', 'currency': None, 'category': 'FX',
        'source_url': 'https://data-api.ecb.europa.eu/service/data/EXR/Q.GBP.EUR.SP00.A',
        'values': {},
    },
    'CNY/EUR': {
        'unit': 'CNY/EUR', 'currency': None, 'category': 'FX',
        'source_url': 'https://data-api.ecb.europa.eu/service/data/EXR/Q.CNY.EUR.SP00.A',
        'values': {},
    },
    'JPY/EUR': {
        'unit': 'JPY/EUR', 'currency': None, 'category': 'FX',
        'source_url': 'https://data-api.ecb.europa.eu/service/data/EXR/Q.JPY.EUR.SP00.A',
        'values': {},
    },
    'IDR/EUR': {
        'unit': 'IDR/EUR', 'currency': None, 'category': 'FX',
        'source_url': 'https://data-api.ecb.europa.eu/service/data/EXR/Q.IDR.EUR.SP00.A',
        'values': {},
    },
    'PHP/EUR': {
        'unit': 'PHP/EUR', 'currency': None, 'category': 'FX',
        'source_url': 'https://data-api.ecb.europa.eu/service/data/EXR/Q.PHP.EUR.SP00.A',
        'values': {},
    },
}

PERIODS = [
    (2023, 1), (2023, 2), (2023, 3), (2023, 4),
    (2024, 1), (2024, 2), (2024, 3), (2024, 4),
    (2025, 1), (2025, 2), (2025, 3), (2025, 4),
    (2026, 1), (2026, 2),
]

SCENARIOS_DATA = {
    'High RM Intensity': {
        'description': 'High raw material intensity (e.g. batteries, specialty chemicals)',
        'breakdown': {
            'Raw Materials': 0.68, 'Energy & Utilities': 0.04,
            'Direct Labor Costs': 0.06, 'Manufacturing Overhead': 0.15,
            'Packaging': 0.07, 'Logistics': 0,
        }
    },
    'Medium RM Intensity': {
        'description': 'Medium raw material intensity (e.g. coagulants, polymers)',
        'breakdown': {
            'Raw Materials': 0.60, 'Energy & Utilities': 0.04,
            'Direct Labor Costs': 0.10, 'Manufacturing Overhead': 0.18,
            'Packaging': 0.08, 'Logistics': 0,
        }
    },
    'Low RM Intensity': {
        'description': 'Low raw material intensity (e.g. sulfuric acid, commodity chemicals)',
        'breakdown': {
            'Raw Materials': 0.25, 'Energy & Utilities': 0.33,
            'Direct Labor Costs': 0.20, 'Manufacturing Overhead': 0.02,
            'Packaging': 0.05, 'Logistics': 0.15,
        }
    },
}

FAMILIES = [
    'Solvents', 'Polymers', 'Surfactants', 'Acids & Bases',
    'Coagulants', 'Specialty Chemicals', 'Other',
]


def seed():
    """Populate the database with initial data."""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(CommodityIndex).first()
        if existing:
            print("Database already seeded. Skipping.")
            return

        # Regions must exist before index values (region is an FK to regions.code).
        print("Seeding regions...")
        by_code = {}
        for code, name, _ in REGIONS_SEED:
            r = Region(code=code, name=name)
            db.add(r)
            by_code[code] = r
        db.flush()
        for code, _, parent_code in REGIONS_SEED:
            if parent_code:
                by_code[code].parent_id = by_code[parent_code].id
        db.flush()

        print("Seeding chemical families...")
        for name in FAMILIES:
            db.add(ChemicalFamily(name=name))

        print("Seeding commodity indexes...")
        # All commodities with a registered scraper get scrape_enabled=True
        from app.services.scraper import SCRAPER_REGISTRY
        scrape_enabled_names = set(SCRAPER_REGISTRY.keys())

        for name, data in INDEXES_DATA.items():
            commodity = CommodityIndex(
                name=name,
                unit=data['unit'],
                currency=data.get('currency'),
                category=data.get('category'),
                source_url=data.get('source_url'),
                scrape_enabled=name in scrape_enabled_names,
            )
            db.add(commodity)
            db.flush()

            for region, values in data['values'].items():
                for i, (year, quarter) in enumerate(PERIODS):
                    if i < len(values):
                        iv = IndexValue(
                            commodity_id=commodity.id,
                            region=region,
                            year=year,
                            quarter=quarter,
                            value=values[i],
                            source="seed",
                        )
                        db.add(iv)

        print("Seeding scenarios...")
        for name, data in SCENARIOS_DATA.items():
            scenario = CostScenario(
                name=name,
                description=data['description'],
                is_system=True,
                team_id=None,
                breakdown=data['breakdown'],
            )
            db.add(scenario)

        db.commit()
        commodity_count = len(INDEXES_DATA)
        seeded_count = sum(1 for d in INDEXES_DATA.values() if d['values'])
        print(f"Seeded {len(FAMILIES)} chemical families.")
        print(f"Seeded {commodity_count} commodities ({seeded_count} with initial data, {commodity_count - seeded_count} awaiting data).")
        print(f"Seeded {len(SCENARIOS_DATA)} system scenarios.")
        print("Done.")

    finally:
        db.close()


def seed_update():
    """Update existing commodity records with currency, category, and new entries.
    Safe to run multiple times — updates existing rows and adds missing ones.
    Also backfills any missing seed IndexValue rows for existing commodities.
    """
    db = SessionLocal()
    try:
        from app.services.scraper import SCRAPER_REGISTRY
        scrape_enabled_names = set(SCRAPER_REGISTRY.keys())

        for name, data in INDEXES_DATA.items():
            existing = db.query(CommodityIndex).filter(
                CommodityIndex.name == name
            ).first()

            if existing:
                existing.currency = data.get('currency')
                existing.category = data.get('category')
                existing.source_url = data.get('source_url')
                existing.scrape_enabled = name in scrape_enabled_names
                commodity_id = existing.id
            else:
                commodity = CommodityIndex(
                    name=name,
                    unit=data['unit'],
                    currency=data.get('currency'),
                    category=data.get('category'),
                    source_url=data.get('source_url'),
                    scrape_enabled=name in scrape_enabled_names,
                )
                db.add(commodity)
                db.flush()
                commodity_id = commodity.id

            # Backfill missing seed values (won't overwrite scraped data)
            for region, values in data.get('values', {}).items():
                for i, (year, quarter) in enumerate(PERIODS):
                    if i >= len(values):
                        break
                    exists = db.query(IndexValue).filter(
                        IndexValue.commodity_id == commodity_id,
                        IndexValue.region == region,
                        IndexValue.year == year,
                        IndexValue.quarter == quarter,
                    ).first()
                    if not exists:
                        db.add(IndexValue(
                            commodity_id=commodity_id,
                            region=region,
                            year=year,
                            quarter=quarter,
                            value=values[i],
                            source="seed",
                        ))

        db.commit()
        print(f"Updated/added {len(INDEXES_DATA)} commodity indexes (with seed value backfill).")

    finally:
        db.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        seed_update()
    else:
        seed()
        seed_update()
