"""Strategy API (design §2.6 and §4.2 "Strategy"; demo spec §6, landing model §9).

    GET    /api/strategy/categories                         adopted + suggested
    POST   /api/strategy/categories/{slug}/adopt            start a strategy (idempotent)
    PUT    /api/strategy/categories/{slug}                  status, owner
    GET    /api/strategy/categories/{slug}                  playbook meta + overlay
    GET    /api/strategy/categories/{slug}/analysis         report panels (Strategic Analysis)
    GET    /api/strategy/categories/{slug}/spend            the team's own spend
    GET    /api/strategy/categories/{slug}/levers           authored ⊕ scores ⊕ custom
    POST   /api/strategy/categories/{slug}/levers           add a custom lever
    PUT    /api/strategy/levers/{lever_id}                  score an authored lever
    PUT    /api/strategy/custom-levers/{id}                 edit a custom lever
    DELETE /api/strategy/custom-levers/{id}
    PUT    /api/strategy/categories/{slug}/objectives       the 7 objectives
    GET    /api/strategy/categories/{slug}/actions
    POST   /api/strategy/categories/{slug}/actions
    PUT    /api/strategy/actions/{id}
    DELETE /api/strategy/actions/{id}

Every route takes `team_id` and gates on `strategy.view` (reads) or
`strategy.edit` (writes). Team rows are under RLS; the session is the usual
`get_db` + `get_current_user` one, and every write builds its response
**before** `commit()` — the RLS GUCs are transaction-local, so a post-commit
re-read can come back empty (the rule `routers/formulas.py` documents).

First writes are race-safe. Adopting a category, a team's first score on a
lever and its first answer on an objective each insert a row under a unique
constraint; two requests at the same moment (a double click) would both see
"no row yet". The insert runs in a SAVEPOINT, and the request that loses the
race picks up the winner's row instead of failing (`_insert_once`).

Nothing here writes a platform row: a playbook, lever or report is only ever
read. The computation lives in `services/strategy.py`.
"""
import uuid
from datetime import date
from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.strategy import (
    ACTION_STATUSES, LEVER_STATUSES, OBJECTIVES, PRIORITIES, CustomLever, LeverScore,
    Playbook, PlaybookLever, StrategyAction, StrategyRecord, TeamObjective,
)
from app.models.team import Team
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.strategy import (
    ActionCreate, ActionOut, ActionsOut, ActionUpdate, AdoptOut, AnalysisOut,
    CategoriesOut, CategoryDetailOut, CategoryRow, CustomLeverCreate, CustomLeverUpdate,
    LeverOut, LeversOut, LeverScoreUpdate, ObjectiveIn, ObjectivesOut, RecordUpdate,
    SpendOut,
)
from app.services import strategy as svc
from app.services.audit import log_event
from app.services.permissions import require_permission

router = APIRouter()

VIEW, EDIT = "strategy.view", "strategy.edit"
SCORE_FIELDS = ("applies", "ease", "savings_score", "savings_value", "status", "notes")
CUSTOM_FIELDS = ("title", "guidance", "applies", "ease", "savings_score", "savings_value",
                 "status", "notes", "objectives")
ACTION_FIELDS = ("title", "description", "lever_id", "custom_lever_id", "assignee_user_id",
                 "start_date", "due_date", "status", "pct_complete")
OBJECTIVE_CODES = {code for code, _n, _d in OBJECTIVES}
DEFAULT_CUSTOM_STATUS = "Identified"
# An ISO 4217-shaped code; left out or empty = the team's own currency.
CURRENCY = Query(None, pattern=r"^([A-Za-z]{3})?$",
                 description="ISO currency code, e.g. EUR. Default: the team's own.")

T = TypeVar("T")


# ── Guards ───────────────────────────────────────────────────────────────────

def _require(db: Session, user: User, team_id: uuid.UUID, key: str) -> None:
    """The permission check, then the team itself. In that order: a non-member
    gets 403 whether or not the team exists, so the answer never reveals which
    team ids are real. Only a super-admin (whom `has_permission` lets through
    for any id) can reach the 404; without it a write on a made-up team id
    would fail on the `team_id` foreign key as a 500."""
    require_permission(db, user, team_id, key)
    if db.query(Team.id).filter(Team.id == team_id).first() is None:
        raise HTTPException(status_code=404, detail="Team not found")


def _currency(db: Session, team_id: uuid.UUID, value: str | None) -> str | None:
    """`reporting_currency` must be a currency the app knows for this team
    (svc.known_currencies). Checked after the permission check, so a
    non-member learns nothing from it. Empty or left out = the team's own."""
    if not value:
        return None
    code = value.upper()
    known = svc.known_currencies(db, team_id)
    if code not in known:
        raise HTTPException(status_code=422, detail=(
            f"Unknown reporting currency: {value}. One of: {', '.join(sorted(known))}"))
    return code


def _playbook(db: Session, slug: str) -> Playbook:
    # Postgres text cannot hold NUL; psycopg2 refuses such a parameter before
    # the query runs. No playbook slug contains one, so it is simply not found.
    if "\x00" in slug:
        raise HTTPException(status_code=404, detail="Strategy category not found")
    pb = db.query(Playbook).filter(Playbook.slug == slug).first()
    if pb is None:
        raise HTTPException(status_code=404, detail="Strategy category not found")
    return pb


def _check_lever_status(status: str | None) -> None:
    if status is not None and status not in LEVER_STATUSES:
        raise HTTPException(status_code=400, detail=(
            f"Unknown lever status: {status}. One of: {', '.join(LEVER_STATUSES)}"))


def _check_objective_codes(codes) -> None:
    unknown = sorted(set(codes or []) - OBJECTIVE_CODES)
    if unknown:
        raise HTTPException(status_code=400, detail=(
            f"Unknown objective code(s): {', '.join(unknown)}"))


def _check_member(db: Session, team_id: uuid.UUID, user_id: uuid.UUID | None, what: str) -> None:
    if user_id is not None and not svc.is_member(db, team_id, user_id):
        raise HTTPException(status_code=400, detail=f"The {what} must be a member of the team")


def _jsonable(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if isinstance(v, (uuid.UUID,)):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif v is not None and not isinstance(v, (str, int, float, bool, list, dict)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _lever_row(db: Session, team_id: uuid.UUID, slug: str, *, lever_id: int | None = None,
               custom_lever_id: uuid.UUID | None = None) -> dict:
    rows = svc.merged_levers(db, team_id, [slug]).get(slug, [])
    return next(r for r in rows if (lever_id is not None and r["lever_id"] == lever_id)
                or (custom_lever_id is not None and r["custom_lever_id"] == custom_lever_id))


def _require_title(title: str | None, what: str) -> None:
    if not (title or "").strip():
        raise HTTPException(status_code=400, detail=f"{what} needs a title")


# ── First writes (unique rows) ───────────────────────────────────────────────

def _insert_once(db: Session, obj: T, find: Callable[[], T | None]) -> tuple[T, bool]:
    """Insert `obj` inside a SAVEPOINT and return `(obj, True)`. If a
    concurrent request inserted the same unique row first, the insert fails
    with an IntegrityError, only the savepoint rolls back (the request's
    transaction and its RLS settings carry on), and the winner's row comes
    back as `(row, False)`. Any other integrity error is re-raised."""
    try:
        with db.begin_nested():
            db.add(obj)
            db.flush()
    except IntegrityError:
        existing = find()
        if existing is None:
            raise
        return existing, False
    return obj, True


def _find_record(db: Session, team_id: uuid.UUID, slug: str) -> StrategyRecord | None:
    return db.query(StrategyRecord).filter(
        StrategyRecord.team_id == team_id, StrategyRecord.playbook_slug == slug).first()


def _find_score(db: Session, team_id: uuid.UUID, lever_id: int) -> LeverScore | None:
    return db.query(LeverScore).filter(
        LeverScore.team_id == team_id, LeverScore.lever_id == lever_id).first()


def _find_objectives(db: Session, team_id: uuid.UUID, slug: str) -> dict[str, TeamObjective]:
    return {o.objective_code: o for o in db.query(TeamObjective).filter(
        TeamObjective.team_id == team_id, TeamObjective.playbook_slug == slug)}


def _find_objective(db: Session, team_id: uuid.UUID, slug: str,
                    code: str) -> TeamObjective | None:
    return db.query(TeamObjective).filter(
        TeamObjective.team_id == team_id, TeamObjective.playbook_slug == slug,
        TeamObjective.objective_code == code).first()


# ── Landing ──────────────────────────────────────────────────────────────────

@router.get("/categories", response_model=CategoriesOut)
def list_categories(team_id: uuid.UUID, reporting_currency: str | None = CURRENCY,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """The team's adopted strategy categories and, separately, the playbooks
    its portfolio reaches that it has not adopted yet."""
    _require(db, current_user, team_id, VIEW)
    currency = _currency(db, team_id, reporting_currency)
    return svc.landing(svc.TeamContext(db, team_id, currency))


@router.post("/categories/{slug}/adopt", response_model=AdoptOut)
def adopt_category(slug: str, team_id: uuid.UUID, reporting_currency: str | None = CURRENCY,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """Start a strategy on a playbook. Idempotent: a second call returns the
    existing record with `created: false` — also when two calls race (a
    double click): exactly one creates, the other returns its record."""
    _require(db, current_user, team_id, EDIT)
    currency = _currency(db, team_id, reporting_currency)
    _playbook(db, slug)
    rec = _find_record(db, team_id, slug)
    created = False
    if rec is None:
        rec, created = _insert_once(
            db, StrategyRecord(team_id=team_id, playbook_slug=slug, status="Active"),
            lambda: _find_record(db, team_id, slug))
    if created:
        log_event(db, team_id, current_user.id, "create", "strategy_record", str(rec.id),
                  new_value={"playbook_slug": slug, "status": rec.status})
    row = svc.category_rows(svc.TeamContext(db, team_id, currency), [slug])[slug]
    db.commit()
    return {"created": created, "category": row}


@router.put("/categories/{slug}", response_model=CategoryRow)
def update_category(slug: str, team_id: uuid.UUID, data: RecordUpdate,
                    reporting_currency: str | None = CURRENCY,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """Status and owner of an adopted category. Omitted fields are kept;
    `owner_user_id: null` unassigns."""
    _require(db, current_user, team_id, EDIT)
    currency = _currency(db, team_id, reporting_currency)
    _playbook(db, slug)
    rec = _find_record(db, team_id, slug)
    if rec is None:
        raise HTTPException(status_code=404, detail=(
            "Category not adopted by this team — POST .../adopt first"))
    fields = data.model_fields_set
    if "status" in fields:
        if data.status not in svc.RECORD_STATUSES:
            raise HTTPException(status_code=400, detail=(
                f"Unknown status: {data.status}. One of: {', '.join(svc.RECORD_STATUSES)}"))
    if "owner_user_id" in fields:
        _check_member(db, team_id, data.owner_user_id, "owner")
    prev = {"status": rec.status, "owner_user_id": rec.owner_user_id}
    if "status" in fields:
        rec.status = data.status
    if "owner_user_id" in fields:
        rec.owner_user_id = data.owner_user_id
    db.flush()
    log_event(db, team_id, current_user.id, "update", "strategy_record", str(rec.id),
              previous_value=_jsonable(prev),
              new_value=_jsonable({"status": rec.status, "owner_user_id": rec.owner_user_id}))
    row = svc.category_rows(svc.TeamContext(db, team_id, currency), [slug])[slug]
    db.commit()
    return row


# ── Category detail and tabs ─────────────────────────────────────────────────

@router.get("/categories/{slug}", response_model=CategoryDetailOut)
def category_detail(slug: str, team_id: uuid.UUID, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, VIEW)
    pb = _playbook(db, slug)
    return svc.category_detail(svc.TeamContext(db, team_id), pb)


@router.get("/categories/{slug}/analysis", response_model=AnalysisOut)
def category_analysis(slug: str, team_id: uuid.UUID, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """The delivered report cut for the Strategic Analysis tab. A playbook
    with no report returns `available: false` and no panels, not a 404."""
    _require(db, current_user, team_id, VIEW)
    return svc.analysis(db, _playbook(db, slug))


@router.get("/categories/{slug}/spend", response_model=SpendOut)
def category_spend(slug: str, team_id: uuid.UUID, reporting_currency: str | None = CURRENCY,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, VIEW)
    currency = _currency(db, team_id, reporting_currency)
    pb = _playbook(db, slug)
    return svc.category_spend(svc.TeamContext(db, team_id, currency), pb)


# ── Levers ───────────────────────────────────────────────────────────────────

@router.get("/categories/{slug}/levers", response_model=LeversOut)
def list_levers(slug: str, team_id: uuid.UUID, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, VIEW)
    _playbook(db, slug)
    levers = svc.merged_levers(db, team_id, [slug]).get(slug, [])
    return {"playbook_slug": slug, "total": len(levers),
            "applying": sum(1 for r in levers if r["applies"]),
            "plotted": sum(1 for r in levers if r["plotted"]),
            "gemstones": svc.lever_groups(levers), "levers": levers}


@router.put("/levers/{lever_id}", response_model=LeverOut)
def score_lever(lever_id: int, team_id: uuid.UUID, data: LeverScoreUpdate,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """The team's override on an authored lever. Omitted fields are kept; an
    explicit `null` returns that field to the playbook default."""
    _require(db, current_user, team_id, EDIT)
    lever = db.query(PlaybookLever).filter(PlaybookLever.id == lever_id).first()
    if lever is None:
        raise HTTPException(status_code=404, detail="Lever not found")
    fields = [f for f in SCORE_FIELDS if f in data.model_fields_set]
    if "status" in fields:
        _check_lever_status(data.status)
    values = {f: getattr(data, f) for f in fields}

    def snapshot(sc: LeverScore) -> dict:
        return {f: getattr(sc, f) for f in SCORE_FIELDS}

    score = _find_score(db, team_id, lever_id)
    prev = snapshot(score) if score else None
    if score is None and any(v is not None for v in values.values()):
        # The team's first override on this lever.
        first = LeverScore(team_id=team_id, lever_id=lever_id, updated_by=current_user.id,
                           **values)
        score, created = _insert_once(db, first, lambda: _find_score(db, team_id, lever_id))
        if not created:
            prev = snapshot(score)      # a concurrent first edit won: update its row
    if score is not None:
        for f, v in values.items():
            setattr(score, f, v)
        score.updated_by = current_user.id
        new = snapshot(score)
        if all(v is None for v in new.values()):
            # Everything back to the defaults: no override row left behind.
            db.delete(score)
        db.flush()
        log_event(db, team_id, current_user.id, "update", "lever_score", str(lever_id),
                  previous_value=_jsonable(prev) if prev else None, new_value=_jsonable(new))
    # else: no override row and nothing to store — a no-op, so no audit row.
    row = _lever_row(db, team_id, lever.playbook_slug, lever_id=lever_id)
    db.commit()
    return row


@router.post("/categories/{slug}/levers", response_model=LeverOut, status_code=201)
def create_custom_lever(slug: str, team_id: uuid.UUID, data: CustomLeverCreate,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """Add a team's own opportunity ("+ Add custom opportunity")."""
    _require(db, current_user, team_id, EDIT)
    _playbook(db, slug)
    _require_title(data.title, "A custom lever")
    gem = svc.gemstone_code(data.gemstone)
    if gem is None:
        raise HTTPException(status_code=400, detail=f"Unknown gemstone: {data.gemstone}")
    # A new opportunity starts "Identified", also when the body sends
    # `"status": null` (the form's empty select).
    status = data.status if data.status is not None else DEFAULT_CUSTOM_STATUS
    _check_lever_status(status)
    _check_objective_codes(data.objectives)
    lever = CustomLever(
        team_id=team_id, playbook_slug=slug, gemstone_code=gem, title=data.title.strip(),
        guidance=data.guidance, applies=data.applies, ease=data.ease,
        savings_score=data.savings_score, savings_value=data.savings_value,
        status=status, notes=data.notes, objectives=sorted(set(data.objectives)),
        created_by=current_user.id,
    )
    db.add(lever)
    db.flush()
    log_event(db, team_id, current_user.id, "create", "custom_lever", str(lever.id),
              new_value=_jsonable({"playbook_slug": slug, "gemstone": gem, "title": lever.title}))
    row = _lever_row(db, team_id, slug, custom_lever_id=lever.id)
    db.commit()
    return row


def _custom_lever(db: Session, team_id: uuid.UUID, lever_id: uuid.UUID) -> CustomLever:
    lever = db.query(CustomLever).filter(
        CustomLever.id == lever_id, CustomLever.team_id == team_id).first()
    if lever is None:
        raise HTTPException(status_code=404, detail="Custom lever not found")
    return lever


@router.put("/custom-levers/{lever_id}", response_model=LeverOut)
def update_custom_lever(lever_id: uuid.UUID, team_id: uuid.UUID, data: CustomLeverUpdate,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, EDIT)
    lever = _custom_lever(db, team_id, lever_id)
    fields = data.model_fields_set
    prev = {f: getattr(lever, f) for f in CUSTOM_FIELDS}
    prev["gemstone"] = lever.gemstone_code
    if "gemstone" in fields:
        gem = svc.gemstone_code(data.gemstone)
        if gem is None:
            raise HTTPException(status_code=400, detail=f"Unknown gemstone: {data.gemstone}")
        lever.gemstone_code = gem
    if "status" in fields:
        _check_lever_status(data.status)
    if "objectives" in fields:
        _check_objective_codes(data.objectives)
    if "title" in fields:
        _require_title(data.title, "A custom lever")
    for f in CUSTOM_FIELDS:
        if f in fields:
            value = getattr(data, f)
            if f == "title":
                value = value.strip()
            elif f == "objectives":
                value = sorted(set(value or []))
            setattr(lever, f, value)
    db.flush()
    new = {f: getattr(lever, f) for f in CUSTOM_FIELDS}
    new["gemstone"] = lever.gemstone_code
    log_event(db, team_id, current_user.id, "update", "custom_lever", str(lever.id),
              previous_value=_jsonable(prev), new_value=_jsonable(new))
    row = _lever_row(db, team_id, lever.playbook_slug, custom_lever_id=lever.id)
    db.commit()
    return row


@router.delete("/custom-levers/{lever_id}")
def delete_custom_lever(lever_id: uuid.UUID, team_id: uuid.UUID,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """Actions created from the lever stay, unlinked (`custom_lever_id` NULL)."""
    _require(db, current_user, team_id, EDIT)
    lever = _custom_lever(db, team_id, lever_id)
    log_event(db, team_id, current_user.id, "delete", "custom_lever", str(lever.id),
              previous_value=_jsonable({"playbook_slug": lever.playbook_slug,
                                        "title": lever.title}))
    db.delete(lever)
    db.commit()
    return {"status": "deleted"}


# ── Objectives ───────────────────────────────────────────────────────────────

@router.put("/categories/{slug}/objectives", response_model=ObjectivesOut)
def save_objectives(slug: str, team_id: uuid.UUID, data: list[ObjectiveIn],
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """The team's answers on the 7 objectives (send all 7; any subset is
    accepted and only those rows are written)."""
    _require(db, current_user, team_id, EDIT)
    _playbook(db, slug)
    codes = [o.code for o in data]
    if len(codes) != len(set(codes)):
        raise HTTPException(status_code=400, detail="Each objective may appear once")
    _check_objective_codes(codes)
    bad = sorted({o.priority for o in data if o.priority is not None} - set(PRIORITIES))
    if bad:
        raise HTTPException(status_code=400, detail=(
            f"Unknown priority: {', '.join(bad)}. One of: {', '.join(PRIORITIES)}"))
    existing = _find_objectives(db, team_id, slug)

    def snapshot(row: TeamObjective) -> dict:
        return {"selected": row.selected, "priority": row.priority, "note": row.note}

    prev = {c: snapshot(o) for c, o in existing.items() if c in codes}
    # In code order, so two concurrent first saves take their row locks in the
    # same order and queue behind each other instead of deadlocking.
    for o in sorted(data, key=lambda o: o.code):
        row = existing.get(o.code)
        if row is None:
            first = TeamObjective(team_id=team_id, playbook_slug=slug, objective_code=o.code,
                                  selected=o.selected, priority=o.priority, note=o.note,
                                  updated_by=current_user.id)
            row, created = _insert_once(
                db, first, lambda code=o.code: _find_objective(db, team_id, slug, code))
            if created:
                continue
            prev[o.code] = snapshot(row)    # a concurrent save won: update its row
        row.selected, row.priority, row.note = o.selected, o.priority, o.note
        row.updated_by = current_user.id
    db.flush()
    log_event(db, team_id, current_user.id, "update", "team_objectives", slug,
              previous_value=prev or None,
              new_value={o.code: {"selected": o.selected, "priority": o.priority,
                                  "note": o.note} for o in data})
    out = {"playbook_slug": slug, "objectives": svc.objectives_overlay(db, team_id, slug)}
    db.commit()
    return out


# ── Actions ──────────────────────────────────────────────────────────────────

def _check_action_links(db: Session, team_id: uuid.UUID, slug: str,
                        lever_id: int | None, custom_lever_id: uuid.UUID | None) -> None:
    if lever_id is not None and custom_lever_id is not None:
        raise HTTPException(status_code=400, detail=(
            "An action links to one lever: lever_id or custom_lever_id, not both"))
    if lever_id is not None:
        ok = db.query(PlaybookLever.id).filter(
            PlaybookLever.id == lever_id, PlaybookLever.playbook_slug == slug).first()
        if ok is None:
            raise HTTPException(status_code=400, detail="Lever not found in this category")
    if custom_lever_id is not None:
        ok = db.query(CustomLever.id).filter(
            CustomLever.id == custom_lever_id, CustomLever.team_id == team_id,
            CustomLever.playbook_slug == slug).first()
        if ok is None:
            raise HTTPException(status_code=400, detail=(
                "Custom lever not found in this category"))


def _check_action_values(status: str | None, start, due) -> None:
    if status is not None and status not in ACTION_STATUSES:
        raise HTTPException(status_code=400, detail=(
            f"Unknown action status: {status}. One of: {', '.join(ACTION_STATUSES)}"))
    if start and due and due < start:
        raise HTTPException(status_code=400, detail="The due date is before the start date")


def _action(db: Session, team_id: uuid.UUID, action_id: uuid.UUID) -> StrategyAction:
    action = db.query(StrategyAction).filter(
        StrategyAction.id == action_id, StrategyAction.team_id == team_id).first()
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


@router.get("/categories/{slug}/actions", response_model=ActionsOut)
def list_actions(slug: str, team_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, VIEW)
    _playbook(db, slug)
    rows = svc.action_rows(db, team_id, slug)
    return {"playbook_slug": slug, "today": date.today(),
            "counts": svc.action_counts(rows), "actions": rows}


@router.post("/categories/{slug}/actions", response_model=ActionOut, status_code=201)
def create_action(slug: str, team_id: uuid.UUID, data: ActionCreate,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, EDIT)
    _playbook(db, slug)
    _require_title(data.title, "An action")
    _check_action_links(db, team_id, slug, data.lever_id, data.custom_lever_id)
    _check_member(db, team_id, data.assignee_user_id, "assignee")
    _check_action_values(data.status, data.start_date, data.due_date)
    action = StrategyAction(
        team_id=team_id, playbook_slug=slug, lever_id=data.lever_id,
        custom_lever_id=data.custom_lever_id, title=data.title.strip(),
        description=data.description, assignee_user_id=data.assignee_user_id,
        start_date=data.start_date, due_date=data.due_date, status=data.status,
        pct_complete=data.pct_complete, created_by=current_user.id,
    )
    db.add(action)
    db.flush()
    log_event(db, team_id, current_user.id, "create", "strategy_action", str(action.id),
              new_value=_jsonable({f: getattr(action, f) for f in ACTION_FIELDS}))
    row = svc.action_rows(db, team_id, slug, [action])[0]
    db.commit()
    return row


@router.put("/actions/{action_id}", response_model=ActionOut)
def update_action(action_id: uuid.UUID, team_id: uuid.UUID, data: ActionUpdate,
                  db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    """Partial update. Setting `lever_id` clears `custom_lever_id` and vice
    versa; send both as null to unlink."""
    _require(db, current_user, team_id, EDIT)
    action = _action(db, team_id, action_id)
    fields = data.model_fields_set
    if "title" in fields:
        _require_title(data.title, "An action")
    if "status" in fields and data.status is None:
        raise HTTPException(status_code=400, detail="An action needs a status")
    if "pct_complete" in fields and data.pct_complete is None:
        raise HTTPException(status_code=400, detail="pct_complete cannot be null")
    lever_id = data.lever_id if "lever_id" in fields else action.lever_id
    custom_id = data.custom_lever_id if "custom_lever_id" in fields else action.custom_lever_id
    if "lever_id" in fields and data.lever_id is not None and "custom_lever_id" not in fields:
        custom_id = None
    if "custom_lever_id" in fields and data.custom_lever_id is not None \
            and "lever_id" not in fields:
        lever_id = None
    _check_action_links(db, team_id, action.playbook_slug, lever_id, custom_id)
    if "assignee_user_id" in fields:
        _check_member(db, team_id, data.assignee_user_id, "assignee")
    start = data.start_date if "start_date" in fields else action.start_date
    due = data.due_date if "due_date" in fields else action.due_date
    _check_action_values(data.status if "status" in fields else None, start, due)

    prev = {f: getattr(action, f) for f in ACTION_FIELDS}
    for f in ("title", "description", "assignee_user_id", "start_date", "due_date",
              "status", "pct_complete"):
        if f in fields:
            value = getattr(data, f)
            setattr(action, f, value.strip() if f == "title" else value)
    action.lever_id, action.custom_lever_id = lever_id, custom_id
    db.flush()
    log_event(db, team_id, current_user.id, "update", "strategy_action", str(action.id),
              previous_value=_jsonable(prev),
              new_value=_jsonable({f: getattr(action, f) for f in ACTION_FIELDS}))
    row = svc.action_rows(db, team_id, action.playbook_slug, [action])[0]
    db.commit()
    return row


@router.delete("/actions/{action_id}")
def delete_action(action_id: uuid.UUID, team_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    _require(db, current_user, team_id, EDIT)
    action = _action(db, team_id, action_id)
    log_event(db, team_id, current_user.id, "delete", "strategy_action", str(action.id),
              previous_value=_jsonable({"playbook_slug": action.playbook_slug,
                                        "title": action.title, "status": action.status}))
    db.delete(action)
    db.commit()
    return {"status": "deleted"}
