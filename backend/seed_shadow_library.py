"""
Seed the platform "shadow" formula library from sample_idea/full_shadow_formula_library.html.

Seeds two things (idempotent — safe to re-run):
  1. The ~29 commodity indexes referenced by the formulas that aren't already in
     the catalog (free public sources where one exists; manual/proxy otherwise).
  2. The 42 product formula templates as PLATFORM defaults (team_id IS NULL),
     each an advanced expression of the form
        P0 * ( w1*V1/V1B + w2*V2/V2B + ... + wFlat )
     where Vi is an index variable (resolved by name -> commodity_id), ViB is a
     fixed base anchor (default 100), and wFlat = 1 - sum(signed index weights)
     so the template evaluates to P0 at the base period and moves with the
     indices thereafter. Margin + fixed conversion costs ride flat (they don't
     track commodity indices) — the should-cost-vs-price model.

Run:  cd backend && source venv/bin/activate && python -m seed_shadow_library
"""
from sqlalchemy import text

from app.database import SessionLocal
from app.models import CommodityIndex, FormulaTemplate

SEED_USER_EMAIL = "jil@staminachem.com"  # super-admin; created_by for platform templates

# ── Part A: commodity indexes to seed (only those not already present) ────────
# (name, unit, currency, category, provider, frequency, source_url, scrape_enabled)
# Free live feed = scrape_enabled True with a real public source; the specialist
# paywalled inputs (Platts/ICIS/TZMI/ICDA) have no free feed -> manual, value via
# upload later. New rows seed NO values (consistent with app/seed.py).
NEW_INDEXES = [
    # Free public sources
    ("Industrial Electricity", "EUR/MWh", "EUR", "Energy", "Eurostat", "Quarterly",
     "https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_205/", True),
    ("Sulfur", "$/mt", "USD", "Chemical", "World Bank", "Quarterly",
     "https://www.worldbank.org/en/research/commodity-markets", True),
    ("Crude Palm Oil (CPO)", "$/mt", "USD", "Chemical", "MPOB", "Quarterly",
     "https://bepi.mpob.gov.my/", True),
    ("Palm Kernel Oil (PKO)", "$/mt", "USD", "Chemical", "MPOB", "Quarterly",
     "https://bepi.mpob.gov.my/", True),
    ("Sunflower Oil", "$/mt", "USD", "Chemical", "World Bank", "Quarterly",
     "https://www.worldbank.org/en/research/commodity-markets", True),
    ("Kerosene (jet)", "$/bbl", "USD", "Energy", "EIA", "Quarterly",
     "https://www.eia.gov/petroleum/", True),
    ("Silica Sand", "$/mt", "USD", "Metal", "USGS", "Quarterly",
     "https://www.usgs.gov/centers/national-minerals-information-center", True),
    # Manual / proxy (no free feed) — chemicals
    ("Ethylene Oxide", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Propylene Oxide", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Alpha Olefins", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Bisphenol A (BPA)", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Epichlorohydrin (ECH)", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Aniline", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Acrylonitrile (ACN)", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Acrylic Acid", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Formaldehyde", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Nitric Acid", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Hydrogen (grey)", "$/kg", "USD", "Energy", None, "Quarterly", None, False),
    ("Methyl Chloride", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Ethyleneamines (TEPA/DETA)", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Paraphenylenediamine (PPD)", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Terephthaloyl Chloride", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("NMP", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Salt (NaCl)", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Limestone", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    ("Tallow", "$/mt", "USD", "Chemical", None, "Quarterly", None, False),
    # Manual / proxy — metals & minerals
    ("Ilmenite", "$/mt", "USD", "Metal", None, "Quarterly", None, False),
    ("Rutile", "$/mt", "USD", "Metal", None, "Quarterly", None, False),
    ("Chromite", "$/mt", "USD", "Metal", None, "Quarterly", None, False),
]

# Free public source pages for indexes seeded without one — verified live.
# Chemicals → businessanalytiq (the same provider as the seeded chemical indexes);
# minerals → USGS National Minerals Information Center. Backfilled onto rows whose
# source_url is still empty (idempotent).
INDEX_SOURCE_URLS = {
    "Ethylene Oxide": "https://businessanalytiq.com/procurementanalytics/index/ethylene-oxide-price-index/",
    "Propylene Oxide": "https://businessanalytiq.com/procurementanalytics/index/propylene-oxide-price-index/",
    "Acrylonitrile (ACN)": "https://businessanalytiq.com/procurementanalytics/index/acrylonitrile-price-index/",
    "Acrylic Acid": "https://businessanalytiq.com/procurementanalytics/index/acrylic-acid/",
    "Formaldehyde": "https://businessanalytiq.com/procurementanalytics/index/formaldehyde-price-index/",
    "Nitric Acid": "https://businessanalytiq.com/procurementanalytics/index/nitric-acid-price-index/",
    "NMP": "https://businessanalytiq.com/procurementanalytics/index/n-methyl-2-pyrrolidone-nmp-price-index/",
    "Methyl Chloride": "https://businessanalytiq.com/procurementanalytics/index/chloromethane-price-index/",
    "Ethyleneamines (TEPA/DETA)": "https://businessanalytiq.com/procurementanalytics/index/ethylenediamine-price-index/",
    "Aniline": "https://businessanalytiq.com/procurementanalytics/index/aniline-price-index/",
    "Bisphenol A (BPA)": "https://businessanalytiq.com/procurementanalytics/index/bisphenol-a-price-index/",
    "Epichlorohydrin (ECH)": "https://businessanalytiq.com/procurementanalytics/index/epichlorohydrin-price-index/",
    "Hydrogen (grey)": "https://businessanalytiq.com/procurementanalytics/index/green-hydrogen-price-index/",
    # Minerals — USGS NMIC (free reference; no per-commodity free scrape feed exists)
    "Salt (NaCl)": "https://www.usgs.gov/centers/national-minerals-information-center/salt-statistics-and-information",
    "Limestone": "https://www.usgs.gov/centers/national-minerals-information-center/crushed-stone-statistics-and-information",
    "Ilmenite": "https://www.usgs.gov/centers/national-minerals-information-center/titanium-statistics-and-information",
    "Rutile": "https://www.usgs.gov/centers/national-minerals-information-center/titanium-statistics-and-information",
    "Chromite": "https://www.usgs.gov/centers/national-minerals-information-center/chromium-statistics-and-information",
}

# ── Variable symbol per canonical index name (kept short & readable) ──────────
SYMBOL = {
    "Sulfur": "S", "Industrial Electricity": "ELEC", "Natural Gas": "GAS",
    "Chlorine": "CL", "Salt (NaCl)": "NACL", "Limestone": "LIME",
    "Hydrogen (grey)": "H2", "Benzene": "BENZ", "Kerosene (jet)": "KERO",
    "Sulfuric Acid": "H2SO4", "Naphtha": "NAPH", "Palm Kernel Oil (PKO)": "PKO",
    "Ethylene Oxide": "EO", "Alpha Olefins": "AO", "Ethylene": "ETH",
    "Propylene": "PROP", "Toluene": "TOL", "Bisphenol A (BPA)": "BPA", "Epichlorohydrin (ECH)": "ECH",
    "Crude Palm Oil (CPO)": "CPO", "Tallow": "TALL", "Sunflower Oil": "SUNF",
    "Aniline": "ANIL", "Nitric Acid": "HNO3", "Acrylic Acid": "AA",
    "Ethanol": "ETOH", "Acrylonitrile (ACN)": "ACN", "Aluminum": "AL",
    "Hydrochloric Acid": "HCL", "Iron": "FE", "Phosphoric Acid": "H3PO4",
    "Formaldehyde": "FORM", "Ethyleneamines (TEPA/DETA)": "AMINE",
    "Methyl Chloride": "MECL", "Propylene Oxide": "PO", "Silica Sand": "SIL",
    "Paraphenylenediamine (PPD)": "PPD", "Terephthaloyl Chloride": "TPC",
    "NMP": "NMP", "Ilmenite": "ILM", "Rutile": "RUT", "Zinc": "ZN",
    "Chromite": "CHR",
}

# Placeholder base price per family ($/mt order-of-magnitude; user calibrates).
FAMILY = {
    "cinorg": ("Commodity inorganics", "P1", 200),
    "surf":   ("Surfactants", "P1", 1500),
    "solv":   ("Solvents", "P1", 1200),
    "resi":   ("Resins & polymers", "P1", 1300),
    "oleo":   ("Oleochemicals", "P2", 1400),
    "petro":  ("Petrochemical intermediates", "P2", 2500),
    "coag":   ("Coagulants & flocculants", "P2", 400),
    "of":     ("Oilfield chemicals", "P3", 3000),
    "fiber":  ("Fibers & reinforcements", "P3", 8000),
    "pigm":   ("Pigments & metal oxides", "P3", 3000),
}

# ── Part B: the 42 formulas ──────────────────────────────────────────────────
# (name, detail, family_key, margin_str, [(index_name | "FOLD", weight_pct), ...])
# "FOLD" = a tiny/recycled input (catalyst, air, initiator) with no index — its
# weight is absorbed into the flat term. Duplicate index names are aggregated.
FORMULAS = [
    # Commodity inorganics
    ("Sulfuric acid", "H₂SO₄ — contact process", "cinorg", "5–8%",
     [("Sulfur", 55), ("Industrial Electricity", 20), ("Natural Gas", 10)]),
    ("Hydrochloric acid", "HCl — chlor-alkali by-product", "cinorg", "5–8%",
     [("Chlorine", 30), ("Salt (NaCl)", 20), ("Industrial Electricity", 35)]),
    ("Caustic soda", "NaOH — chlor-alkali", "cinorg", "8–15%",
     [("Salt (NaCl)", 15), ("Chlorine", -10), ("Industrial Electricity", 45)]),
    ("Soda ash", "Na₂CO₃ — Solvay", "cinorg", "8–12%",
     [("Salt (NaCl)", 18), ("Limestone", 12), ("Industrial Electricity", 18), ("Natural Gas", 22)]),
    ("Hydrogen peroxide", "H₂O₂ — anthraquinone", "cinorg", "12–18%",
     [("Hydrogen (grey)", 20), ("FOLD", 10), ("Industrial Electricity", 25), ("Natural Gas", 13)]),
    # Surfactants
    ("LAS / LABS", "Linear alkylbenzene sulfonate", "surf", "10–14%",
     [("Benzene", 38), ("Kerosene (jet)", 22), ("Sulfuric Acid", 10), ("Industrial Electricity", 8), ("Naphtha", 7)]),
    ("AES", "Alcohol ethoxy sulfates", "surf", "10–14%",
     [("Palm Kernel Oil (PKO)", 42), ("Ethylene Oxide", 23), ("Sulfuric Acid", 8), ("Industrial Electricity", 10)]),
    ("AOS", "Alpha olefin sulfonates", "surf", "11–15%",
     [("Alpha Olefins", 50), ("Sulfuric Acid", 12), ("Industrial Electricity", 12)]),
    # Solvents
    ("Ethanol (industrial)", "Ethylene hydration", "solv", "6–10%",
     [("Ethylene", 62), ("Industrial Electricity", 12), ("Natural Gas", 8)]),
    ("IPA", "Isopropanol — propylene hydration", "solv", "6–10%",
     [("Propylene", 62), ("Industrial Electricity", 12), ("Natural Gas", 8)]),
    ("Ethylene glycol", "MEG — EO hydration", "solv", "5–8%",
     [("Ethylene Oxide", 60), ("Industrial Electricity", 15), ("Natural Gas", 8)]),
    ("Acetone", "Cumene process co-product", "solv", "5–8%",
     [("Benzene", 35), ("Propylene", 28), ("Industrial Electricity", 12), ("Natural Gas", 8)]),
    # Resins & polymers
    ("Polyethylene (PE)", "HDPE / LDPE / LLDPE", "resi", "6–10%",
     [("Ethylene", 70), ("Industrial Electricity", 8), ("Natural Gas", 4)]),
    ("Polypropylene (PP)", "Homo and copolymer", "resi", "6–10%",
     [("Propylene", 70), ("Industrial Electricity", 8), ("Natural Gas", 4)]),
    ("PVC", "EDC/VCM chlor-alkali route", "resi", "5–8%",
     [("Ethylene", 35), ("Chlorine", 25), ("Industrial Electricity", 22), ("Natural Gas", 5)]),
    ("Styrene / PS", "Benzene + ethylene → SM", "resi", "6–10%",
     [("Benzene", 40), ("Ethylene", 25), ("Industrial Electricity", 10), ("Natural Gas", 8)]),
    ("Epoxy resin", "Standard liquid BPA + ECH", "resi", "12–18%",
     [("Bisphenol A (BPA)", 40), ("Epichlorohydrin (ECH)", 28), ("Industrial Electricity", 8), ("Natural Gas", 4)]),
    # Oleochemicals
    ("Glycerine (refined)", "CPO / biodiesel by-product", "oleo", "8–12%",
     [("Crude Palm Oil (CPO)", 55), ("Industrial Electricity", 15), ("Natural Gas", 10)]),
    ("Fatty acids C12–C14", "Lauric / myristic — PKO route", "oleo", "8–12%",
     [("Palm Kernel Oil (PKO)", 62), ("Industrial Electricity", 12), ("Natural Gas", 8)]),
    ("Stearic acid C18", "Tallow / CPO hydrogenation", "oleo", "8–12%",
     [("Tallow", 58), ("Hydrogen (grey)", 10), ("Industrial Electricity", 10), ("Natural Gas", 7)]),
    ("Oleic acid C18:1", "High-oleic sunflower / CPO", "oleo", "10–14%",
     [("Sunflower Oil", 65), ("Industrial Electricity", 10), ("Natural Gas", 6)]),
    # Petrochemical intermediates
    ("MDI", "Methylene diphenyl diisocyanate", "petro", "10–18%",
     [("Benzene", 38), ("Aniline", 18), ("Chlorine", 10), ("Industrial Electricity", 12), ("Natural Gas", 5)]),
    ("TDI", "Toluene diisocyanate", "petro", "12–20%",
     [("Toluene", 42), ("Nitric Acid", 12), ("Chlorine", 10), ("Industrial Electricity", 12), ("Natural Gas", 5)]),
    ("Acrylic acid", "Propylene oxidation", "petro", "10–15%",
     [("Propylene", 55), ("FOLD", 5), ("Industrial Electricity", 15), ("Natural Gas", 8)]),
    ("Ethyl acrylate", "Acrylic acid + ethanol", "petro", "10–15%",
     [("Acrylic Acid", 52), ("Ethanol", 18), ("Industrial Electricity", 10), ("Natural Gas", 5)]),
    ("Acrylamide", "Acrylonitrile hydration", "petro", "10–15%",
     [("Acrylonitrile (ACN)", 58), ("Industrial Electricity", 14), ("Natural Gas", 8)]),
    # Coagulants & flocculants
    ("Aluminium sulfate", "Alum — Al₂(SO₄)₃", "coag", "8–12%",
     [("Aluminum", 42), ("Sulfuric Acid", 28), ("Industrial Electricity", 12)]),
    ("PAC", "Polyaluminium chloride", "coag", "8–13%",
     [("Aluminum", 40), ("Hydrochloric Acid", 25), ("Industrial Electricity", 14)]),
    ("Ferric sulfate", "Fe₂(SO₄)₃", "coag", "8–12%",
     [("Iron", 38), ("Sulfuric Acid", 30), ("Industrial Electricity", 12)]),
    ("PAM", "Polyacrylamide", "coag", "10–16%",
     [("Acrylonitrile (ACN)", 55), ("FOLD", 8), ("Industrial Electricity", 12), ("Natural Gas", 7)]),
    # Oilfield chemicals
    ("Corrosion inhibitors", "Imidazoline / amine-based", "of", "15–25%",
     [("Sunflower Oil", 35), ("Ethyleneamines (TEPA/DETA)", 25), ("Industrial Electricity", 8), ("Natural Gas", 5)]),
    ("Scale inhibitors", "Phosphonate (ATMP/HEDP)", "of", "15–25%",
     [("Phosphoric Acid", 32), ("Formaldehyde", 20), ("Ethyleneamines (TEPA/DETA)", 12), ("Industrial Electricity", 8)]),
    ("Biocides (QUAT)", "Quaternary ammonium", "of", "15–25%",
     [("Palm Kernel Oil (PKO)", 38), ("Methyl Chloride", 18), ("Industrial Electricity", 10), ("Natural Gas", 5)]),
    ("Demulsifiers", "EO/PO block copolymer", "of", "15–22%",
     [("Ethylene Oxide", 32), ("Propylene Oxide", 28), ("Industrial Electricity", 8), ("Natural Gas", 5)]),
    # Fibers & reinforcements
    ("Carbon fiber", "PAN precursor route", "fiber", "15–25%",
     [("Acrylonitrile (ACN)", 30), ("Industrial Electricity", 38), ("Natural Gas", 10)]),
    ("Glass fiber (E-glass)", "Silica + boron — continuous strand", "fiber", "12–18%",
     [("Silica Sand", 18), ("Limestone", 8), ("Industrial Electricity", 25), ("Natural Gas", 28)]),
    ("Aramid fiber", "Para-aramid PPTA — Kevlar-type", "fiber", "18–30%",
     [("Paraphenylenediamine (PPD)", 28), ("Terephthaloyl Chloride", 22), ("NMP", 8), ("Industrial Electricity", 15), ("Natural Gas", 6)]),
    # Pigments & metal oxides
    ("TiO₂ — sulfate", "Ilmenite + H₂SO₄ route", "pigm", "12–18%",
     [("Ilmenite", 30), ("Sulfuric Acid", 18), ("Industrial Electricity", 18), ("Natural Gas", 12)]),
    ("TiO₂ — chloride", "Rutile + Cl₂ → TiCl₄", "pigm", "12–18%",
     [("Rutile", 28), ("Chlorine", 18), ("Industrial Electricity", 25), ("Natural Gas", 8)]),
    ("Iron oxide (synthetic)", "Fe₂O₃ — precipitation", "pigm", "12–18%",
     [("Iron", 35), ("Sulfuric Acid", 18), ("Industrial Electricity", 18), ("Natural Gas", 8)]),
    ("Zinc oxide", "French process — Zn oxidation", "pigm", "10–15%",
     [("Zinc", 62), ("Industrial Electricity", 12), ("Natural Gas", 8)]),
    ("Chromium oxide", "Cr₂O₃ — chromite reduction", "pigm", "12–20%",
     [("Chromite", 38), ("Chromite", 18), ("Industrial Electricity", 15), ("Natural Gas", 8)]),
]


def _build_expression(components):
    """Return (expression, variables) for one formula.

    Aggregates duplicate index references, drops FOLD into the flat term, and
    sets wFlat so the expression evaluates to P0 at the base period.
    """
    agg: dict[str, float] = {}
    for name, wt in components:
        if name == "FOLD":
            continue
        agg[name] = agg.get(name, 0.0) + wt

    terms = []
    variables: dict[str, dict] = {"P0": {"type": "fixed", "value": None}}  # value set by caller
    signed_sum = 0.0
    for name, wt in agg.items():
        sym = SYMBOL.get(name)
        if sym is None:
            raise KeyError(f"No symbol mapping for index '{name}'")
        coeff = round(wt / 100.0, 4)
        signed_sum += coeff
        base_sym = sym + "B"
        terms.append(f"{coeff}*{sym}/{base_sym}")
        variables[sym] = {"type": "index", "name": name}  # commodity_id filled in later
        variables[base_sym] = {"type": "fixed", "value": 100}

    flat = round(1.0 - signed_sum, 4)
    body = " + ".join(terms)
    expression = f"P0*({body} + {flat})"
    return expression, variables


def run():
    db = SessionLocal()
    # Seed context: bypass RLS so platform rows (team_id IS NULL) can be written.
    db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))

    user_id = db.execute(
        text("SELECT id FROM users WHERE email = :e"), {"e": SEED_USER_EMAIL}
    ).scalar()
    if not user_id:
        raise SystemExit(f"Seed user {SEED_USER_EMAIL} not found")

    # ── Part A: indexes ──────────────────────────────────────────────────────
    inserted_idx = skipped_idx = 0
    for name, unit, ccy, cat, prov, freq, url, scrape in NEW_INDEXES:
        exists = db.query(CommodityIndex).filter(CommodityIndex.name == name).first()
        if exists:
            skipped_idx += 1
            continue
        db.add(CommodityIndex(
            name=name, unit=unit, currency=ccy, category=cat,
            provider=prov, frequency=freq, source_url=url, scrape_enabled=scrape,
        ))
        inserted_idx += 1
    db.commit()

    # Backfill free source URLs onto indexes still missing one (idempotent).
    filled_urls = 0
    for nm, src in INDEX_SOURCE_URLS.items():
        ci = db.query(CommodityIndex).filter(CommodityIndex.name == nm).first()
        if ci and not ci.source_url:
            ci.source_url = src
            filled_urls += 1
    db.commit()

    # Resolve index name -> commodity_id for variable wiring.
    id_by_name = {c.name: c.id for c in db.query(CommodityIndex).all()}

    # ── Part B: formula templates ────────────────────────────────────────────
    inserted_tpl = updated_tpl = 0
    for name, detail, fam_key, margin, components in FORMULAS:
        fam_label, priority, p0 = FAMILY[fam_key]
        expression, variables = _build_expression(components)
        variables["P0"]["value"] = p0
        # Wire index variables to real commodity_ids.
        for sym, vdef in variables.items():
            if vdef.get("type") == "index":
                cid = id_by_name.get(vdef.pop("name"))
                if cid is None:
                    raise SystemExit(f"Index for {sym} in '{name}' not found after seed")
                vdef["commodity_id"] = cid
        description = f"{detail}. {fam_label} ({priority}). Margin est. {margin}."

        row = (
            db.query(FormulaTemplate)
            .filter(FormulaTemplate.team_id.is_(None), FormulaTemplate.name == name)
            .first()
        )
        if row:
            row.description = description
            row.expression = expression
            row.variables = variables
            updated_tpl += 1
        else:
            db.add(FormulaTemplate(
                team_id=None, created_by=user_id, name=name,
                description=description, expression=expression, variables=variables,
            ))
            inserted_tpl += 1
    db.commit()
    db.close()

    print(f"indexes:   +{inserted_idx} inserted, {skipped_idx} already present")
    print(f"source_urls backfilled: {filled_urls}")
    print(f"templates: +{inserted_tpl} inserted, {updated_tpl} updated "
          f"({inserted_tpl + updated_tpl}/42)")


if __name__ == "__main__":
    run()
