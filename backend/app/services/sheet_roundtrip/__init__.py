from app.services.sheet_roundtrip.base import SheetColumnSpec, SheetPayloadSpec
from app.services.sheet_roundtrip.dimension_decision import DimensionDecisionSpec
from app.services.sheet_roundtrip.formula_coverage_price import FormulaCoveragePriceSpec
from app.services.sheet_roundtrip.taxonomy_reconciliation import TaxonomyReconciliationSpec

PAYLOAD_REGISTRY: dict[str, SheetPayloadSpec] = {
    "formula_coverage_price": FormulaCoveragePriceSpec(),
    # SCRUM-77: the analyst-decided half of the dimension layer. A second
    # payload is a spec plus this line — the mechanism never branched.
    "dimension_decision": DimensionDecisionSpec(),
    # Phase 2 item 1 (post-Wave-3 roadmap): the analyst-decided mapping of the
    # 2026-07 drop's family/subfamily names onto the existing platform taxonomy.
    "taxonomy_reconciliation": TaxonomyReconciliationSpec(),
}


def get_spec(payload_key: str) -> SheetPayloadSpec:
    spec = PAYLOAD_REGISTRY.get(payload_key)
    if not spec:
        raise KeyError(f"Unknown sheet payload '{payload_key}'")
    return spec


__all__ = ["SheetColumnSpec", "SheetPayloadSpec", "PAYLOAD_REGISTRY", "get_spec"]
