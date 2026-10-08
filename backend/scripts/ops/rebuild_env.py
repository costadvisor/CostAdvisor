#!/usr/bin/env python
"""Rebuild a CostAdvisor database from nothing: schema, users, reference data,
content. The staging build and every package's scratch database use it
(design §5.6 and §7).

    python scripts/ops/rebuild_env.py reset  --url URL --confirm-db NAME
    python scripts/ops/rebuild_env.py build  --url URL --confirm-db NAME \\
        [--users FILE] [--staff EMAIL ...] \\
        [--drop-dir DIR] [--expect-commit SHA] [--skip-content | --only LOADERS] \\
        [--demo-buyer [--demo-owner EMAIL]]
    python scripts/ops/rebuild_env.py verify --url URL --confirm-db NAME \\
        [--expect-commit SHA] [--compare-url SOURCE_URL]
    python scripts/ops/rebuild_env.py dump    --url URL --confirm-db NAME --out FILE
    python scripts/ops/rebuild_env.py restore --url URL --confirm-db NAME --in FILE

**One database role** (TRIM T1). Everything runs through `--url`: the reset,
the migrations, the seeds and the loads. The role must own the database's
`public` schema (locally `costadvisor` owns the scratch databases it created;
on staging the app connects as `postgres`). There is no app role and no
ownership check yet; that hardening comes later.

**Guard.** Every subcommand refuses to run unless `--confirm-db` equals the
database name in `--url`, and `reset` / `build` refuse while another session
is connected to that database (a running backend would hold locks and see its
tables vanish). The database must exist: create it first
(`createdb -T template0 NAME`).

`reset`
    DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT USAGE TO PUBLIC.
    Every table, row, sequence and the alembic stamp go.

`build`
    1. reset
    2. `alembic heads` must be exactly one head; `alembic upgrade head`
    3. users: `provision_user.py --users FILE --staff EMAIL…` (the content
       loader writes platform rows under the first super-admin, so a content
       build needs one)
    4. reference data: `seed_all.py --stages 1`
    5. content, unless `--skip-content` (`--only` limits the loaders):
       `seed_content_drop.py --dry-run`, the real run (must end "Committed."),
       then `--dry-run` again, which must report 0 changes
    6. `--demo-buyer`: `seed_demo_buyer.py`, then its `--dry-run` = 0 changes
    7. verify

`verify`
    `alembic current` equals the single head; with `--expect-commit`, the
    latest `content_loads` row names that commit; the row counts below are
    printed, and with `--compare-url` every one must equal the source
    database's.

`dump` (design §3.4)
    `PGOPTIONS='-c app.bypass_rls=on' pg_dump --enable-row-security -Fc`.
    The role owns the tables but is not a superuser, so FORCE row-level
    security applies to it: a plain pg_dump stops with "query would be
    affected by row-level security policy". pg_dump writes the RLS flags and
    policies after the data, so a restore's COPY goes through. The file must
    not exist yet and must lie outside this git checkout (it holds licensed
    content). Prints its size and sha256, the alembic revision and the content
    load it carries.

`restore` (design §3.4)
    Refuses unless the `public` schema holds no table, view or sequence (run
    `reset` first) and no other session is connected. Then
    `pg_restore --no-owner --no-privileges --single-transaction
    --exit-on-error`, so a failure leaves the schema empty again, and ANALYZE.
    Objects end up owned by the restoring role. pg_restore may be older than
    the server (a v16 client restoring into PostgreSQL 18 is supported), but
    it must be at least the version of the pg_dump that wrote the file.

Every child process gets `DATABASE_URL` (and `CONTENT_DROP_DIR` from
`--drop-dir`) in its environment, which wins over `backend/.env`. Exit 0 on
success, 1 when a check fails, 2 on bad arguments or a missing database.

Contracts with the other scripts (keep them stable): `seed_content_drop.py`
prints one `<loader> changes=<n>` line per loader in its summary and ends a
real run with "Committed."; `seed_demo_buyer.py` prints `total changes: <n>`
(LoadReport.render). `--expect-commit` is passed through to
`seed_content_drop.py`, `--demo-owner` to `seed_demo_buyer.py --owner-email`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
PROVISION = BACKEND / "scripts" / "ops" / "provision_user.py"


class CheckFailed(RuntimeError):
    pass


class BadArgs(RuntimeError):
    pass


# ── Small helpers ────────────────────────────────────────────────────────────

def _db_name(url: str) -> str:
    return make_url(url).database or ""


def _shown(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def _confirm(url: str, confirm: str) -> None:
    name = _db_name(url)
    if not name:
        raise BadArgs(f"the URL names no database: {_shown(url)}")
    if confirm != name:
        raise BadArgs(f"--confirm-db {confirm!r} does not match the database in --url ({name!r})")


def _engine(url: str):
    return create_engine(url, pool_pre_ping=True)


def _check_reachable(url: str) -> None:
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — report any connection failure plainly
        raise BadArgs(f"cannot connect to {_shown(url)}: {type(exc).__name__}: "
                      f"{str(exc).splitlines()[0]}\n"
                      f"  If the database does not exist yet: createdb -T template0 "
                      f"{_db_name(url)}") from None
    finally:
        engine.dispose()


def _step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def _child_env(url: str, drop_dir: str | None = None) -> dict:
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    if drop_dir:
        env["CONTENT_DROP_DIR"] = drop_dir
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _run(args: list[str], env: dict) -> str:
    """Run a child process from backend/, stream its output, return it.
    Raises CheckFailed on a non-zero exit."""
    print("$ " + " ".join(args[1:] if args[0] == sys.executable else args), flush=True)
    proc = subprocess.Popen([*args], cwd=BACKEND, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    code = proc.wait()
    output = "".join(lines)
    if code != 0:
        label = " ".join(Path(a).name for a in args[1:3]) if args[0] == sys.executable else args[0]
        raise CheckFailed(f"{label} exited {code}")
    return output


_LOADER_CHANGES = re.compile(r"^\s+\S+\s+changes=\s*(\d+)", re.M)
_TOTAL_CHANGES = re.compile(r"^total changes:\s*(\d+)", re.M)


def changes_in(output: str) -> int | None:
    """The change count a loader CLI printed: the sum of its per-loader
    `changes=` lines, else of its `total changes:` lines. None if neither."""
    per_loader = _LOADER_CHANGES.findall(output)
    if per_loader:
        return sum(int(n) for n in per_loader)
    totals = _TOTAL_CHANGES.findall(output)
    if totals:
        return sum(int(n) for n in totals)
    return None


def _other_sessions(url: str) -> int:
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()")).scalar()
    finally:
        engine.dispose()


# ── Alembic ──────────────────────────────────────────────────────────────────

def _alembic_config(url: str):
    from alembic.config import Config
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def alembic_heads(url: str) -> set[str]:
    from alembic.script import ScriptDirectory
    return set(ScriptDirectory.from_config(_alembic_config(url)).get_heads())


def alembic_current(url: str) -> set[str]:
    from alembic.runtime.migration import MigrationContext
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            return set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()


# ── reset ────────────────────────────────────────────────────────────────────

def reset(url: str) -> None:
    _check_reachable(url)
    others = _other_sessions(url)
    if others:
        raise CheckFailed(
            f"{others} other session(s) are connected to {_db_name(url)}; stop them first "
            "(a running backend, a psql shell, a test run)")
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            owner = conn.execute(text(
                "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'"
            )).scalar()
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("GRANT USAGE ON SCHEMA public TO PUBLIC"))
            me = conn.execute(text("SELECT current_user")).scalar()
    finally:
        engine.dispose()
    print(f"reset {_db_name(url)}: schema public dropped and recreated "
          f"(was owned by {owner}, now by {me})")


# ── verify ───────────────────────────────────────────────────────────────────

# (label, SQL). A query returns rows of (detail, count); a scalar count is
# returned with an empty detail. Every query runs with the RLS bypass on.
COUNT_QUERIES: list[tuple[str, str]] = [
    ("users", "SELECT '', count(*) FROM users"),
    ("teams", "SELECT '', count(*) FROM teams"),
    ("regions", "SELECT '', count(*) FROM regions"),
    ("chemical_families", "SELECT '', count(*) FROM chemical_families"),
    ("subfamilies", "SELECT '', count(*) FROM subfamilies"),
    ("product_lines", "SELECT '', count(*) FROM product_lines"),
    ("formula_templates platform",
     "SELECT card_kind || ' / ' || coalesce(supply_status, '-'), count(*) "
     "FROM formula_templates WHERE team_id IS NULL GROUP BY 1"),
    ("formula_templates platform without line",
     "SELECT '', count(*) FROM formula_templates WHERE team_id IS NULL AND product_line_id IS NULL"),
    ("formula_templates team", "SELECT '', count(*) FROM formula_templates WHERE team_id IS NOT NULL"),
    ("formula_region_coverage", "SELECT '', count(*) FROM formula_region_coverage"),
    ("formula_template_components", "SELECT '', count(*) FROM formula_template_components"),
    ("industries", "SELECT '', count(*) FROM industries"),
    ("categories", "SELECT '', count(*) FROM categories"),
    ("category_shared", "SELECT '', count(*) FROM category_shared"),
    ("industry_out", "SELECT '', count(*) FROM industry_out"),
    ("category_placements", "SELECT '', count(*) FROM category_placements"),
    ("editorial_blocks platform",
     "SELECT block_type, count(*) FROM editorial_blocks WHERE team_id IS NULL GROUP BY 1"),
    ("editorial_block_versions", "SELECT '', count(*) FROM editorial_block_versions"),
    ("producers", "SELECT '', count(*) FROM producers"),
    ("producer_formulas", "SELECT '', count(*) FROM producer_formulas"),
    ("producer_formulas counting",
     "SELECT '', count(*) FROM producer_formulas WHERE counts_toward_floor"),
    ("dimension_terms", "SELECT '', count(*) FROM dimension_terms"),
    ("dimension_assertions", "SELECT '', count(*) FROM dimension_assertions"),
    ("market_reports", "SELECT '', count(*) FROM market_reports"),
    ("market_report_sections", "SELECT '', count(*) FROM market_report_sections"),
    ("market_report_panels", "SELECT '', count(*) FROM market_report_panels"),
    ("market_report_lines", "SELECT '', count(*) FROM market_report_lines"),
    ("playbooks", "SELECT '', count(*) FROM playbooks"),
    ("playbook_levers", "SELECT '', count(*) FROM playbook_levers"),
    ("playbook_objectives", "SELECT '', count(*) FROM playbook_objectives"),
    ("commodity_indexes", "SELECT '', count(*) FROM commodity_indexes"),
    ("index_monthly_values", "SELECT '', count(*) FROM index_monthly_values"),
    ("type_codes", "SELECT '', count(*) FROM type_codes"),
    ("content_loads", "SELECT '', count(*) FROM content_loads"),
]


def row_counts(url: str) -> dict[str, int | str]:
    """label → count (or the error text when the table is missing)."""
    out: dict[str, int | str] = {}
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            conn.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
            for label, sql in COUNT_QUERIES:
                nested = conn.begin_nested()
                try:
                    rows = conn.execute(text(sql)).all()
                    nested.commit()
                except Exception as exc:  # noqa: BLE001 — one missing table must not hide the rest
                    nested.rollback()
                    out[label] = f"error: {str(exc).splitlines()[0]}"
                    continue
                for detail, n in rows:
                    out[f"{label} · {detail}" if detail else label] = int(n)
            trans.rollback()
    finally:
        engine.dispose()
    return out


def latest_content_load(url: str) -> dict | None:
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, source_commit, finished_at FROM content_loads "
                "ORDER BY id DESC LIMIT 1")).mappings().first()
            return dict(row) if row else None
    finally:
        engine.dispose()


def verify(url: str, expect_commit: str | None = None, compare_url: str | None = None) -> None:
    _check_reachable(url)
    failures: list[str] = []

    heads, current = alembic_heads(url), alembic_current(url)
    if len(heads) != 1:
        failures.append(f"alembic has {len(heads)} heads: {sorted(heads)}")
    if current != heads:
        failures.append(f"alembic current {sorted(current) or '(empty)'} is not head {sorted(heads)}")
    else:
        print(f"alembic current = head ({', '.join(sorted(heads))})")

    if expect_commit:
        load = latest_content_load(url)
        if load is None:
            failures.append("no content_loads row; expected one for " + expect_commit)
        elif load["source_commit"] != expect_commit:
            failures.append(f"latest content load is {load['source_commit']}, expected {expect_commit}")
        else:
            print(f"latest content load #{load['id']} = {expect_commit}")

    counts = row_counts(url)
    source = row_counts(compare_url) if compare_url else None
    width = max(len(k) for k in counts)
    print(f"\nrow counts in {_db_name(url)}" + (f" vs {_db_name(compare_url)}" if compare_url else ""))
    for label in sorted(set(counts) | set(source or {})):
        here = counts.get(label, 0)
        if source is None:
            print(f"  {label:<{width}}  {here}")
            continue
        there = source.get(label, 0)
        mark = "" if here == there else "   <-- differs"
        print(f"  {label:<{width}}  {here}  {there}{mark}")
        if here != there:
            failures.append(f"{label}: {here} here, {there} in the source")
    errors = [f"{k}: {v}" for k, v in counts.items() if isinstance(v, str)]
    failures += errors

    if failures:
        raise CheckFailed("verify failed:\n  " + "\n  ".join(failures))
    print("\nverify passed")


# ── dump / restore ───────────────────────────────────────────────────────────

def _libpq_env(url: str, *, bypass_rls: bool = False) -> dict:
    """The environment that points pg_dump / pg_restore at `url` without
    putting the password on a command line."""
    u = make_url(url)
    env = dict(os.environ)
    for key in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGOPTIONS",
                "PGSSLMODE", "PGSERVICE"):
        env.pop(key, None)
    env["PGHOST"] = u.host or "localhost"
    env["PGPORT"] = str(u.port or 5432)
    if u.username:
        env["PGUSER"] = u.username
    if u.password is not None:
        env["PGPASSWORD"] = str(u.password)
    env["PGDATABASE"] = u.database or ""
    sslmode = u.query.get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = sslmode if isinstance(sslmode, str) else sslmode[0]
    if bypass_rls:
        env["PGOPTIONS"] = "-c app.bypass_rls=on"
    return env


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise BadArgs(f"{name} is not on PATH (install the PostgreSQL client tools)")
    return path


def _tool_major(path: str) -> int:
    out = subprocess.run([path, "--version"], capture_output=True, text=True, check=True).stdout
    m = re.search(r"\)\s+(\d+)", out) or re.search(r"(\d+)(?:\.\d+)?", out)
    return int(m.group(1))


def _server(url: str) -> tuple[int, str]:
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            num = int(conn.execute(text("SHOW server_version_num")).scalar())
            full = conn.execute(text("SHOW server_version")).scalar()
    finally:
        engine.dispose()
    return num // 10000, full


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO.resolve())
        return True
    except ValueError:
        return False


def public_objects(url: str) -> dict[str, int]:
    """Tables, views and sequences in the `public` schema."""
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT count(*) FILTER (WHERE c.relkind IN ('r', 'p')), "
                "       count(*) FILTER (WHERE c.relkind IN ('v', 'm')), "
                "       count(*) FILTER (WHERE c.relkind = 'S') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public'")).one()
    finally:
        engine.dispose()
    return {"tables": int(row[0]), "views": int(row[1]), "sequences": int(row[2])}


def dump(url: str, out: str) -> None:
    _check_reachable(url)
    target = Path(out).expanduser()
    if _inside_repo(target):
        raise BadArgs(f"{target} is inside the git checkout {REPO}; a dump holds licensed "
                      "content: write it outside (for example ~/secure/)")
    if target.exists():
        raise BadArgs(f"{target} already exists; remove it or pick another name")
    if not target.parent.is_dir():
        raise BadArgs(f"no such directory: {target.parent}")
    pg_dump = _tool("pg_dump")
    server_major, server_full = _server(url)
    client_major = _tool_major(pg_dump)
    print(f"server PostgreSQL {server_full}; pg_dump {client_major}")
    if client_major < server_major:
        raise BadArgs(f"pg_dump {client_major} cannot dump a PostgreSQL {server_major} server; "
                      f"use pg_dump {server_major} or later")
    heads, current = alembic_heads(url), alembic_current(url)
    if current != heads:
        raise CheckFailed(f"alembic current {sorted(current) or '(empty)'} is not head "
                          f"{sorted(heads)}; dump a built database")
    load = latest_content_load(url)
    started = time.monotonic()
    cmd = [pg_dump, "--enable-row-security", "--format=custom", "--no-password",
           "--file", str(target)]
    print("$ PGOPTIONS='-c app.bypass_rls=on' " + " ".join(Path(c).name if i == 0 else c
                                                           for i, c in enumerate(cmd)))
    proc = subprocess.run(cmd, env=_libpq_env(url, bypass_rls=True),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        target.unlink(missing_ok=True)
        raise CheckFailed(f"pg_dump exited {proc.returncode}: {proc.stderr.strip()[:2000]}")
    target.chmod(0o600)
    toc = _toc(target)
    data = sum(1 for line in toc if " TABLE DATA " in line)
    print(f"dumped {_db_name(url)} → {target} in {time.monotonic() - started:.1f} s")
    print(f"  size    {target.stat().st_size:,} bytes")
    print(f"  sha256  {_sha256(target)}")
    print(f"  alembic {', '.join(sorted(current))}")
    print(f"  content load " + (f"#{load['id']} = {load['source_commit']}" if load else "(none)"))
    print(f"  {data} table data entries")


def _toc(path: Path) -> list[str]:
    proc = subprocess.run([_tool("pg_restore"), "--list", str(path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise BadArgs(f"{path} is not a readable custom-format dump: {proc.stderr.strip()[:500]}")
    return [line for line in proc.stdout.splitlines() if line and not line.startswith(";")]


def restore(url: str, infile: str) -> None:
    source = Path(infile).expanduser()
    if not source.is_file():
        raise BadArgs(f"dump not found: {source}")
    _check_reachable(url)
    others = _other_sessions(url)
    if others:
        raise CheckFailed(
            f"{others} other session(s) are connected to {_db_name(url)}; stop them first "
            "(a running backend or worker, a psql shell)")
    objects = public_objects(url)
    if any(objects.values()):
        raise CheckFailed(
            f"schema public in {_db_name(url)} is not empty ({objects['tables']} tables, "
            f"{objects['views']} views, {objects['sequences']} sequences); run `reset` first")
    pg_restore = _tool("pg_restore")
    server_major, server_full = _server(url)
    client_major = _tool_major(pg_restore)
    print(f"server PostgreSQL {server_full}; pg_restore {client_major}")
    toc = _toc(source)
    # The dump of a reset database may carry its own `CREATE SCHEMA public`
    # (the recreated schema is not the default one). `reset` already made it,
    # and --exit-on-error would stop on "already exists": leave that entry out.
    skip = [line for line in toc if re.search(r"\bSCHEMA\s+-\s+public\b", line)]
    cmd = [pg_restore, "--no-owner", "--no-privileges", "--single-transaction",
           "--exit-on-error", "--no-password", "--dbname", _db_name(url)]
    listfile = None
    if skip:
        listfile = source.with_name(source.name + f".{os.getpid()}.list")
        listfile.write_text("\n".join(line for line in toc if line not in skip) + "\n",
                            encoding="utf-8")
        cmd += ["--use-list", str(listfile)]
        print(f"leaving out {len(skip)} entry(ies) for the public schema itself (reset made it)")
    cmd.append(str(source))
    started = time.monotonic()
    print("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
    try:
        proc = subprocess.run(cmd, env=_libpq_env(url), capture_output=True, text=True)
    finally:
        if listfile:
            listfile.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise CheckFailed(f"pg_restore exited {proc.returncode} (nothing was kept: one "
                          f"transaction): {proc.stderr.strip()[:2000]}")
    engine = _engine(url)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("ANALYZE"))
    finally:
        engine.dispose()
    objects = public_objects(url)
    print(f"restored {source.name} into {_db_name(url)} in {time.monotonic() - started:.1f} s "
          f"({objects['tables']} tables, {objects['sequences']} sequences); ANALYZE done")


# ── build ────────────────────────────────────────────────────────────────────

def _manifest_commit(drop_dir: str) -> str | None:
    path = Path(drop_dir) / "_manifest.json"
    if not path.is_file():
        raise BadArgs(f"no _manifest.json in {drop_dir}")
    with path.open(encoding="utf-8") as fh:
        return (json.load(fh).get("git") or {}).get("commit")


def build(args) -> None:
    url = args.url
    content = not args.skip_content
    if content:
        if not args.drop_dir:
            raise BadArgs("a content build needs --drop-dir (or pass --skip-content)")
        if not (args.users or args.staff):
            raise BadArgs("a content build needs a super-admin: pass --staff EMAIL or --users FILE")
        commit = _manifest_commit(args.drop_dir)
        if args.expect_commit and commit != args.expect_commit:
            raise BadArgs(f"the drop at {args.drop_dir} is {commit}, not --expect-commit "
                          f"{args.expect_commit}")
    if args.demo_buyer and (not content or args.only):
        raise BadArgs("--demo-buyer needs the full content load (no --skip-content, no --only)")
    if args.users and not Path(args.users).is_file():
        raise BadArgs(f"users file not found: {args.users}")
    env = _child_env(url, args.drop_dir)
    py = sys.executable

    _step(f"1. reset {_db_name(url)}")
    reset(url)

    _step("2. migrate")
    heads = alembic_heads(url)
    if len(heads) != 1:
        raise CheckFailed(f"alembic has {len(heads)} heads: {sorted(heads)}")
    print(f"single head: {next(iter(heads))}")
    _run([py, "-m", "alembic", "upgrade", "head"], env)

    _step("3. users")
    if args.users or args.staff:
        cmd = [py, str(PROVISION)]
        if args.users:
            cmd += ["--users", str(args.users)]
        for email in args.staff:
            cmd += ["--staff", email]
        _run(cmd, env)
    else:
        print("no --users / --staff: no user provisioned")

    _step("4. reference data")
    _run([py, "seed_all.py", "--stages", "1"], env)

    if content:
        loader = [py, "seed_content_drop.py"]
        if args.only:
            loader += ["--only", args.only]
        if args.expect_commit:
            loader += ["--expect-commit", args.expect_commit]
        _step("5a. content: dry run")
        _run([*loader, "--dry-run"], env)
        _step("5b. content: load")
        out = _run(loader, env)
        if not re.search(r"^Committed\.\s*$", out, re.M):
            raise CheckFailed("the content load did not end with 'Committed.'")
        _step("5c. content: dry run again (must change nothing)")
        out = _run([*loader, "--dry-run"], env)
        n = changes_in(out)
        if n is None:
            raise CheckFailed("could not read a change count from the second dry run")
        if n:
            raise CheckFailed(f"the second dry run reports {n} change(s); expected 0")
        print("second dry run: 0 changes")
    else:
        _step("5. content: skipped (--skip-content)")

    if args.demo_buyer:
        buyer = [py, "seed_demo_buyer.py"]
        if args.demo_owner:
            buyer += ["--owner-email", args.demo_owner]
        _step("6a. demo buyer")
        _run(buyer, env)
        _step("6b. demo buyer: dry run (must change nothing)")
        out = _run([*buyer, "--dry-run"], env)
        n = changes_in(out)
        if n is None:
            raise CheckFailed("could not read a change count from the demo buyer dry run")
        if n:
            raise CheckFailed(f"the demo buyer dry run reports {n} change(s); expected 0")
        print("demo buyer dry run: 0 changes")

    _step("7. verify")
    # A partial load (--only) may not record a content load; only a full one
    # is held to the expected commit.
    verify(url, expect_commit=args.expect_commit if content and not args.only else None)
    print(f"\nbuild of {_db_name(url)} complete")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--url", required=True, help="database URL (the one role, T1)")
        p.add_argument("--confirm-db", required=True, metavar="NAME",
                       help="must equal the database name in --url")

    p_reset = sub.add_parser("reset", help="drop and recreate the public schema")
    common(p_reset)

    p_build = sub.add_parser("build", help="reset, migrate, users, reference data, content, verify")
    common(p_build)
    p_build.add_argument("--users", help="JSON or CSV users export (provision_user.py)")
    p_build.add_argument("--staff", action="append", default=[], metavar="EMAIL",
                         help="super-admin linked on first Google sign-in (repeatable)")
    p_build.add_argument("--drop-dir", help="the extracted content drop (CONTENT_DROP_DIR)")
    p_build.add_argument("--expect-commit", metavar="SHA",
                         help="refuse a drop extracted from another commit")
    group = p_build.add_mutually_exclusive_group()
    group.add_argument("--skip-content", action="store_true", help="stop after the reference seed")
    group.add_argument("--only", metavar="NAME[,NAME]", help="run only these content loaders")
    p_build.add_argument("--demo-buyer", action="store_true", help="seed the demo buyer team")
    p_build.add_argument("--demo-owner", metavar="EMAIL", help="the demo team's owner")

    p_verify = sub.add_parser("verify", help="alembic head, content commit, row counts")
    common(p_verify)
    p_verify.add_argument("--expect-commit", metavar="SHA")
    p_verify.add_argument("--compare-url", help="source database whose counts must match")

    p_dump = sub.add_parser("dump", help="pg_dump -Fc with the RLS bypass (design §3.4)")
    common(p_dump)
    p_dump.add_argument("--out", required=True, metavar="FILE",
                        help="new file outside the git checkout")

    p_restore = sub.add_parser("restore", help="pg_restore into an empty schema (after reset)")
    common(p_restore)
    p_restore.add_argument("--in", dest="infile", required=True, metavar="FILE")

    args = parser.parse_args(argv)
    try:
        _confirm(args.url, args.confirm_db)
        print(f"database: {_shown(args.url)}", flush=True)
        if args.command == "reset":
            reset(args.url)
        elif args.command == "build":
            build(args)
        elif args.command == "verify":
            verify(args.url, expect_commit=args.expect_commit, compare_url=args.compare_url)
        elif args.command == "dump":
            dump(args.url, args.out)
        elif args.command == "restore":
            restore(args.url, args.infile)
    except BadArgs as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except CheckFailed as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
