#!/usr/bin/env python3
"""Deny list: keep licensed drop text out of git.

Standard library only. Run it isolated:  python3 -I deny_list.py ...
It reads our own extraction output (a drop dir such as docs/drop_live) as data.
It never imports or runs anything from the drop or from Laurent's repo.

Two modes:

  build <drop_dir> [--literals-ref REF] [--repo PATH] [--exclude FILE]
      Writes <drop_dir>/deny_list.txt: distinctive strings from the drop.
        1. Quote texts: every `maker_quote` and `quote` value, split into
           sentences; sentences of 40+ characters are kept.
        2. Supplier notes: every `supplierNote`, split the same way.
        3. September test literals: string constants that the September demo
           tests introduced (at REF, against its parent) and that occur in the
           drop. Only distinctive ones are kept: a composite line key
           ("a|||b"), or 3+ words and 20+ characters with no markup.
      Entries listed in the exclude file (one per line, default
      <drop_dir>/../deny_exclude.txt, outside git) are left out. Use it for a
      generic phrase that also appears in our own code.

  check <drop_dir>
      Reads a diff on stdin (for example `git diff origin/dev`). Looks at the
      added lines only. Consecutive added lines are joined, comment markers and
      quote characters dropped, case and spacing normalised, so a sentence
      wrapped over several lines or string literals is still found. Prints each
      hit as file:line and exits 1 on any hit, 0 when clean, 2 on a usage or
      setup error (for example no deny_list.txt: run build first).

The pre-push hook (local, not committed) pipes the outgoing diff into `check`.
A hit is shown to the owner, who decides.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

LIST_NAME = "deny_list.txt"
MIN_SENTENCE = 40          # quote and supplier-note sentences
MIN_LITERAL = 20           # test literals (unless a composite key)
MIN_WORDS = 3
DEFAULT_REF = "refs/local/demo-2026-09-25"
QUOTE_KEYS = {"maker_quote", "quote"}
NOTE_KEYS = {"supplierNote"}
KEY_SEP = "|||"

_QUOTE_CHARS = dict.fromkeys(map(ord, "\"'`‘’‚‛“”„«»"), None)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_WS = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")
_COMMENT_LEAD = re.compile(r"^\s*(?:#+|//+|/\*+|\*+(?!/)|<!--|--|\{/\*)\s?")
_COMMENT_TAIL = re.compile(r"\s*(?:\*/\}?|-->)\s*$")


def normalise(text: str) -> str:
    """Case-folded, quotes dropped, dashes unified, whitespace collapsed."""
    text = text.translate(_QUOTE_CHARS).translate(_DASHES)
    return _WS.sub(" ", text).strip().casefold()


def sentences(text: str):
    for part in _SENTENCE_END.split(text or ""):
        part = part.strip()
        if len(part) >= MIN_SENTENCE:
            yield part


def _walk(node, out_quotes: list, out_notes: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                if k in QUOTE_KEYS:
                    out_quotes.append(v)
                elif k in NOTE_KEYS:
                    out_notes.append(v)
            else:
                _walk(v, out_quotes, out_notes)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out_quotes, out_notes)


def _drop_corpus(drop: Path) -> str:
    parts = []
    for p in sorted(drop.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".html") and p.name != LIST_NAME:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def _string_constants(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {n.value.strip() for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _distinctive(lit: str) -> bool:
    if KEY_SEP in lit:
        return any(len(part.strip()) >= 4 for part in lit.split(KEY_SEP))
    if "<" in lit or "=" in lit:
        return False
    return len(lit) >= MIN_LITERAL and len(lit.split()) >= MIN_WORDS


def september_literals(repo: Path, ref: str, corpus: str) -> list[str]:
    """Distinctive string constants the September tests added that occur in the drop."""
    try:
        _git(repo, "rev-parse", "--verify", "--quiet", ref)
    except subprocess.CalledProcessError:
        print(f"warning: {ref} not found in {repo}; September literals skipped", file=sys.stderr)
        return []
    parent = f"{ref}^"
    changed = []
    for line in _git(repo, "diff", "--name-status", parent, ref, "--", "backend/tests").splitlines():
        status, *paths = line.split("\t")
        if status[:1] in "AM" and paths[-1].endswith(".py"):
            changed.append(paths[-1])
    before: set[str] = set()
    for name in _git(repo, "ls-tree", "-r", "--name-only", parent, "--", "backend/tests").split():
        if name.endswith(".py"):
            before |= _string_constants(_git(repo, "show", f"{parent}:{name}"))
    found = set()
    for name in changed:
        for lit in _string_constants(_git(repo, "show", f"{ref}:{name}")) - before:
            if not _distinctive(lit):
                continue
            # A composite key is matched on its parts too: tests often hold them apart.
            pieces = [lit] + ([p.strip() for p in lit.split(KEY_SEP)] if KEY_SEP in lit else [])
            for piece in pieces:
                if piece and (piece in corpus or json.dumps(piece, ensure_ascii=False)[1:-1] in corpus):
                    if piece == lit or _distinctive(piece):
                        found.add(piece)
    return sorted(found)


def build(drop: Path, ref: str, repo: Path, exclude: Path | None) -> int:
    raw = drop / "raw"
    if not raw.is_dir():
        print(f"error: {raw} not found (is {drop} a drop dir?)", file=sys.stderr)
        return 2
    quotes: list[str] = []
    notes: list[str] = []
    for p in sorted(raw.glob("*.json")):
        try:
            _walk(json.loads(p.read_text(encoding="utf-8")), quotes, notes)
        except json.JSONDecodeError:
            print(f"warning: {p.name} is not JSON; skipped", file=sys.stderr)
    entries: dict[str, str] = {}
    for kind, texts in (("quote", quotes), ("note", notes)):
        for t in texts:
            for s in sentences(t):
                entries.setdefault(normalise(s), kind)
    lits = september_literals(repo, ref, _drop_corpus(drop))
    for lit in lits:
        entries.setdefault(normalise(lit), "literal")
    excluded = 0
    if exclude and exclude.is_file():
        for line in exclude.read_text(encoding="utf-8").splitlines():
            key = normalise(line)
            if key and not line.lstrip().startswith("#") and entries.pop(key, None):
                excluded += 1
    manifest = {}
    try:
        manifest = json.loads((drop / "_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    commit = (manifest.get("git") or {}).get("commit", "unknown")
    counts = {k: sum(1 for v in entries.values() if v == k) for k in ("quote", "note", "literal")}
    out = drop / LIST_NAME
    with out.open("w", encoding="utf-8") as fh:
        fh.write(f"# deny list for drop commit {commit}; normalised entries, one per line\n")
        fh.write(f"# quote sentences {counts['quote']}, note sentences {counts['note']}, "
                 f"test literals {counts['literal']}, excluded {excluded}\n")
        for key in sorted(entries):
            fh.write(key + "\n")
    print(f"wrote {out}: {len(entries)} entries "
          f"(quotes {counts['quote']}, notes {counts['note']}, literals {counts['literal']}, "
          f"excluded {excluded})")
    return 0


def _load(drop: Path) -> list[str]:
    path = drop / LIST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: run `deny_list.py build {drop}` first")
    return [line.rstrip("\n") for line in path.open(encoding="utf-8")
            if line.strip() and not line.startswith("#")]


def _runs(diff_lines):
    """Yield (file, [(new_line_no, text), ...]) for each run of consecutive added lines."""
    file, new_no, run = None, 0, []
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in diff_lines:
        line = line.rstrip("\r\n")
        if line.startswith("diff --git "):
            if run:
                yield file, run
            file, run = None, []
        elif line.startswith("+++ "):
            path = line[4:]
            file = path[2:] if path.startswith("b/") else path
        elif line.startswith("--- "):
            continue
        elif (m := hunk.match(line)):
            if run:
                yield file, run
            run, new_no = [], int(m.group(1))
        elif line.startswith("+"):
            run.append((new_no, line[1:]))
            new_no += 1
        else:
            if run:
                yield file, run
                run = []
            if line.startswith(" "):
                new_no += 1
    if run:
        yield file, run


def _clean_line(text: str) -> str:
    text = _COMMENT_LEAD.sub("", text)
    text = _COMMENT_TAIL.sub("", text)
    return text


def check(drop: Path, stream) -> int:
    try:
        entries = _load(drop)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    prefix_len = 12
    index: dict[str, list[str]] = {}
    for e in entries:
        index.setdefault(e[:prefix_len], []).append(e)
    hits = []
    for file, run in _runs(stream):
        # Build one normalised blob for the run, remembering where each line starts.
        blob, starts = "", []
        for no, text in run:
            piece = normalise(_clean_line(text))
            if not piece:
                continue
            if blob:
                blob += " "
            starts.append((len(blob), no))
            blob += piece
        for i in range(len(blob)):
            cands = index.get(blob[i:i + prefix_len])
            if not cands:
                continue
            for e in cands:
                if blob.startswith(e, i):
                    line_no = max((n for off, n in starts if off <= i), default=run[0][0])
                    hits.append((file, line_no, e))
    for file, line_no, e in hits:
        shown = e if len(e) <= 60 else e[:57] + "..."
        print(f"{file}:{line_no}: drop text: {shown}")
    if hits:
        print(f"deny_list: {len(hits)} hit(s). Licensed drop text must not leave this "
              "machine. Show this to the owner.", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    b = sub.add_parser("build")
    b.add_argument("drop_dir", type=Path)
    b.add_argument("--literals-ref", default=DEFAULT_REF)
    b.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    b.add_argument("--exclude", type=Path, default=None)
    c = sub.add_parser("check")
    c.add_argument("drop_dir", type=Path)
    args = ap.parse_args(argv)
    if args.mode == "build":
        exclude = args.exclude or (args.drop_dir.resolve().parent / "deny_exclude.txt")
        return build(args.drop_dir, args.literals_ref, args.repo, exclude)
    return check(args.drop_dir, sys.stdin)


if __name__ == "__main__":
    sys.exit(main())
