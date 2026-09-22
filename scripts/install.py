#!/usr/bin/env python3
"""Add the context-router skill to Codex, Claude Code, OpenCode and Kimi Code projects safely."""
import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {
    "codex": ".agents",
    "claude": ".claude",
    "opencode": ".opencode",
    "kimi": ".agents",
}
BEGIN = "<!-- context-router:begin -->"
END = "<!-- context-router:end -->"

OPENCODE_COMMAND = """---
description: Find task-specific repository context locally; add --jev only for approved remote shadow scoring
agent: build
---

Load the `context-router` skill. For `$ARGUMENTS`, build a deterministic local index, search it, and run
`task-search` for related prior task/spec/plan/evidence records. Use only necessary task facets and exact
prior-task facts, while retaining authoritative repository instructions and live evidence.
Do not send content to TypeSafe unless `$ARGUMENTS` includes `--jev`, the shortlist has been reviewed, and
the router invocation includes `--allow-remote`. Report candidate paths, local reasons, and any privacy or
fallback decision before proceeding.
"""
CLAUDE_COMMAND = """---
description: Find task-specific repository context locally; add --jev only for approved remote shadow scoring
---

Load the `context-router` skill. For `$ARGUMENTS`, build a deterministic local index, search it, and run
`task-search` for related prior task/spec/plan/evidence records. Retain authoritative repository instructions
and live evidence. Do not send content to TypeSafe unless
`$ARGUMENTS` includes `--jev`, the shortlist is reviewed, and the router invocation includes
`--allow-remote`. Report candidate paths, local reasons, and any privacy or fallback decision.
"""


def startup_block(skill_path):
    return (
        f"{BEGIN}\n"
        "For each new session handling a substantive repository task, use the local\n"
        f"`{skill_path}/scripts/context_router.py` index/search flow before loading\n"
        "non-kernel documents, and run `task-search` to decide whether exact facts from\n"
        "related prior tasks are needed. Keep the kernel (user task, applicable instructions,\n"
        "current changes, and runtime/build facts), then open only task-relevant facets.\n"
        "This local routing is mandatory and does not require `/context-route`. Remote Jev\n"
        "shadow scoring remains opt-in: send shortlisted source only after privacy review,\n"
        "with `--jev` and the router's `--allow-remote` flag. Router results never override\n"
        "repository instructions, accepted requirements, or live conflict evidence.\n"
        f"{END}\n"
    )


def ensure_safe(root, relative):
    path = root / relative
    for node in (path, *path.parents):
        if node == root:
            break
        if node.is_symlink():
            raise ValueError(f"Refusing symlink in destination path: {node}")
    return path


def skill_files(root):
    resolved_root = root.resolve()
    files = []
    for source in sorted(root.rglob("*")):
        relative = source.relative_to(root)
        node = root
        for part in relative.parts:
            node = node / part
            if node.is_symlink():
                raise ValueError(f"Refusing symlink in source skill: {relative}")
        if not source.resolve().is_relative_to(resolved_root):
            raise ValueError(f"Source skill path escapes bundle: {relative}")
        if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
            files.append(source)
    return files


def build_plan(project, runners, bootstrap=True):
    if not project.is_dir():
        raise ValueError(f"Not a project directory: {project}")
    desired = {}
    for runner in runners:
        dest = Path(RUNNERS[runner]) / "skills" / "context-router"
        for source in skill_files(ROOT):
            desired[dest / source.relative_to(ROOT)] = source.read_bytes()
        if runner in ("opencode", "claude"):
            desired[Path(RUNNERS[runner]) / "commands" / "context-route.md"] = (
                OPENCODE_COMMAND if runner == "opencode" else CLAUDE_COMMAND
            ).encode()
    if bootstrap:
        bootstraps = {}
        if "codex" in runners or "opencode" in runners or "kimi" in runners:
            host = "codex" if "codex" in runners else ("kimi" if "kimi" in runners else "opencode")
            bootstraps[Path("AGENTS.md")] = f"{RUNNERS[host]}/skills/context-router"
        if "claude" in runners:
            bootstraps[Path("CLAUDE.md")] = ".claude/skills/context-router"
        for relative, skill_path in bootstraps.items():
            path = ensure_safe(project, relative)
            existing = path.read_text() if path.exists() else ""
            block = startup_block(skill_path)
            if BEGIN in existing or END in existing:
                if existing.count(BEGIN) != 1 or existing.count(END) != 1:
                    raise ValueError(f"Existing context-router startup block differs: {path}; merge manually")
                start = existing.index(BEGIN)
                end = existing.index(END) + len(END)
                previous = existing[start:end]
                known = {startup_block(f"{base}/skills/context-router").rstrip("\n") for base in RUNNERS.values()}
                if previous not in known:
                    raise ValueError(f"Existing context-router startup block differs: {path}; merge manually")
                # Prefer a stable Codex path when present; otherwise retain the existing
                # valid runner path. This supports adding runners in either order.
                if relative == Path("AGENTS.md") and ".agents/skills/context-router" in previous:
                    block = startup_block(".agents/skills/context-router")
                desired[relative] = (existing[:start] + block.rstrip("\n") + existing[end:]).encode()
            else:
                desired[relative] = (existing + ("\n\n" if existing else "") + block).encode()
    plan = []
    for relative, content in sorted(desired.items()):
        path = ensure_safe(project, relative)
        if path.exists() and not path.is_file():
            raise ValueError(f"Destination is not a file: {path}")
        old = path.read_bytes() if path.exists() else None
        is_bootstrap = bootstrap and relative in (Path("AGENTS.md"), Path("CLAUDE.md"))
        if old is not None and old != content and not is_bootstrap:
            raise ValueError(f"Refusing to overwrite differing file: {path}; merge or back it up explicitly")
        if old != content:
            plan.append((path, old, content))
    return plan


def apply_plan(project, plan):
    # Recheck before any writes to detect stale previews and redirected paths.
    for path, old, _ in plan:
        ensure_safe(project, path.relative_to(project))
        current = path.read_bytes() if path.exists() else None
        if current != old:
            raise ValueError(f"Destination changed during installation: {path}")
    for path, _, content in plan:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--runner", action="append", choices=[*RUNNERS, "all"], required=True)
    parser.add_argument("--no-bootstrap", action="store_true", help="Do not append activation instructions")
    parser.add_argument("--apply", action="store_true", help="Write the previewed additive installation")
    args = parser.parse_args(argv)
    try:
        runners = list(RUNNERS) if "all" in args.runner else list(dict.fromkeys(args.runner))
        project = args.project.expanduser().resolve()
        plan = build_plan(project, runners, not args.no_bootstrap)
        for path, old, _ in plan:
            print(f"{'UPDATE' if old is not None else 'CREATE'} {path.relative_to(project)}")
        if args.apply:
            apply_plan(project, plan)
            print(f"Installed {len(plan)} changed files. Restart runners; local routing activates at session start.")
        else:
            print(f"Preview: {len(plan)} changed files; no writes. Add --apply to install.")
        return 0
    except (ValueError, OSError) as error:
        print(f"Installation stopped: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
