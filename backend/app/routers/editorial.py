"""Editorial content API (Wave 3, SCRUM-76 / INT-2 · CON-1/2/7).

    GET  /api/editorial/blocks                  list, filterable by subject
    POST /api/editorial/blocks                  author a block (platform or team)
    GET  /api/editorial/blocks/{id}             one block at its current version
    PUT  /api/editorial/blocks/{id}             append a version (forks a platform
                                                block when a team edits it)
    POST /api/editorial/blocks/{id}/approve     records approver + timestamp
    GET  /api/editorial/blocks/{id}/versions    full history
    GET  /api/editorial/blocks/{id}/versions/{version_no}
    DELETE /api/editorial/blocks/{id}
    GET  /api/editorial/cards/{subject_type}/{subject_code}   the composed card

**Why this is not in `formulas.py`:** that router keys everything on a
`{template_id}` UUID (`/{template_id}/components`, `/{template_id}/coverage`,
`/{template_id}/evaluate`), so a `{code}` path segment would collide with it —
and the template-less subjects have no UUID to fall back on in the first place.

**Two permission surfaces, deliberately.** Platform authoring gates on
`has_platform_permission` + `UserPlatformRole` (the Content Editor role the
migration seeds, or a super admin); team authoring gates on the team's
`content.*` keys. They are different questions: "may you write the library
everybody reads" is not "may you write your own copy".
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.editorial import (
    BLOCK_TYPES, PROVENANCE_BADGES, PROVENANCE_STATES, SUBJECT_TYPES, EditorialBlock,
    EditorialBlockVersion,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.editorial import (
    BlockCreate, BlockEdit, BlockOut, CardOut, ProvenanceBadge, VersionOut,
)
from app.services.audit import log_event
from app.services.editorial import (
    add_version, approve_block, create_block, fork_block, read_card,
    validate_vocab, visible_block,
)
from app.services.permissions import require_permission, require_platform_permission

router = APIRouter()

# SCRUM-75 / SCRUM-134 owns the derived numbers at formula x region combo grain.
# Advertised on the card so a consumer knows where the other half lives.
DERIVED_PAYLOAD_ENDPOINT = "/api/formulas/{template_id}/evaluate"


def _out(block: EditorialBlock) -> BlockOut:
    v = block.current_version
    return BlockOut(
        id=block.id, team_id=block.team_id, origin_id=block.origin_id,
        subject_type=block.subject_type, subject_code=block.subject_code,
        block_type=block.block_type, region=block.region,
        template_id=block.template_id, commodity_id=block.commodity_id,
        family_id=block.family_id, subfamily_id=block.subfamily_id,
        body_format=block.body_format, provenance=block.provenance,
        badge=ProvenanceBadge(**PROVENANCE_BADGES[block.provenance]),
        current_version_no=v.version_no if v else None,
        body_text=v.body_text if v else None,
        body_json=v.body_json if v else None,
        approved_by=block.approved_by, approved_at=block.approved_at,
        expires_at=block.expires_at, is_stale=block.is_stale,
        internal_note=block.internal_note, source_note=block.source_note,
        created_at=block.created_at, updated_at=block.updated_at,
    )


def _require_write(db: Session, user: User, team_id: uuid.UUID, *, platform: bool,
                   key: str = "content.edit") -> None:
    if platform:
        require_platform_permission(db, user, key)
    else:
        require_permission(db, user, team_id, key)


@router.get("/blocks", response_model=list[BlockOut])
def list_blocks(team_id: uuid.UUID,
                subject_type: str | None = Query(None),
                subject_code: str | None = Query(None),
                block_type: str | None = Query(None),
                provenance: list[str] | None = Query(None),
                limit: int = Query(200, ge=1, le=1000),
                offset: int = Query(0, ge=0),
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """Blocks visible to this team: its own plus the platform library.

    `provenance` filters the four-state review ladder, which is what an
    approvals queue reads — "everything not yet signed off" is the question, and
    answering it by fetching the whole library and filtering client-side stops
    working the moment the editorial loader lands its real volume.
    """
    require_permission(db, current_user, team_id, "content.view")
    if subject_type and subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")
    if block_type and block_type not in BLOCK_TYPES:
        raise HTTPException(422, f"Invalid block_type. Allowed: {sorted(BLOCK_TYPES)}")
    for prov in (provenance or []):
        if prov not in PROVENANCE_STATES:
            raise HTTPException(
                422, f"Invalid provenance. Allowed: {sorted(PROVENANCE_STATES)}")

    from sqlalchemy import or_
    q = (
        db.query(EditorialBlock)
        .options(joinedload(EditorialBlock.current_version))
        .filter(or_(EditorialBlock.team_id.is_(None),
                    EditorialBlock.team_id == team_id))
    )
    if subject_type:
        q = q.filter(EditorialBlock.subject_type == subject_type)
    if subject_code:
        q = q.filter(EditorialBlock.subject_code == subject_code)
    if block_type:
        q = q.filter(EditorialBlock.block_type == block_type)
    if provenance:
        q = q.filter(EditorialBlock.provenance.in_(provenance))
    rows = (
        q.order_by(EditorialBlock.subject_code, EditorialBlock.block_type)
        .offset(offset).limit(limit).all()
    )
    return [_out(b) for b in rows]


@router.post("/blocks", response_model=BlockOut, status_code=201)
def create(team_id: uuid.UUID, data: BlockCreate, db: Session = Depends(get_db),
           current_user: User = Depends(get_current_user)):
    _require_write(db, current_user, team_id, platform=data.platform)
    validate_vocab(data.subject_type, data.block_type, data.body_format, data.provenance)

    block = create_block(
        db,
        team_id=None if data.platform else team_id,
        subject_type=data.subject_type, subject_code=data.subject_code,
        block_type=data.block_type, region=data.region,
        body_text=data.body_text, body_json=data.body_json,
        body_format=data.body_format, provenance=data.provenance,
        internal_note=data.internal_note, source_note=data.source_note,
        expires_at=data.expires_at, author=current_user,
    )
    out = _out(block)
    log_event(db, team_id, current_user.id, "create", "editorial_block", str(block.id),
              new_value={"subject": f"{data.subject_type}:{data.subject_code}",
                         "block_type": data.block_type, "platform": data.platform})
    db.commit()
    return out


@router.get("/blocks/{block_id}", response_model=BlockOut)
def get_block(block_id: uuid.UUID, team_id: uuid.UUID, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "content.view")
    return _out(visible_block(db, block_id, team_id))


@router.put("/blocks/{block_id}", response_model=BlockOut)
def edit_block(block_id: uuid.UUID, team_id: uuid.UUID, data: BlockEdit,
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """Append a version.

    A team editing a **platform** block gets a fork: the platform row is what
    every other team reads, so it must not mutate. Editing the platform row
    itself requires the platform permission.
    """
    block = visible_block(db, block_id, team_id)
    validate_vocab(block.subject_type, block.block_type, data.body_format, data.provenance)

    forked = False
    if block.team_id is None:
        if _can_author_platform(db, current_user):
            _require_write(db, current_user, team_id, platform=True)
        else:
            require_permission(db, current_user, team_id, "content.edit")
            block = fork_block(db, block, team_id, author=current_user)
            forked = True
    else:
        require_permission(db, current_user, team_id, "content.edit")

    for field in ("internal_note", "source_note", "expires_at"):
        value = getattr(data, field)
        if value is not None:
            setattr(block, field, value)

    add_version(db, block, body_text=data.body_text, body_json=data.body_json,
                body_format=data.body_format, provenance=data.provenance,
                change_note=data.change_note, author=current_user)
    out = _out(block)
    log_event(db, team_id, current_user.id, "update", "editorial_block", str(block.id),
              new_value={"version": out.current_version_no, "forked": forked,
                         "provenance": data.provenance})
    db.commit()
    return out


def _can_author_platform(db: Session, user: User) -> bool:
    from app.services.permissions import has_platform_permission
    return has_platform_permission(db, user, "content.edit")


@router.post("/blocks/{block_id}/approve", response_model=BlockOut)
def approve(block_id: uuid.UUID, team_id: uuid.UUID, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user)):
    """Record the approver and the timestamp, and set `human_approved`.

    A later edit clears it — `add_version` drops the approval whenever the new
    provenance is not `human_approved`, because approval is what removes the
    customer-facing caveat and an edit is not an approval.
    """
    block = visible_block(db, block_id, team_id)
    _require_write(db, current_user, team_id,
                   platform=block.team_id is None, key="content.approve")
    approve_block(db, block, current_user)
    out = _out(block)
    log_event(db, team_id, current_user.id, "approve", "editorial_block", str(block.id),
              new_value={"version": out.current_version_no})
    db.commit()
    return out


@router.get("/blocks/{block_id}/versions", response_model=list[VersionOut])
def list_versions(block_id: uuid.UUID, team_id: uuid.UUID, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    require_permission(db, current_user, team_id, "content.view")
    visible_block(db, block_id, team_id)
    return (
        db.query(EditorialBlockVersion)
        .filter(EditorialBlockVersion.block_id == block_id)
        .order_by(EditorialBlockVersion.version_no)
        .all()
    )


@router.get("/blocks/{block_id}/versions/{version_no}", response_model=VersionOut)
def get_version(block_id: uuid.UUID, version_no: int, team_id: uuid.UUID,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """The prior text stays readable after an edit — that is the point of
    append-only versioning."""
    require_permission(db, current_user, team_id, "content.view")
    visible_block(db, block_id, team_id)
    v = (
        db.query(EditorialBlockVersion)
        .filter(EditorialBlockVersion.block_id == block_id,
                EditorialBlockVersion.version_no == version_no)
        .first()
    )
    if v is None:
        raise HTTPException(404, "Version not found")
    return v


@router.delete("/blocks/{block_id}")
def delete_block(block_id: uuid.UUID, team_id: uuid.UUID, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    block = visible_block(db, block_id, team_id)
    _require_write(db, current_user, team_id,
                   platform=block.team_id is None, key="content.delete")
    log_event(db, team_id, current_user.id, "delete", "editorial_block", str(block.id))
    db.delete(block)
    db.commit()
    return {"status": "deleted"}


@router.get("/cards/{subject_type}/{subject_code:path}", response_model=CardOut)
def card(subject_type: str, subject_code: str, team_id: uuid.UUID,
         region: str | None = Query(None),
         db: Session = Depends(get_db),
         current_user: User = Depends(get_current_user)):
    """Every block type for one subject, in one request.

    One query regardless of how many block types are present — the whole reason
    `current_version_id` exists rather than a per-type read. `subject_code` uses
    a `:path` converter because the `subfamily` key is
    `"<family>|<subfamily>"` and a family name can contain characters a plain
    segment would mangle.
    """
    require_permission(db, current_user, team_id, "content.view")
    result = read_card(db, subject_type, subject_code, team_id, region=region)
    return CardOut(
        subject_type=result.subject_type, subject_code=result.subject_code,
        region=region,
        blocks={b.block_type: _out(b) for b in result.blocks},
        resolved_from=result.resolved_from,
        derived_payload_endpoint=DERIVED_PAYLOAD_ENDPOINT,
    )
