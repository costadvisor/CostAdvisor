"""The content drop's source chain (WP-2; design §1.2–§1.5, §3.2, §3.3).

* Reader (`app/services/content_drop/reader.py`): the combo-id repair, the
  region and line-key helpers, the drop's provenance (`manifest()`) and the
  completeness gate (`assert_complete()`, `check_commit()`).
* CLI (`seed_content_drop.py`): `--only`, `--expect-commit`, `--stats`, the
  one-transaction dry run, and the single `content_loads` row a full real run
  writes (a dry run or a partial run writes none).
* Extractor and refresh (`tools/drop_extract/extract_db.mjs`, `refresh.sh`):
  the Git LFS guard, the one-database-page rule, an incomplete extraction
  stopping the loader, and the `.next` → swap → `.prev` refresh.

The tests that read the real drop compare the reader with what the extractor
recorded and with `tests/content_drop_expect.py` (an independent reading of the
same files); no count or text from the drop is written here. The harness tests
use made-up drops in a tmp dir and stub loaders, so they hold whether or not
the real loaders run on this database. Every row a test writes is removed by
the test.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

import seed_content_drop
from app.models.strategy import Playbook
from app.services.content_drop import reader
from app.services.drop.report import LoadReport, TableDiff
from tests import content_drop_expect as expect

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools" / "drop_extract"
EXTRACTOR = TOOLS / "extract_db.mjs"
REFRESH = TOOLS / "refresh.sh"
LFS_POINTER = (
    "version https://git-lfs.github.com/spec/v1\n"
    "oid sha256:" + "0" * 64 + "\nsize 12345\n"
)
# Made up: 40 hex characters, like a real sha.
FAKE_COMMIT = "c0ffee" + "0" * 34

needs_drop = pytest.mark.skipif(not reader.drop_available(),
                                reason="content drop not extracted (set CONTENT_DROP_DIR)")
needs_node = pytest.mark.skipif(
    shutil.which("node") is None or not (TOOLS / "node_modules" / "acorn").is_dir(),
    reason="node, or acorn in tools/drop_extract/node_modules (npm ci --ignore-scripts), missing")


def _rebuild_env():
    """`scripts/ops/rebuild_env.py`, for its parser of this CLI's output."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "rebuild_env.py"
    spec = importlib.util.spec_from_file_location("_rebuild_env_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Made-up drops ────────────────────────────────────────────────────────────

def _write_drop(root: Path, *, commit: str | None = FAKE_COMMIT, failed=(), partial=(),
                rows=None, scripts=None, dirty=False, omit=()) -> Path:
    """The manifests of an extract_db.mjs drop, and nothing else: enough for
    the gate, which runs before any loader reads a data file."""
    git = None if commit is None else {
        "commit": commit, "date": "2026-01-01T12:00:00+02:00", "subject": "made up",
        "branch": "main", "dirty": dirty}
    top = {
        "extractor": "made-up extractor label",
        "extracted_at": "2026-01-02T03:04:05.000Z",
        "git": git,
        "constants": {"database": 1, "indexes": 1, "failed": list(failed), "partial": list(partial)},
    }
    page = {
        "source": "made up", "git": git,
        "extracted": rows if rows is not None else [
            {"name": "ALPHA", "kind": "ObjectExpression", "ok": True, "count": 1}],
        "not_extracted": [],
        "scripts": scripts if scripts is not None else [{"index": 0, "length": 10, "parsed": True}],
    }
    root.mkdir(parents=True, exist_ok=True)
    if "top" not in omit:
        (root / "_manifest.json").write_text(json.dumps(top), encoding="utf-8")
    for sub in ("raw", "indexes"):
        (root / sub).mkdir(exist_ok=True)
        if sub not in omit:
            (root / sub / "_manifest.json").write_text(json.dumps(page), encoding="utf-8")
    return root


@pytest.fixture
def use_drop(monkeypatch):
    """Point the reader at a drop dir for one test; caches reset both ways."""
    def _use(path: Path) -> Path:
        monkeypatch.setenv(reader.CONTENT_DROP_ENV, str(path))
        reader.reset_caches()
        return path
    yield _use
    reader.reset_caches()


@pytest.fixture
def no_database(monkeypatch):
    """Fail the test if the CLI opens a database session."""
    def _refuse():
        raise AssertionError("the CLI opened the database before refusing the drop")
    monkeypatch.setattr(seed_content_drop, "SessionLocal", _refuse)


# ── Reader: keys and codes ───────────────────────────────────────────────────

def test_repair_combo_id_and_region_maps():
    escaped = "TST-AAA-LIQ" + "\\u00b7" + "EU"
    assert reader.repair_combo_id(escaped) == "TST-AAA-LIQ·EU"
    assert reader.repair_combo_id("TST-AAA-LIQ·EU") == "TST-AAA-LIQ·EU"
    assert reader.combo_pid(escaped) == "TST-AAA-LIQ"
    assert reader.app_region("EU") == "Europe"
    assert reader.app_region("GL") == "GLOBAL"
    assert reader.app_region("XX") is None
    assert reader.drop_region("Latam") == "LA"
    assert reader.split_line_key("Family A|||Line B") == ("Family A", "Line B")
    with pytest.raises(ValueError):
        reader.split_line_key("no separator")


def test_missing_drop_dir_fails_loudly(use_drop, tmp_path, no_database, capsys):
    use_drop(tmp_path / "absent")
    assert reader.drop_available() is False
    with pytest.raises(reader.DropNotAvailable):
        reader.raw("FORMULA_COMBOS")
    result = seed_content_drop.run(dry_run=True)
    assert result.code == 2 and not result.reports
    assert reader.CONTENT_DROP_ENV in capsys.readouterr().err
    assert seed_content_drop.main(["--dry-run"]) == 2


# ── Reader: provenance and the completeness gate ─────────────────────────────

def test_manifest_reads_the_provenance(use_drop, tmp_path):
    use_drop(_write_drop(tmp_path / "drop"))
    m = reader.manifest()
    assert m.commit == FAKE_COMMIT
    assert m.date == datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert m.date.tzinfo is not None
    assert m.branch == "main"
    assert m.extractor_version == "made-up extractor label"
    assert m.dirty is False
    assert m.extracted_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert m.drop_dir == tmp_path / "drop"
    assert reader.assert_complete().commit == FAKE_COMMIT


@pytest.mark.parametrize("damage", [
    {"failed": ["ALPHA"]},
    {"partial": ["ALPHA"]},
    {"rows": [{"name": "ALPHA", "kind": "ObjectExpression", "ok": False, "reason": "made up"}]},
    {"rows": [{"name": "ALPHA", "kind": "ObjectExpression", "ok": True, "complete": False}]},
    {"scripts": [{"index": 0, "length": 10, "parsed": False}]},
    {"omit": ("raw",)},
    {"omit": ("indexes",)},
    {"omit": ("top",)},
    {"commit": None},
], ids=["failed-constant", "partial-constant", "page-row-failed", "page-row-partial",
        "script-not-parsed", "no-raw-manifest", "no-indexes-manifest", "no-manifest",
        "no-commit"])
def test_an_incomplete_or_unpinned_drop_is_refused_before_the_database(
        damage, use_drop, tmp_path, no_database, capsys):
    use_drop(_write_drop(tmp_path / "drop", **damage))
    with pytest.raises(reader.DropRefused):
        reader.assert_complete()
    for dry_run in (True, False):
        result = seed_content_drop.run(dry_run=dry_run)
        assert result.code == 2 and result.error and not result.reports
    assert "ERROR:" in capsys.readouterr().err


def test_expect_commit_must_name_the_drops_commit_exactly(use_drop, tmp_path, no_database):
    use_drop(_write_drop(tmp_path / "drop"))
    assert reader.check_commit(FAKE_COMMIT).commit == FAKE_COMMIT
    assert reader.check_commit(FAKE_COMMIT.upper()).commit == FAKE_COMMIT
    assert reader.check_commit(None).commit == FAKE_COMMIT
    other = "f" * 40
    for wrong in (other, FAKE_COMMIT[:8], ""):
        with pytest.raises(reader.DropRefused):
            reader.check_commit(wrong)
        assert seed_content_drop.run(dry_run=True, expect_commit=wrong).code == 2
    assert seed_content_drop.main(["--dry-run", "--expect-commit", other]) == 2


# ── Reader: the real drop ────────────────────────────────────────────────────

@needs_drop
def test_the_drop_in_use_is_complete_and_pinned():
    m = reader.assert_complete()
    assert m.commit == expect.source_commit()
    assert reader.check_commit(expect.source_commit()).commit == m.commit
    with pytest.raises(reader.DropRefused):
        reader.check_commit(m.commit[:8])
    assert m.date is not None and m.date.tzinfo is not None
    assert m.extractor_version
    assert reader.incomplete_reasons() == []


@needs_drop
def test_line_key_of_reads_the_record_level_line():
    combos = reader.raw("FORMULA_COMBOS")
    assert combos
    compared = 0
    for pid in combos:
        authored = expect.record_line_key(pid)
        if authored is not None:
            assert reader.line_key_of(pid) == authored, pid
            assert pid in reader.pids_on_line(authored)
            compared += 1
    assert compared and all(reader.line_key_of(pid) for pid in combos)
    assert reader.line_key_of("NOT-A-PID") is None

    # Every combo id, repaired, is `<its record's pid>·<region>`; the escaped
    # form exists in this drop, so the repair has work to do.
    literal = "\\u00b7"
    escaped = 0
    for pid, rec in combos.items():
        for combo in rec.get("combos") or []:
            escaped += literal in combo["id"]
            fixed = reader.repair_combo_id(combo["id"])
            assert literal not in fixed and "·" in fixed
            assert reader.combo_pid(combo["id"]) == pid
    assert escaped > 0


@needs_drop
def test_reader_reads_what_the_extractor_wrote():
    m = reader.manifest()
    root = m.drop_dir
    checked = 0
    for page, read in (("raw", reader.raw), ("indexes", reader.indexes_file)):
        rows = json.loads((root / page / "_manifest.json").read_text(encoding="utf-8"))["extracted"]
        for row in rows:
            if row.get("ok") and row.get("kind") != "function" and row.get("count") is not None:
                assert len(read(row["name"])) == row["count"], (page, row["name"])
                checked += 1
    assert checked

    delivered = [r for r in reader.report_manifest() if (root / "reports" / r["delivered"]).is_file()]
    assert len(delivered) == m.raw["reports"] == expect.report_counts()["reports"]
    assert len(reader.playbook_files()) == m.raw["playbooks"] == expect.playbook_counts()["playbooks"]
    assert len(reader.axis()["families"]) == len(expect.families())
    assert len(reader.tree()["industries"]) == len(expect.industries())
    assert reader.report_html(delivered[0]["delivered"])
    with pytest.raises(ValueError):
        reader.report_html("../raw/FIDX.json")


def test_loader_identity_is_the_first_super_admin(db):
    has_admin = db.execute(text(
        "SELECT 1 FROM users WHERE is_super_admin AND deleted_at IS NULL LIMIT 1")).scalar()
    if not has_admin:
        with pytest.raises(RuntimeError):
            reader.loader_user_id(db)
        return
    uid = reader.loader_user_id(db)
    first = db.execute(text(
        "SELECT id FROM users WHERE is_super_admin AND deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1")).scalar()
    assert uid == first


# ── CLI harness ──────────────────────────────────────────────────────────────

def test_only_is_validated_and_kept_in_dependency_order():
    assert seed_content_drop.parse_only(None) == list(seed_content_drop.LOADER_ORDER)
    assert seed_content_drop.parse_only("playbooks,taxonomy") == ["taxonomy", "playbooks"]
    with pytest.raises(ValueError):
        seed_content_drop.parse_only("taxonomy,bogus")
    with pytest.raises(SystemExit) as exc:
        seed_content_drop.main(["--only", "bogus"])
    assert exc.value.code == 2


class _StubLoader:
    """Writes one platform row and accounts for it, like a real loader."""

    def __init__(self, slug: str, selects: int = 0, fail: bool = False):
        self.slug = slug
        self.selects = selects
        self.fail = fail

    def load(self, db, report: LoadReport):
        diff = TableDiff("playbooks")
        if db.get(Playbook, self.slug) is None:
            db.add(Playbook(slug=self.slug, name="Harness stub"))
            diff.created += 1
        else:
            diff.unchanged += 1
        for _ in range(self.selects):
            db.execute(text("SELECT 1"))
        report.tables.append(diff)
        if self.fail:
            db.flush()
            raise RuntimeError("stub loader failure")
        return report


class _NoopLoader:
    def load(self, db, report: LoadReport):
        report.tables.append(TableDiff("nothing"))
        return report


@pytest.fixture
def harness(db, use_drop, tmp_path, monkeypatch):
    """A complete made-up drop, stub loaders, and cleanup of every row the
    runs write (the stub's playbook and any content_loads row)."""
    drop = use_drop(_write_drop(tmp_path / "drop"))
    slug = f"test-harness-{uuid.uuid4().hex[:8]}"
    before = db.execute(text("SELECT coalesce(max(id), 0) FROM content_loads")).scalar()
    db.rollback()

    def _stubs(stub, others=None):
        loaders = {"playbooks": stub}
        if others is not None:
            loaders.update({n: others for n in seed_content_drop.LOADER_ORDER if n != "playbooks"})
        monkeypatch.setattr(seed_content_drop, "resolve_loader", lambda name: loaders.get(name))

    def _new_loads():
        db.expire_all()
        rows = db.execute(text(
            "SELECT * FROM content_loads WHERE id > :b ORDER BY id"), {"b": before}).mappings().all()
        db.rollback()
        return rows

    yield {"drop": drop, "slug": slug, "stubs": _stubs, "new_loads": _new_loads}
    db.rollback()
    db.execute(text("DELETE FROM playbooks WHERE slug = :s"), {"s": slug})
    db.execute(text("DELETE FROM content_loads WHERE id > :b AND source_commit = :c"),
               {"b": before, "c": FAKE_COMMIT})
    db.commit()


def test_dry_run_rolls_back_and_a_partial_run_records_no_load(db, harness, capsys):
    from app.database import bypass_rls_var

    harness["stubs"](_StubLoader(harness["slug"]))
    result = seed_content_drop.run(dry_run=True)
    out = capsys.readouterr().out
    assert result.code == 0 and list(result.reports) == ["playbooks"]
    assert result.missing == [n for n in seed_content_drop.LOADER_ORDER if n != "playbooks"]
    assert "[taxonomy] skipped" in out and "DRY RUN" in out and "Committed." not in out
    assert bypass_rls_var.get() is True  # the db fixture's value, restored
    db.expire_all()
    assert db.get(Playbook, harness["slug"]) is None
    assert harness["new_loads"]() == []

    result = seed_content_drop.run(only=["playbooks"])
    out = capsys.readouterr().out
    assert result.code == 0 and result.changes == 1 and result.content_load_id is None
    assert out.rstrip().endswith("Committed.") and "no content_loads row" in out
    db.expire_all()
    assert db.get(Playbook, harness["slug"]) is not None
    assert harness["new_loads"]() == []

    result = seed_content_drop.run(only=["playbooks"])
    out = capsys.readouterr().out
    assert result.changes == 0 and "total changes: 0" in out
    assert _rebuild_env().changes_in(out) == 0


def test_a_full_real_run_records_one_content_load(db, harness, capsys):
    harness["stubs"](_StubLoader(harness["slug"]), others=_NoopLoader())
    first_admin = db.execute(text(
        "SELECT id FROM users WHERE is_super_admin AND deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1")).scalar()
    db.rollback()

    assert seed_content_drop.run(dry_run=True).content_load_id is None
    assert harness["new_loads"]() == []
    capsys.readouterr()

    result = seed_content_drop.run(expect_commit=FAKE_COMMIT)
    out = capsys.readouterr().out
    assert result.code == 0 and result.content_load_id is not None
    assert list(result.reports) == list(seed_content_drop.LOADER_ORDER)
    rows = harness["new_loads"]()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == result.content_load_id
    assert row["source_commit"] == FAKE_COMMIT
    assert row["source_date"] == datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert row["source_branch"] == "main"
    assert row["extractor_version"] == "made-up extractor label"
    assert row["drop_dir"] == str(harness["drop"].resolve())
    assert row["started_at"] <= row["finished_at"]
    assert row["loaded_by"] == first_admin
    assert row["dry_run"] is False
    assert set(row["counts"]) == set(seed_content_drop.LOADER_ORDER)  # JSONB: no key order
    assert row["counts"]["playbooks"]["changes"] == 1
    assert row["counts"]["playbooks"]["tables"]["playbooks"]["created"] == 1
    assert row["notes"]["loaders"] == list(seed_content_drop.LOADER_ORDER)
    assert row["notes"]["source_dirty"] is False

    # The output contract rebuild_env.py parses.
    assert out.rstrip().endswith("Committed.")
    assert f"Recorded content load #{row['id']}" in out
    assert _rebuild_env().changes_in(out) == result.changes == 1

    # Unchanged rerun: 0 changes, and a second record (one per real run).
    again = seed_content_drop.run()
    assert again.changes == 0 and again.content_load_id > row["id"]
    assert len(harness["new_loads"]()) == 2


def test_a_failing_loader_leaves_nothing_behind(db, harness):
    harness["stubs"](_StubLoader(harness["slug"], fail=True), others=_NoopLoader())
    with pytest.raises(RuntimeError, match="stub loader failure"):
        seed_content_drop.run()
    db.expire_all()
    assert db.get(Playbook, harness["slug"]) is None
    assert harness["new_loads"]() == []


def test_a_dirty_source_is_reported_not_refused(db, harness, use_drop, tmp_path, capsys):
    # refresh.sh refuses a dirty source; a drop that got here anyway loads with
    # a warning, and the flag is recorded in content_loads.notes.
    use_drop(_write_drop(tmp_path / "dirty", dirty=True))
    assert reader.assert_complete().dirty is True
    harness["stubs"](_NoopLoader(), others=_NoopLoader())
    result = seed_content_drop.run()
    assert result.code == 0 and result.content_load_id is not None
    assert "WARNING: the source tree had uncommitted changes" in capsys.readouterr().out
    rows = harness["new_loads"]()
    assert len(rows) == 1 and rows[0]["notes"]["source_dirty"] is True


def test_stats_prints_statements_and_time_per_loader(db, harness, capsys):
    harness["stubs"](_StubLoader(harness["slug"], selects=3))
    result = seed_content_drop.run(dry_run=True, stats=True)
    out = capsys.readouterr().out
    stats = result.stats["playbooks"]
    assert stats.statements >= 3 and stats.seconds >= 0
    stat_lines = [ln for ln in out.splitlines() if "statements=" in ln]
    assert any(ln.split()[0] == "playbooks" and "time=" in ln for ln in stat_lines)
    # Stats lines never look like change counts to rebuild_env.py.
    assert not any("changes=" in ln for ln in stat_lines)
    assert _rebuild_env().changes_in(out) == result.changes
    # Without --stats nothing is measured.
    assert seed_content_drop.run(dry_run=True).stats == {}


# ── Extractor and refresh.sh (made-up sources; never Laurent's repo) ─────────

def _fake_source(root: Path, page: str = "<script>const ALPHA = {a: [1, 2], b: 'x'};</script>",
                 pages=("intelligence_mockup (1).html",)) -> Path:
    sweep = root / "ClaudeSweep"
    data = root / "ClaudeReports" / "data"
    reports = root / "ClaudeReports" / "reports"
    for d in (sweep, data, reports):
        d.mkdir(parents=True, exist_ok=True)
    for name in pages:
        (sweep / name).write_text(page, encoding="utf-8")
    (sweep / "indexes_mockup.html").write_text(
        "<script>const INDEXES = [{id: 'made-up'}];</script>", encoding="utf-8")
    for name in ("supply_axis_v3.json", "category_tree.json", "v1_scope.json", "industries_v2.json"):
        (data / name).write_text("{}", encoding="utf-8")
    (reports / "MANIFEST.json").write_text(
        json.dumps({"reports": [{"slug": "made_up", "delivered": "made_up.html"}]}), encoding="utf-8")
    (reports / "made_up.html").write_text("<section>made up</section>", encoding="utf-8")
    (reports / "playbook_made_up_appdata.json").write_text("{}", encoding="utf-8")
    return root


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
         "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        check=True, capture_output=True, text=True).stdout.strip()


def _commit_all(root: Path, message: str = "made up") -> str:
    if not (root / ".git").exists():
        _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _extract(src: Path, out: Path, *extra: str):
    return subprocess.run(["node", str(EXTRACTOR), str(src), str(out), *extra],
                          capture_output=True, text=True, timeout=120)


def _refresh(src: Path, drop: Path, *extra: str):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CONTENT_DROP_DIR", "CONTENT_SOURCE_REPO")}
    return subprocess.run(["bash", str(REFRESH), "--repo", str(src), "--drop-dir", str(drop), *extra],
                          capture_output=True, text=True, timeout=300, env=env)


def _lfs_message(src: Path) -> str:
    return f"LFS pointer: the owner runs `git -C {src.resolve()} lfs pull`, then retry."


@needs_node
def test_extractor_stops_on_an_lfs_pointer_and_writes_nothing(tmp_path):
    src = _fake_source(tmp_path / "src")
    out = tmp_path / "out"
    out.mkdir()
    (out / "sentinel").write_text("kept", encoding="utf-8")
    for pointer in (src / "ClaudeSweep" / "intelligence_mockup (1).html",
                    src / "ClaudeReports" / "reports" / "made_up.html"):
        original = pointer.read_text(encoding="utf-8")
        pointer.write_text(LFS_POINTER, encoding="utf-8")
        proc = _extract(src, out, "--overwrite")
        assert proc.returncode == 1, proc.stderr
        assert _lfs_message(src) in proc.stderr
        assert sorted(p.name for p in out.iterdir()) == ["sentinel"]
        pointer.write_text(original, encoding="utf-8")


@needs_node
def test_extractor_writes_a_pinned_drop_the_loader_accepts(tmp_path, use_drop):
    src = _fake_source(tmp_path / "src")
    commit = _commit_all(src)
    out = tmp_path / "drop"
    proc = _extract(src, out)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((out / "raw" / "ALPHA.json").read_text()) == {"a": [1, 2], "b": "x"}
    use_drop(out)
    m = reader.check_commit(commit)
    assert m.commit == commit and m.branch == "main" and m.dirty is False
    assert reader.raw("ALPHA") == {"a": [1, 2], "b": "x"}
    assert reader.manifest().raw["sources"]["db_page"].endswith("intelligence_mockup (1).html")


@needs_node
def test_an_incomplete_extraction_exits_3_and_the_loader_refuses_it(tmp_path, use_drop, no_database):
    src = _fake_source(tmp_path / "src",
                       page="<script>const ALPHA = {a: 1}; const BETA = {b: madeUpCall()};</script>")
    _commit_all(src)
    out = tmp_path / "drop"
    proc = _extract(src, out)
    assert proc.returncode == 3, proc.stderr
    assert "INCOMPLETE" in proc.stderr
    top = json.loads((out / "_manifest.json").read_text())
    assert top["constants"]["partial"] == ["BETA"]
    use_drop(out)
    with pytest.raises(reader.DropRefused, match="incomplete"):
        reader.assert_complete()
    assert seed_content_drop.run(dry_run=True).code == 2


@needs_node
def test_extractor_needs_exactly_one_database_page(tmp_path):
    src = _fake_source(tmp_path / "src", pages=("intelligence_mockup (1).html",
                                                 "intelligence_mockup (2).html"))
    _commit_all(src)
    proc = _extract(src, tmp_path / "out")
    assert proc.returncode == 1 and "several database pages" in proc.stderr
    assert not (tmp_path / "out").exists()
    proc = _extract(src, tmp_path / "out", "--db-file", "ClaudeSweep/intelligence_mockup (2).html")
    assert proc.returncode == 0, proc.stderr
    top = json.loads((tmp_path / "out" / "_manifest.json").read_text())
    assert top["sources"]["db_page"].endswith("intelligence_mockup (2).html")
    for outside in ("../elsewhere.html", "/etc/hostname"):
        proc = _extract(src, tmp_path / "out2", "--db-file", outside)
        assert proc.returncode == 1 and "outside the source" in proc.stderr


@needs_node
def test_refresh_swaps_in_a_new_commit_and_keeps_the_previous(tmp_path):
    src = _fake_source(tmp_path / "src")
    first = _commit_all(src)
    drop = tmp_path / "docs" / "drop_live"
    drop.parent.mkdir()

    proc = _refresh(src, drop)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((drop / "_manifest.json").read_text())["git"]["commit"] == first
    assert (drop / "deny_list.txt").is_file()
    assert first in proc.stdout and "--expect-commit " + first in proc.stdout
    assert not (tmp_path / "docs" / "drop_live.next").exists()

    (src / "ClaudeSweep" / "intelligence_mockup (1).html").write_text(
        "<script>const ALPHA = {a: [3]};</script>", encoding="utf-8")
    second = _commit_all(src, "made up, again")
    proc = _refresh(src, drop)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((drop / "_manifest.json").read_text())["git"]["commit"] == second
    prev = tmp_path / "docs" / "drop_live.prev"
    assert json.loads((prev / "_manifest.json").read_text())["git"]["commit"] == first
    assert json.loads((drop / "raw" / "ALPHA.json").read_text()) == {"a": [3]}


@needs_node
def test_refresh_refuses_and_leaves_the_drop_untouched(tmp_path):
    src = _fake_source(tmp_path / "src")
    _commit_all(src)
    drop = tmp_path / "docs" / "drop_live"
    drop.mkdir(parents=True)
    (drop / "sentinel").write_text("kept", encoding="utf-8")

    def untouched():
        return sorted(p.name for p in drop.iterdir()) == ["sentinel"]

    # A Git LFS pointer: stops before extracting.
    page = src / "ClaudeSweep" / "intelligence_mockup (1).html"
    original = page.read_text(encoding="utf-8")
    page.write_text(LFS_POINTER, encoding="utf-8")
    proc = _refresh(src, drop)
    assert proc.returncode == 1 and _lfs_message(src) in proc.stderr
    assert untouched() and not (tmp_path / "docs" / "drop_live.next").exists()

    # A dirty source tree: the data would not match the commit.
    page.write_text(original.replace("'x'", "'y'"), encoding="utf-8")
    proc = _refresh(src, drop)
    assert proc.returncode == 1 and "not clean" in proc.stderr
    assert untouched()
    page.write_text(original, encoding="utf-8")

    # An incomplete extraction: kept in .next for inspection, never swapped in.
    page.write_text("<script>const ALPHA = {a: madeUpCall()};</script>", encoding="utf-8")
    _commit_all(src, "made up, broken")
    proc = _refresh(src, drop)
    assert proc.returncode == 1 and "INCOMPLETE" in proc.stderr
    assert untouched() and (tmp_path / "docs" / "drop_live.next").is_dir()

    # Never into the source tree.
    (src / "docs").mkdir()
    proc = _refresh(src, src / "docs" / "drop_live")
    assert proc.returncode == 1 and "inside the source repo" in proc.stderr
    assert not (src / "docs" / "drop_live").exists()
