# Context Router Jev-Only Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the router into core/shadow/parser modules, add a `.env` contract, add `start`/`check`/`packet` commands, and document the Jev context workflow.

**Architecture:** `scripts/context_router.py` keeps indexing/search/prior-task logic plus the new local commands and the `.env` loader; `scripts/shadow.py` owns everything remote (Jev policy, questions, cache keys, HTTP); `scripts/parser.py` owns argparse construction and dispatch. `shadow.py` imports core one-way; core never imports shadow at module level; the CLI shim in `context_router.py` imports parser lazily under `__main__`.

**Tech Stack:** Python 3.11+ stdlib only, unittest + mock (existing suite style).

## Global Constraints

- Python 3.11+; stdlib only; no new dependencies.
- Pinned remote model `jev-1.13.0`; `POLICY_VERSION = "context-router-v2-task-history"`; `ENDPOINT = "https://api.typesafe.ai/v1/systemone"` (move verbatim into `shadow.py`).
- Remote consent rule unchanged: `--allow-remote` required; key alone never consents.
- Index/cache files mode `0600`, atomic replace, symlink refusal (existing `write_jsonl` behavior).
- No code comments unless required; match existing style (type hints, no docstrings on private helpers beyond module docstring).
- Never commit `.env` or echo the key.

---

### Task 1: Module split (shadow.py, parser.py)

**Files:**
- Create: `scripts/shadow.py`
- Create: `scripts/parser.py`
- Modify: `scripts/context_router.py` (remove remote code, CLI construction, `main`; keep `__main__` shim)
- Modify: `tests/test_context_router.py` (module loading + references)

**Interfaces:**
- Consumes: existing `build_index`, `search`, `related_tasks`, `read_jsonl`, `write_jsonl` stay in `context_router.py` with unchanged signatures.
- Produces: `shadow.MODEL`, `shadow.POLICY_VERSION`, `shadow.ENDPOINT`, `shadow.QUESTIONS`, `shadow.THRESHOLDS`, `shadow._NoRedirectHandler`, `shadow._cache_key(model, task, role, owned_paths, candidate) -> str`, `shadow._ask_jev(task, role, owned_paths, candidate, model, api_key, timeout) -> dict`, `shadow._validate_evaluation(value, expected_model=None) -> dict`, `shadow._route(answers) -> str`, `shadow.shadow(records, task, role, cache_path, limit=12, owned_paths=None, model=shadow.MODEL, timeout=30.0, task_records_only=False) -> list[dict]`; `parser.build_parser() -> argparse.ArgumentParser`, `parser.main(argv=None) -> int`.

- [ ] **Step 1: Update tests to load three modules and run to verify failure**

Replace the module-loading header of `tests/test_context_router.py` (everything before `class FakeResponse`) with:

```python
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import context_router as router
import shadow
import parser as cli
```

Change these references inside existing tests (behavior identical, new homes):
- `router.QUESTIONS` -> `shadow.QUESTIONS` (3 occurrences: lines ~98, 122, 169, 210)
- `router.MODEL` -> `shadow.MODEL`
- `router.ENDPOINT` -> `shadow.ENDPOINT`
- `router.shadow(` -> `shadow.shadow(`
- `router._cache_key(` -> `shadow._cache_key(`
- `router._ask_jev(` -> `shadow._ask_jev(`
- `router._NoRedirectHandler` -> `shadow._NoRedirectHandler`
- `router._parser()` -> `cli.build_parser()`
- `router.urllib.request` -> `shadow.urllib.request` (in `mock.patch.object` targets), and `router.urllib.error.HTTPError` -> `shadow.urllib.error.HTTPError`

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'shadow'`

- [ ] **Step 2: Create scripts/shadow.py**

Move verbatim from `context_router.py`: the `MODEL`, `POLICY_VERSION`, `ENDPOINT` constants; `QUESTIONS`, `THRESHOLDS`; `_NoRedirectHandler`; `_cache_key`; `_ask_jev`; `_validate_evaluation`; `_route`; `shadow`. Add one-way core imports. Full file:

```python
#!/usr/bin/env python3
"""Explicit, consent-gated Jev shadow scoring over a local context shortlist."""

import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from context_router import read_jsonl, search, write_jsonl

MODEL = "jev-1.13.0"
POLICY_VERSION = "context-router-v2-task-history"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = { ... }      # move the existing dict verbatim
THRESHOLDS = { ... }     # move the existing dict verbatim


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a TypeSafe bearer token to a redirected origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
```

(then `_cache_key`, `_ask_jev`, `_validate_evaluation`, `_route`, `shadow` bodies copied unchanged from `context_router.py` lines 434-597; `shadow()` keeps per-candidate `write_jsonl` cache writes and reads `api_key = os.environ.get("TYPESAFE_API_KEY")`.)

- [ ] **Step 3: Create scripts/parser.py**

```python
#!/usr/bin/env python3
"""Argument parsing and dispatch for the context-router CLI."""

import argparse
import json
import sys
from pathlib import Path

import context_router as core
import shadow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=core.__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    env_parent = argparse.ArgumentParser(add_help=False)
    env_parent.add_argument("--env", type=Path, help="Path to a .env file (default: ./.env when present)")

    index = subparsers.add_parser("index", parents=[env_parent], help="Build a deterministic local JSONL index")
    index.add_argument("--project", type=Path, required=True)
    index.add_argument("--out", type=Path, required=True)
    index.add_argument("--root", action="append")
    index.add_argument("--chunk-lines", type=int, default=80)

    search_parser = subparsers.add_parser("search", parents=[env_parent], help="Search an existing index locally")
    search_parser.add_argument("--index", type=Path, required=True)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=20)

    task_search = subparsers.add_parser("task-search", parents=[env_parent], help="Find related previous task records")
    task_search.add_argument("--index", type=Path, required=True)
    task_search.add_argument("--query", required=True)
    task_search.add_argument("--limit", type=int, default=8)
    task_search.add_argument("--matches-per-task", type=int, default=3)

    shadow_parser = subparsers.add_parser("shadow", parents=[env_parent], help="Score a local shortlist with Jev")
    shadow_parser.add_argument("--index", type=Path, required=True)
    shadow_parser.add_argument("--task", required=True)
    shadow_parser.add_argument("--role", default="coordinator")
    shadow_parser.add_argument("--owned-path", action="append", default=[])
    shadow_parser.add_argument("--cache", type=Path, required=True)
    shadow_parser.add_argument("--limit", type=int, default=12)
    shadow_parser.add_argument("--model", default=shadow.MODEL)
    shadow_parser.add_argument("--timeout", type=float, default=30.0)
    shadow_parser.add_argument("--previous-tasks-only", action="store_true", help="Score only indexed previous-task records")
    shadow_parser.add_argument("--allow-remote", action="store_true", required=True, help="Explicitly consent to sending shortlisted project context to TypeSafe")
    return parser


def _dispatch(args) -> object:
    if args.command == "index":
        records = core.build_index(args.project, args.root, args.chunk_lines)
        core.write_jsonl(args.out, records)
        return {"index": str(args.out), "chunks": len(records), "files": len({r["path"] for r in records})}
    if args.command == "search":
        return core.search(core.read_jsonl(args.index), args.query, args.limit)
    if args.command == "task-search":
        return core.related_tasks(core.read_jsonl(args.index), args.query, args.limit, args.matches_per_task)
    return shadow.shadow(
        core.read_jsonl(args.index),
        args.task,
        args.role,
        args.cache,
        args.limit,
        args.owned_path,
        args.model,
        args.timeout,
        args.previous_tasks_only,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _dispatch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"context-router: {error}", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Slim scripts/context_router.py**

Delete: `MODEL`, `POLICY_VERSION`, `ENDPOINT`, `QUESTIONS`, `THRESHOLDS`, `_NoRedirectHandler`, `_cache_key`, `_ask_jev`, `_validate_evaluation`, `_route`, `shadow`, `_parser`, `main`, and now-unused imports (`os` stays if `write_jsonl` uses it — it does; `urllib.error`/`urllib.request` imports go; `argparse`/`sys` go). Replace the `if __name__ == "__main__":` block with:

```python
if __name__ == "__main__":
    import parser

    raise SystemExit(parser.main())
```

- [ ] **Step 5: Run tests and smoke the CLI**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -8`
Expected: OK (all existing tests pass, including `test_shadow_cli_requires_explicit_remote_consent` via `cli.build_parser`).

Run: `python3 scripts/context_router.py search --help > /dev/null && echo ok`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add scripts/shadow.py scripts/parser.py scripts/context_router.py tests/test_context_router.py
git commit -m "refactor: split shadow scoring and CLI parser into modules"
```

---

### Task 2: Behavior-preserving cleanup

**Files:**
- Modify: `scripts/context_router.py` (`_kind`, `_files`, `search`)
- Modify: `tests/test_context_router.py` (kind-mapping test)

**Interfaces:**
- Produces: `_validate_record(record: dict) -> None` in `context_router.py` (called once per `search()` invocation set, not per record in the ranking loop — validate all records up front, then rank).

- [ ] **Step 1: Write the failing test**

Add to `ContextRouterTests`:

```python
    def test_kind_mapping_is_unambiguous(self):
        kinds = {
            "data.json": router._kind(Path("data.json")),
            "data.jsonc": router._kind(Path("data.jsonc")),
            "conf.yaml": router._kind(Path("conf.yaml")),
            "scene.tscn": router._kind(Path("scene.tscn")),
            "main.gd": router._kind(Path("main.gd")),
            "notes.md": router._kind(Path("notes.md")),
            "package.json": router._kind(Path("package.json")),
        }
        self.assertEqual(kinds, {
            "data.json": "data",
            "data.jsonc": "data",
            "conf.yaml": "configuration",
            "scene.tscn": "configuration",
            "main.gd": "code",
            "notes.md": "markdown",
            "package.json": "configuration",
        })
```

Run: `python3 -m unittest tests.test_context_router.ContextRouterTests.test_kind_mapping_is_unambiguous -v`
Expected: PASS already? Verify: current `_kind` returns `data` for `.json`/`.jsonc` (configuration set lacks them) and `configuration` for `.yaml/.yml` — so this test documents the mapping and must PASS before the fix; it guards the cleanup. If it fails, fix `_kind` to match this table (the spec's mapping is authoritative).

- [ ] **Step 2: Apply the cleanup edits**

In `_kind`, collapse the two dict membership checks into one explicit ordered mapping (same results):

```python
def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if path.name in {"project.godot", "package.json"}:
        return "configuration"
    if suffix == ".json" or suffix == ".jsonc":
        return "data"
    if suffix in {".cfg", ".ini", ".toml", ".yaml", ".yml", ".tscn", ".tres"}:
        return "configuration"
    if suffix in {".gd", ".cs", ".gdshader", ".go", ".java", ".js", ".jsx", ".kt", ".php", ".py", ".rb", ".rs", ".sh", ".ts", ".tsx"}:
        return "code"
    return "text"
```

In `_files`, parenthesize the filter:

```python
            if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in ROOT_FILES):
                continue
```

In `search`, hoist validation out of the loop:

```python
def _validate_record(record: dict) -> None:
    required = {
        "id": str,
        "path": str,
        "kind": str,
        "line_start": int,
        "line_end": int,
        "content_hash": str,
        "text": str,
    }
    if any(not isinstance(record.get(key), expected) for key, expected in required.items()):
        raise ValueError("Context index contains a malformed record")
    if not isinstance(record.get("heading", ""), str):
        raise ValueError("Context index contains a malformed heading")
```

`search` becomes: build `query_terms`, then `for record in records:` first calls `_validate_record(record)` (respecting the existing `task_records_only` filter order is not required — validate every record), then score. The per-record `raise ValueError("Context index contains a malformed record")` and `"malformed heading"` messages stay identical so `test_malformed_cache_is_rejected_cleanly` and `test_malformed_heading_is_rejected_cleanly` keep passing.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add scripts/context_router.py tests/test_context_router.py
git commit -m "refactor: explicit kind mapping, filter parens, hoisted record validation"
```

---

### Task 3: .env loader, kill-switch, and .env.example

**Files:**
- Modify: `scripts/context_router.py` (add `load_env_file`)
- Modify: `scripts/shadow.py` (kill-switch check)
- Modify: `scripts/parser.py` (apply env before dispatch, validate toggle)
- Create: `.env.example`
- Modify: `.gitignore`
- Create: `.env` (untracked, operator key)
- Modify: `tests/test_context_router.py`

**Interfaces:**
- Produces: `core.load_env_file(path: Path) -> dict[str, str]` (never touches os.environ). Parser contract: values from the file are applied with `os.environ.setdefault`; `CONTEXT_ROUTER_JEV` must be `0` or `1` when set; `shadow.shadow` raises `RuntimeError("remote Jev disabled by CONTEXT_ROUTER_JEV=0")` when the variable is `0`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_load_env_file_parses_ignoring_comments_and_blank_lines(self):
        env_file = self.project / ".env"
        env_file.write_text("# comment\n\nTYPESAFE_API_KEY=abc123\nCONTEXT_ROUTER_JEV=0\nQUOTED=\"x y\"\n")
        values = router.load_env_file(env_file)
        self.assertEqual(values, {"TYPESAFE_API_KEY": "abc123", "CONTEXT_ROUTER_JEV": "0", "QUOTED": "x y"})

    def test_load_env_file_rejects_malformed_lines(self):
        env_file = self.project / ".env"
        env_file.write_text("GOOD=1\nNOT_A_PAIR\n")
        with self.assertRaisesRegex(ValueError, "malformed"):
            router.load_env_file(env_file)

    def test_jev_kill_switch_blocks_shadow_even_with_key_and_consent(self):
        records = router.build_index(self.project, chunk_lines=10)
        opener = mock.Mock()
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret", "CONTEXT_ROUTER_JEV": "0"}), mock.patch.object(
            shadow.urllib.request, "build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(RuntimeError, "CONTEXT_ROUTER_JEV=0"):
                shadow.shadow(records, "shop cancellation", "tdd", self.project / "cache.jsonl", limit=1)
        opener.open.assert_not_called()

    def test_parser_rejects_invalid_jev_toggle(self):
        with mock.patch.dict(os.environ, {"CONTEXT_ROUTER_JEV": "maybe"}):
            self.assertEqual(
                cli.main(["search", "--index", str(self.project / "i.jsonl"), "--query", "x"]), 2
            )
```

Run: `python3 -m unittest ... -v` for the four tests.
Expected: FAIL — `load_env_file` missing; kill-switch text missing; toggle not validated.

- [ ] **Step 2: Implement**

In `context_router.py` add:

```python
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            raise ValueError(f"Malformed .env entry at {path}:{line_no}")
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values
```

In `shadow.shadow`, immediately after the docstring-less signature locals (`owned_paths = owned_paths or []`):

```python
    if os.environ.get("CONTEXT_ROUTER_JEV", "1") == "0":
        raise RuntimeError("remote Jev disabled by CONTEXT_ROUTER_JEV=0")
```

In `parser.main`, between `parse_args` and `_dispatch`:

```python
    env_path = args.env if getattr(args, "env", None) else Path(".env")
    if env_path.is_file():
        for key, value in core.load_env_file(env_path).items():
            os.environ.setdefault(key, value)
    toggle = os.environ.get("CONTEXT_ROUTER_JEV", "1")
    if toggle not in {"0", "1"}:
        print("context-router: CONTEXT_ROUTER_JEV must be 0 or 1", file=sys.stderr)
        return 2
```

Add `import os` to `parser.py`.

- [ ] **Step 3: Create .env.example and .gitignore entry; write local .env**

`.env.example`:

```
# Jev remote scoring (https://api.typesafe.ai). Copy to .env and fill in.
# A key authorizes authentication only; --allow-remote is still required consent.
TYPESAFE_API_KEY=
# Set to 0 to hard-disable remote shadow scoring. Default is 1.
CONTEXT_ROUTER_JEV=1
```

`.gitignore` gains:

```
.env
```

Local untracked `.env` (never committed):

```
TYPESAFE_API_KEY=ses_f3ca53da1ffe6Erj4k1NaJzJCd
CONTEXT_ROUTER_JEV=1
```

- [ ] **Step 4: Run full suite and verify .env is ignored**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5` — Expected: OK
Run: `git check-ignore .env && git status --porcelain | grep -c '^.. \.env$'; true`
Expected: `.env` printed by check-ignore; grep count `0` (or no output).

- [ ] **Step 5: Commit**

```bash
git add scripts/context_router.py scripts/shadow.py scripts/parser.py .env.example .gitignore tests/test_context_router.py
git commit -m "feat: .env contract with TYPESAFE_API_KEY and CONTEXT_ROUTER_JEV kill-switch"
```

---

### Task 4: start command (scaffold, scan, guide, optional index)

**Files:**
- Modify: `scripts/context_router.py` (add `start_partition`)
- Modify: `scripts/parser.py` (add subcommand)
- Modify: `tests/test_context_router.py`

**Interfaces:**
- Produces: `core.start_partition(project: Path, task_id: str, index_out: Path | None = None, roots: list[str] | None = None) -> dict` returning `{"created": [...], "skipped": [...], "existing_prior_tasks": [...], "guidance": [...], "index": {...} | None}`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_start_scaffolds_task_partition_without_overwriting(self):
        report = router.start_partition(self.project, "2026-09-23-shop-fix")
        created = {Path(p).as_posix() for p in report["created"]}
        self.assertIn("tasks/2026-09-23-shop-fix/spec.md", created)
        self.assertIn("tasks/2026-09-23-shop-fix/plan.md", created)
        self.assertIn("tasks/2026-09-23-shop-fix/evidence.md", created)
        self.assertIn("architecture/README.md", created)
        self.assertTrue(report["guidance"])
        self.assertEqual(report["skipped"], [])
        second = router.start_partition(self.project, "2026-09-23-shop-fix")
        self.assertEqual(set(second["created"]), set())
        self.assertEqual(len(second["skipped"]), 4)

    def test_start_rejects_unsafe_task_id(self):
        for bad in ("../escape", "a/b", "", "."):
            with self.assertRaises(ValueError):
                router.start_partition(self.project, bad)

    def test_start_scans_existing_prior_task_records(self):
        (self.project / "specs").mkdir()
        (self.project / "specs/old-thing-design.md").write_text("# Old\n")
        (self.project / "docs/superpowers/plans").mkdir(parents=True)
        (self.project / "docs/superpowers/plans/2026-08-01-x.md").write_text("# P\n")
        report = router.start_partition(self.project, "new-task")
        self.assertIn("specs/old-thing-design.md", report["existing_prior_tasks"])
        self.assertIn("docs/superpowers/plans/2026-08-01-x.md", report["existing_prior_tasks"])
        self.assertIn("know-how/tasks/2026-09-01-shop-cancellation", report["existing_prior_tasks"])

    def test_start_with_index_builds_loadable_index(self):
        out = self.project / "private-index.jsonl"
        report = router.start_partition(self.project, "idx-task", index_out=out)
        self.assertEqual(report["index"]["chunks"], len(router.read_jsonl(out)))
```

Run: expected FAIL — `start_partition` missing.

- [ ] **Step 2: Implement `start_partition` in context_router.py**

```python
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_TASK_SCAFFOLDS = {
    "spec.md": "# Spec: {task_id}\n\n## Behavior\n\n## Boundaries\n\n## Acceptance\n\n",
    "plan.md": "# Plan: {task_id}\n\n## Increments\n\n## Ownership\n\n## Evidence\n\n",
    "evidence.md": "# Evidence: {task_id}\n\n## Commands\n\n## Results\n\n",
}

_ARCHITECTURE_SCAFFOLD = (
    "# Architecture\n\nRecord decisions, ownership boundaries and invariants here.\n\n"
    "## Decisions\n\n## Invariants\n\n"
)

_SCAN_DIRS = {"tasks", "specs", "plans"}


def _scan_prior_tasks(project: Path) -> list[str]:
    found: set[str] = set()
    for path in sorted(project.rglob("*")):
        if not path.is_dir() or path.is_symlink() or path.name not in _SCAN_DIRS:
            continue
        if path.name == "tasks":
            found.update(
                child.relative_to(project).as_posix()
                for child in sorted(path.iterdir())
                if child.is_dir() and not child.is_symlink()
            )
        else:
            found.update(
                child.relative_to(project).as_posix()
                for child in sorted(path.glob("*.md"))
                if child.is_file() and not child.is_symlink()
            )
    return sorted(found)


def start_partition(project: Path, task_id: str, index_out: Path | None = None, roots: list[str] | None = None) -> dict:
    project = project.resolve()
    if not project.is_dir():
        raise ValueError(f"Not a project directory: {project}")
    if not task_id or not _TASK_ID.fullmatch(task_id) or task_id in {".", ".."}:
        raise ValueError(f"Unsafe task id: {task_id!r}")
    created: list[str] = []
    skipped: list[str] = []
    task_dir = project / "tasks" / task_id
    for name, template in _TASK_SCAFFOLDS.items():
        path = task_dir / name
        if path.exists():
            skipped.append(path.relative_to(project).as_posix())
            continue
        task_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(template.format(task_id=task_id), encoding="utf-8")
        created.append(path.relative_to(project).as_posix())
    architecture = project / "architecture" / "README.md"
    if architecture.exists():
        skipped.append(architecture.relative_to(project).as_posix())
    else:
        architecture.parent.mkdir(parents=True, exist_ok=True)
        architecture.write_text(_ARCHITECTURE_SCAFFOLD, encoding="utf-8")
        created.append(architecture.relative_to(project).as_posix())
    guidance = [
        "Partition still-binding prior-task facts (decisions, failed approaches, migration constraints, unfinished acceptance criteria) into the new task artifacts; record which prior task supplied each fact.",
        f"Record architectural decisions and invariants in {architecture.relative_to(project).as_posix()}; current instructions and live code stay authoritative over history.",
        "Run task-search before loading non-kernel documents; open only the exact supporting sections.",
    ]
    index_report = None
    if index_out is not None:
        records = build_index(project, roots)
        write_jsonl(index_out, records)
        index_report = {"index": str(index_out), "chunks": len(records), "files": len({r["path"] for r in records})}
    return {
        "created": created,
        "skipped": skipped,
        "existing_prior_tasks": _scan_prior_tasks(project),
        "guidance": guidance,
        "index": index_report,
    }
```

- [ ] **Step 3: Wire the subcommand in parser.py**

Inside `build_parser`, after `shadow_parser`:

```python
    start = subparsers.add_parser("start", parents=[env_parent], help="Scaffold the task partition and report prior-task records")
    start.add_argument("--project", type=Path, required=True)
    start.add_argument("--task-id", default=datetime.date.today().isoformat() + "-untitled")
    start.add_argument("--index", type=Path)
    start.add_argument("--root", action="append")
```

Add `import datetime` to `parser.py`. Extend `_dispatch`:

```python
    if args.command == "start":
        return core.start_partition(args.project, args.task_id, args.index, args.root)
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add scripts/context_router.py scripts/parser.py tests/test_context_router.py
git commit -m "feat: start command scaffolds task partition with prior-task scan"
```

---

### Task 5: check command (index staleness)

**Files:**
- Modify: `scripts/context_router.py` (add `check_staleness`)
- Modify: `scripts/parser.py`
- Modify: `tests/test_context_router.py`

**Interfaces:**
- Produces: `core.check_staleness(project: Path, index_path: Path, roots: list[str] | None = None) -> dict` returning `{"stale": bool, "added": [...], "removed": [...], "changed": [...], "indexed_files": int, "current_files": int}`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_check_detects_added_changed_removed_and_fresh(self):
        out = self.project / "private-index.jsonl"
        router.write_jsonl(out, router.build_index(self.project, chunk_lines=10))
        fresh = router.check_staleness(self.project, out)
        self.assertFalse(fresh["stale"])
        self.assertEqual((fresh["added"], fresh["removed"], fresh["changed"]), ([], [], []))
        (self.project / "survivor/shop/service.gd").write_text("extends Node\n\nfunc cancel_service():\n    remaining_uses = 0\n")
        new_file = self.project / "know-how/NEW.md"
        new_file.write_text("# New\n")
        (self.project / "know-how/ARCHITECTURE.md").unlink()
        report = router.check_staleness(self.project, out)
        self.assertTrue(report["stale"])
        self.assertIn("know-how/NEW.md", report["added"])
        self.assertIn("know-how/ARCHITECTURE.md", report["removed"])
        self.assertIn("survivor/shop/service.gd", report["changed"])

    def test_check_with_empty_index_reports_everything_added(self):
        out = self.project / "empty.jsonl"
        out.write_text("")
        report = router.check_staleness(self.project, out)
        self.assertTrue(report["stale"])
        self.assertEqual(report["indexed_files"], 0)
        self.assertTrue(report["added"])
```

Run: expected FAIL — `check_staleness` missing.

- [ ] **Step 2: Implement in context_router.py**

```python
def check_staleness(project: Path, index_path: Path, roots: list[str] | None = None) -> dict:
    project = project.resolve()
    indexed: dict[str, list[str]] = {}
    for record in read_jsonl(index_path):
        indexed.setdefault(record["path"], []).append(record["content_hash"])
    current: dict[str, list[str]] = {}
    for path in _files(project, _roots(project, roots)):
        relative = path.relative_to(project).as_posix()
        current[relative] = [
            hashlib.sha256(chunk["text"].encode()).hexdigest()
            for chunk in _chunks(project, path, 80)
        ]
    added = sorted(set(current) - set(indexed))
    removed = sorted(set(indexed) - set(current))
    changed = sorted(
        path for path in set(indexed) & set(current) if sorted(indexed[path]) != sorted(current[path])
    )
    return {
        "stale": bool(added or removed or changed),
        "added": added,
        "removed": removed,
        "changed": changed,
        "indexed_files": len(indexed),
        "current_files": len(current),
    }
```

Note: `check` re-chunks with the default `chunk_lines=80` (same default as `index`); chunk-line mismatch surfaces as `changed`, which is correct-by-definition for staleness reporting.

- [ ] **Step 3: Wire the subcommand in parser.py**

```python
    check = subparsers.add_parser("check", parents=[env_parent], help="Report index staleness against the current tree")
    check.add_argument("--project", type=Path, required=True)
    check.add_argument("--index", type=Path, required=True)
    check.add_argument("--root", action="append")
```

```python
    if args.command == "check":
        return core.check_staleness(args.project, args.index, args.root)
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add scripts/context_router.py scripts/parser.py tests/test_context_router.py
git commit -m "feat: check command reports index staleness deterministically"
```

---

### Task 6: packet command (worker-packet skeleton)

**Files:**
- Modify: `scripts/context_router.py` (add `build_packet`)
- Modify: `scripts/parser.py`
- Modify: `tests/test_context_router.py`

**Interfaces:**
- Produces: `core.build_packet(records: list[dict], task: str, role: str = "coordinator", owned_paths: list[str] | None = None, limit: int = 12, task_limit: int = 8, matches_per_task: int = 3) -> dict` returning `{"task", "role", "owned_paths", "acceptance": [], "facets": [...], "prior_tasks": [...], "provenance_note": str}`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_packet_assembles_facets_and_prior_tasks_without_network(self):
        records = router.build_index(self.project, chunk_lines=10)
        packet = router.build_packet(records, "shop cancellation service use", "tdd", ["survivor/shop"])
        self.assertEqual(packet["task"], "shop cancellation service use")
        self.assertEqual(packet["role"], "tdd")
        self.assertEqual(packet["owned_paths"], ["survivor/shop"])
        self.assertEqual(packet["acceptance"], [])
        self.assertTrue(packet["facets"])
        facet = packet["facets"][0]
        self.assertEqual(set(facet), {"path", "heading", "line_start", "line_end", "score", "reasons"})
        self.assertTrue(packet["prior_tasks"])
        self.assertEqual(packet["prior_tasks"][0]["task_id"], "2026-09-01-shop-cancellation")
        self.assertNotIn("text", packet["facets"][0])
        self.assertIn("provenance", packet["provenance_note"].lower())

    def test_packet_on_empty_index_returns_empty_sections(self):
        packet = router.build_packet([], "anything")
        self.assertEqual(packet["facets"], [])
        self.assertEqual(packet["prior_tasks"], [])
```

Run: expected FAIL — `build_packet` missing.

- [ ] **Step 2: Implement in context_router.py**

```python
_PROVENANCE_NOTE = (
    "Record which prior task and artifact supplied each historical constraint; "
    "authoritative repository instructions and live code win conflicts."
)


def build_packet(
    records: list[dict],
    task: str,
    role: str = "coordinator",
    owned_paths: list[str] | None = None,
    limit: int = 12,
    task_limit: int = 8,
    matches_per_task: int = 3,
) -> dict:
    facets = [
        {
            "path": match["path"],
            "heading": match["heading"],
            "line_start": match["line_start"],
            "line_end": match["line_end"],
            "score": match["score"],
            "reasons": match["reasons"],
        }
        for match in search(records, task, limit)
    ]
    prior_tasks = related_tasks(records, task, task_limit, matches_per_task)
    return {
        "task": task,
        "role": role,
        "owned_paths": sorted(owned_paths or []),
        "acceptance": [],
        "facets": facets,
        "prior_tasks": prior_tasks,
        "provenance_note": _PROVENANCE_NOTE,
    }
```

- [ ] **Step 3: Wire the subcommand in parser.py**

```python
    packet = subparsers.add_parser("packet", parents=[env_parent], help="Assemble a worker-packet skeleton from local search")
    packet.add_argument("--index", type=Path, required=True)
    packet.add_argument("--task", required=True)
    packet.add_argument("--role", default="coordinator")
    packet.add_argument("--owned-path", action="append", default=[])
    packet.add_argument("--limit", type=int, default=12)
    packet.add_argument("--task-limit", type=int, default=8)
    packet.add_argument("--matches-per-task", type=int, default=3)
```

```python
    if args.command == "packet":
        return core.build_packet(
            core.read_jsonl(args.index),
            args.task,
            args.role,
            args.owned_path,
            args.limit,
            args.task_limit,
            args.matches_per_task,
        )
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add scripts/context_router.py scripts/parser.py tests/test_context_router.py
git commit -m "feat: packet command assembles local worker-packet skeletons"
```

---

### Task 7: Documentation (usage reference, README, SKILL.md, installer text)

**Files:**
- Create: `references/jev-context-usage.md`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `scripts/install.py` (command text only)
- Modify: `tests/test_install.py`

**Interfaces:**
- Consumes: command surfaces from Tasks 3-6 (`--env`, `start`, `check`, `packet`).
- Produces: installer command text that still contains the strings `task-search`, `--jev`, `--allow-remote` (asserted by tests).

- [ ] **Step 1: Create references/jev-context-usage.md**

Write the operator runbook with these exact sections: `Per-task flow` (kernel first; `index` -> `search`/`task-search` -> `packet`; shortlist privacy review; `shadow --allow-remote` with `--role coordinator|tdd|verifier` and `--previous-tasks-only`; interpret `shadow_route` include/conflict/exclude), `Reading shadow output` (thresholds live in `shadow.py`; `conflict` and `exclude` never silently delete authoritative sources), `Key supply and toggle` (`.env` with `TYPESAFE_API_KEY`, `CONTEXT_ROUTER_JEV=0` hard-off; `--allow-remote` still required), `Stop and fallback` (mirror remote-policy.md; shadow failure must not block), `Metrics` (counts/IDs only, no source contents).

- [ ] **Step 2: Update SKILL.md**

After the `task-search` paragraph, add one paragraph: `packet` assembles the worker-packet skeleton locally; `start` scaffolds `tasks/<id>/` plus `architecture/` and reports prior-task records to partition; `check` reports index staleness. Update the "Remote Jev scoring" paragraph: key may come from `.env` (`TYPESAFE_API_KEY`), `CONTEXT_ROUTER_JEV=0` hard-disables, and link `references/jev-context-usage.md` alongside the remote policy.

- [ ] **Step 3: Update README.md**

In "Use per task", add `packet` after `task-search` with a one-line description. In "Optional remote Jev shadow scoring", add: keys can be supplied via `.env` (`TYPESAFE_API_KEY`, `CONTEXT_ROUTER_JEV=1|0`); link the usage reference. Add a short "Bootstrap a task partition" snippet showing `start --project . --task-id <id> --index <private-index.jsonl>` and `check --project . --index <private-index.jsonl>`.

- [ ] **Step 4: Update installer command text**

In `scripts/install.py`, extend both `OPENCODE_COMMAND` and `CLAUDE_COMMAND` bodies (keep frontmatter and required strings): after the `task-search` sentence add "Assemble the worker packet with `packet` and keep the index fresh with `check`; `start` scaffolds the task partition." Add to the TypeSafe sentence: "a key may be supplied by `.env` and `CONTEXT_ROUTER_JEV=0` hard-disables remote scoring."

- [ ] **Step 5: Add installer test**

```python
    def test_install_copies_all_script_modules(self):
        installer.apply_plan(self.project, installer.build_plan(self.project, ["codex"]))
        scripts = self.project / ".agents/skills/context-router/scripts"
        for name in ("context_router.py", "shadow.py", "parser.py"):
            self.assertTrue((scripts / name).is_file())
```

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add references/jev-context-usage.md README.md SKILL.md scripts/install.py tests/test_install.py
git commit -m "docs: Jev usage runbook, packet/check/start docs, refreshed installer text"
```

---

### Task 8: Final validation and CLI smoke

**Files:**
- No source changes expected; fix anything this uncovers.

- [ ] **Step 1: Full suite**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK, zero failures/errors.

- [ ] **Step 2: CLI smoke on this repository**

```bash
python3 scripts/context_router.py start --project . --task-id smoke --index /tmp/ctx-smoke-index.jsonl > /dev/null
python3 scripts/context_router.py check --project . --index /tmp/ctx-smoke-index.jsonl | python3 -c "import json,sys; d=json.load(sys.stdin); print('stale:', d['stale'])"
python3 scripts/context_router.py packet --index /tmp/ctx-smoke-index.jsonl --task "jev shadow scoring flow" --limit 3 | python3 -c "import json,sys; d=json.load(sys.stdin); print('facets:', len(d['facets']), 'prior:', len(d['prior_tasks']))"
rm -rf tasks/smoke /tmp/ctx-smoke-index.jsonl
```

Expected: `stale: False` (or True right after scaffolding — either is fine, command must not error), `facets:` a count with no exception. Clean up the smoke artifacts.

- [ ] **Step 3: Confirm clean tree**

Run: `git status --porcelain`
Expected: empty (`.env` ignored).
