"""Editorial block model + versioning (Wave 3, SCRUM-76 / INT-2 · CON-1/2/7).

The July drop landed two halves. The costing spine got a home in units 1–5.
The other half — synthesis routes, supplier notes, dated outlooks, macro
drivers, substitution risk, supply/demand splits, compliance detail, index
narratives — sits in `sample_idea/costadvisor-data/raw/*.json` and **nothing in
the schema could hold any of it**. This is the store and the read path.

Not a CMS. Verified against the drop rather than assumed: the five editorial
files carry **zero** HTML tags (`CURATED_CONTENT`, `FUTURE_OUTLOOK`,
`SUPPLY_DEMAND_COMPLIANCE`, `SYNTHESIS_ROUTES`, `INDEX_NARRATIVES` — 0 each;
`CURRENT_EVENTS_OUTLOOK` has 104 markdown bolds and no tags). It is structured
fields and short prose, one row per block type. Two tables cover it.

The design decisions, each forced by something in the data:

**`subject_code` NOT NULL, `template_id` nullable.** 53 of 423
`CURATED_CONTENT` keys have no `formula_templates` row in the live DB (12.5%),
including 36 `GRP-*` group pseudo-keys that are roll-ups, not formulas. Under a
hard FK those rows would not raise — they would simply not be there afterwards,
and nothing downstream could tell "never authored" from "dropped at import".
The same reasoning makes `commodity_id` / `family_id` / `product_line_id`
convenience joins rather than identity.

**Four subject types.** `formula | index | product_line | family`. A
`product_line` subject is keyed by the line's `line_key`. The index
namespace is real and verified clean: all 27 `INDEX_NARRATIVES` keys and all 80
`INDEX_SOURCE_META` keys resolve to a loaded `commodity_indexes.commodity_key`.
One polymorphic subject beats three near-identical tables.

**`region` nullable = the wildcard.** Only `CURRENT_EVENTS_OUTLOOK` carries a
region key today and it is always `"*"` (249 of 249), but the consumer reads
`entry[region] || entry['*']`, so the dimension has to survive the load —
collapsing it now means re-deriving it out of prose later.

**Provenance is four-state**, not two: `imported | ai_draft | human_edited |
human_approved`. A bulk import is neither AI-draft nor human-approved, so a
two-state flag forces every imported row into a bucket where both readings are
false — and this drives customer-visible copy, not an internal marker. Approval
is what clears the caveat, and an edit is not an approval.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Re-exported rather than redefined (SCRUM-78 asked for one home for the
# trust/provenance vocabulary), so the editorial provenance states and the
# combo trust grade cannot drift into two spellings. Every existing reader
# imports these names from this module, so they stay available here.
from app.constants.trust import (
    PROVENANCE_AI_DRAFT, PROVENANCE_BADGES, PROVENANCE_HUMAN_APPROVED,
    PROVENANCE_HUMAN_EDITED, PROVENANCE_IMPORTED, PROVENANCE_STATES,
)
from app.database import Base

SUBJECT_TYPES = ("formula", "index", "product_line", "family")

# The block-type vocabulary, read off the drop rather than invented.
#
#   CURATED_CONTENT           functionalities · applications · suppliers ·
#                             supplier_note · compliance · macro_drivers ·
#                             substitution · negotiation_note
#   SUPPLY_DEMAND_COMPLIANCE  supply · demand · compliance
#   FUTURE_OUTLOOK            macro_drivers · substitution
#   SYNTHESIS_ROUTES          synthesis_route (feedstocks + reaction + note)
#   CURRENT_EVENTS_OUTLOOK    current_events
#   INDEX_NARRATIVES          index_narrative (why3m + why24m)
#   INDEX_SOURCE_META         index_source_meta (agency + freq + proxy)
#   FAMILY_FUNCTIONALITY_*    functionalities at family / product-line grain
#
# `suppliers`, `supply` and `demand` are retired (design §2.4, §3.2): supplier
# rows live in `producer_formulas` only, and the supply/demand splits were
# ruled invented by the source. No block of those types is written or served.
BLOCK_TYPES = (
    "functionalities",
    "applications",
    "supplier_note",
    "compliance",
    "macro_drivers",
    "substitution",
    "synthesis_route",
    "current_events",
    "negotiation_note",
    "index_narrative",
    "index_source_meta",
)
RETIRED_BLOCK_TYPES = ("suppliers", "supply", "demand")

# The block types a **platform** block on a `formula` subject may be served
# with (design §4.2). Every `/api/editorial/*` read applies it; a platform
# formula block of any other type answers 404. The content loader writes
# exactly these types (`content_drop.content.FORMULA_BLOCKS`).
PUBLIC_FORMULA_BLOCK_TYPES = (
    "functionalities",
    "applications",
    "supplier_note",
    "compliance",
    "macro_drivers",
    "substitution",
    "synthesis_route",
    "current_events",
    "negotiation_note",
)
assert set(PUBLIC_FORMULA_BLOCK_TYPES) <= set(BLOCK_TYPES)
assert not set(RETIRED_BLOCK_TYPES) & set(BLOCK_TYPES)

BODY_FORMATS = ("text", "json")

# The wildcard the dated outlooks use. Stored as NULL — a sentinel string in a
# region column would collide with the `regions.code` vocabulary.
REGION_WILDCARD = "*"


class EditorialBlock(Base):
    """One block type for one subject — the identity and the pointer to its
    current version. The content itself lives on the versions.

    Platform-readable with team forks (`team_id IS NULL` = platform), the
    `tx1a2b3c4d5e` policy shape. A team editing a platform block **forks** it;
    the platform row must not mutate.
    """
    __tablename__ = "editorial_blocks"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('formula','index','product_line','family')",
            name="ck_editorial_subject_type",
        ),
        CheckConstraint(
            "provenance IN ('imported','ai_draft','human_edited','human_approved')",
            name="ck_editorial_provenance",
        ),
        CheckConstraint("body_format IN ('text','json')", name="ck_editorial_body_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL = platform. Uniqueness is re-scoped by partial indexes in the
    # migration, the same way uq_chem_fam_platform_name / _team_name do it.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True)
    # Back-link to the platform original a fork came from.
    origin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_blocks.id", ondelete="SET NULL"), nullable=True)

    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # The identity. NOT NULL and never an FK — see the module docstring.
    subject_code: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL = the "*" wildcard the dated outlooks use.
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Convenience joins, not identity. Null wherever the subject has no row in
    # our taxonomy.
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("formula_templates.id", ondelete="SET NULL"), nullable=True)
    commodity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("commodity_indexes.id", ondelete="SET NULL"), nullable=True)
    family_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chemical_families.id", ondelete="SET NULL"), nullable=True)
    product_line_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("product_lines.id", ondelete="SET NULL"), nullable=True, index=True)

    body_format: Mapped[str] = mapped_column(String(8), nullable=False, default="text")
    provenance: Mapped[str] = mapped_column(String(16), nullable=False, default="imported")

    # One round trip for a whole card: the card query joins blocks to versions on
    # this, so the query count does not grow with the number of block types.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("editorial_block_versions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Columns this story creates and deliberately does not consume ─────────
    # The dated outlooks carry built-in expiry and a large share were written to
    # one vantage date: 220 of 249 `CURRENT_EVENTS_OUTLOOK` entries mention
    # "June 2026", so imported uncritically they all expire on the same day.
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # CON-5's staleness recompute job reads these; nothing writes them yet.
    derived_from: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Several `supplierNote` values are author-to-self backlog text that would
    # render verbatim to a customer (13 of 423 carry a marker like "not publicly
    # disclosed" / "check" / "revisit"). This is where that goes instead.
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The payload carries no citation URLs anywhere.
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    versions: Mapped[list["EditorialBlockVersion"]] = relationship(
        back_populates="block", cascade="all, delete-orphan",
        order_by="EditorialBlockVersion.version_no",
        foreign_keys="EditorialBlockVersion.block_id",
    )
    current_version: Mapped["EditorialBlockVersion | None"] = relationship(
        foreign_keys=[current_version_id], post_update=True, viewonly=True,
    )

    @property
    def badge(self) -> dict:
        return PROVENANCE_BADGES[self.provenance]


class EditorialBlockVersion(Base):
    """One authored revision. Append-only: an edit adds a version and repoints
    the block, so the prior text stays readable by `version_no`."""
    __tablename__ = "editorial_block_versions"
    __table_args__ = (
        UniqueConstraint("block_id", "version_no", name="uq_ebv_block_version"),
        CheckConstraint(
            "provenance IN ('imported','ai_draft','human_edited','human_approved')",
            name="ck_ebv_provenance",
        ),
        CheckConstraint("body_format IN ('text','json')", name="ck_ebv_body_format"),
        # A version has to actually carry the body its format declares — a row
        # claiming `json` with an empty `body_json` reads as authored and is not.
        CheckConstraint(
            "(body_format = 'text' AND body_text IS NOT NULL) OR "
            "(body_format = 'json' AND body_json IS NOT NULL)",
            name="ck_ebv_body_matches_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("editorial_blocks.id", ondelete="CASCADE"),
        nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)

    # Several block types are structured objects — routes, drivers, supply and
    # demand splits — not prose that happens to be formatted. `body_format`
    # declares which column is authoritative.
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_json: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    body_format: Mapped[str] = mapped_column(String(8), nullable=False, default="text")

    provenance: Mapped[str] = mapped_column(String(16), nullable=False, default="imported")
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    authored_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    block: Mapped[EditorialBlock] = relationship(
        back_populates="versions", foreign_keys=[block_id],
    )

    @property
    def body(self):
        return self.body_json if self.body_format == "json" else self.body_text
