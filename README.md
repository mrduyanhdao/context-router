# Context Router

> Local-first context discovery for coding agents: index less, know more, leak nothing.

A standalone skill that retrieves the smallest sufficient repository context for an agent task: deterministic local index/search, grouped prior-task discovery (`task-search`), and optional explicit Jev shadow scoring. Works in **Codex, Claude Code, OpenCode and Kimi Code** projects of any language — no Godot, Superpowers, provider SDK or network access required for local use.

- **Deterministic** — same repo, same task summary, same candidates. No embeddings, no model calls.
- **Local by default** — `index` and `search` never touch the network; remote scoring is an explicit, consent-gated opt-in.
- **History-aware** — `task-search` groups prior task/spec/plan/evidence records by task ID so old decisions surface without dumping old text.
- **Runner-portable** — one installer for Codex, Claude Code, OpenCode and Kimi Code.

## Init guide

### 1. Requirements

- Python 3.11+ on the PATH used by the runners.
- A target project directory (any repository; no marker file required).
- Remote Jev scoring (optional): a `TYPESAFE_API_KEY` in the environment or project `.env`, plus a privacy/ZDR review. Everything below works without it.

### 2. Install into a project

Run from this repository. Installation is preview-first; nothing is written until `--apply`:

```sh
# Preview an OpenCode + Claude Code installation without changing the project.
python3 scripts/install.py --project /path/to/repo --runner opencode --runner claude

# Apply after reviewing the preview.
python3 scripts/install.py --project /path/to/repo --runner opencode --runner claude --apply

# All four runners (Codex, Claude Code, OpenCode, Kimi Code).
python3 scripts/install.py --project /path/to/repo --runner all --apply
```

What gets installed (additive; existing files are preserved):

- Skill copies in each runner directory: `.agents/skills/context-router` (Codex and Kimi Code share it), `.claude/skills/context-router`, `.opencode/skills/context-router`.
- `/context-route` command for Claude Code and OpenCode.
- A marked activation block appended to `AGENTS.md` (Codex, OpenCode, Kimi Code) or `CLAUDE.md` (Claude Code), loaded at session start so local routing is the default for substantive tasks. Use `--no-bootstrap` to install definitions without the block.

The installer never mutates global config, credentials or remote state. Conflicting existing files stop installation before any write; an identical reinstallation is a no-op.

### 3. Use per task

From the project root, keep index and cache files in a private location (OS temp directory or a path already ignored by version control), then run the local commands before loading non-kernel documents. `<skill-path>` is the installed copy, for example `.opencode/skills/context-router`:

```sh
python3 <skill-path>/scripts/context_router.py index --project . --out <private-index.jsonl>
python3 <skill-path>/scripts/context_router.py search --index <private-index.jsonl> --query "<task summary>"
python3 <skill-path>/scripts/context_router.py task-search --index <private-index.jsonl> --query "<task summary>"
python3 <skill-path>/scripts/context_router.py packet --index <private-index.jsonl> --task "<task summary>" --role <role>
```

Keep the kernel (user task, applicable instructions, current changes, runtime/build facts), then open only the task-relevant facets the search identifies. `packet` assembles those results into a worker-packet skeleton (facets, grouped prior-task facts, provenance note) that the coordinator fills with acceptance criteria. Use `task-search` results to decide whether exact facts from related prior tasks are needed — never load history merely because vocabulary overlaps.

### 4. Bootstrap a task partition

For new work, scaffold the structure the indexer and `task-search` already understand:

```sh
python3 <skill-path>/scripts/context_router.py start --project . --task-id <id> --index <private-index.jsonl>
python3 <skill-path>/scripts/context_router.py check --project . --index <private-index.jsonl>
```

`start` creates `tasks/<id>/{spec,plan,evidence}.md` and `architecture/README.md` (never overwriting), lists existing prior-task records, and prints guidance to partition still-binding history. `check` reports index staleness (`added`/`removed`/`changed`) so a stale index is rebuilt before it misleads.

### 5. Optional remote Jev shadow scoring

Local routing never calls the network. Only after a deliberate privacy review of the shortlist, with `TYPESAFE_API_KEY` available and approval established, run:

```sh
python3 <skill-path>/scripts/context_router.py shadow --index <private-index.jsonl> --task "<task>" --role <role> --cache <private-cache.jsonl> --previous-tasks-only --allow-remote
```

The key can be supplied through a project `.env` (see `.env.example`); real environment variables take precedence. `CONTEXT_ROUTER_JEV=0` hard-disables remote scoring even with a key and `--allow-remote`. A key alone is not approval; `--allow-remote` is required consent. Data handling, the question policy, metrics and fallback rules are in [the remote policy](references/remote-policy.md), and the per-task sequence with output interpretation is in [the Jev usage runbook](references/jev-context-usage.md).

### 6. Reinstall or update

Re-run the same install command. Identical content is a no-op; changed skill files are refused with a `merge or back it up explicitly` error so local edits are never silently overwritten. A known managed block is replaced in place (adding runners in any order keeps a single stable block); a modified or unknown block stops with `merge manually`.

### 7. Validate this package

```sh
python3 -m unittest discover -s tests -v
```

### 8. Remove

Delete the installed skill directories, the `.claude`/`.opencode` `commands/context-route.md` files, and the managed `<!-- context-router:begin/end -->` block from `AGENTS.md`/`CLAUDE.md`. Delete private index and cache files when the review window ends.

## License

[MIT](LICENSE)
