"""Monthly news API (Wave 4). This file starts with SCRUM-213 / NEWS-1, search profiles.

    GET    /api/news/profiles                                   every platform family + its profile status
    GET    /api/news/profiles/{family_id}                       the family profile, its overrides, its product lines
    PUT    /api/news/profiles/{family_id}                       create/replace the family profile
    PUT    /api/news/profiles/{family_id}/lines/{line_id}       create/replace a product line override
    DELETE /api/news/profiles/{family_id}/lines/{line_id}       remove an override (back to inheriting)
    GET    /api/news/profiles/{family_id}/effective             the merged profile the sweep would use
    POST   /api/news/profiles/{family_id}/draft                 Claude drafts a profile — returned, not saved
    POST   /api/news/profiles/{family_id}/test                  what the profile would find now — nothing saved
    GET    /api/news/producers                                  producer picker for the editor
    GET    /api/news/editors                                    who can own a profile
    GET    /api/news/enabled                                    is the feature on for me (preview flag)

Visibility: while the feature is being built it is behind a flag,
``NEWS_PREVIEW_EMAILS`` (see ``app.config``). Anyone not on that list gets a
404 from every endpoint, as if the feature did not exist, and the portal hides
the tab (``GET /api/news/enabled``). ``*`` opens it to every Content Editor.

Permission: platform ``content.edit`` (the Content Editor role, or a super
admin) on every endpoint. Profiles drive platform editorial content, so this is
"may you write the library everybody reads", the same gate editorial uses for
platform blocks. Draft and test also spend API money, which is one more reason
not to open them wider — and why both are rate-limited per user.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.chemical_family import ChemicalFamily
from app.models.producer import Producer
from app.models.region import Region
from app.models.search_profile import SearchProfile
from app.models.product_line import ProductLine
from app.rate_limit import limiter
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.search_profile import (
    DraftOut, EditorRef, EffectiveProfile, FamilyProfileSummary, NewsItemOut, ProducerRef,
    ProductLineRef, ProfileDetailOut, ProfileOut, ProfileWrite, ProfileFields, RegionRef,
    TestRunOut, TestRunRequest,
)
from app.services import anthropic_client as claude
from app.services.news_profile_drafts import draft_profile
from app.services.news_search import load_brief_context, search_news
from app.services.permissions import has_platform_permission, require_platform_permission
from app.services.search_profiles import (
    ProfileNotFound, ProfileValidationError, delete_override, effective_from_fields,
    family_lines, get_effective_profile, platform_editors, profile_warnings, save_profile,
)

router = APIRouter()

PERMISSION = "content.edit"
# Each draft is one Claude call, each test up to a handful of searches: cheap
# one at a time, not in a loop. Per user (the limiter keys on the login).
DRAFT_LIMIT = "20/hour"
TEST_LIMIT = "15/hour"


def news_enabled_for(user: User) -> bool:
    """Is the news feature switched on for this person (the preview flag)?"""
    raw = get_settings().news_preview_emails.strip()
    if raw == "*":
        return True
    allowed = {e.strip().casefold() for e in raw.split(",") if e.strip()}
    return bool(user.email) and user.email.casefold() in allowed


def _require(db: Session, user: User) -> None:
    if not news_enabled_for(user):
        # 404, not 403: to anyone outside the preview the feature does not exist.
        raise HTTPException(404, "Not found")
    require_platform_permission(db, user, PERMISSION)


def _require_claude() -> None:
    if not claude.is_configured():
        raise HTTPException(503, "Claude is not configured on this server (ANTHROPIC_API_KEY).")


def _family(db: Session, family_id: int) -> ChemicalFamily:
    family = db.get(ChemicalFamily, family_id)
    if family is None:
        raise HTTPException(404, "Family not found")
    return family


def _profile_out(row: SearchProfile) -> ProfileOut:
    return ProfileOut(
        **ProfileFields.from_row(row).model_dump(),
        id=row.id, family_id=row.family_id, product_line_id=row.product_line_id,
        is_active=row.is_active, provenance=row.provenance,
        owner_editor_id=row.owner_editor_id,
        owner_editor_name=(row.owner_editor.display_name or row.owner_editor.email)
        if row.owner_editor else None,
        notes=row.notes,
        producers=[ProducerRef(id=p.id, name=p.name) for p in row.producers],
        regions=[RegionRef(code=r.code, name=r.name) for r in row.regions],
        updated_at=row.updated_at,
    )


def _detail(db: Session, family: ChemicalFamily) -> ProfileDetailOut:
    rows = db.query(SearchProfile).filter(SearchProfile.family_id == family.id).all()
    base = next((r for r in rows if r.product_line_id is None), None)
    overrides = sorted((r for r in rows if r.product_line_id is not None), key=lambda r: r.product_line_id)
    overridden = {r.product_line_id for r in overrides}
    lines = family_lines(db, family.id)
    return ProfileDetailOut(
        family_id=family.id, family_code=family.code, family_name=family.name,
        product_lines=[ProductLineRef(id=ln.id, name=ln.name,
                                      subfamily_name=ln.subfamily.name if ln.subfamily else None,
                                      has_override=ln.id in overridden) for ln in lines],
        profile=_profile_out(base) if base else None,
        overrides=[_profile_out(r) for r in overrides],
    )


def _save(db: Session, user: User, family_id: int, data: ProfileWrite,
          product_line_id: int | None) -> ProfileDetailOut:
    family = _family(db, family_id)
    try:
        save_profile(
            db, family_id, ProfileFields(**data.model_dump(include=set(ProfileFields.model_fields))),
            product_line_id=product_line_id, is_active=data.is_active,
            owner_editor_id=data.owner_editor_id, notes=data.notes, actor_id=user.id,
        )
    except ProfileValidationError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return _detail(db, family)


# ── Is it on for me? ─────────────────────────────────────────────────────────

@router.get("/enabled")
def enabled(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Whether the portal should show the news features to this person."""
    return {"enabled": news_enabled_for(current_user)
            and has_platform_permission(db, current_user, PERMISSION)}


# ── Profiles ─────────────────────────────────────────────────────────────────

@router.get("/profiles", response_model=list[FamilyProfileSummary])
def list_profiles(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    families = (db.query(ChemicalFamily)
                .order_by(ChemicalFamily.code.nullslast(), ChemicalFamily.name).all())
    bases = {r.family_id: r for r in db.query(SearchProfile)
             .filter(SearchProfile.product_line_id.is_(None)).all()}
    override_counts = dict(db.query(SearchProfile.family_id, func.count(SearchProfile.id))
                           .filter(SearchProfile.product_line_id.isnot(None))
                           .group_by(SearchProfile.family_id).all())
    out = []
    for f in families:
        base = bases.get(f.id)
        out.append(FamilyProfileSummary(
            family_id=f.id, family_code=f.code, family_name=f.name, has_profile=base is not None,
            provenance=base.provenance if base else None,
            is_active=base.is_active if base else False,
            owner_editor_name=(base.owner_editor.display_name or base.owner_editor.email)
            if base and base.owner_editor else None,
            override_count=override_counts.get(f.id, 0),
            updated_at=base.updated_at if base else None,
            warnings=profile_warnings(base),
        ))
    return out


@router.get("/profiles/{family_id}", response_model=ProfileDetailOut)
def get_profile(family_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    return _detail(db, _family(db, family_id))


@router.put("/profiles/{family_id}", response_model=ProfileDetailOut)
def put_profile(family_id: int, data: ProfileWrite, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    return _save(db, current_user, family_id, data, None)


@router.put("/profiles/{family_id}/lines/{line_id}", response_model=ProfileDetailOut)
def put_override(family_id: int, line_id: int, data: ProfileWrite,
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    return _save(db, current_user, family_id, data, line_id)


@router.delete("/profiles/{family_id}/lines/{line_id}", response_model=ProfileDetailOut)
def remove_override(family_id: int, line_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    family = _family(db, family_id)
    if not delete_override(db, family_id, line_id, actor_id=current_user.id):
        raise HTTPException(404, "No override for that product line")
    db.commit()
    return _detail(db, family)


@router.get("/profiles/{family_id}/effective", response_model=EffectiveProfile)
def effective_profile(family_id: int, product_line_id: int | None = Query(None),
                      db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    _family(db, family_id)
    try:
        return get_effective_profile(db, family_id, product_line_id)
    except ProfileNotFound as e:
        raise HTTPException(404, str(e))


# ── Claude: draft (sub-task 3) and test run (sub-task 4) ─────────────────────

@router.post("/profiles/{family_id}/draft", response_model=DraftOut)
@limiter.limit(DRAFT_LIMIT)
def draft(request: Request, family_id: int, db: Session = Depends(get_db),
          current_user: User = Depends(get_current_user)):
    """A Claude-drafted profile for the editor to review in the form. Not saved:
    the editor saves it like any edit, which records it as theirs."""
    _require(db, current_user)
    _family(db, family_id)
    _require_claude()
    try:
        d = draft_profile(db, family_id)
    except claude.AnthropicError as e:
        raise HTTPException(502, f"Claude could not draft this profile: {e}")
    producers = (db.query(Producer).filter(Producer.id.in_(d.fields.producer_ids)).all()
                 if d.fields.producer_ids else [])
    by_id = {p.id: p for p in producers}
    regions = (db.query(Region).filter(Region.code.in_(d.fields.region_codes)).all()
               if d.fields.region_codes else [])
    by_code = {r.code: r for r in regions}
    db.rollback()  # belt and braces: drafting never writes
    return DraftOut(
        fields=d.fields,
        producers=[ProducerRef(id=i, name=by_id[i].name) for i in d.fields.producer_ids if i in by_id],
        regions=[RegionRef(code=c, name=by_code[c].name) for c in d.fields.region_codes if c in by_code],
        rationale=d.rationale, dropped=d.dropped, cost_usd=d.usage.cost_usd,
    )


@router.post("/profiles/{family_id}/test", response_model=TestRunOut)
@limiter.limit(TEST_LIMIT)
def test_run(request: Request, family_id: int, data: TestRunRequest, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    """What this profile would find right now. Saves nothing — no news items,
    no profile changes — so an editor can try settings freely."""
    _require(db, current_user)
    _family(db, family_id)
    _require_claude()
    if data.product_line_id is not None:
        line = db.get(ProductLine, data.product_line_id)
        if line is None or line.family_id != family_id or line.retired_at is not None:
            raise HTTPException(422, "That product line is not a current line of this family")
    try:
        profile = (effective_from_fields(db, family_id, data.fields, data.product_line_id)
                   if data.fields is not None
                   else get_effective_profile(db, family_id, data.product_line_id))
    except ProfileNotFound:
        raise HTTPException(404, "This family has no saved profile yet: fill in the form and test that.")
    if not profile.search_terms:
        raise HTTPException(422, "Add at least one search term before testing.")
    settings = get_settings()
    cap = settings.news_test_run_max_searches
    max_searches = min(data.max_searches or cap, cap)
    ctx = load_brief_context(db, profile)
    db.rollback()  # release the read transaction before the slow API calls
    outcome = search_news(profile, ctx, max_searches=max_searches,
                          time_budget=settings.news_test_run_time_budget)

    def items(xs):
        return [NewsItemOut(**vars(x)) for x in xs]

    return TestRunOut(
        family_id=family_id, product_line_id=data.product_line_id, window_start=outcome.window_start,
        queries=outcome.queries, kept=items(outcome.kept),
        below_threshold=items(outcome.below_threshold), dropped=items(outcome.dropped),
        searches=outcome.usage.web_searches, input_tokens=outcome.usage.input_tokens,
        output_tokens=outcome.usage.output_tokens, cost_usd=outcome.usage.cost_usd,
        error=outcome.error, saved=False,
    )


# ── Pickers ──────────────────────────────────────────────────────────────────

@router.get("/producers", response_model=list[ProducerRef])
def producers(q: str | None = Query(None, max_length=100), limit: int = Query(30, ge=1, le=100),
              db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require(db, current_user)
    query = db.query(Producer)
    if q and q.strip():
        query = query.filter(Producer.normalized_name.contains(q.strip().casefold()))
    return [ProducerRef(id=p.id, name=p.name) for p in query.order_by(Producer.name).limit(limit).all()]


@router.get("/editors", response_model=list[EditorRef])
def editors(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """People who can own a profile (super admins and Content Editors)."""
    _require(db, current_user)
    return [EditorRef(id=u.id, name=u.display_name or u.email) for u in platform_editors(db)]
