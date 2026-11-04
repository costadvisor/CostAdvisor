"""Loading news search profiles (Wave 4, SCRUM-213 / NEWS-1).

``get_effective_profile()`` is the one entry point the test run (SCRUM-213),
the monthly sweep (SCRUM-214) and the classifier (SCRUM-215) should use, so
the merge rules live in exactly one place (``search_profile_merge``).

The tables are platform-only with no RLS, so no ``bypass_rls_var`` is needed
to read them; Celery tasks will still set it for the tables they join to.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants.trust import PROVENANCE_HUMAN_EDITED
from app.models.chemical_family import ChemicalFamily
from app.models.producer import Producer
from app.models.region import Region
from app.models.search_profile import SearchProfile
from app.models.product_line import ProductLine
from app.models.user import User
from app.services.audit import log_event
from app.schemas.search_profile import EffectiveProfile, ProfileFields
from app.services.search_profile_merge import merge_fields


class ProfileNotFound(LookupError):
    pass


def get_profile_row(db: Session, family_id: int, product_line_id: int | None = None) -> SearchProfile | None:
    """The base profile (``product_line_id=None``) or one product line's override."""
    scope = (SearchProfile.product_line_id.is_(None) if product_line_id is None
             else SearchProfile.product_line_id == product_line_id)
    stmt = select(SearchProfile).where(SearchProfile.family_id == family_id, scope)
    return db.scalars(stmt).one_or_none()


def get_effective_profile(db: Session, family_id: int, product_line_id: int | None = None) -> EffectiveProfile:
    """The family's base profile with the product line's override merged on top.

    A product line without an override simply gets the family profile.
    """
    base = get_profile_row(db, family_id)
    if base is None:
        raise ProfileNotFound(f"no search profile for family {family_id}")

    override = None
    if product_line_id is not None:
        row = get_profile_row(db, family_id, product_line_id)
        override = ProfileFields.from_row(row) if row else None

    return EffectiveProfile(
        family_id=family_id,
        product_line_id=product_line_id,
        is_active=base.is_active,
        provenance=base.provenance,
        owner_editor_id=base.owner_editor_id,
        **merge_fields(ProfileFields.from_row(base), override),
    )


# ── Writing (SCRUM-213 sub-task 2: the portal editor) ────────────────────────

class ProfileValidationError(ValueError):
    """Bad input an editor can fix: unknown producer, wrong product line, ..."""


def _platform_family(db: Session, family_id: int) -> ChemicalFamily:
    family = db.get(ChemicalFamily, family_id)
    if family is None:
        raise ProfileNotFound(f"no family {family_id}")
    return family


def family_lines(db: Session, family_id: int) -> list[ProductLine]:
    """The family's current product lines (retired lines are hidden, as elsewhere)."""
    return (db.query(ProductLine)
            .filter(ProductLine.family_id == family_id, ProductLine.retired_at.is_(None))
            .order_by(ProductLine.sort_order, ProductLine.name).all())


def _current_line(db: Session, family_id: int, product_line_id: int) -> ProductLine:
    line = db.get(ProductLine, product_line_id)
    if line is None or line.family_id != family_id or line.retired_at is not None:
        raise ProfileValidationError(
            f"product line {product_line_id} is not a current line of family {family_id}")
    return line


def _resolve_links(db: Session, fields: ProfileFields) -> tuple[list[Producer], list[Region]]:
    producers = (db.query(Producer).filter(Producer.id.in_(fields.producer_ids)).all()
                 if fields.producer_ids else [])
    missing = set(fields.producer_ids) - {p.id for p in producers}
    if missing:
        raise ProfileValidationError(f"unknown producer id(s): {', '.join(sorted(map(str, missing)))}")
    regions = (db.query(Region).filter(Region.code.in_(fields.region_codes)).all()
               if fields.region_codes else [])
    missing_r = set(fields.region_codes) - {r.code for r in regions}
    if missing_r:
        raise ProfileValidationError(f"unknown region code(s): {', '.join(sorted(missing_r))}")
    # Keep the editor's order.
    p_order = {pid: i for i, pid in enumerate(fields.producer_ids)}
    r_order = {code: i for i, code in enumerate(fields.region_codes)}
    return (sorted(producers, key=lambda p: p_order[p.id]),
            sorted(regions, key=lambda r: r_order[r.code]))


def snapshot(row: SearchProfile | None) -> dict | None:
    """JSON-safe copy of a profile's settings, for the audit log."""
    if row is None:
        return None
    data = ProfileFields.from_row(row).model_dump(mode="json")
    data.update(is_active=row.is_active, provenance=row.provenance,
                owner_editor_id=str(row.owner_editor_id) if row.owner_editor_id else None)
    return data


def save_profile(
    db: Session,
    family_id: int,
    fields: ProfileFields,
    *,
    product_line_id: int | None = None,
    provenance: str = PROVENANCE_HUMAN_EDITED,
    is_active: bool | None = None,
    owner_editor_id: uuid.UUID | None = None,
    notes: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> SearchProfile:
    """Create or replace a family profile (``product_line_id=None``) or an override.

    Replaces every editable field with ``fields``: the editor always sends the
    whole form, so a cleared list really is cleared. Flushes, does not commit.
    """
    _platform_family(db, family_id)
    if product_line_id is not None:
        _current_line(db, family_id, product_line_id)
        if get_profile_row(db, family_id) is None:
            raise ProfileValidationError("create the family profile before overriding a product line")
    elif not (fields.terms or fields.synonyms or fields.cas_numbers):
        # A family profile is what the sweep searches with: without a name to
        # search for it would search for nothing, so it cannot be saved empty.
        raise ProfileValidationError("add at least one search term, other name or CAS number")
    if owner_editor_id is not None and db.get(User, owner_editor_id) is None:
        raise ProfileValidationError(f"unknown user {owner_editor_id}")
    producers, regions = _resolve_links(db, fields)

    row = get_profile_row(db, family_id, product_line_id)
    before = snapshot(row)
    if row is None:
        row = SearchProfile(family_id=family_id, product_line_id=product_line_id)
        db.add(row)

    for name in ("terms", "synonyms", "cas_numbers", "exclude_terms", "event_types",
                 "preferred_sites", "blocked_sites", "language", "min_relevance", "lookback_days"):
        setattr(row, name, getattr(fields, name))
    row.producers = producers
    row.regions = regions
    row.provenance = provenance
    if product_line_id is None:
        # Activation and ownership are family-level; an override inherits them.
        if is_active is not None:
            row.is_active = is_active
        row.owner_editor_id = owner_editor_id
    if notes is not None:
        row.notes = notes or None
    db.flush()

    if actor_id is not None:
        log_event(db, None, actor_id, "create" if before is None else "update", "search_profile",
                  str(row.id), previous_value=before, new_value=snapshot(row))
    return row


def delete_override(db: Session, family_id: int, product_line_id: int, *, actor_id: uuid.UUID) -> bool:
    row = get_profile_row(db, family_id, product_line_id)
    if row is None:
        return False
    log_event(db, None, actor_id, "delete", "search_profile", str(row.id), previous_value=snapshot(row))
    db.delete(row)
    db.flush()
    return True


def effective_from_fields(db: Session, family_id: int, fields: ProfileFields,
                          product_line_id: int | None = None) -> EffectiveProfile:
    """An effective profile built from *unsaved* form values, for the test run.

    With ``product_line_id`` the fields are an override merged onto the saved
    family profile; without, they are the family profile itself.
    """
    if product_line_id is None:
        base_fields, override = fields, None
        base = get_profile_row(db, family_id)
    else:
        base = get_profile_row(db, family_id)
        if base is None:
            raise ProfileNotFound(f"no search profile for family {family_id}")
        base_fields, override = ProfileFields.from_row(base), fields
    return EffectiveProfile(
        family_id=family_id, product_line_id=product_line_id,
        is_active=base.is_active if base else False,
        provenance=base.provenance if base else PROVENANCE_HUMAN_EDITED,
        owner_editor_id=base.owner_editor_id if base else None,
        **merge_fields(base_fields, override),
    )


# ── Editor aids (warnings, owner picker) ─────────────────────────────────────

def profile_warnings(row: SearchProfile | None) -> list[str]:
    """Plain-language problems with a family profile, for the list screen.

    Advice, not rules: a profile with warnings can still be saved and run.
    """
    if row is None:
        return ["No profile yet"]
    out = []
    if not (row.terms or row.synonyms or row.cas_numbers):
        out.append("Nothing to search for")
    if not row.producers:
        out.append("No producers to watch")
    if not row.regions:
        out.append("No regions")
    if not row.exclude_terms and not row.blocked_sites:
        out.append("No exclusions yet (expect noise)")
    if row.is_active and row.owner_editor_id is None:
        out.append("Active but nobody owns it")
    return out


def platform_editors(db: Session) -> list[User]:
    """People who can own a profile: super admins and holders of a platform
    role granting ``content.edit`` (the Content Editor role)."""
    from app.models.rbac import Permission, RolePermission, UserPlatformRole

    editor_ids = (select(UserPlatformRole.user_id)
                  .join(RolePermission, RolePermission.role_id == UserPlatformRole.role_id)
                  .join(Permission, Permission.id == RolePermission.permission_id)
                  .where(Permission.key == "content.edit"))
    return (db.query(User)
            .filter((User.is_super_admin.is_(True)) | (User.id.in_(editor_ids)))
            .order_by(User.display_name, User.email).all())
