"""Unit tests for news search profile validation and merge rules (SCRUM-213).

Pure functions, no DB: these run without the dev Postgres.
"""
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.search_profile import EVENT_TYPES, EffectiveProfile, ProfileFields, is_valid_cas
from app.services.search_profile_merge import (
    DEFAULT_LANGUAGE,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MIN_RELEVANCE,
    merge_fields,
)

P1, P2, P3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

FIBERGLASS = ProfileFields(
    terms=["fiberglass", "glass fibre"],
    synonyms=["E-glass", "fibreglass"],
    cas_numbers=["65997-17-3"],
    exclude_terms=["boat", "insulation installer"],
    preferred_sites=["https://www.compositesworld.com/news"],
    producer_ids=[P1, P2],
    region_codes=["Europe"],
)


def effective(base, override=None):
    return EffectiveProfile(
        family_id=1, product_line_id=None, is_active=False, provenance="ai_draft",
        owner_editor_id=None,
        **merge_fields(base, override),
    )


def test_family_only_fills_defaults():
    p = effective(FIBERGLASS)
    assert p.language == DEFAULT_LANGUAGE
    assert p.min_relevance == DEFAULT_MIN_RELEVANCE
    assert p.lookback_days == DEFAULT_LOOKBACK_DAYS
    assert p.event_types == list(EVENT_TYPES)
    assert p.terms == ["fiberglass", "glass fibre"]


def test_line_adds_to_lists_without_duplicates():
    line = ProfileFields(terms=["roving", "Fiberglass"], producer_ids=[P2, P3], exclude_terms=["kayak"])
    p = effective(FIBERGLASS, line)
    assert p.terms == ["fiberglass", "glass fibre", "roving"]
    assert p.producer_ids == [P1, P2, P3]
    assert p.exclude_terms == ["boat", "insulation installer", "kayak"]


def test_line_scalars_replace_family():
    base = FIBERGLASS.model_copy(update={"min_relevance": 0.5, "lookback_days": 31})
    p = effective(base, ProfileFields(min_relevance=0.8))
    assert p.min_relevance == 0.8
    assert p.lookback_days == 31


def test_event_types_replace_when_line_sets_them():
    base = FIBERGLASS.model_copy(update={"event_types": ["new_plant", "m_and_a", "closure"]})
    p = effective(base, ProfileFields(event_types=["new_plant"]))
    assert p.event_types == ["new_plant"]


def test_blocked_site_wins_over_preferred():
    line = ProfileFields(blocked_sites=["compositesworld.com"])
    p = effective(FIBERGLASS, line)
    assert p.preferred_sites == []
    assert p.blocked_sites == ["compositesworld.com"]


def test_sites_are_normalised():
    assert FIBERGLASS.preferred_sites == ["compositesworld.com"]


def test_text_lists_cleaned():
    f = ProfileFields(terms=["  glass   fibre ", "Glass Fibre", ""])
    assert f.terms == ["glass fibre"]


def test_cas_check_digit():
    assert is_valid_cas("65997-17-3")
    assert is_valid_cas("7732-18-5")
    assert not is_valid_cas("65997-17-4")
    with pytest.raises(ValidationError):
        ProfileFields(cas_numbers=["65997-17-4"])


def test_min_relevance_bounds():
    with pytest.raises(ValidationError):
        ProfileFields(min_relevance=1.5)


def test_search_terms_combines_terms_synonyms_and_cas():
    p = effective(FIBERGLASS)
    assert p.search_terms == ["fiberglass", "glass fibre", "E-glass", "fibreglass", "65997-17-3"]


def test_region_codes_are_case_sensitive():
    # regions.code is case-sensitive, so "NWE" and "nwe" are not merged.
    p = effective(FIBERGLASS, ProfileFields(region_codes=["NWE", "Europe"]))
    assert p.region_codes == ["Europe", "NWE"]


def test_provenance_vocabulary_matches_migration():
    from app.constants.trust import PROVENANCE_STATES
    assert set(PROVENANCE_STATES) == {"imported", "ai_draft", "human_edited", "human_approved"}
