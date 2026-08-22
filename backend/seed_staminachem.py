"""
Seed script: Staminachem demo data
Creates 5 products, 2 suppliers each, cost models with formula components,
actual prices and volumes across 2023-2025 to showcase gap analysis.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.database import engine

now = datetime.now(timezone.utc).isoformat()

TEAM_ID = "6ee41dc2-bd26-4a50-8589-f601c54a335d"   # Jil Varghese's Team
CREATED_BY = "13099867-d73e-400b-8f76-b557ad5c05e5" # jil@staminachem.com

# ─── 1. Index data (fill Caustic Soda — id=2 has no values yet) ──────────────
# Caustic Soda (NaOH) quarterly market prices $/mt — European spot
CAUSTIC_SODA_VALUES = [
    ("Europe", 2022, 1, 490.0), ("Europe", 2022, 2, 510.0),
    ("Europe", 2022, 3, 480.0), ("Europe", 2022, 4, 440.0),
    ("Europe", 2023, 1, 390.0), ("Europe", 2023, 2, 360.0),
    ("Europe", 2023, 3, 340.0), ("Europe", 2023, 4, 320.0),
    ("Europe", 2024, 1, 310.0), ("Europe", 2024, 2, 330.0),
    ("Europe", 2024, 3, 315.0), ("Europe", 2024, 4, 300.0),
    ("Europe", 2025, 1, 295.0), ("Europe", 2025, 2, 285.0),
    ("Europe", 2025, 3, 290.0), ("Europe", 2025, 4, 280.0),
]

# Sulfuric Acid (id=3) quarterly values $/mt
SULFURIC_ACID_VALUES = [
    ("Europe", 2022, 1, 195.0), ("Europe", 2022, 2, 210.0),
    ("Europe", 2022, 3, 200.0), ("Europe", 2022, 4, 185.0),
    ("Europe", 2023, 1, 175.0), ("Europe", 2023, 2, 165.0),
    ("Europe", 2023, 3, 158.0), ("Europe", 2023, 4, 150.0),
    ("Europe", 2024, 1, 148.0), ("Europe", 2024, 2, 155.0),
    ("Europe", 2024, 3, 150.0), ("Europe", 2024, 4, 142.0),
    ("Europe", 2025, 1, 140.0), ("Europe", 2025, 2, 135.0),
    ("Europe", 2025, 3, 138.0), ("Europe", 2025, 4, 132.0),
]

# Hydrochloric Acid (id=4) quarterly values $/mt
HCL_VALUES = [
    ("Europe", 2022, 1, 290.0), ("Europe", 2022, 2, 320.0),
    ("Europe", 2022, 3, 305.0), ("Europe", 2022, 4, 275.0),
    ("Europe", 2023, 1, 255.0), ("Europe", 2023, 2, 240.0),
    ("Europe", 2023, 3, 228.0), ("Europe", 2023, 4, 215.0),
    ("Europe", 2024, 1, 210.0), ("Europe", 2024, 2, 220.0),
    ("Europe", 2024, 3, 215.0), ("Europe", 2024, 4, 205.0),
    ("Europe", 2025, 1, 200.0), ("Europe", 2025, 2, 195.0),
    ("Europe", 2025, 3, 198.0), ("Europe", 2025, 4, 190.0),
]

INDEX_BACKFILLS = [
    (2, CAUSTIC_SODA_VALUES),
    (3, SULFURIC_ACID_VALUES),
    (4, HCL_VALUES),
]

# ─── 2. Suppliers ─────────────────────────────────────────────────────────────
SUPPLIERS = [
    {"name": "Brenntag SE",   "country": "Germany"},
    {"name": "INEOS Group",   "country": "United Kingdom"},
    {"name": "Solvay SA",     "country": "Belgium"},
    {"name": "BASF SE",       "country": "Germany"},
    {"name": "Evonik Industries", "country": "Germany"},
]

# ─── 3. Products + Cost Models ────────────────────────────────────────────────
# Each entry: product + list of (supplier_name, base_price, base_year, base_quarter,
#             components[(label, commodity_id, weight)])
# commodity_ids: 2=Caustic Soda, 3=Sulfuric Acid, 4=Hydrochloric Acid,
#                35=Energy & Utilities, 36=Chlorine, 37=Ammonia, 32=Brent Crude Oil

COST_MODELS = [
    {
        "product": "Caustic Soda 50% Solution",
        "unit": "mt",
        "family": "Industrial Chemicals",
        "region": "Europe",
        "currency": "USD",
        "suppliers": [
            {
                "name": "Brenntag SE",
                "base_price": 450.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Raw NaOH",  2,  0.60),
                    ("Energy",    35, 0.20),
                    ("Fixed",     None, 0.20),
                ],
            },
            {
                "name": "INEOS Group",
                "base_price": 445.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Raw NaOH",  2,  0.60),
                    ("Energy",    35, 0.20),
                    ("Fixed",     None, 0.20),
                ],
            },
        ],
    },
    {
        "product": "Sodium Hypochlorite 15%",
        "unit": "mt",
        "family": "Industrial Chemicals",
        "region": "Europe",
        "currency": "USD",
        "suppliers": [
            {
                "name": "Solvay SA",
                "base_price": 280.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Chlorine",     36, 0.50),
                    ("Caustic Soda", 2,  0.30),
                    ("Fixed",        None, 0.20),
                ],
            },
            {
                "name": "Brenntag SE",
                "base_price": 285.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Chlorine",     36, 0.50),
                    ("Caustic Soda", 2,  0.30),
                    ("Fixed",        None, 0.20),
                ],
            },
        ],
    },
    {
        "product": "Sulfuric Acid 98%",
        "unit": "mt",
        "family": "Industrial Chemicals",
        "region": "Europe",
        "currency": "USD",
        "suppliers": [
            {
                "name": "BASF SE",
                "base_price": 195.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Sulfur/Acid",  3,  0.55),
                    ("Energy",       35, 0.25),
                    ("Fixed",        None, 0.20),
                ],
            },
            {
                "name": "INEOS Group",
                "base_price": 188.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Sulfur/Acid",  3,  0.55),
                    ("Energy",       35, 0.25),
                    ("Fixed",        None, 0.20),
                ],
            },
        ],
    },
    {
        "product": "Ammonia Solution 25%",
        "unit": "mt",
        "family": "Industrial Chemicals",
        "region": "Europe",
        "currency": "EUR",
        "suppliers": [
            {
                "name": "Evonik Industries",
                "base_price": 320.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Ammonia",  37, 0.65),
                    ("Energy",   35, 0.15),
                    ("Fixed",    None, 0.20),
                ],
            },
            {
                "name": "BASF SE",
                "base_price": 315.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("Ammonia",  37, 0.65),
                    ("Energy",   35, 0.15),
                    ("Fixed",    None, 0.20),
                ],
            },
        ],
    },
    {
        "product": "Hydrochloric Acid 33%",
        "unit": "mt",
        "family": "Industrial Chemicals",
        "region": "Europe",
        "currency": "USD",
        "suppliers": [
            {
                "name": "Solvay SA",
                "base_price": 265.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("HCl Raw",  4,  0.60),
                    ("Energy",   35, 0.20),
                    ("Fixed",    None, 0.20),
                ],
            },
            {
                "name": "BASF SE",
                "base_price": 258.0,
                "base_year": 2023, "base_quarter": 1,
                "components": [
                    ("HCl Raw",  4,  0.60),
                    ("Energy",   35, 0.20),
                    ("Fixed",    None, 0.20),
                ],
            },
        ],
    },
]

# ─── 4. Actual prices + volumes ───────────────────────────────────────────────
# Quarters for purchase history
QUARTERS = [
    (2023,1),(2023,2),(2023,3),(2023,4),
    (2024,1),(2024,2),(2024,3),(2024,4),
    (2025,1),(2025,2),(2025,3),(2025,4),
]

# Prices by (product, supplier) key — index matches QUARTERS list above
# Deliberately above should-cost to show positive gaps
ACTUAL_PRICES = {
    ("Caustic Soda 50% Solution", "Brenntag SE"):    [470, 485, 462, 435, 420, 440, 425, 408, 398, 385, 392, 378],
    ("Caustic Soda 50% Solution", "INEOS Group"):    [460, 475, 455, 425, 410, 428, 418, 400, 390, 378, 384, 368],
    ("Sodium Hypochlorite 15%",   "Solvay SA"):      [295, 302, 288, 272, 265, 278, 270, 258, 252, 244, 248, 238],
    ("Sodium Hypochlorite 15%",   "Brenntag SE"):    [298, 308, 292, 275, 268, 282, 272, 261, 254, 246, 250, 242],
    ("Sulfuric Acid 98%",         "BASF SE"):        [202, 215, 206, 192, 186, 196, 192, 182, 178, 172, 175, 168],
    ("Sulfuric Acid 98%",         "INEOS Group"):    [196, 208, 200, 186, 180, 190, 185, 176, 172, 166, 170, 162],
    ("Ammonia Solution 25%",      "Evonik Industries"):[340, 355, 338, 318, 305, 318, 310, 298, 290, 280, 285, 274],
    ("Ammonia Solution 25%",      "BASF SE"):        [335, 348, 330, 312, 298, 312, 304, 292, 284, 274, 278, 268],
    ("Hydrochloric Acid 33%",     "Solvay SA"):      [278, 290, 278, 260, 252, 262, 255, 242, 235, 228, 231, 222],
    ("Hydrochloric Acid 33%",     "BASF SE"):        [272, 282, 270, 254, 245, 256, 248, 236, 229, 222, 225, 216],
}

# Volumes (mt) by (product, supplier) — realistic quarterly purchasing volumes
ACTUAL_VOLUMES = {
    ("Caustic Soda 50% Solution", "Brenntag SE"):    [85, 90, 95, 88, 82, 87, 92, 85, 80, 85, 90, 83],
    ("Caustic Soda 50% Solution", "INEOS Group"):    [40, 42, 45, 41, 38, 40, 43, 40, 37, 39, 42, 38],
    ("Sodium Hypochlorite 15%",   "Solvay SA"):      [120, 130, 140, 125, 115, 122, 132, 120, 112, 118, 128, 116],
    ("Sodium Hypochlorite 15%",   "Brenntag SE"):    [55, 60, 65, 58, 52, 56, 62, 56, 51, 54, 60, 54],
    ("Sulfuric Acid 98%",         "BASF SE"):        [200, 215, 225, 205, 195, 205, 218, 200, 190, 200, 212, 195],
    ("Sulfuric Acid 98%",         "INEOS Group"):    [80, 85, 90, 82, 78, 82, 88, 80, 76, 80, 85, 78],
    ("Ammonia Solution 25%",      "Evonik Industries"):[45, 48, 52, 46, 43, 46, 50, 45, 42, 45, 48, 43],
    ("Ammonia Solution 25%",      "BASF SE"):        [30, 32, 35, 31, 28, 30, 33, 30, 28, 29, 32, 28],
    ("Hydrochloric Acid 33%",     "Solvay SA"):      [65, 70, 75, 68, 62, 66, 71, 65, 60, 64, 69, 62],
    ("Hydrochloric Acid 33%",     "BASF SE"):        [35, 38, 40, 36, 33, 36, 38, 35, 32, 34, 37, 33],
}


def run(team_id: str = TEAM_ID, created_by: str = CREATED_BY):
    with engine.begin() as conn:
        # Bypass RLS for seed scripts (same pattern as Celery tasks)
        conn.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        conn.execute(text("SELECT set_config('app.current_team_id', :tid, true)"), {"tid": team_id})

        # ── 1. Backfill index values ─────────────────────────────────────
        print("=== Backfilling index values ===")
        for commodity_id, values in INDEX_BACKFILLS:
            inserted = 0
            for region, year, quarter, value in values:
                conn.execute(text(
                    "INSERT INTO index_values (commodity_id, region, year, quarter, value, source) "
                    "VALUES (:cid, :region, :year, :quarter, :value, 'seed') "
                    "ON CONFLICT (commodity_id, region, year, quarter) DO UPDATE SET value = :value"
                ), {"cid": commodity_id, "region": region, "year": year, "quarter": quarter, "value": value})
                inserted += 1
            print(f"  commodity_id={commodity_id}: upserted {inserted} values")

        # ── 2. Chemical family ───────────────────────────────────────────
        row = conn.execute(text("SELECT id FROM chemical_families WHERE name = 'Industrial Chemicals'")).fetchone()
        if row:
            family_id = row[0]
        else:
            family_id = conn.execute(text(
                "INSERT INTO chemical_families (name) VALUES ('Industrial Chemicals') RETURNING id"
            )).scalar()
        print(f"  Chemical family: Industrial Chemicals (id={family_id})")

        # ── 3. Suppliers ─────────────────────────────────────────────────
        print("=== Creating suppliers ===")
        supplier_ids = {}
        for s in SUPPLIERS:
            row = conn.execute(text(
                "SELECT id FROM suppliers WHERE name = :name AND team_id = :tid"
            ), {"name": s["name"], "tid": team_id}).fetchone()
            if row:
                supplier_ids[s["name"]] = str(row[0])
                print(f"  Exists: {s['name']} (id={row[0]})")
            else:
                sid = conn.execute(text(
                    "INSERT INTO suppliers (team_id, name, country, created_at) "
                    "VALUES (:tid, :name, :country, :now) RETURNING id"
                ), {"tid": team_id, "name": s["name"], "country": s["country"], "now": now}).scalar()
                supplier_ids[s["name"]] = str(sid)
                print(f"  Created: {s['name']} (id={sid})")

        # ── 4. Products ───────────────────────────────────────────────────
        print("=== Creating products ===")
        product_ids = {}
        for cm in COST_MODELS:
            row = conn.execute(text(
                "SELECT id FROM products WHERE name = :name AND team_id = :tid"
            ), {"name": cm["product"], "tid": team_id}).fetchone()
            if row:
                product_ids[cm["product"]] = str(row[0])
                print(f"  Exists: {cm['product']} (id={row[0]})")
            else:
                pid = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO products (id, team_id, created_by, name, formula, unit, chemical_family_id, created_at, updated_at) "
                    "VALUES (:id, :tid, :uid, :name, :formula, :unit, :fid, :now, :now)"
                ), {
                    "id": pid, "tid": team_id, "uid": created_by,
                    "name": cm["product"], "formula": cm["product"],
                    "unit": cm["unit"], "fid": family_id, "now": now,
                })
                product_ids[cm["product"]] = pid
                print(f"  Created: {cm['product']} (id={pid})")

        # ── 5. Cost models + formula versions + components ────────────────
        print("=== Creating cost models ===")
        cm_id_map = {}  # (product, supplier) -> cost_model_id
        for cm in COST_MODELS:
            product_id = product_ids[cm["product"]]
            for sup in cm["suppliers"]:
                supplier_id = supplier_ids[sup["name"]]
                row = conn.execute(text(
                    "SELECT id FROM cost_models WHERE product_id = :pid AND supplier_id = :sid AND team_id = :tid"
                ), {"pid": product_id, "sid": supplier_id, "tid": team_id}).fetchone()

                if row:
                    cm_id = str(row[0])
                    print(f"  Exists: {cm['product']} / {sup['name']} (id={cm_id})")
                else:
                    cm_id = str(uuid.uuid4())
                    conn.execute(text(
                        "INSERT INTO cost_models (id, team_id, product_id, supplier_id, region, currency, created_by, created_at, updated_at) "
                        "VALUES (:id, :tid, :pid, :sid, :region, :currency, :uid, :now, :now)"
                    ), {
                        "id": cm_id, "tid": team_id, "pid": product_id,
                        "sid": supplier_id, "region": cm["region"], "currency": cm["currency"],
                        "uid": created_by, "now": now,
                    })
                    print(f"  Created: {cm['product']} / {sup['name']} (id={cm_id})")

                    # Formula version
                    fv_id = conn.execute(text(
                        "INSERT INTO formula_versions (cost_model_id, base_price, base_year, base_quarter, "
                        "margin_type, margin_value, created_at, updated_at) "
                        "VALUES (:cmid, :bp, :by, :bq, 'unknown', 0, :now, :now) RETURNING id"
                    ), {
                        "cmid": cm_id, "bp": sup["base_price"],
                        "by": sup["base_year"], "bq": sup["base_quarter"], "now": now,
                    }).scalar()

                    for label, commodity_id, weight in sup["components"]:
                        conn.execute(text(
                            "INSERT INTO formula_components (formula_version_id, label, commodity_id, weight) "
                            "VALUES (:fvid, :label, :cid, :weight)"
                        ), {"fvid": fv_id, "label": label, "cid": commodity_id, "weight": weight})

                cm_id_map[(cm["product"], sup["name"])] = cm_id

        # ── 6. Actual prices ──────────────────────────────────────────────
        print("=== Seeding actual prices ===")
        price_count = 0
        for (product, supplier), prices in ACTUAL_PRICES.items():
            cm_id = cm_id_map.get((product, supplier))
            if not cm_id:
                print(f"  WARN: no cost model for {product} / {supplier}")
                continue
            for i, (year, quarter) in enumerate(QUARTERS):
                if i >= len(prices) or prices[i] is None:
                    continue
                conn.execute(text(
                    "INSERT INTO actual_prices (cost_model_id, uploaded_by, year, quarter, price, source_file, uploaded_at) "
                    "VALUES (:cmid, :uid, :year, :quarter, :price, :source, :now) "
                    "ON CONFLICT (cost_model_id, year, quarter) DO UPDATE SET price = :price, uploaded_by = :uid"
                ), {
                    "cmid": cm_id, "uid": created_by,
                    "year": year, "quarter": quarter, "price": prices[i],
                    "source": "staminachem_demo.csv", "now": now,
                })
                price_count += 1
        print(f"  Upserted {price_count} price records")

        # ── 7. Actual volumes ─────────────────────────────────────────────
        print("=== Seeding actual volumes ===")
        vol_count = 0
        for (product, supplier), vols in ACTUAL_VOLUMES.items():
            cm_id = cm_id_map.get((product, supplier))
            if not cm_id:
                continue
            for i, (year, quarter) in enumerate(QUARTERS):
                if i >= len(vols) or vols[i] is None:
                    continue
                conn.execute(text(
                    "INSERT INTO actual_volumes (cost_model_id, uploaded_by, year, quarter, volume, unit, source_file, uploaded_at) "
                    "VALUES (:cmid, :uid, :year, :quarter, :volume, :unit, :source, :now) "
                    "ON CONFLICT (cost_model_id, year, quarter) DO UPDATE SET volume = :volume, unit = :unit, uploaded_by = :uid"
                ), {
                    "cmid": cm_id, "uid": created_by,
                    "year": year, "quarter": quarter, "volume": vols[i],
                    "unit": "mt", "source": "staminachem_demo.csv", "now": now,
                })
                vol_count += 1
        print(f"  Upserted {vol_count} volume records")

        print("\n=== Done! ===")
        print(f"  5 products, {len(SUPPLIERS)} suppliers, {len(cm_id_map)} cost models")
        print(f"  {price_count} price records, {vol_count} volume records")


if __name__ == "__main__":
    run()
