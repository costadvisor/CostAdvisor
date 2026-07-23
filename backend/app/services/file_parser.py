"""
Parse uploaded CSV and Excel files for index data, actual prices, volumes, and FX rates.
"""
import io
import re
import pandas as pd


def _detect_format(filename: str) -> str:
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        return "excel"
    return "csv"


def _parse_period(period_str: str) -> tuple[int, int]:
    """
    Parse period strings like 'Q1-2023', 'Q1-23', 'Q1 2023', '2023-Q1' into (year, quarter).
    """
    period_str = period_str.strip()

    m = re.match(r"Q(\d)[- ](\d{2,4})", period_str, re.IGNORECASE)
    if m:
        q = int(m.group(1))
        y = int(m.group(2))
        if y < 100:
            y += 2000
        return (y, q)

    m = re.match(r"(\d{4})[- ]Q(\d)", period_str, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    raise ValueError(f"Cannot parse period: {period_str}")


def _read_file(content: bytes, filename: str) -> pd.DataFrame:
    fmt = _detect_format(filename)
    if fmt == "excel":
        df = pd.read_excel(io.BytesIO(content))
    else:
        df = pd.read_csv(io.BytesIO(content))
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def parse_index_upload(content: bytes, filename: str) -> dict:
    """
    Parse index override upload.
    Expected columns: material, region, period, value

    Returns {"rows": [...], "errors": [{"row": N, "message": "..."}]}.
    Raises ValueError only for structural issues (missing required columns).
    """
    df = _read_file(content, filename)

    required = {"material", "region", "period", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected: material, region, period, value."
        )

    rows = []
    errors = []
    for idx, row in df.iterrows():
        row_num = int(idx) + 2
        try:
            year, quarter = _parse_period(str(row["period"]))
        except ValueError:
            errors.append({
                "row": row_num,
                "message": f"Invalid period '{row['period']}'. Use format Q1-2023 or 2023-Q1.",
            })
            continue
        try:
            value = float(row["value"])
        except (ValueError, TypeError):
            errors.append({
                "row": row_num,
                "message": f"Invalid value '{row['value']}'. Must be a number.",
            })
            continue
        material = str(row["material"]).strip()
        if not material:
            errors.append({"row": row_num, "message": "Material cannot be empty."})
            continue
        rows.append({
            "material": material,
            "region": str(row["region"]).strip(),
            "year": year,
            "quarter": quarter,
            "value": value,
        })
    return {"rows": rows, "errors": errors}


def parse_coverage_price_upload(content: bytes, filename: str) -> dict:
    """
    Parse catalog combo base-price upload (Scrum 58/60 coverage anchors).
    Expected columns: formula (catalog code, e.g. OLE-FAC-SAT), region, base_price
    Optional columns: currency, base_period (Q1-2025), margin_pct

    Returns {"rows": [...], "errors": [{"row": N, "message": "..."}]}.
    Raises ValueError only for structural issues (missing required columns).
    """
    df = _read_file(content, filename)

    required = {"formula", "region", "base_price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected: formula, region, base_price. Optional: currency, base_period, margin_pct."
        )

    rows = []
    errors = []
    for idx, row in df.iterrows():
        row_num = int(idx) + 2
        code = str(row["formula"]).strip()
        region = str(row["region"]).strip()
        if not code or code.lower() == "nan":
            errors.append({"row": row_num, "message": "Formula code cannot be empty."})
            continue
        if not region or region.lower() == "nan":
            errors.append({"row": row_num, "message": "Region cannot be empty."})
            continue
        try:
            base_price = float(row["base_price"])
            if base_price < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors.append({
                "row": row_num,
                "message": f"Invalid base_price '{row['base_price']}'. Must be a non-negative number.",
            })
            continue

        out = {"code": code, "region": region, "base_price": base_price,
               "currency": None, "base_year": None, "base_quarter": None, "margin_pct": None}

        if "currency" in df.columns and pd.notna(row["currency"]):
            ccy = str(row["currency"]).strip().upper()
            if len(ccy) != 3:
                errors.append({"row": row_num, "message": f"Invalid currency '{row['currency']}'. Use a 3-letter code."})
                continue
            out["currency"] = ccy
        if "base_period" in df.columns and pd.notna(row["base_period"]):
            try:
                out["base_year"], out["base_quarter"] = _parse_period(str(row["base_period"]))
            except ValueError:
                errors.append({
                    "row": row_num,
                    "message": f"Invalid base_period '{row['base_period']}'. Use format Q1-2025 or 2025-Q1.",
                })
                continue
        if "margin_pct" in df.columns and pd.notna(row["margin_pct"]):
            try:
                out["margin_pct"] = float(row["margin_pct"])
            except (ValueError, TypeError):
                errors.append({"row": row_num, "message": f"Invalid margin_pct '{row['margin_pct']}'."})
                continue

        rows.append(out)
    return {"rows": rows, "errors": errors}


def parse_price_upload(content: bytes, filename: str) -> dict:
    """
    Parse actual price upload.
    Expected columns: period, price
    Optional columns: incoterm, named_place

    Returns {"rows": [...], "errors": [{"row": N, "message": "..."}]} instead of
    raising on bad individual rows — the caller decides how to handle partial failures.
    Raises ValueError only for structural issues (missing required columns, unreadable file).
    """
    df = _read_file(content, filename)

    required = {"period", "price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected: period, price. Optional: incoterm, named_place."
        )

    has_incoterm = "incoterm" in df.columns
    has_named_place = "named_place" in df.columns

    rows = []
    errors = []
    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # +2: 1-based + header row
        try:
            year, quarter = _parse_period(str(row["period"]))
        except ValueError:
            errors.append({
                "row": row_num,
                "message": f"Invalid period '{row['period']}'. Use format Q1-2023 or 2023-Q1.",
            })
            continue
        try:
            price = float(row["price"])
        except (ValueError, TypeError):
            errors.append({
                "row": row_num,
                "message": f"Invalid price '{row['price']}'. Must be a number.",
            })
            continue

        entry = {"year": year, "quarter": quarter, "price": price}
        if has_incoterm:
            v = row["incoterm"]
            entry["incoterm"] = str(v).strip().upper() if pd.notna(v) and str(v).strip() else None
        if has_named_place:
            v = row["named_place"]
            entry["named_place"] = str(v).strip() if pd.notna(v) and str(v).strip() else None
        rows.append(entry)

    return {"rows": rows, "errors": errors}


def parse_volume_upload(content: bytes, filename: str) -> dict:
    """
    Parse actual volume upload.
    Expected columns: period, volume
    Optional column: unit

    Returns {"rows": [...], "errors": [{"row": N, "message": "..."}]}.
    Raises ValueError only for structural issues (missing required columns).
    """
    df = _read_file(content, filename)

    required = {"period", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected: period, volume. Optional: unit."
        )

    has_unit = "unit" in df.columns
    rows = []
    errors = []
    for idx, row in df.iterrows():
        row_num = int(idx) + 2
        try:
            year, quarter = _parse_period(str(row["period"]))
        except ValueError:
            errors.append({
                "row": row_num,
                "message": f"Invalid period '{row['period']}'. Use format Q1-2023 or 2023-Q1.",
            })
            continue
        try:
            volume = float(row["volume"])
        except (ValueError, TypeError):
            errors.append({
                "row": row_num,
                "message": f"Invalid volume '{row['volume']}'. Must be a number.",
            })
            continue
        entry = {"year": year, "quarter": quarter, "volume": volume}
        if has_unit:
            v = row["unit"]
            entry["unit"] = str(v).strip() if pd.notna(v) and str(v).strip() else "kg"
        rows.append(entry)
    return {"rows": rows, "errors": errors}


def parse_fx_upload(content: bytes, filename: str) -> dict:
    """
    Parse FX rate upload.
    Expected columns: from_currency, to_currency, period, rate

    Returns {"rows": [...], "errors": [{"row": N, "message": "..."}]}.
    Raises ValueError only for structural issues (missing required columns).
    """
    df = _read_file(content, filename)

    required = {"from_currency", "to_currency", "period", "rate"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            f"Expected: from_currency, to_currency, period, rate."
        )

    rows = []
    errors = []
    for idx, row in df.iterrows():
        row_num = int(idx) + 2
        try:
            year, quarter = _parse_period(str(row["period"]))
        except ValueError:
            errors.append({
                "row": row_num,
                "message": f"Invalid period '{row['period']}'. Use format Q1-2023 or 2023-Q1.",
            })
            continue
        try:
            rate = float(row["rate"])
        except (ValueError, TypeError):
            errors.append({
                "row": row_num,
                "message": f"Invalid rate '{row['rate']}'. Must be a number.",
            })
            continue
        rows.append({
            "from_currency": str(row["from_currency"]).strip().upper(),
            "to_currency": str(row["to_currency"]).strip().upper(),
            "year": year,
            "quarter": quarter,
            "rate": rate,
        })
    return {"rows": rows, "errors": errors}
