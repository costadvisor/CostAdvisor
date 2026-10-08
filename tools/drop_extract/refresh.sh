#!/usr/bin/env bash
# Refresh the content drop from Laurent's repo, as it stands (design §1.3, §1.5, §3.3).
#
#   tools/drop_extract/refresh.sh [--repo DIR] [--drop-dir DIR] [--db-file PATH]
#
#   --repo DIR       the source clone (default $CONTENT_SOURCE_REPO, else
#                    $HOME/costadvisor-database)
#   --drop-dir DIR   the drop to replace (default $CONTENT_DROP_DIR, else
#                    <this checkout>/docs/drop_live)
#   --db-file PATH   passed to extract_db.mjs when several database pages match
#
# Steps: Git LFS check of every input -> extract into <drop>.next -> check its
# manifest (no failed or partial constant, a source commit, a clean source
# tree) -> build <drop>.next/deny_list.txt -> swap: <drop> becomes <drop>.prev
# (the previous .prev is removed) and <drop>.next becomes <drop> -> print the
# source commit and the load commands.
#
# It never pulls and never runs `git lfs pull`: both write into Laurent's
# working tree, and pulling is the owner's manual step. It never executes
# anything from the source repo: the extractor only parses it. On any refusal
# the current drop is left untouched (a failed extraction stays in <drop>.next
# for inspection).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
TOP=$(cd "$HERE/../.." && pwd -P)
REPO=${CONTENT_SOURCE_REPO:-$HOME/costadvisor-database}
DROP=${CONTENT_DROP_DIR:-$TOP/docs/drop_live}
DB_FILE=
LFS_PREFIX='version https://git-lfs.github.com/spec/v1'

die() { echo "refresh: $*" >&2; exit 1; }
usage() { sed -n '3,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || usage; REPO=$2; shift 2 ;;
    --repo=*) REPO=${1#*=}; shift ;;
    --drop-dir) [ $# -ge 2 ] || usage; DROP=$2; shift 2 ;;
    --drop-dir=*) DROP=${1#*=}; shift ;;
    --db-file) [ $# -ge 2 ] || usage; DB_FILE=$2; shift 2 ;;
    --db-file=*) DB_FILE=${1#*=}; shift ;;
    -h|--help) usage ;;
    *) echo "refresh: unknown argument $1" >&2; usage ;;
  esac
done

# ── Preconditions ────────────────────────────────────────────────────────────
command -v node >/dev/null || die "node is not installed"
command -v python3 >/dev/null || die "python3 is not installed"
[ -d "$HERE/node_modules/acorn" ] \
  || die "acorn is not installed: run (cd $HERE && npm ci --ignore-scripts) first"
[ -d "$REPO" ] || die "source repo not found: $REPO (pass --repo or set CONTENT_SOURCE_REPO)"
REPO=$(cd "$REPO" && pwd -P)
[ -n "$DROP" ] || die "empty --drop-dir"
DROP_PARENT=$(dirname "$DROP")
[ -d "$DROP_PARENT" ] || die "the drop's parent folder does not exist: $DROP_PARENT"
DROP="$(cd "$DROP_PARENT" && pwd -P)/$(basename "$DROP")"
case "$(basename "$DROP")" in
  ""|.|..|/) die "refusing drop dir $DROP" ;;
esac
case "$DROP/" in
  "$REPO"/*) die "the drop dir $DROP is inside the source repo; never write into it" ;;
esac
# Licensed content must never land in a tracked path.
if git -C "$DROP_PARENT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$DROP_PARENT" check-ignore -q "$DROP" \
    || die "$DROP is inside a git work tree and not ignored; the drop must live in a gitignored folder (docs/)"
fi
NEXT="$DROP.next"
PREV="$DROP.prev"

# ── 1. Git LFS check (before anything is extracted) ──────────────────────────
pointers=()
check_file() {
  local f=$1
  [ -f "$f" ] || return 0
  if [ "$(head -c ${#LFS_PREFIX} -- "$f" 2>/dev/null)" = "$LFS_PREFIX" ]; then
    pointers+=("${f#"$REPO"/}")
  fi
}
shopt -s nullglob
if [ -n "$DB_FILE" ]; then
  check_file "$REPO/$DB_FILE"
else
  for f in "$REPO"/ClaudeSweep/intelligence_mockup*.html; do check_file "$f"; done
fi
check_file "$REPO/ClaudeSweep/indexes_mockup.html"
for f in "$REPO"/ClaudeReports/data/*.json "$REPO"/ClaudeReports/reports/*; do check_file "$f"; done
shopt -u nullglob
if [ ${#pointers[@]} -gt 0 ]; then
  echo "refresh: ${#pointers[@]} input(s) are Git LFS pointer files, not data:" >&2
  printf '  %s\n' "${pointers[@]:0:20}" >&2
  [ ${#pointers[@]} -le 20 ] || echo "  ... and $(( ${#pointers[@]} - 20 )) more" >&2
  echo "LFS pointer: the owner runs \`git -C $REPO lfs pull\`, then retry." >&2
  echo "Nothing was extracted; $DROP is unchanged." >&2
  exit 1
fi

# ── 2. Source (read only; no pull) ───────────────────────────────────────────
git_src() { git --no-optional-locks -C "$REPO" -c core.fsmonitor=false "$@"; }
if git_src rev-parse --git-dir >/dev/null 2>&1; then
  echo "source: $REPO @ $(git_src rev-parse HEAD) ($(git_src log -1 --format=%cI))"
else
  echo "source: $REPO (not a git repo: the drop will name no commit, and the loader will refuse it)"
fi

# ── 3. Extract into <drop>.next ──────────────────────────────────────────────
rm -rf -- "$NEXT"
extract=(node "$HERE/extract_db.mjs" "$REPO" "$NEXT")
[ -z "$DB_FILE" ] || extract+=(--db-file "$DB_FILE")
set +e
"${extract[@]}"
code=$?
set -e
if [ $code -eq 3 ]; then
  die "INCOMPLETE extraction: kept $NEXT for inspection; $DROP is unchanged"
elif [ $code -ne 0 ]; then
  die "extract_db.mjs exited $code; $DROP is unchanged"
fi

# ── 4. Check the manifest ────────────────────────────────────────────────────
NEW_COMMIT=$(python3 -I - "$NEXT" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
problems = []
def load(*parts):
    p = root.joinpath(*parts)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{p}: {exc}")
        return {}
top = load("_manifest.json")
c = top.get("constants") or {}
for key in ("failed", "partial"):
    if c.get(key):
        problems.append(f"constants {key}: {', '.join(c[key])}")
for page in ("raw", "indexes"):
    sub = load(page, "_manifest.json")
    for row in sub.get("extracted") or []:
        if row.get("ok") is False or row.get("complete") is False:
            problems.append(f"{page}: {row.get('name')} failed or partial")
    for block in sub.get("scripts") or []:
        if block.get("parsed") is False:
            problems.append(f"{page}: a script block did not parse")
git = top.get("git") or {}
if not git.get("commit"):
    problems.append("no source commit (git.commit): extract from a clone of the source repo")
if git.get("dirty") is not False:
    problems.append(f"the source tree is not clean (git.dirty = {git.get('dirty')!r}): "
                    "the data would not match the commit")
if problems:
    for p in problems:
        print(f"refresh: {p}", file=sys.stderr)
    sys.exit(1)
print(git["commit"])
PY
) || die "the new drop's manifest is refused (above); kept $NEXT for inspection; $DROP is unchanged"

# ── 5. Deny list for the new drop ────────────────────────────────────────────
python3 -I "$HERE/deny_list.py" build "$NEXT" \
  || die "deny_list.py build failed; kept $NEXT; $DROP is unchanged"

# ── 6. Swap ──────────────────────────────────────────────────────────────────
OLD_COMMIT=
if [ -e "$DROP" ]; then
  OLD_COMMIT=$(python3 -I -c 'import json,sys; print((json.load(open(sys.argv[1])).get("git") or {}).get("commit") or "")' \
    "$DROP/_manifest.json" 2>/dev/null || true)
  rm -rf -- "$PREV"
  mv -- "$DROP" "$PREV"
fi
mv -- "$NEXT" "$DROP"

echo
echo "drop refreshed: $DROP"
echo "  source commit: $NEW_COMMIT"
if [ -n "$OLD_COMMIT" ]; then
  [ "$OLD_COMMIT" = "$NEW_COMMIT" ] && echo "  previous: $OLD_COMMIT (same commit)" \
    || echo "  previous: $OLD_COMMIT (kept in $PREV)"
fi
echo
echo "Next (from backend/; see design §1.3):"
echo "  CONTENT_DROP_DIR=$DROP ./venv/bin/python seed_content_drop.py --dry-run --expect-commit $NEW_COMMIT"
echo "  CONTENT_DROP_DIR=$DROP ./venv/bin/python seed_content_drop.py --expect-commit $NEW_COMMIT"
echo "  CONTENT_DROP_DIR=$DROP ./venv/bin/python seed_content_drop.py --dry-run    # expect 0 changes"
