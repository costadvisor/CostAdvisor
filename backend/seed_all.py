"""
seed_all.py — one command to fully populate a CostAdvisor database.

Run (fresh DB):
    cd backend && alembic upgrade head && python seed_all.py
    python seed_all.py --stages 1        # platform reference data only

On Railway:
    cd /app && python seed_all.py

Idempotent: safe to re-run. Every stage upserts or skips rows that already
exist, so a second run creates nothing.

Stages run in dependency order — this order matters:
  1. Platform reference data — regions, commodity indexes,
     index values, system scenarios          (app.seed.seed + seed_update)
  2. Users, teams, team memberships           (inline, exact dev UUIDs)
     jil@staminachem.com (13099867…) and team 6ee41dc2… MUST be created here,
     because stages 3 and 4 look them up by those exact ids.
  3. Shadow formula library — +29 commodity indexes + 42 platform templates
     (seed_shadow_library.run — raises SystemExit if jil@ is missing, hence >2)
  4. Staminachem demo — products, suppliers, cost models, prices, volumes
     (seed_staminachem.run — hardcodes team 6ee41dc2… + jil as created_by)
"""
import uuid

from app.database import SessionLocal, bypass_rls_var
from app.models import User, Team, TeamMembership, Plan

# ── Real (non-test) identities mirrored from the dev database ────────────────
# Only the genuine accounts are seeded; pytest `test-*` fixtures are excluded.
# The first row (jil) and team "Jil Varghese's Team" are load-bearing: the
# shadow-library and staminachem seeds resolve them by these exact ids.

# (id, google_id, email, display_name, is_super_admin, company)
USERS = [
    ("13099867-d73e-400b-8f76-b557ad5c05e5", "102376091324909423268",
     "jil@staminachem.com", "Jil Varghese Palliyan", True, None),
    ("8ff9291d-a320-4386-b165-21fce344645a", "104678450264048384166",
     "jvp.placement.rep@gmail.com", "JVP", True, None),
    ("667994fc-6f31-4475-a9d2-2425041ee79e", "110126955129822734409",
     "tve23cs073@cet.ac.in", "Student", False, None),
    ("234d94b0-8f0a-4420-a218-52198af2e964", "108088942146167519718",
     "jilvarghese.2005@gmail.com", "JIL VARGHESE", False, "UNISYNC"),
    ("d277d221-e87d-4072-936b-84caf252dc6a", "seed:alexander@staminachem.com",
     "alexander@staminachem.com", "Alexander", True, None),
]

# (id, name, created_by) — plan is resolved by name at seed time (see below):
# the "Free" plan's UUID is assigned by the RBAC migration and can differ per DB,
# so hardcoding its id here would risk an FK violation on a fresh database.
DEFAULT_PLAN_NAME = "Free"
TEAMS = [
    ("6ee41dc2-bd26-4a50-8589-f601c54a335d", "Jil Varghese's Team",
     "13099867-d73e-400b-8f76-b557ad5c05e5"),
    ("d3710731-c16b-44ca-a00c-b49c78cfe460", "Test Team4",
     "13099867-d73e-400b-8f76-b557ad5c05e5"),
    ("b3ebbd6f-03cc-430b-b5c1-905928285ed5", "JIL VARGHESE PALLIYAN's Team",
     "8ff9291d-a320-4386-b165-21fce344645a"),
    ("449a2651-93ba-40c5-b1bc-e679363c4e20", "HEY",
     "8ff9291d-a320-4386-b165-21fce344645a"),
    ("709cdc8e-834f-4f28-9008-3f49d437102b", "New Team",
     "13099867-d73e-400b-8f76-b557ad5c05e5"),
]

# (user_id, team_id, role) — owner / admin / member
MEMBERSHIPS = [
    ("13099867-d73e-400b-8f76-b557ad5c05e5", "6ee41dc2-bd26-4a50-8589-f601c54a335d", "owner"),
    ("13099867-d73e-400b-8f76-b557ad5c05e5", "d3710731-c16b-44ca-a00c-b49c78cfe460", "owner"),
    ("8ff9291d-a320-4386-b165-21fce344645a", "b3ebbd6f-03cc-430b-b5c1-905928285ed5", "owner"),
    ("8ff9291d-a320-4386-b165-21fce344645a", "6ee41dc2-bd26-4a50-8589-f601c54a335d", "admin"),
    ("13099867-d73e-400b-8f76-b557ad5c05e5", "709cdc8e-834f-4f28-9008-3f49d437102b", "owner"),
    ("8ff9291d-a320-4386-b165-21fce344645a", "709cdc8e-834f-4f28-9008-3f49d437102b", "member"),
    ("667994fc-6f31-4475-a9d2-2425041ee79e", "6ee41dc2-bd26-4a50-8589-f601c54a335d", "member"),
    ("667994fc-6f31-4475-a9d2-2425041ee79e", "709cdc8e-834f-4f28-9008-3f49d437102b", "admin"),
    ("d277d221-e87d-4072-936b-84caf252dc6a", "6ee41dc2-bd26-4a50-8589-f601c54a335d", "admin"),
]


def seed_identities():
    """Insert the real users/teams/memberships if missing. Idempotent."""
    # Platform-level writes with no per-request user context — bypass RLS. The
    # contextvar form is used (not raw set_config) so the after_begin listener
    # re-applies the bypass on every transaction the later seed stages open too.
    bypass_rls_var.set(True)
    db = SessionLocal()
    try:
        plan = db.query(Plan).filter(Plan.name == DEFAULT_PLAN_NAME).first()
        plan_id = plan.id if plan else None

        created_u = 0
        for uid, gid, email, name, is_sa, company in USERS:
            if db.get(User, uuid.UUID(uid)):
                continue
            db.add(User(id=uuid.UUID(uid), google_id=gid, email=email,
                        display_name=name, is_super_admin=is_sa, company=company))
            created_u += 1
        db.flush()  # users must exist before teams (teams.created_by FK -> users.id)

        created_t = 0
        for tid, name, created_by in TEAMS:
            if db.get(Team, uuid.UUID(tid)):
                continue
            db.add(Team(id=uuid.UUID(tid), name=name,
                        created_by=uuid.UUID(created_by), plan_id=plan_id))
            created_t += 1
        db.flush()  # teams must exist before memberships (team_memberships FK)

        created_m = 0
        for user_id, team_id, role in MEMBERSHIPS:
            if db.get(TeamMembership, (uuid.UUID(user_id), uuid.UUID(team_id))):
                continue
            db.add(TeamMembership(user_id=uuid.UUID(user_id),
                                  team_id=uuid.UUID(team_id), role=role))
            created_m += 1

        db.commit()
        print(f"  users +{created_u}, teams +{created_t}, memberships +{created_m} "
              f"(plan='{DEFAULT_PLAN_NAME}' resolved to {plan_id})")
    finally:
        db.close()


def _stage(n, title, fn):
    print(f"\n=== Stage {n}: {title} ===")
    try:
        fn()
    except SystemExit as e:
        # seed_shadow_library raises SystemExit when its seed user is missing.
        print(f"!!! Stage {n} ({title}) aborted: {e}")
        raise
    except Exception as e:  # noqa: BLE001 — surface the failing stage, then re-raise
        print(f"!!! Stage {n} ({title}) FAILED: {e!r}")
        raise


def _stage_1():
    from app import seed as app_seed
    app_seed.seed()
    app_seed.seed_update()


def _stage_3():
    import seed_shadow_library
    seed_shadow_library.run()


def _stage_4():
    import seed_staminachem
    seed_staminachem.run()


# Imports stay inside each stage, so `--stages 1` loads no demo seed module.
STAGES = {
    1: ("platform reference data", _stage_1),
    2: ("users / teams / memberships", seed_identities),
    3: ("shadow formula library", _stage_3),
    4: ("staminachem demo data", _stage_4),
}


def parse_stages(value: str | None) -> list[int]:
    """`"1,2"` → `[1, 2]`, kept in dependency order. None means every stage."""
    if not value:
        return sorted(STAGES)
    try:
        wanted = {int(part) for part in value.split(",") if part.strip()}
    except ValueError:
        raise SystemExit(f"--stages takes stage numbers, e.g. 1,2 (got {value!r})")
    unknown = wanted - set(STAGES)
    if unknown or not wanted:
        raise SystemExit(f"--stages: unknown stage(s) {sorted(unknown)}; "
                         f"choose from {sorted(STAGES)}")
    return sorted(wanted)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Populate a CostAdvisor database.")
    parser.add_argument("--stages", help="comma list of stages to run (default: all). "
                        "1 = platform reference data only.")
    args = parser.parse_args(argv)

    for n in parse_stages(args.stages):
        title, fn = STAGES[n]
        _stage(n, title, fn)

    print("\n=== seed_all complete ===")


if __name__ == "__main__":
    main()
