"""Shared trust + provenance vocabulary (Wave 3, SCRUM-78 / INT-4).

**One module, two consumers.** The editorial blocks (unit 7) and the combo trust
grade both need to say how much a number has been vouched for, and the ticket
asks for a single home so `imported` / `ai_draft` / `human_edited` /
`human_approved` and the combo grade cannot drift into two spellings. Unit 7's
model now imports the provenance half from here rather than defining its own.

The grade answers a different question from either `coverage_tier` column, and
the distinction is why it gets its own field:

    coverage_tier        how well covered is this combo?  (worst retrieval tier)
    proxy_density_tier   how much of it is proxied?       (the drop's P1/P2/P3)
    trust_grade          is this worth a human's time?

Coverage is an *input* to the grade. Storing the grade in `coverage_tier` would
put an input and its own output in the same column.
"""

# ── Provenance: has a person been through this? ───────────────────────────────
PROVENANCE_IMPORTED = "imported"
PROVENANCE_AI_DRAFT = "ai_draft"
PROVENANCE_HUMAN_EDITED = "human_edited"
PROVENANCE_HUMAN_APPROVED = "human_approved"

PROVENANCE_STATES = (
    PROVENANCE_IMPORTED,
    PROVENANCE_AI_DRAFT,
    PROVENANCE_HUMAN_EDITED,
    PROVENANCE_HUMAN_APPROVED,
)

# The state-to-badge mapping lives with the states, because the state machine
# and the customer-facing claim are the same thing. Only `human_approved`
# clears the caveat.
PROVENANCE_BADGES = {
    PROVENANCE_IMPORTED: {
        "label": "Reference data",
        "caveat": "Imported from our reference library; not individually reviewed.",
        "reviewed": False,
    },
    PROVENANCE_AI_DRAFT: {
        "label": "AI draft",
        "caveat": "Machine-drafted and not yet reviewed by an analyst.",
        "reviewed": False,
    },
    PROVENANCE_HUMAN_EDITED: {
        "label": "Analyst edited",
        "caveat": "Edited by an analyst; awaiting sign-off.",
        "reviewed": False,
    },
    PROVENANCE_HUMAN_APPROVED: {
        "label": "Analyst approved",
        "caveat": None,
        "reviewed": True,
    },
}

# ── The combo trust grade ────────────────────────────────────────────────────
#
# Graded rather than boolean on purpose. "Any proxy input means review" would
# put most of the library in the queue in one pass — proxies are a large share
# of the resolution layer, and roughly a quarter of indexed cost weight resolves
# through a single series, so the flags cluster rather than spread. A grade is
# rankable; a flag that is on everywhere is not.
GRADE_HIGH = "high"          # every input resolves to a real series, no proxy, weights closed
GRADE_MEDIUM = "medium"      # resolves, but through a proxy, or weights drift mildly
GRADE_LOW = "low"            # heavy proxy density, or the weight set does not close
GRADE_BLOCKED = "blocked"    # an input resolves to no series at all — cannot be priced
GRADE_UNRATED = "unrated"    # nothing to judge: no lines, or no type-code links yet

TRUST_GRADES = (GRADE_HIGH, GRADE_MEDIUM, GRADE_LOW, GRADE_BLOCKED, GRADE_UNRATED)

# Worst first, for ranking a queue.
GRADE_SEVERITY = {
    GRADE_BLOCKED: 0,
    GRADE_LOW: 1,
    GRADE_UNRATED: 2,
    GRADE_MEDIUM: 3,
    GRADE_HIGH: 4,
}

# Which grades put a combo in front of a human. `medium` deliberately does not:
# a proxy-backed input is a softer signal, not a defect, and queueing every one
# of them is the failure mode described above. `unrated` does not either — there
# is nothing for an expert to look at yet.
GRADES_NEEDING_REVIEW = (GRADE_BLOCKED, GRADE_LOW)

# The customer-facing caveat, keyed by grade. The mockup renders one
# unconditionally for the whole library; this is what replaces it, in both
# directions — an always-true default caveats numbers nobody has questioned, an
# always-false one vouches for numbers nobody has looked at.
GRADE_CAVEATS = {
    GRADE_HIGH: None,
    GRADE_MEDIUM: (
        "Part of this combo's cost lines are priced through a stand-in index. "
        "Treat the should-cost as directional."
    ),
    GRADE_LOW: (
        "This combo's cost-line weights have not been validated against direct "
        "index data. Treat the should-cost as directional pending review."
    ),
    GRADE_BLOCKED: (
        "One or more of this combo's cost lines has no price series behind it, "
        "so the should-cost cannot be computed from data alone."
    ),
    GRADE_UNRATED: (
        "This combo has no priced cost lines yet, so there is nothing to grade."
    ),
}

# Why a grade came out the way it did. Every reason is reported with the
# type-codes or lines behind it — an ungraded "low" tells a reviewer nothing
# about what to go and look at.
REASON_NO_SERIES = "type_code_resolves_to_no_series"
REASON_AMBIGUOUS = "type_code_is_ambiguous"
REASON_NO_TYPE_CODE = "line_has_no_type_code_link"
REASON_PROXY = "priced_through_a_proxy"
REASON_PROXY_DISAGREEMENT = "line_and_type_code_disagree_on_proxy_status"
REASON_WEIGHTS_OPEN = "weight_set_does_not_close"
REASON_NO_LINES = "combo_has_no_cost_lines"

TRUST_REASONS = (
    REASON_NO_SERIES, REASON_AMBIGUOUS, REASON_NO_TYPE_CODE, REASON_PROXY,
    REASON_PROXY_DISAGREEMENT, REASON_WEIGHTS_OPEN, REASON_NO_LINES,
)

# Which proxy_status column the derivation believes. A substantial share of
# indexed cost lines carry a `proxy_status` that contradicts the one on their
# own type-code row, so *something* has to be named as authoritative — and the
# other one's disagreement is reported as its own reason rather than hidden.
PROXY_STATUS_SOURCE = "type_code_registry"
