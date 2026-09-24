#!/usr/bin/env python3
"""Argument parsing and dispatch for the context-router CLI."""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import context_router as core
import shadow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=core.__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    env_parent = argparse.ArgumentParser(add_help=False)
    env_parent.add_argument(
        "--env", type=Path, help="Path to a .env file (default: ./.env when present)"
    )

    index = subparsers.add_parser(
        "index", parents=[env_parent], help="Build a deterministic local JSONL index"
    )
    index.add_argument("--project", type=Path, required=True)
    index.add_argument("--out", type=Path, required=True)
    index.add_argument("--root", action="append")
    index.add_argument("--chunk-lines", type=int, default=80)

    search_parser = subparsers.add_parser(
        "search", parents=[env_parent], help="Search an existing index locally"
    )
    search_parser.add_argument("--index", type=Path, required=True)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=20)

    task_search = subparsers.add_parser(
        "task-search", parents=[env_parent], help="Find related previous task records"
    )
    task_search.add_argument("--index", type=Path, required=True)
    task_search.add_argument("--query", required=True)
    task_search.add_argument("--limit", type=int, default=8)
    task_search.add_argument("--matches-per-task", type=int, default=3)

    shadow_parser = subparsers.add_parser(
        "shadow", parents=[env_parent], help="Score a local shortlist with Jev"
    )
    shadow_parser.add_argument("--index", type=Path, required=True)
    shadow_parser.add_argument("--task", required=True)
    shadow_parser.add_argument("--role", default="coordinator")
    shadow_parser.add_argument("--owned-path", action="append", default=[])
    shadow_parser.add_argument("--cache", type=Path, required=True)
    shadow_parser.add_argument("--limit", type=int, default=12)
    shadow_parser.add_argument("--model", default=shadow.MODEL)
    shadow_parser.add_argument("--timeout", type=float, default=30.0)
    shadow_parser.add_argument(
        "--previous-tasks-only",
        action="store_true",
        help="Score only indexed previous-task records",
    )
    shadow_parser.add_argument(
        "--allow-remote",
        action="store_true",
        required=True,
        help="Explicitly consent to sending shortlisted project context to TypeSafe",
    )

    start = subparsers.add_parser(
        "start", parents=[env_parent], help="Scaffold the task partition and report prior-task records"
    )
    start.add_argument("--project", type=Path, required=True)
    start.add_argument("--task-id", default=datetime.date.today().isoformat() + "-untitled")
    start.add_argument("--index", type=Path)
    start.add_argument("--root", action="append")
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
    if args.command == "start":
        return core.start_partition(args.project, args.task_id, args.index, args.root)
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
    env_path = args.env if getattr(args, "env", None) else Path(".env")
    if env_path.is_file():
        for key, value in core.load_env_file(env_path).items():
            os.environ.setdefault(key, value)
    toggle = os.environ.get("CONTEXT_ROUTER_JEV", "1")
    if toggle not in {"0", "1"}:
        print("context-router: CONTEXT_ROUTER_JEV must be 0 or 1", file=sys.stderr)
        return 2
    try:
        result = _dispatch(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"context-router: {error}", file=sys.stderr)
        return 2
