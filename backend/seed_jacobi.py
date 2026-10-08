"""
Seed script: Add Jacobi activated carbon indexes, products, supplier, and cost models
from the Excel files (indices costing + proposition formules).

Commodity indexes are resolved by name. The run refuses before writing
anything when a cost-model line names an index that neither exists nor is
created here, or when a name it would write values to belongs to a content
series (`commodity_key` set): demo values never land on a licensed series.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.database import engine

now = datetime.now(timezone.utc).isoformat()

TEAM_ID = "1db4d50c-b2c5-496b-8450-bf12b31484c0"
CREATED_BY = "d9cbd7a9-93ca-4400-a204-7f9ceaa87a23"  # Alexis Thomas

# ─── 1. Commodity Indexes ────────────────────────────────────────────────────

# (name, unit, source_url, currency, category)
NEW_COMMODITIES = [
    ("Coconut Charcoal",            "USD/Mt",   "https://coconutcommunity.org/page-statistics/weekly-price-update", "USD", "Metal"),
    ("Coal ZCE",                    "USD/Mt",   "https://www.investing.com/commodities/zce-thermal-coal-futures", "USD", "Energy"),
    ("Coal Newcastle",              "USD/t",    "https://www.investing.com/commodities/newcastle-coal-futures", "USD", "Energy"),
    ("LNG JKM",                     "USD/MMBtu","https://www.investing.com/commodities/lng-japan-korea-marker", "USD", "Energy"),
    ("Freight Shanghai-Rotterdam",  "$/40ft",   "https://www.drewry.co.uk/", "USD", "Freight"),
    ("Labor Indonesia",             "$/month",  "https://tradingeconomics.com/indonesia/minimum-wages", "USD", "Labor"),
    ("Labor Philippines",           "$/month",  "https://tradingeconomics.com/philippines/minimum-wages", "USD", "Labor"),
    ("Labor Europe",                "EUR/h",    "https://ec.europa.eu/eurostat/databrowser/view/lc_lci_r2_a/default/table", "EUR", "Labor"),
    ("Energie Europe",              "EUR/kWh",  "https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_205/default/table", "EUR", "Energy"),
    ("Manufacturing Goods France",  "EUR",      "https://ec.europa.eu/eurostat/databrowser/view/sts_inpr_m/default/table", "EUR", "PPI"),
    ("Natural Gas INSEE",           "index",    "https://www.insee.fr/fr/statistiques/serie/010764295", "EUR", "Energy"),
]

# Quarterly index values from the V3 sheet (indices costing - Stripped.xlsx)
# Format: (name, region, [(year, quarter, value), ...])
INDEX_VALUES = [
    ("Coconut Charcoal", "Asia", [
        (2022,1,539.40),(2022,2,483.82),(2022,3,473.04),(2022,4,406.07),
        (2023,1,403.77),(2023,2,405.77),(2023,3,373.47),(2023,4,354.64),
        (2024,1,385.82),(2024,2,422.21),(2024,3,456.14),(2024,4,546.51),
        (2025,1,656.26),(2025,2,879.85),(2025,3,973.85),(2025,4,943.21),
        (2026,1,922.80),
    ]),
    ("LNG JKM", "Asia", [
        (2022,1,29365),(2022,2,29088),(2022,3,44467),(2022,4,30120),
        (2023,1,15633),(2023,2,11025),(2023,3,12917),(2023,4,15125),
        (2024,1,9145),(2024,2,11625),(2024,3,13247),(2024,4,14262),
        (2025,1,13788),(2025,2,12165),(2025,3,11435),(2025,4,10955),
    ]),
    ("Coal Newcastle", "Asia", [
        (2022,1,252.08),(2022,2,380.18),(2022,3,422.20),(2022,4,386.32),
        (2023,1,207.28),(2023,2,150.92),(2023,3,151.13),(2023,4,133.22),
        (2024,1,125.53),(2024,2,139.37),(2024,3,143.80),(2024,4,136.93),
        (2025,1,106.85),(2025,2,102.73),(2025,3,110.95),(2025,4,107.22),
    ]),
    ("Coal ZCE", "Asia", [
        (2022,1,100.11),(2022,2,117.01),(2022,3,116.30),(2022,4,137.32),
        (2023,1,112.89),(2023,2,113.75),(2023,3,113.75),(2023,4,113.75),
        (2024,1,113.75),(2024,2,113.75),(2024,3,113.75),(2024,4,113.75),
        (2025,1,113.75),(2025,2,113.75),(2025,3,113.75),(2025,4,113.75),
        (2026,1,113.75),
    ]),
    ("Labor Indonesia", "Asia", [
        (2022,1,236.50),(2022,2,236.50),(2022,3,236.50),(2022,4,236.50),
        (2023,1,294.29),(2023,2,294.29),(2023,3,294.29),(2023,4,294.29),
        (2024,1,304.32),(2024,2,304.32),(2024,3,304.32),(2024,4,304.32),
        (2025,1,324.16),(2025,2,324.16),(2025,3,324.16),(2025,4,324.16),
    ]),
    ("Labor Philippines", "Asia", [
        (2022,1,251.47),(2022,2,251.47),(2022,3,251.47),(2022,4,251.47),
        (2023,1,314.39),(2023,2,314.39),(2023,3,314.39),(2023,4,314.39),
        (2024,1,332.42),(2024,2,332.42),(2024,3,332.42),(2024,4,332.42),
        (2025,1,332.42),(2025,2,332.42),(2025,3,332.42),(2025,4,332.42),
    ]),
    ("Freight Shanghai-Rotterdam", "Asia", [
        (2022,1,12600),(2022,2,10650),(2022,3,8250),(2022,4,3100),
        (2023,1,2300),(2023,2,1800),(2023,3,1500),(2023,4,1600),
        (2024,1,2500),(2024,2,3500),(2024,3,6000),(2024,4,4500),
        (2025,1,3500),(2025,2,2800),(2025,3,2576),(2025,4,2087),
    ]),
    ("Labor Europe", "Europe", [
        (2022,1,40.80),(2022,2,40.80),(2022,3,40.80),(2022,4,40.80),
        (2023,1,42.40),(2023,2,42.40),(2023,3,42.40),(2023,4,42.40),
        (2024,1,43.70),(2024,2,43.70),(2024,3,43.70),(2024,4,43.70),
        (2025,1,44.11),(2025,2,44.54),(2025,3,44.98),(2025,4,45.42),
    ]),
    ("Energie Europe", "Europe", [
        (2022,1,0.1705),(2022,2,0.1705),(2022,3,0.2082),(2022,4,0.2082),
        (2023,1,0.1977),(2023,2,0.1977),(2023,3,0.1805),(2023,4,0.1805),
        (2024,1,0.1573),(2024,2,0.1573),(2024,3,0.1617),(2024,4,0.1617),
        (2025,1,0.1547),(2025,2,0.1547),(2025,3,0.1547),(2025,4,0.1547),
    ]),
    # European market indexes (from V3 rows 17-26)
    ("Aluminum", "Europe", [
        (2022,1,3266.20),(2022,2,2881.10),(2022,3,2353.40),(2022,4,2323.70),
        (2023,1,2397.93),(2023,2,2262.47),(2023,3,2153.97),(2023,4,2189.03),
        (2024,1,2199.10),(2024,2,2518.53),(2024,3,2384.47),(2024,4,2572.60),
        (2025,1,2626.77),(2025,2,2444.37),(2025,3,2616.40),(2025,4,2803.85),
    ]),
    ("Iron", "Europe", [
        (2022,1,120.70),(2022,2,153.50),(2022,3,145.83),(2022,4,128.93),
        (2023,1,110.50),(2023,2,104.70),(2023,3,94.63),(2023,4,92.97),
        (2024,1,94.57),(2024,2,92.17),(2024,3,92.10),(2024,4,90.57),
        (2025,1,91.27),(2025,2,92.97),(2025,3,91.67),(2025,4,90.70),
    ]),
    ("Chlorine", "Europe", [
        (2022,1,0.35),(2022,2,0.41),(2022,3,0.43),(2022,4,0.43),
        (2023,1,0.42),(2023,2,0.40),(2023,3,0.39),(2023,4,0.39),
        (2024,1,0.40),(2024,2,0.39),(2024,3,0.39),(2024,4,0.39),
        (2025,1,0.38),(2025,2,0.44),(2025,3,0.42),(2025,4,0.39),
    ]),
    ("Ammonia", "Europe", [
        (2022,1,1.23),(2022,2,1.31),(2022,3,1.25),(2022,4,1.25),
        (2023,1,1.13),(2023,2,0.55),(2023,3,0.71),(2023,4,0.71),
        (2024,1,0.68),(2024,2,0.59),(2024,3,0.59),(2024,4,0.67),
        (2025,1,0.69),(2025,2,0.65),(2025,3,0.54),(2025,4,0.60),
    ]),
    ("Brent Crude Oil (EIA)", "GLOBAL", [
        (2022,1,94.07),(2022,2,113.78),(2022,3,97.43),(2022,4,88.63),
        (2023,1,82.16),(2023,2,78.07),(2023,3,86.18),(2023,4,84.07),
        (2024,1,82.53),(2024,2,84.85),(2024,3,79.83),(2024,4,73.47),
        (2025,1,75.49),(2025,2,67.25),(2025,3,68.40),(2025,4,64.15),
        (2026,1,66.82),(2026,2,63.50),
    ]),
    ("Oil Price", "Europe", [
        (2022,1,100.17),(2022,2,113.33),(2022,3,100.73),(2022,4,88.57),
        (2023,1,81.23),(2023,2,78.37),(2023,3,86.57),(2023,4,83.77),
        (2024,1,82.97),(2024,2,84.63),(2024,3,79.87),(2024,4,74.50),
        (2025,1,75.80),(2025,2,67.87),(2025,3,68.77),(2025,4,63.90),
    ]),
    ("Natural Gas INSEE", "Europe", [
        (2022,1,170.73),(2022,2,181.43),(2022,3,234.47),(2022,4,217.37),
        (2023,1,191.37),(2023,2,142.77),(2023,3,136.50),(2023,4,142.27),
        (2024,1,139.07),(2024,2,142.93),(2024,3,152.43),(2024,4,163.30),
        (2025,1,170.67),(2025,2,160.27),(2025,3,160.17),(2025,4,154.07),
    ]),
    ("Energy & Utilities", "Europe", [
        (2022,1,0.1241),(2022,2,0.1241),(2022,3,0.1237),(2022,4,0.1237),
        (2023,1,0.2071),(2023,2,0.2071),(2023,3,0.1939),(2023,4,0.1939),
        (2024,1,0.1570),(2024,2,0.1570),(2024,3,0.1469),(2024,4,0.1469),
        (2025,1,0.1337),(2025,2,0.1337),(2025,3,0.1337),(2025,4,0.1337),
    ]),
    ("Manufacturing Goods France", "Europe", [
        (2024,4,117.90),
        (2025,1,119.03),(2025,2,118.43),(2025,3,118.53),(2025,4,118.30),
    ]),
    ("Direct Labor Costs", "Europe", [
        (2022,1,40.80),(2022,2,40.80),(2022,3,40.80),(2022,4,40.80),
        (2023,1,42.40),(2023,2,42.40),(2023,3,42.40),(2023,4,42.40),
        (2024,1,43.70),(2024,2,43.70),(2024,3,43.70),(2024,4,43.70),
        (2025,1,44.11),(2025,2,44.54),(2025,3,44.98),(2025,4,45.42),
    ]),
    ("Labor China", "Asia", [
        (2022,1,100.00),(2022,2,100.80),(2022,3,101.60),(2022,4,102.40),
        (2023,1,103.20),(2023,2,104.00),(2023,3,104.80),(2023,4,105.60),
        (2024,1,106.40),(2024,2,107.20),(2024,3,108.00),(2024,4,108.80),
        (2025,1,109.60),(2025,2,110.40),(2025,3,111.20),(2025,4,112.00),
        (2026,1,112.80),(2026,2,113.60),
    ]),
]

# ─── 2. Chemical family ──────────────────────────────────────────────────────


# ─── 3. Products (from Proposition formules) ─────────────────────────────────

PRODUCTS = [
    {"name": "Aquasorb MP23",     "formula": "Virgin Mineral AC",     "unit": "kg"},
    {"name": "Aquasorb CR",       "formula": "Virgin Coco AC",        "unit": "kg"},
    {"name": "Aquasorb 2000",     "formula": "Reactivated Mineral AC","unit": "kg"},
    {"name": "Aquasorb CS",       "formula": "Reactivated Coco AC",   "unit": "kg"},
]

# ─── 4. Cost model formulas (from Proposition formules) ──────────────────────
# Each: (product_name, base_price, base_year, base_quarter, region, components)
# components: [(label, commodity_name_or_None, weight)]

COST_MODELS = [
    {
        "product": "Aquasorb MP23",
        "base_price": 1000,  # placeholder base price (EUR/t)
        "base_year": 2023, "base_quarter": 1,
        "region": "Europe", "currency": "EUR",
        "components": [
            ("Coal",    "Coal ZCE",                   0.51),
            ("Freight", "Freight Shanghai-Rotterdam",  0.24),
            ("Labor",   "Labor Europe",                0.05),
            ("Fixed",   None,                          0.20),
        ],
    },
    {
        "product": "Aquasorb CR",
        "base_price": 1000,
        "base_year": 2023, "base_quarter": 1,
        "region": "Europe", "currency": "EUR",
        "components": [
            ("Coconut Charcoal", "Coconut Charcoal",           0.65),
            ("Freight",          "Freight Shanghai-Rotterdam",  0.12),
            ("Labor",            "Labor Europe",                0.03),
            ("Fixed",            None,                          0.20),
        ],
    },
    {
        "product": "Aquasorb 2000",
        "base_price": 1000,
        "base_year": 2023, "base_quarter": 1,
        "region": "Europe", "currency": "EUR",
        "components": [
            ("Energy",  "Natural Gas INSEE",  0.3728),
            ("Labor",   "Labor Europe",       0.1864),
            ("Make-up", "Coal ZCE",           0.2408),
            ("Fixed",   None,                 0.2000),
        ],
    },
    {
        "product": "Aquasorb CS",
        "base_price": 1000,
        "base_year": 2023, "base_quarter": 1,
        "region": "Europe", "currency": "EUR",
        "components": [
            ("Energy",  "Natural Gas INSEE",   0.2478),
            ("Labor",   "Labor Europe",        0.1239),
            ("Make-up", "Coconut Charcoal",    0.4283),
            ("Fixed",   None,                  0.2000),
        ],
    },
]

SUPPLIER_NAME = "Jacobi Carbons"
SUPPLIER_COUNTRY = "Sweden"


class MissingReferenceData(RuntimeError):
    """An index the demo needs is missing, or is a content series."""


def check_commodities(existing: dict[str, str | None]) -> None:
    """`existing` maps every commodity name in the database to its
    `commodity_key`. Raises before any write (see the module docstring)."""
    created = {row[0] for row in NEW_COMMODITIES}
    written = created | {name for name, _region, _values in INDEX_VALUES}
    used = {name for cm in COST_MODELS for _label, name, _w in cm["components"] if name}
    missing = sorted(n for n in used if n not in existing and n not in created)
    content = sorted(n for n in written | used if existing.get(n) is not None)
    if missing or content:
        problems = []
        if missing:
            problems.append(f"missing: {', '.join(missing)}")
        if content:
            problems.append(f"owned by the content load: {', '.join(content)}")
        raise MissingReferenceData("Jacobi demo commodity indexes: " + "; ".join(problems))


def run():
    with engine.begin() as conn:
        # ── 1. Insert commodity indexes ──────────────────────────────────
        commodity_ids = {}

        # Get existing commodity ids
        rows = conn.execute(text(
            "SELECT id, name, commodity_key FROM commodity_indexes")).fetchall()
        check_commodities({r[1]: r[2] for r in rows})
        for r in rows:
            commodity_ids[r[1]] = r[0]

        for row in NEW_COMMODITIES:
            name, unit, source_url = row[0], row[1], row[2]
            currency = row[3] if len(row) > 3 else None
            category = row[4] if len(row) > 4 else None
            if name not in commodity_ids:
                result = conn.execute(text(
                    "INSERT INTO commodity_indexes (name, unit, source_url, currency, category, scrape_enabled) "
                    "VALUES (:name, :unit, :url, :currency, :category, false) RETURNING id"
                ), {"name": name, "unit": unit, "url": source_url, "currency": currency, "category": category})
                commodity_ids[name] = result.scalar()
                print(f"  Created commodity: {name} (id={commodity_ids[name]})")
            else:
                # Update source_url, currency, category if missing
                conn.execute(text(
                    "UPDATE commodity_indexes SET source_url = COALESCE(NULLIF(source_url, ''), :url), "
                    "currency = COALESCE(currency, :currency), category = COALESCE(category, :category) "
                    "WHERE id = :id"
                ), {"url": source_url, "currency": currency, "category": category, "id": commodity_ids[name]})
                print(f"  Commodity exists: {name} (id={commodity_ids[name]})")

        # ── 2. Insert index values ───────────────────────────────────────
        inserted_vals = 0
        skipped_vals = 0
        for commodity_name, region, values in INDEX_VALUES:
            cid = commodity_ids.get(commodity_name)
            if cid is None:
                print(f"  WARNING: commodity '{commodity_name}' not found, skipping values")
                continue
            for year, quarter, value in values:
                result = conn.execute(text(
                    "INSERT INTO index_values (commodity_id, region, year, quarter, value, source) "
                    "VALUES (:cid, :region, :year, :quarter, :value, 'seed') "
                    "ON CONFLICT (commodity_id, region, year, quarter) DO UPDATE SET value = :value "
                    "RETURNING id"
                ), {"cid": cid, "region": region, "year": year, "quarter": quarter, "value": value})
                inserted_vals += 1
        print(f"  Upserted {inserted_vals} index values")

        # Products get no chemical family: families are the platform taxonomy,
        # loaded from the content drop, and a seed must not add its own.

        # ── 4. Supplier ──────────────────────────────────────────────────
        row = conn.execute(text(
            "SELECT id FROM suppliers WHERE name = :name AND team_id = :tid"
        ), {"name": SUPPLIER_NAME, "tid": TEAM_ID}).fetchone()
        if row:
            supplier_id = row[0]
            print(f"  Supplier exists: {SUPPLIER_NAME} (id={supplier_id})")
        else:
            supplier_id = conn.execute(text(
                "INSERT INTO suppliers (team_id, name, country, created_at) "
                "VALUES (:tid, :name, :country, :now) RETURNING id"
            ), {"tid": TEAM_ID, "name": SUPPLIER_NAME, "country": SUPPLIER_COUNTRY, "now": now}).scalar()
            print(f"  Created supplier: {SUPPLIER_NAME} (id={supplier_id})")

        # ── 5. Products ──────────────────────────────────────────────────
        product_ids = {}
        for p in PRODUCTS:
            row = conn.execute(text(
                "SELECT id FROM products WHERE name = :name AND team_id = :tid"
            ), {"name": p["name"], "tid": TEAM_ID}).fetchone()
            if row:
                product_ids[p["name"]] = row[0]
                print(f"  Product exists: {p['name']} (id={row[0]})")
            else:
                pid = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO products (id, team_id, created_by, name, formula, unit, created_at, updated_at) "
                    "VALUES (:id, :tid, :uid, :name, :formula, :unit, :now, :now)"
                ), {
                    "id": pid, "tid": TEAM_ID, "uid": CREATED_BY,
                    "name": p["name"], "formula": p["formula"], "unit": p["unit"],
                    "now": now,
                })
                product_ids[p["name"]] = pid
                print(f"  Created product: {p['name']} (id={pid})")

        # ── 6. Cost models + formula versions + components ───────────────
        for cm in COST_MODELS:
            product_id = product_ids[cm["product"]]

            # Check if cost model already exists for this product + supplier + team
            row = conn.execute(text(
                "SELECT id FROM cost_models WHERE product_id = :pid AND supplier_id = :sid AND team_id = :tid"
            ), {"pid": product_id, "sid": supplier_id, "tid": TEAM_ID}).fetchone()

            if row:
                print(f"  Cost model exists for {cm['product']} (id={row[0]})")
                continue

            cm_id = str(uuid.uuid4())
            conn.execute(text(
                "INSERT INTO cost_models (id, team_id, product_id, supplier_id, region, currency, created_by, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, :sid, :region, :currency, :uid, :now, :now)"
            ), {
                "id": cm_id, "tid": TEAM_ID, "pid": product_id,
                "sid": supplier_id, "region": cm["region"], "currency": cm["currency"],
                "uid": CREATED_BY, "now": now,
            })
            print(f"  Created cost model: {cm['product']} (id={cm_id})")

            # Formula version
            fv_id = conn.execute(text(
                "INSERT INTO formula_versions (cost_model_id, base_price, base_year, base_quarter, margin_type, margin_value, created_at, updated_at) "
                "VALUES (:cmid, :bp, :by, :bq, 'unknown', 0, :now, :now) RETURNING id"
            ), {
                "cmid": cm_id, "bp": cm["base_price"],
                "by": cm["base_year"], "bq": cm["base_quarter"], "now": now,
            }).scalar()
            print(f"    Formula version: v1 (id={fv_id})")

            # Components
            for label, commodity_name, weight in cm["components"]:
                cid = commodity_ids.get(commodity_name) if commodity_name else None
                conn.execute(text(
                    "INSERT INTO formula_components (formula_version_id, label, commodity_id, weight) "
                    "VALUES (:fvid, :label, :cid, :weight)"
                ), {"fvid": fv_id, "label": label, "cid": cid, "weight": weight})
                print(f"    Component: {label} (weight={weight}, commodity_id={cid})")

        print("\nDone!")


if __name__ == "__main__":
    run()
