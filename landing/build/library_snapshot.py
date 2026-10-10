# Writes data/library.snapshot.json: the REAL library data the landing page shows (the blue
# "from the library" thread, plan note "Intelligence thread (10 Oct)").
# Only composite should-cost indexes (base 100 = Jan 2023) and structure counts/names are written.
# Never written: recipe weights, cost-line lists or shares, series keys, tags, product codes, maker
# names or counts, internal statuses, report/playbook prose. The guards at the end refuse to write
# otherwise. Reads our own extraction of the content drop as data (run it isolated, -I); it never
# imports or runs anything from the drop.
#   python3 -I build/library_snapshot.py <drop dir, e.g. ../docs/drop_live> data/library.snapshot.json
# Cycle percentile, seasonality and volatility are the app's values (its calibration and seasonal
# factors live in the database, not the drop): re-read them from the card's market tab on refresh.
# Then: node build/prerender.mjs && node build/og.mjs && node --test tests/story.test.mjs
import json, sys, statistics

drop, out_path = sys.argv[1], sys.argv[2]
raw = drop + '/raw'
fc = json.load(open(raw + '/FORMULA_COMBOS.json'))['BCI-FECL3-LIQ']
cov = json.load(open(raw + '/FCOVERED.json'))
fidx = json.load(open(raw + '/FIDX.json'))
ffore = json.load(open(raw + '/FFORE.json'))

ORDER = ['EU', 'NA', 'CN', 'APAC', 'IN', 'MEA']  # the app's region-chip order on the product page
series = {}
for combo in fc['combos']:
    act = [0.0] * 42; fore = [0.0] * 6; tot = 0
    for w, _label, tag, _cat in combo['lines']:
        tot += w
        key = cov.get(tag)
        if key in fidx:  # an index line: weight x series(m) / series(Jan 2023)
            s = fidx[key]; b = s[0]; f = ffore.get(key, [s[-1]] * 6)
            for i in range(42): act[i] += w * s[i] / b
            for i in range(6): fore[i] += w * f[i] / b
        else:            # fixed / margin line: held flat at its weight
            for i in range(42): act[i] += w
            for i in range(6): fore[i] += w
    series[combo['region']] = ([a * 100 / tot for a in act], [a * 100 / tot for a in fore])

eu, eu_f = series['EU']
r1 = lambda v: round(v + 1e-9, 1)
r2 = lambda v: round(v + 1e-9, 2)
lo, hi = min(eu), max(eu)
q = lambda i: sum(eu[i:i + 3]) / 3  # quarter average from month index i
mom = [(eu[i] / eu[i - 1] - 1) * 100 for i in range(1, 42)]

snap = {
    "_comment": "REAL library data for the landing page (the blue 'from the library' thread). Computed on 10 October 2026 from the current content drop (extracted 8 October 2026 from the research database, commit 5ed3280): for each region, the should-cost index = sum of weight x series(month) / series(Jan 2023) over the index lines, plus the weight of the fixed and margin lines, divided by 100 (base 100 = January 2023), as the app's market tab computes it. Only the composite index is published here: no recipe weights, no cost lines, no series codes, no product codes, no maker names or counts, no report or playbook text. Cycle, seasonality and volatility are the app's own values for the same card (market tab, staging build, 9 October 2026). Report section names are the app's 'In this analysis' labels; playbook family names and counts are structure only. Refresh with the drop.",
    "as_of": "October 2026",
    "data_to": "2026-06",
    "data_to_label": "Jun 2026",
    "base_label": "Jan 2023 = 100",
    "card": {
        "name": "Ferric chloride liquid FeCl3 40%",
        "short": "Ferric chloride 40%",
        "family": "Inorganic Chemicals",
        "line": "Oxidative ferric-salt manufacture",
        "industry": "Municipal Water",
        "category": "Coagulation",
        "bought_in": {"categories": 27, "industries": 25},
        "badge": {"label": "Verified makers",
                  "note": "Independent makers of this exact product are verified on their own pages."},
        "drivers": ["iron scrap", "hydrochloric acid", "electricity", "process water", "labour"],
        "recipe_line_count": len(fc['combos'][0]['lines']),
        "regions": [{"code": c, "jun": r1(series[c][0][-1]), "dec": r1(series[c][1][-1])} for c in ORDER],
        "region": "EU",
        "series": {
            "from": "2023-01", "to": "2026-06",
            "actual": [r2(v) for v in eu],
            "forecast_from": "2026-07", "forecast_to": "2026-12",
            "forecast": [r2(v) for v in eu_f],
            "forecast_kind": "modelled",
        },
        "quarters": {"Q1-23": r2(q(0)), "Q2-25": r2(q(27)), "Q1-26": r2(q(36)), "Q2-26": r2(q(39))},
        "move_3m": {"from": r1(eu[38]), "to": r1(eu[41]), "label": "Mar–Jun 2026"},
        "cycle": {"window_months": 42, "low": r1(lo), "high": r1(hi), "current": r1(eu[-1]),
                  "percentile": round((eu[-1] - lo) / (hi - lo) * 100), "position": "mid-range"},
        "seasonality": {"peak": "Feb", "trough": "Oct", "spread_pts": 4.4},
        "volatility": {"percentile": 30, "label": "low to moderate", "mom_sd_pct": r1(statistics.pstdev(mom))},
    },
    "report": {
        "name": "Coagulants", "written": "August 2026", "product_lines": 4,
        "sections": ["Overview & market sizing", "How it works", "Applications", "Technology", "Process to price",
                     "Supply landscape", "PESTEL", "Porter's five forces", "Market drivers to watch", "Kraljic positioning"],
    },
    "playbook": {
        "name": "Coagulants", "levers": 19, "levers_apply": 17,
        "families": ["Pricing & Conditions", "Volume Concentration", "Global Sourcing", "Product Spec Improvement",
                     "Joint Process Improvement", "Relationship Restructuring", "Category Management", "Differentiation"],
    },
}

# Guards: the numbers the brief names must reproduce, and nothing private may slip in.
assert r1(eu[-1]) == 99.2 and r1(eu_f[-1]) == 94.8, (eu[-1], eu_f[-1])
assert snap['card']['cycle']['percentile'] == 43 and r1(lo) == 95.2 and r1(hi) == 104.5
txt = json.dumps(snap)
for bad in ['BCI-', 'FECL3', '"weight', '"share', '"makers', '"producers', '"suppliers', '"cost_lines', '"components', '"stack', '"pid', 'iron-scrap', 'elec-eu', 'lci-', 'PLAT-', 'live']:
    assert bad not in txt, bad
json.dump(snap, open(out_path, 'w'), ensure_ascii=False, indent=1)
print('ok', out_path, 'EU', snap['card']['regions'][0], 'Q1-23', snap['card']['quarters']['Q1-23'], 'Q2-26', snap['card']['quarters']['Q2-26'])
