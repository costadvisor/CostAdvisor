"""The supply taxonomy contract (design §4.2): `/api/taxonomy`.

Shapes for `routers/taxonomy.py`; page builders code against
`docs/api/taxonomy.md`. Counts are counts of cards (listed catalogue
templates on a line that is not hidden); `default_view` counts the ones the
Products grid opens on. No count of suppliers appears here.
"""
from pydantic import BaseModel


class Flag(BaseModel):
    code: str
    label: str


class CardCounts(BaseModel):
    listed: int
    default_view: int


class SubfamilyCounts(CardCounts):
    lines: int


class FamilyCounts(SubfamilyCounts):
    subfamilies: int


class TreeCounts(FamilyCounts):
    families: int


class TreeLine(BaseModel):
    id: int
    name: str
    platform: str | None = None
    in_v1_scope: bool
    has_report: bool
    flags: list[Flag]
    counts: CardCounts


class TreeSubfamily(BaseModel):
    # null only for a bucket of lines with no sub-family (none today)
    id: int | None = None
    # null for the deliberately unnamed node of a family
    name: str | None = None
    counts: SubfamilyCounts
    lines: list[TreeLine]


class TreeFamily(BaseModel):
    id: int
    name: str
    counts: FamilyCounts
    subfamilies: list[TreeSubfamily]


class TaxonomyOut(BaseModel):
    counts: TreeCounts
    families: list[TreeFamily]
    # cards with no product line ("Product line not yet published")
    unplaced: CardCounts


class FamilyItem(BaseModel):
    id: int
    name: str
    counts: FamilyCounts


class FamilyListOut(BaseModel):
    total: int
    items: list[FamilyItem]


class IdName(BaseModel):
    id: int
    name: str


class SubfamilyRef(BaseModel):
    id: int
    name: str | None = None


class PickerLine(BaseModel):
    id: int
    name: str
    line_key: str
    family: IdName
    subfamily: SubfamilyRef | None = None
    platform: str | None = None
    former_names: list[str]
    counts: CardCounts


class LinePickerOut(BaseModel):
    total: int
    items: list[PickerLine]
