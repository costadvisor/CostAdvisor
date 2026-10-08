"""
Seed script: Load Jacobi purchase history (prices + volumes) from jacobi_demo_data.xlsx
Creates products and cost models for each product+type combo, then seeds actual_prices and actual_volumes.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.database import engine

now = datetime.now(timezone.utc).isoformat()

TEAM_ID = "1db4d50c-b2c5-496b-8450-bf12b31484c0"
CREATED_BY = "d9cbd7a9-93ca-4400-a204-7f9ceaa87a23"  # Alexis Thomas
SUPPLIER_NAME = "Jacobi Carbons"

# Quarters in the Excel (columns C through O)
QUARTERS = [
    (2023, 1), (2023, 2), (2023, 3), (2023, 4),
    (2024, 1), (2024, 2), (2024, 3), (2024, 4),
    (2025, 1), (2025, 2), (2025, 3), (2025, 4),
    (2026, 1),
]

# Price data: (product, type, [price_per_quarter...])
# From "Price (EUR per MT)" sheet, rows 5-18
PRICES = [
    ("Aquasorb CS 8×30", "Virgin Coco", [2500, 2500, 2051, 1798, 1779, 1779, 1779, 1779, 2200, 2200, 2816, 2816, 3333]),
    ("Aquasorb CS 8×30", "Reactivated Coco", [1124, 997, 981, 996, 994, 1004, 1029, 1057, 1078, 1053, 1055, 1041, 1041]),
    ("Aquasorb CS 10×20", "Virgin Coco", [2400, 2400, 2039, 1816, 1804, 1804, 1804, 1804, 2173, 2173, 2725, 2725, 3196]),
    ("Aquasorb CS 10×20", "Reactivated Coco", [1079, 957, 941, 956, 954, 964, 987, 1015, 1035, 1011, 1013, 1000, 1000]),
    ("Aquasorb CX 8×30", "Virgin Coco", [2800, 2800, 2205, 1860, 1841, 1841, 1841, 1841, 2273, 2273, 2909, 2909, 3634]),
    ("Aquasorb CX 8×30", "Reactivated Coco", [1259, 1117, 1098, 1115, 1113, 1124, 1152, 1184, 1208, 1180, 1182, 1166, 1166]),
    ("Aquasorb MP23", "Virgin Mineral", [2400, 2400, 2326, 2326, 2326, 2326, 2326, 2326, 2580, 2580, 2580, 2580, 2210]),
    ("Aquasorb MP23", "Reactivated Mineral", [1079, 957, 941, 956, 954, 964, 987, 1015, 1035, 1011, 1013, 1000, 1000]),
    ("Aquasorb MP25", "Virgin Mineral", [2450, 2450, 2379, 2379, 2379, 2379, 2379, 2379, 2405, 2405, 2405, 2405, 2009]),
    ("Aquasorb AFP23", "Virgin Mineral", [2200, 2200, 2039, 2039, 2039, 2039, 2039, 2039, 2162, 2162, 2162, 2162, 1772]),
    ("Aquasorb MP23-UF", "Virgin Mineral", [2700, 2700, 2392, 2392, 2392, 2392, 2392, 2392, 1952, 1952, 1952, 1952, None]),  # Q1 2026 = N/A
    ("Aquasorb MP23-UF", "Reactivated Mineral", [1214, 1077, 1059, 1075, 1073, 1084, 1111, 1142, 1164, 1137, 1140, 1125, 1125]),
    ("Aquasorb 2000", "Virgin Mineral", [2350, 2350, 2206, 1940, 1785, 1785, 1785, 1785, 1807, 1807, 1807, 1807, 1718]),
    ("Aquasorb 2000", "Reactivated Mineral", [1057, 938, 922, 936, 935, 944, 967, 994, 1014, 990, 992, 979, 979]),
]

# Volume data: (product, type, [volume_per_quarter...])
# From "Volume (MT)" sheet, rows 5-18
VOLUMES = [
    ("Aquasorb CS 8×30", "Virgin Coco", [26.1, 27.6, 33.4, 29, 26.9, 28.4, 34.4, 29.9, 27.7, 29.2, 35.4, 30.7, 28.2]),
    ("Aquasorb CS 8×30", "Reactivated Coco", [104.4, 110.2, 133.4, 116, 107.5, 113.5, 137.4, 119.4, 110.6, 116.8, 141.4, 123, 112.7]),
    ("Aquasorb CS 10×20", "Virgin Coco", [7.2, 7.6, 9.2, 8, 7.4, 7.8, 9.5, 8.2, 7.6, 8.1, 9.8, 8.5, 7.8]),
    ("Aquasorb CS 10×20", "Reactivated Coco", [28.8, 30.4, 36.8, 32, 29.7, 31.3, 37.9, 33, 30.6, 32.2, 39, 33.9, 31.1]),
    ("Aquasorb CX 8×30", "Virgin Coco", [15.3, 16.2, 19.5, 17, 15.8, 16.6, 20.1, 17.5, 16.2, 17.1, 20.7, 18, 16.5]),
    ("Aquasorb CX 8×30", "Reactivated Coco", [61.2, 64.6, 78.2, 68, 63, 66.6, 80.6, 70, 64.9, 68.5, 82.9, 72.1, 66.1]),
    ("Aquasorb MP23", "Virgin Mineral", [15.3, 16.2, 19.5, 17, 15.8, 16.6, 20.1, 17.5, 16.2, 17.1, 20.7, 18, 16.5]),
    ("Aquasorb MP23", "Reactivated Mineral", [61.2, 64.6, 78.2, 68, 63, 66.6, 80.6, 70, 64.9, 68.5, 82.9, 72.1, 66.1]),
    ("Aquasorb MP25", "Virgin Mineral", [13.5, 14.2, 17.2, 15, 13.9, 14.7, 17.8, 15.5, 14.3, 15.1, 18.3, 15.9, 14.6]),
    ("Aquasorb AFP23", "Virgin Mineral", [20.7, 21.8, 26.4, 23, 21.3, 22.5, 27.2, 23.7, 21.9, 23.2, 28, 24.4, 22.4]),
    ("Aquasorb MP23-UF", "Virgin Mineral", [2.2, 2.3, 2.8, 2.4, 2.2, 2.3, 2.8, 2.5, 2.3, 2.4, 2.9, 2.5, 2.3]),
    ("Aquasorb MP23-UF", "Reactivated Mineral", [8.6, 9.1, 11, 9.6, 8.9, 9.4, 11.4, 9.9, 9.1, 9.7, 11.7, 10.2, 9.4]),
    ("Aquasorb 2000", "Virgin Mineral", [11.3, 12, 14.5, 12.6, 11.7, 12.3, 14.9, 13, 12, 12.7, 15.4, 13.4, 12.2]),
    ("Aquasorb 2000", "Reactivated Mineral", [45.4, 47.8, 57.9, 50.4, 46.7, 49.3, 59.7, 51.9, 48.1, 50.7, 61.4, 53.4, 49]),
]


def run():
    with engine.begin() as conn:
        # ── 1. Find supplier ────────────────────────────────────────────
        row = conn.execute(text(
            "SELECT id FROM suppliers WHERE name = :name AND team_id = :tid"
        ), {"name": SUPPLIER_NAME, "tid": TEAM_ID}).fetchone()
        if not row:
            print(f"ERROR: Supplier '{SUPPLIER_NAME}' not found. Run seed_jacobi.py first.")
            return
        supplier_id = row[0]
        print(f"  Supplier: {SUPPLIER_NAME} (id={supplier_id})")

        # Products get no chemical family: families are the platform taxonomy,
        # loaded from the content drop, and a seed must not add its own.

        # ── 3. Create products + cost models for each line item ─────────
        # Build a map of (product, type) -> cost_model_id
        cm_map = {}
        all_items = set()
        for product, ptype, _ in PRICES:
            all_items.add((product, ptype))

        for product, ptype in sorted(all_items):
            product_name = f"{product} ({ptype})"

            # Check if product exists
            row = conn.execute(text(
                "SELECT id FROM products WHERE name = :name AND team_id = :tid"
            ), {"name": product_name, "tid": TEAM_ID}).fetchone()

            if row:
                product_id = row[0]
                print(f"  Product exists: {product_name} (id={product_id})")
            else:
                product_id = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO products (id, team_id, created_by, name, formula, unit, created_at, updated_at) "
                    "VALUES (:id, :tid, :uid, :name, :formula, :unit, :now, :now)"
                ), {
                    "id": product_id, "tid": TEAM_ID, "uid": CREATED_BY,
                    "name": product_name, "formula": ptype, "unit": "t",
                    "now": now,
                })
                print(f"  Created product: {product_name} (id={product_id})")

            # Check if cost model exists
            row = conn.execute(text(
                "SELECT id FROM cost_models WHERE product_id = :pid AND supplier_id = :sid AND team_id = :tid"
            ), {"pid": product_id, "sid": supplier_id, "tid": TEAM_ID}).fetchone()

            if row:
                cm_id = str(row[0])
                print(f"  Cost model exists: {product_name} (id={cm_id})")
            else:
                cm_id = str(uuid.uuid4())
                conn.execute(text(
                    "INSERT INTO cost_models (id, team_id, product_id, supplier_id, region, currency, created_by, created_at, updated_at) "
                    "VALUES (:id, :tid, :pid, :sid, :region, :currency, :uid, :now, :now)"
                ), {
                    "id": cm_id, "tid": TEAM_ID, "pid": product_id,
                    "sid": supplier_id, "region": "Europe", "currency": "EUR",
                    "uid": CREATED_BY, "now": now,
                })
                print(f"  Created cost model: {product_name} (id={cm_id})")

            cm_map[(product, ptype)] = cm_id

        # ── 4. Seed actual_prices ───────────────────────────────────────
        price_count = 0
        for product, ptype, values in PRICES:
            cm_id = cm_map[(product, ptype)]
            for i, (year, quarter) in enumerate(QUARTERS):
                if i >= len(values) or values[i] is None:
                    continue
                price = values[i]
                conn.execute(text(
                    "INSERT INTO actual_prices (cost_model_id, uploaded_by, year, quarter, price, source_file, uploaded_at) "
                    "VALUES (:cmid, :uid, :year, :quarter, :price, :source, :now) "
                    "ON CONFLICT (cost_model_id, year, quarter) DO UPDATE SET price = :price, uploaded_by = :uid"
                ), {
                    "cmid": cm_id, "uid": CREATED_BY,
                    "year": year, "quarter": quarter, "price": price,
                    "source": "jacobi_demo_data.xlsx", "now": now,
                })
                price_count += 1
        print(f"  Upserted {price_count} price records")

        # ── 5. Seed actual_volumes ──────────────────────────────────────
        volume_count = 0
        for product, ptype, values in VOLUMES:
            cm_id = cm_map[(product, ptype)]
            for i, (year, quarter) in enumerate(QUARTERS):
                if i >= len(values) or values[i] is None:
                    continue
                volume = values[i]
                conn.execute(text(
                    "INSERT INTO actual_volumes (cost_model_id, uploaded_by, year, quarter, volume, unit, source_file, uploaded_at) "
                    "VALUES (:cmid, :uid, :year, :quarter, :volume, 't', :source, :now) "
                    "ON CONFLICT (cost_model_id, year, quarter) DO UPDATE SET volume = :volume, unit = 't', uploaded_by = :uid"
                ), {
                    "cmid": cm_id, "uid": CREATED_BY,
                    "year": year, "quarter": quarter, "volume": volume,
                    "source": "jacobi_demo_data.xlsx", "now": now,
                })
                volume_count += 1
        print(f"  Upserted {volume_count} volume records")

        print("\nDone!")


if __name__ == "__main__":
    run()
