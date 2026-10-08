#!/usr/bin/env python
"""Insert users into a CostAdvisor database before anyone signs in.

    python scripts/ops/provision_user.py --users ~/secure/staging_users.json
    python scripts/ops/provision_user.py --staff alexis@staminachem.com
    python scripts/ops/provision_user.py --users FILE --staff A --staff B --dry-run

The database is `--url`, else `DATABASE_URL`.

**`--users FILE`** re-creates accounts exported from another database
(`SELECT id, email, google_id, display_name, is_super_admin FROM users`). Each
keeps its `id`: seeds and stages look some users up by fixed ids. The file is
JSON (a list of objects, or `{"users": [...]}`) or CSV with a header row. An
optional `created_at` (ISO 8601) is kept too, so account age, which picks the
user platform rows are written under, survives the copy.

**`--staff EMAIL`** (repeatable) creates a super-admin with
`google_id = 'seed:<email>'`. The first Google sign-in with that email links
the account (`routers/auth.py` matches a pre-provisioned user by email and
writes the real Google id).

**Never overwrites.** A user whose email already exists is skipped (compared
case-insensitively), and so is a row whose id or `google_id` is already taken
by another account. Every skip is printed with its reason. Users get no team:
a provisioned user starts with none, as after a fresh sign-up.

One transaction; `--dry-run` rolls it back. Exit 0 when the run finished
(skips included), 2 on a bad file or no database.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

FIELDS = ("id", "email", "google_id", "display_name", "is_super_admin")
STAFF_GOOGLE_PREFIX = "seed:"


class BadInput(ValueError):
    pass


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y")


def read_users(path: Path) -> list[dict]:
    """The users of an export file, validated: every row has an id (UUID), an
    email and a google_id; display_name and is_super_admin are optional."""
    if not path.is_file():
        raise BadInput(f"users file not found: {path}")
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    else:
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
        rows = doc.get("users") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise BadInput(f"{path}: expected a list of users or {{\"users\": [...]}}")
    users = []
    for pos, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise BadInput(f"{path} row {pos}: not an object")
        try:
            uid = uuid.UUID(str(row.get("id")))
        except ValueError:
            raise BadInput(f"{path} row {pos}: id {row.get('id')!r} is not a UUID") from None
        email = (row.get("email") or "").strip()
        google_id = (row.get("google_id") or "").strip()
        if not email or not google_id:
            raise BadInput(f"{path} row {pos}: email and google_id are required")
        created_at = row.get("created_at") or None
        if created_at:
            try:
                datetime.fromisoformat(str(created_at))
            except ValueError:
                raise BadInput(f"{path} row {pos}: created_at {created_at!r} is not ISO 8601") from None
        users.append({
            "id": uid, "email": email, "google_id": google_id,
            "display_name": (row.get("display_name") or None),
            "is_super_admin": _bool(row.get("is_super_admin")),
            "created_at": created_at,
        })
    return users


def staff_user(email: str) -> dict:
    email = email.strip()
    if "@" not in email:
        raise BadInput(f"--staff {email!r} is not an email address")
    return {"id": uuid.uuid4(), "email": email, "google_id": f"{STAFF_GOOGLE_PREFIX}{email}",
            "display_name": email.split("@", 1)[0], "is_super_admin": True, "created_at": None}


def provision(conn, users: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Insert what is missing. Returns (inserted users, [(email, why skipped)])."""
    existing = conn.execute(text("SELECT id, lower(email), google_id FROM users")).all()
    ids = {row[0] for row in existing}
    emails = {row[1] for row in existing}
    google_ids = {row[2] for row in existing}
    inserted, skipped = [], []
    for u in users:
        if u["email"].lower() in emails:
            skipped.append((u["email"], "email already exists"))
            continue
        if u["id"] in ids:
            skipped.append((u["email"], f"id {u['id']} belongs to another account"))
            continue
        if u["google_id"] in google_ids:
            skipped.append((u["email"], "google_id belongs to another account"))
            continue
        conn.execute(text(
            "INSERT INTO users (id, google_id, email, display_name, is_super_admin, created_at) "
            "VALUES (:id, :google_id, :email, :display_name, :is_super_admin, "
            "COALESCE(CAST(:created_at AS timestamptz), clock_timestamp()))"), u)
        ids.add(u["id"])
        emails.add(u["email"].lower())
        google_ids.add(u["google_id"])
        inserted.append(u)
    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="database URL (default: DATABASE_URL)")
    parser.add_argument("--users", type=Path, help="JSON or CSV export of users to re-create")
    parser.add_argument("--staff", action="append", default=[], metavar="EMAIL",
                        help="create a super-admin linked on first Google sign-in (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="do the work, then roll back")
    args = parser.parse_args(argv)

    url = args.url or os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: no database: pass --url or set DATABASE_URL", file=sys.stderr)
        return 2
    if not args.users and not args.staff:
        parser.error("nothing to do: pass --users FILE and/or --staff EMAIL")
    try:
        users = read_users(args.users) if args.users else []
        users += [staff_user(email) for email in args.staff]
    except (BadInput, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            # `users` has no row-level security today; set the bypass anyway so
            # this keeps working if it gains a policy.
            conn.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
            inserted, skipped = provision(conn, users)
            for u in inserted:
                role = "super-admin" if u["is_super_admin"] else "user"
                print(f"  + {u['email']}  ({role}, id {u['id']})")
            for email, why in skipped:
                print(f"  = {email}  skipped: {why}")
            if args.dry_run:
                trans.rollback()
                print(f"DRY RUN: {len(inserted)} to insert, {len(skipped)} skipped; nothing written.")
            else:
                trans.commit()
                print(f"Provisioned {len(inserted)} user(s), {len(skipped)} skipped.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
