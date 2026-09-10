"""Editorial block store + the card read path (Wave 3, SCRUM-76 / INT-2).

**The read-path decision, made before the CRUD API as the ticket requires: a
denormalised endpoint, with `editorial_blocks.current_version_id` as the
pointer — not a materialised published-projection table.**

Why: the card is "every block type for one subject", which is one row per block
type in one table. Joined to `editorial_block_versions` on `current_version_id`
that is a **single query regardless of how many block types exist**, which is
exactly the acceptance criterion. A materialised projection would need a
refresh job on every publish plus a second source of truth for the same rows,
and would buy nothing here. Because the pointer is sufficient, the write path
stays simple: append a version, repoint the block. Nothing has to be published
twice.

The card is deliberately **two calls**: this endpoint owns the composed
editorial read, and the derived payload at formula × region combo grain
(series, components, cycle, seasonality, volatility, tier) is SCRUM-75's. This
story consumes that and re-derives none of it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.chemical_family import ChemicalFamily
from app.models.editorial import (
    BLOCK_TYPES, BODY_FORMATS, EditorialBlock, EditorialBlockVersion,
    PROVENANCE_HUMAN_APPROVED, PROVENANCE_HUMAN_EDITED, PROVENANCE_STATES,
    SUBJECT_TYPES,
)
from app.models.formula_template import FormulaTemplate
from app.models.index_data import CommodityIndex
from app.models.subfamily import Subfamily
from app.models.user import User


# ── Subject resolution ───────────────────────────────────────────────────────

@dataclass
class SubjectLinks:
    """The convenience joins for a subject, where they resolve.

    All nullable by design: `subject_code` is the identity and these are
    lookups. 53 of 423 `CURATED_CONTENT` keys have no platform template, and
    only 14 of 23 drop family names and 4 of 33 `Family|Subfamily` pairs match a
    taxonomy row today — a hard FK would drop exactly those rows and leave
    nothing able to tell "never authored" from "dropped at import".
    """
    template_id: uuid.UUID | None = None
    commodity_id: int | None = None
    family_id: int | None = None
    subfamily_id: int | None = None


def resolve_subject(db: Session, subject_type: str, subject_code: str) -> SubjectLinks:
    links = SubjectLinks()
    if subject_type == "formula":
        row = (
            db.query(FormulaTemplate)
            .filter(FormulaTemplate.code == subject_code,
                    FormulaTemplate.team_id.is_(None))
            .first()
        )
        if row:
            links.template_id = row.id
    elif subject_type == "index":
        # The drop's slug IS the series key, and region is already baked into it
        # where it applies — `-ppi` / `-wb` / `-mb` suffixes are *sources*, not
        # regions, so the slug is never parsed apart.
        row = (
            db.query(CommodityIndex)
            .filter(CommodityIndex.commodity_key == subject_code)
            .first()
        )
        if row:
            links.commodity_id = row.id
    elif subject_type == "family":
        row = (
            db.query(ChemicalFamily)
            .filter(ChemicalFamily.name == subject_code,
                    ChemicalFamily.team_id.is_(None))
            .first()
        )
        if row:
            links.family_id = row.id
    elif subject_type == "subfamily":
        # `"<family>|<subfamily>"` — see `editorial.subfamily_subject_code`.
        family_name, _, sub_name = subject_code.partition("|")
        if sub_name:
            row = (
                db.query(Subfamily)
                .join(ChemicalFamily, ChemicalFamily.id == Subfamily.family_id)
                .filter(Subfamily.name == sub_name,
                        ChemicalFamily.name == family_name,
                        Subfamily.team_id.is_(None))
                .first()
            )
            if row:
                links.subfamily_id = row.id
                links.family_id = row.family_id
    return links


def validate_vocab(subject_type: str, block_type: str, body_format: str,
                   provenance: str | None = None) -> None:
    if subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")
    if block_type not in BLOCK_TYPES:
        raise HTTPException(422, f"Invalid block_type. Allowed: {sorted(BLOCK_TYPES)}")
    if body_format not in BODY_FORMATS:
        raise HTTPException(422, f"Invalid body_format. Allowed: {sorted(BODY_FORMATS)}")
    if provenance is not None and provenance not in PROVENANCE_STATES:
        raise HTTPException(422, f"Invalid provenance. Allowed: {sorted(PROVENANCE_STATES)}")


def _check_body(body_format: str, body_text: str | None, body_json) -> None:
    if body_format == "text" and body_text is None:
        raise HTTPException(422, "body_format 'text' requires body_text")
    if body_format == "json" and body_json is None:
        raise HTTPException(422, "body_format 'json' requires body_json")


# ── Visibility ───────────────────────────────────────────────────────────────

def visible_block(db: Session, block_id: uuid.UUID, team_id: uuid.UUID) -> EditorialBlock:
    """A block this team may read: its own, or platform.

    RLS already hides another team's rows, but this also refuses a row that is
    visible yet not this team's to act on — so a 404 never doubles as a hint
    that somebody else's block exists.
    """
    block = (
        db.query(EditorialBlock)
        .filter(EditorialBlock.id == block_id,
                or_(EditorialBlock.team_id.is_(None),
                    EditorialBlock.team_id == team_id))
        .first()
    )
    if block is None:
        raise HTTPException(404, "Editorial block not found")
    return block


# ── Write path ───────────────────────────────────────────────────────────────

def _next_version_no(db: Session, block_id: uuid.UUID) -> int:
    last = (
        db.query(EditorialBlockVersion.version_no)
        .filter(EditorialBlockVersion.block_id == block_id)
        .order_by(EditorialBlockVersion.version_no.desc())
        .first()
    )
    return (last[0] + 1) if last else 1


def add_version(
    db: Session,
    block: EditorialBlock,
    *,
    body_text: str | None,
    body_json,
    body_format: str,
    provenance: str,
    change_note: str | None,
    author: User | None,
) -> EditorialBlockVersion:
    """Append a version and repoint the block.

    Append-only: the prior version stays readable by `version_no`. Repointing
    also **clears any approval** — approval is what removes the customer-facing
    caveat, and an edit is not an approval, so a row must never keep reading as
    approved after its text changed.
    """
    _check_body(body_format, body_text, body_json)
    version = EditorialBlockVersion(
        block_id=block.id,
        version_no=_next_version_no(db, block.id),
        body_text=body_text if body_format == "text" else None,
        body_json=body_json if body_format == "json" else None,
        body_format=body_format,
        provenance=provenance,
        change_note=change_note,
        authored_by=author.id if author else None,
    )
    db.add(version)
    db.flush()

    block.current_version_id = version.id
    block.body_format = body_format
    block.provenance = provenance
    if provenance != PROVENANCE_HUMAN_APPROVED:
        block.approved_by = None
        block.approved_at = None
    from datetime import datetime, timezone
    block.updated_at = datetime.now(timezone.utc)
    db.flush()
    return version


def create_block(
    db: Session,
    *,
    team_id: uuid.UUID | None,
    subject_type: str,
    subject_code: str,
    block_type: str,
    region: str | None,
    body_text: str | None,
    body_json,
    body_format: str,
    provenance: str,
    internal_note: str | None = None,
    source_note: str | None = None,
    expires_at=None,
    author: User | None = None,
) -> EditorialBlock:
    validate_vocab(subject_type, block_type, body_format, provenance)
    _check_body(body_format, body_text, body_json)

    links = resolve_subject(db, subject_type, subject_code)
    block = EditorialBlock(
        team_id=team_id,
        subject_type=subject_type,
        subject_code=subject_code,
        block_type=block_type,
        # NULL is the "*" wildcard the dated outlooks use; a sentinel string
        # would collide with the `regions.code` vocabulary.
        region=region,
        template_id=links.template_id,
        commodity_id=links.commodity_id,
        family_id=links.family_id,
        subfamily_id=links.subfamily_id,
        body_format=body_format,
        provenance=provenance,
        internal_note=internal_note,
        source_note=source_note,
        expires_at=expires_at,
        created_by=author.id if author else None,
    )
    db.add(block)
    # A duplicate (subject, block_type, region) is a real, expected collision —
    # `substitution` is carried by both CURATED_CONTENT and FUTURE_OUTLOOK for
    # 100 keys with identical content, so a loader will hit this. It is a clean
    # 409, not a 500.
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409,
            f"A {block_type!r} block already exists for "
            f"{subject_type}:{subject_code}"
            + (f" in region {region}" if region else " (wildcard region)"),
        )
    add_version(db, block, body_text=body_text, body_json=body_json,
                body_format=body_format, provenance=provenance,
                change_note="initial", author=author)
    return block


def approve_block(db: Session, block: EditorialBlock, approver: User) -> EditorialBlock:
    from datetime import datetime, timezone

    block.provenance = PROVENANCE_HUMAN_APPROVED
    block.approved_by = approver.id
    block.approved_at = datetime.now(timezone.utc)
    # The current version carries the state too, so an old version never reads
    # as approved once a newer one supersedes it.
    if block.current_version:
        block.current_version.provenance = PROVENANCE_HUMAN_APPROVED
    db.flush()
    return block


def fork_block(
    db: Session, block: EditorialBlock, team_id: uuid.UUID, author: User | None = None
) -> EditorialBlock:
    """Copy a platform block into a team-owned one, history and all.

    A team editing platform content must never mutate the platform row — other
    teams read it. `origin_id` back-links the copy so a later reconciliation can
    still tell where it came from after a rename.
    """
    if block.team_id is not None:
        raise HTTPException(400, "Only a platform block can be forked")
    existing = (
        db.query(EditorialBlock)
        .filter(EditorialBlock.team_id == team_id,
                EditorialBlock.origin_id == block.id)
        .first()
    )
    if existing is not None:
        raise HTTPException(409, "This team already has a fork of that block")

    fork = EditorialBlock(
        team_id=team_id, origin_id=block.id,
        subject_type=block.subject_type, subject_code=block.subject_code,
        block_type=block.block_type, region=block.region,
        template_id=block.template_id, commodity_id=block.commodity_id,
        family_id=block.family_id, subfamily_id=block.subfamily_id,
        body_format=block.body_format,
        # A fork starts unapproved even from an approved original: the sign-off
        # was on the platform text, and the team is about to change it.
        provenance=PROVENANCE_HUMAN_EDITED,
        internal_note=block.internal_note, source_note=block.source_note,
        expires_at=block.expires_at, derived_from=block.derived_from,
        created_by=author.id if author else None,
    )
    db.add(fork)
    db.flush()

    # Copy the history so the fork's version numbering is continuous with what
    # the editor was looking at, then point at the last one.
    last = None
    for v in sorted(block.versions, key=lambda v: v.version_no):
        last = EditorialBlockVersion(
            block_id=fork.id, version_no=v.version_no,
            body_text=v.body_text, body_json=v.body_json, body_format=v.body_format,
            provenance=v.provenance, change_note=v.change_note,
            authored_by=v.authored_by,
        )
        db.add(last)
    db.flush()
    if last is not None:
        fork.current_version_id = last.id
        db.flush()
    return fork


# ── The card read (CON-7) ────────────────────────────────────────────────────

@dataclass
class Card:
    subject_type: str
    subject_code: str
    blocks: list[EditorialBlock]
    # Which side each block came from, so a consumer can show "your edit"
    # against "platform".
    resolved_from: dict


def read_card(
    db: Session,
    subject_type: str,
    subject_code: str,
    team_id: uuid.UUID,
    region: str | None = None,
) -> Card:
    """Every block type for one subject, in one query.

    **Region resolution, the ticket's open question, settled here rather than on
    the client:** a caller may pass a region; a region-specific block wins over
    the wildcard (`region IS NULL`) for the same block type, and passing nothing
    returns the wildcard. Resolving it server-side keeps the rule in one place
    and lets the caller pass the same region it passes to SCRUM-75's derived
    call.

    Team rows shadow platform rows for the same (block_type, region) — a fork is
    an override, so both existing is the normal case, not a conflict.
    """
    if subject_type not in SUBJECT_TYPES:
        raise HTTPException(422, f"Invalid subject_type. Allowed: {sorted(SUBJECT_TYPES)}")

    rows = (
        db.query(EditorialBlock)
        .options(joinedload(EditorialBlock.current_version))
        .filter(EditorialBlock.subject_type == subject_type,
                EditorialBlock.subject_code == subject_code,
                or_(EditorialBlock.team_id.is_(None),
                    EditorialBlock.team_id == team_id))
        .all()
    )

    chosen: dict[str, EditorialBlock] = {}
    resolved_from: dict[str, str] = {}
    for block in rows:
        if region is not None:
            if block.region is not None and block.region != region:
                continue
        elif block.region is not None:
            # No region asked for: only the wildcard applies.
            continue

        key = block.block_type
        incumbent = chosen.get(key)
        if incumbent is None or _outranks(block, incumbent):
            chosen[key] = block
            resolved_from[key] = _origin_label(block, region)

    return Card(
        subject_type=subject_type, subject_code=subject_code,
        blocks=[chosen[k] for k in sorted(chosen)],
        resolved_from=resolved_from,
    )


def _outranks(candidate: EditorialBlock, incumbent: EditorialBlock) -> bool:
    """Team beats platform; a region-specific block beats the wildcard.

    Team-vs-platform first: a fork exists precisely to override, so it must win
    even when the platform row is the more specific one on region.
    """
    cand_team = candidate.team_id is not None
    inc_team = incumbent.team_id is not None
    if cand_team != inc_team:
        return cand_team
    return candidate.region is not None and incumbent.region is None


def _origin_label(block: EditorialBlock, region: str | None) -> str:
    scope = "team" if block.team_id is not None else "platform"
    grain = "region" if block.region is not None else "wildcard"
    return f"{scope}:{grain}"
