---
name: context-router
description: Retrieve the smallest sufficient repository context for an agent task, with deterministic local search and optional explicit Jev shadow scoring. Use for context selection, context-cost reduction, or evidence routing; do not use it to bypass authoritative instructions.
---

# Context Router

Use this skill to discover task-relevant repository evidence without giving workers unrelated source text. Start with a small required kernel: the user task, repository instructions, current changes, and the relevant runtime/build facts. Use the local router to identify candidate facets, then open only the evidence needed to resolve the task.

Run local discovery first:

```sh
python3 <skill-path>/scripts/context_router.py index --project . --out <private-index.jsonl>
python3 <skill-path>/scripts/context_router.py search --index <private-index.jsonl> --query "<task summary>"
python3 <skill-path>/scripts/context_router.py task-search --index <private-index.jsonl> --query "<task summary>"
```

`task-search` groups related task/spec/plan/evidence records by task ID without dumping their full text. Inspect prior-task details only when they may carry a still-binding decision, failed approach, migration constraint, regression evidence, or unfinished acceptance criterion. Do not load history merely because vocabulary overlaps; current instructions, accepted requirements, and live code remain authoritative.

`packet` assembles a local worker-packet skeleton from the search results: task, role, owned paths, an acceptance placeholder for the coordinator, scored facets, grouped prior-task facts and a provenance note. `start` scaffolds `tasks/<task-id>/{spec,plan,evidence}.md` plus `architecture/README.md`, reports existing prior-task records, and prints guidance to partition still-binding history into the new artifacts. `check` compares the index against the current tree and reports `added`/`removed`/`changed` paths so a stale index is re-built before it misleads.

Use results to build a worker packet with task, owned files, acceptance criteria, applicable constraints, selected prior-task facts, and direct source pointers. Record which prior task and artifact supplied each historical constraint. Never use a result to omit repository instructions, an accepted requirement, or live evidence needed to resolve a conflict.

Remote Jev scoring is optional shadow telemetry only. Read [the remote policy](references/remote-policy.md) before using it. It requires both deliberate privacy approval and `--allow-remote`; a key alone is not approval. The key may be supplied through a project `.env` (`TYPESAFE_API_KEY`), and `CONTEXT_ROUTER_JEV=0` hard-disables remote scoring. On any failure, use the normal context process without blocking the task. The per-task sequence, output interpretation and fallback rules are in [the Jev usage runbook](references/jev-context-usage.md).

The installer is preview-first and additive for Codex, Claude Code, OpenCode and Kimi Code. It copies the skill into each runner's skill directory, adds a `/context-route` command for Claude Code and OpenCode, and appends a small managed activation block to `AGENTS.md` (Codex, OpenCode, Kimi Code) or `CLAUDE.md` (Claude Code). The block is loaded at session start, so local index/search becomes the default for substantive new sessions without requiring `/context-route`. No activation authorizes remote source transmission. See [the init guide](README.md) for setup, first use, reinstall and removal steps.
