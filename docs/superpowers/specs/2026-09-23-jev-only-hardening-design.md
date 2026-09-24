# Context Router — Jev-only hardening and workflow flows

Date: 2026-09-23
Status: Approved design, pending implementation plan

## Overview

Improve the standalone context-router skill with Jev as the only remote scorer for
this version. Four work streams: (1) behavior-preserving cleanup of
`scripts/context_router.py`, (2) a `.env` contract that supplies the
`TYPESAFE_API_KEY` and a remote kill-switch, (3) three new local subcommands
(`start`, `check`, `packet`) adopted from the godot-agent-workflow context
model, and (4) documentation: a Jev context-usage reference plus README and
SKILL.md refresh.

Laya-local scoring was considered and deferred; it is out of scope for this
version.

## Goals

- Keep the router dependency-free `python3` (stdlib only) and local-by-default.
- Make the prior-task partition (`tasks/<id>/...`, `architecture/`) creatable and
  discoverable from day one.
- Make index staleness detectable without a full rebuild-and-replace.
- Give coordinators a deterministic worker-packet skeleton.
- Make Jev key supply declarative (`.env`) without weakening the consent policy.

## Non-goals

- No new remote endpoints, models, or question types; `jev-1.13.0` stays pinned.
- No Laya integration this version.
- No behavior change to existing `index`, `search`, `task-search`, `shadow`
  outputs beyond the documented cleanup internals.

## 1. Module split and cleanup of `scripts/`

`context_router.py` is split into three files (requested): the CLI entry path
`scripts/context_router.py` stays the documented entry point, so all existing
command examples keep working.

- `scripts/context_router.py` — core, remote-free: indexing/chunking, `search`,
  `related_tasks`, the new `start`/`check`/`packet` implementations, the `.env`
  loader (`load_env_file`), JSONL I/O, `main()` (thin: parse args via
  `parser.py`, dispatch). Imports `shadow` only lazily inside the `shadow`
  dispatch branch, so `index`/`search` never load remote code.
- `scripts/shadow.py` — everything Jev: `MODEL`, `POLICY_VERSION`, `ENDPOINT`,
  `QUESTIONS`, `THRESHOLDS`, `_NoRedirectHandler`, `_cache_key`, `_ask_jev`,
  `_validate_evaluation`, `_route`, `shadow()`. Imports `search`, `read_jsonl`,
  `write_jsonl` one-way from `context_router`; nothing in core imports shadow
  at module level.
- `scripts/parser.py` — `build_parser()` constructing every subcommand
  (including `shadow`'s flags, which move here) and the `run(args)` dispatch
  table that maps parsed args to core/shadow calls with keyword arguments.
  `context_router.main()` delegates to it.

Both new modules are plain scripts-dir imports (`import shadow`, `import
parser`) — valid because the CLI always runs from `scripts/`, and the
installer already copies every file under `scripts/`. Tests are updated to
import from the new modules; the suite stays green.

Behavior-preserving cleanup within the same change:

- `_kind`: remove unreachable branches; `.json/.jsonc` resolve to `data`,
  `.yaml/.yml` stay `configuration`; document the precedence.
- `_files`: parenthesize the suffix/name filter (`not is_file or (suffix not in
  TEXT_SUFFIXES and name not in ROOT_FILES)`).
- `search`: extract per-record validation into `_validate_record(record)` called
  once before scoring, not inside the ranking loop.
- Keep per-candidate cache writes in `shadow` for crash resumability.

## 2. `.env` contract

- New stdlib loader in `context_router.py`: reads `.env` from the current working
  directory unless `--env PATH` is given; `KEY=VALUE` lines, `#` comments, blank
  lines ignored, no interpolation. Process environment variables always win over
  `.env` values; `.env` never overrides an existing var.
- Keys:
  - `TYPESAFE_API_KEY` — Jev bearer credential for `shadow`.
  - `CONTEXT_ROUTER_JEV` — `0` disables remote shadow entirely (explicit error
    even with key and `--allow-remote`); unset or `1` allows the existing
    consent flow.
- Policy unchanged: a key alone authorizes authentication only; `--allow-remote`
  remains required consent for every `shadow` call.
- Repository ships `.env.example` with placeholders; `.gitignore` gains `.env`.
  The operator's real key lives in an untracked `.env` and is never committed,
  logged, or echoed.

## 3. New subcommands

### `start` — scaffold the task partition

```
context_router.py start --project . [--task-id ID] [--index PATH] [--root R]...
```

- Creates only missing files (existing files are reported `skipped`, never
  overwritten): `tasks/<task-id>/spec.md`, `plan.md`, `evidence.md` with short
  skeletons; `architecture/README.md` if absent.
- `--task-id` defaults to `YYYY-MM-DD-untitled`; must be a single safe path
  component (no separators, no `..`).
- Scans deterministically for existing prior-task info: `tasks/*` (one level),
  `specs/*.md`, `plans/*.md`, `docs/superpowers/specs/*.md`,
  `docs/superpowers/plans/*.md`. Reports relative paths sorted.
- Prints JSON: `{"created": [...], "skipped": [...], "existing_prior_tasks":
  [...], "guidance": [...]}`. Guidance lines instruct the agent to partition
  still-binding prior decisions, failed approaches, migration constraints and
  unfinished acceptance criteria into the new task artifacts, and to record
  architectural decisions in `architecture/README.md`; current instructions and
  live code stay authoritative.
- `--index PATH` additionally builds the index after scaffolding (same
  `build_index`/`write_jsonl` path as `index`).

### `check` — index staleness report

```
context_router.py check --project . --index PATH [--root R]...
```

- Re-chunks the project in memory with the same roots/chunking and compares
  per-path chunk content-hash multisets and the path sets against the index.
- Prints JSON: `{"stale": bool, "added": [paths], "removed": [paths],
  "changed": [paths], "indexed_files": n, "current_files": m}`. Empty arrays on
  a fresh index. Deterministic; local only.

### `packet` — worker-packet skeleton

```
context_router.py packet --index I --task T [--role coordinator]
    [--owned-path P]... [--limit 12] [--task-limit 8] [--matches-per-task 3]
```

- Runs `search` and `related_tasks` and emits one JSON object:
  `{"task", "role", "owned_paths", "acceptance": [], "facets": [{path, heading,
  line_start, line_end, score, reasons}], "prior_tasks": [{task_id, artifacts,
  paths, matches}], "provenance_note"}`.
- Local only; never calls Jev. Scoring stays `shadow`'s job.
- `acceptance` is an empty list placeholder the coordinator fills; the
  `provenance_note` string restates the provenance rule (record which prior task
  supplied each historical constraint).

## 4. Jev context-usage reference

New `references/jev-context-usage.md` documenting the concrete per-task flow:

1. Kernel first (user task, instructions, working-tree status, runtime facts).
2. `index` → `search` → `task-search` (local, deterministic).
3. `packet` to assemble the worker-packet skeleton; partition prior-task facts
   with provenance.
4. Shortlist privacy review; then `shadow` with `--allow-remote` and a role
   (`coordinator` / worker role / `verifier`), `--previous-tasks-only` for
   history-only review.
5. Interpreting `shadow_route` (`include` / `conflict` / `exclude`) and the
   prior-task questions; conflicts escalate to authoritative sources.
6. Stop-and-fallback rules and metrics recording (mirror of the existing remote
   policy, written as an operator runbook).

## 5. Documentation updates

- `README.md`: new commands section, `.env` setup, link to the usage reference.
- `SKILL.md`: add `packet`/`check`/`start` to the flow, mention `.env` key
  supply and the usage reference.
- `scripts/install.py`: refresh the OpenCode/Claude command text to mention
  `packet` and keep the `--jev` consent wording accurate. No structural change
  to the installer.

## 6. Error handling

- Same exception policy as today: expected failures print
  `context-router: <message>` to stderr and exit `2`.
- `start`: existing files are `skipped`, not errors; unsafe `--task-id` is a
  usage error.
- `check`: missing/unreadable index is an error; a valid empty index is
  `stale: true` with everything `added`.
- `packet`: no matches produce empty `facets`/`prior_tasks`, not an error.
- `.env`: a malformed line is a hard error (fail fast, never guess keys);
  `CONTEXT_ROUTER_JEV` values other than `0`/`1` are a hard error.

## 7. Testing

Extend `tests/test_context_router.py` (and keep the full suite green):

- Cleanup safety: existing tests pass unchanged.
- `.env`: precedence (process env beats file), malformed line error, invalid
  `CONTEXT_ROUTER_JEV` error, kill-switch blocks `shadow` even with key +
  `--allow-remote` (using a stubbed remote call).
- `start`: creates skeleton set; rerun is all-`skipped`; unsafe task id
  rejected; scan finds seeded `specs/`/`plans/`/`tasks/` records; `--index`
  produces a loadable index.
- `check`: fresh/added/removed/changed/stale detection on a temp project.
- `packet`: facet and prior-task assembly, empty index yields empty arrays.

`tests/test_install.py` gains a case that the refreshed command text installs
unchanged when already present.
