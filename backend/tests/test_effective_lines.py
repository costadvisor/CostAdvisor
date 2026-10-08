"""effective_lines(): the three ways a team product reaches its product line
(design §2.8), on hand-made rows. No content load needed.

Every platform row here carries a made-up name with a random suffix and is
deleted afterwards; the team rows go with the test team.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.chemical_family import ChemicalFamily
from app.models.cost_model import CostModel, FormulaVersion
from app.models.formula_template import FormulaRegionCoverage, FormulaTemplate
from app.models.product import Product
from app.models.product_line import ProductLine
from app.models.subfamily import Subfamily
from app.services.effective_lines import NONE, effective_line, effective_lines


@pytest.fixture
def taxonomy(db, tenant_a):
    """One made-up family with one sub-family and three lines, and a platform
    template (with one Europe coverage row) on each of the first two lines,
    plus one platform template with a family and no line."""
    tag = uuid.uuid4().hex[:8]
    fam = ChemicalFamily(name=f"Test family {tag}")
    db.add(fam)
    db.flush()
    sub = Subfamily(family_id=fam.id, name=f"Test sub-family {tag}")
    db.add(sub)
    db.flush()
    lines = []
    for n in range(3):
        name = f"Test line {n} {tag}"
        line = ProductLine(family_id=fam.id, subfamily_id=sub.id, platform=f"PLAT-TEST-{n}-{tag}",
                           line_key=f"{fam.name}|||{name}", name=name)
        db.add(line)
        lines.append(line)
    db.flush()
    templates = []
    for n, line_id in enumerate([lines[0].id, lines[1].id, None]):
        t = FormulaTemplate(name=f"Test template {n} {tag}", code=f"ZZT-{tag}-{n}".upper(),
                            created_by=tenant_a["user_id"], team_id=None,
                            family_id=fam.id, product_line_id=line_id)
        db.add(t)
        db.flush()
        db.add(FormulaRegionCoverage(template_id=t.id, region="Europe"))
        templates.append(t)
    db.commit()
    yield {"family": fam, "subfamily": sub, "lines": lines, "templates": templates,
           "team_id": tenant_a["team_id"], "user_id": tenant_a["user_id"]}

    # Platform rows only; the team's products, cost models and forks go with
    # the team (user_factory teardown). Template deletes cascade to coverage.
    for t in templates:
        db.delete(db.get(FormulaTemplate, t.id))
    db.flush()
    for line in lines:
        db.delete(db.get(ProductLine, line.id))
    db.flush()
    db.delete(db.get(Subfamily, sub.id))
    db.delete(db.get(ChemicalFamily, fam.id))
    db.commit()


def _product(db, tx, **fields) -> Product:
    p = Product(team_id=tx["team_id"], created_by=tx["user_id"],
                name=f"Test product {uuid.uuid4().hex[:6]}", **fields)
    db.add(p)
    db.flush()
    return p


def _priced_from(db, tx, product: Product, template: FormulaTemplate, year: int = 2025) -> None:
    """A cost model on `product` with one formula version priced from
    `template`'s Europe combo."""
    coverage = db.query(FormulaRegionCoverage).filter_by(template_id=template.id).one()
    cm = CostModel(team_id=tx["team_id"], product_id=product.id, created_by=tx["user_id"],
                   region="Europe")
    db.add(cm)
    db.flush()
    db.add(FormulaVersion(cost_model_id=cm.id, base_price=100, base_year=year, base_quarter=1,
                          source_coverage_id=coverage.id))
    db.flush()


def test_template_path_uses_the_linked_template_or_its_fork_origin(db, taxonomy):
    tx = taxonomy
    line0, line1 = tx["lines"][0], tx["lines"][1]
    linked = _product(db, tx, formula_template_id=tx["templates"][0].id)
    # A team fork with no line of its own stands for its platform original.
    fork = FormulaTemplate(name="Test fork", created_by=tx["user_id"], team_id=tx["team_id"],
                           origin_id=tx["templates"][1].id)
    db.add(fork)
    db.flush()
    forked = _product(db, tx, formula_template_id=fork.id)
    # A linked template wins over a cost model's template, and over a manual line.
    both = _product(db, tx, formula_template_id=tx["templates"][0].id,
                    product_line_id=tx["lines"][2].id)
    _priced_from(db, tx, both, tx["templates"][1])
    db.commit()

    got = effective_lines(db, [linked.id, forked.id, both.id])

    assert got[linked.id].product_line_id == line0.id
    assert got[linked.id].family_id == tx["family"].id
    assert got[linked.id].subfamily_id == tx["subfamily"].id
    assert got[linked.id].source == "template"
    assert got[linked.id].template_id == tx["templates"][0].id

    assert got[forked.id].product_line_id == line1.id
    assert got[forked.id].source == "template"
    assert got[forked.id].template_id == tx["templates"][1].id

    assert got[both.id].product_line_id == line0.id
    assert got[both.id].source == "template"
    assert got[both.id].line_ids == (line0.id, line1.id)


def test_cost_model_path_reaches_the_template_behind_a_priced_version(db, taxonomy):
    tx = taxonomy
    line0, line1 = tx["lines"][0], tx["lines"][1]
    tracked = _product(db, tx)
    _priced_from(db, tx, tracked, tx["templates"][1])
    # A linked template with no line does not stop the search: the cost
    # model's template answers.
    unpublished = _product(db, tx, formula_template_id=tx["templates"][2].id)
    _priced_from(db, tx, unpublished, tx["templates"][0])
    db.commit()

    got = effective_lines(db, [tracked.id, unpublished.id])

    assert got[tracked.id].product_line_id == line1.id
    assert got[tracked.id].family_id == tx["family"].id
    assert got[tracked.id].subfamily_id == tx["subfamily"].id
    assert got[tracked.id].source == "cost_model"
    assert got[tracked.id].template_id == tx["templates"][1].id

    assert got[unpublished.id].product_line_id == line0.id
    assert got[unpublished.id].source == "cost_model"


def test_manual_path_and_what_is_left_without_a_line(db, taxonomy):
    tx = taxonomy
    line2 = tx["lines"][2]
    custom = _product(db, tx, product_line_id=line2.id)
    # A template with no line and nothing else: its family, no line.
    family_only = _product(db, tx, formula_template_id=tx["templates"][2].id)
    bare = _product(db, tx)
    db.commit()

    got = effective_lines(db, [custom.id, family_only.id, bare.id, uuid.uuid4()])

    assert got[custom.id].product_line_id == line2.id
    assert got[custom.id].family_id == tx["family"].id
    assert got[custom.id].subfamily_id == tx["subfamily"].id
    assert got[custom.id].source == "manual"
    assert got[custom.id].line_ids == (line2.id,)

    assert got[family_only.id].product_line_id is None
    assert got[family_only.id].family_id == tx["family"].id
    assert got[family_only.id].source == "template"

    assert got[bare.id] == NONE
    assert len(got) == 3                       # an unknown id is left out
    assert effective_line(db, uuid.uuid4()) == NONE
    assert effective_lines(db, []) == {}
