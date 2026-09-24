#!/usr/bin/env python3
"""Deterministic repository-context discovery: indexing, search, prior tasks, packets."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

DEFAULT_ROOTS = (
    "know-how",
    "docs",
    "src",
    "lib",
    "app",
    "packages",
    "scripts",
    "test",
    "tests",
    "config",
    "addons",
    "survivor",
)
ROOT_FILES = (
    "AGENTS.md", "CLAUDE.md", "README.md", "CONTRIBUTING.md", "project.godot",
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
)
TEXT_SUFFIXES = {
    ".cfg",
    ".cs",
    ".css",
    ".gd",
    ".gdshader",
    ".ini",
    ".json",
    ".jsonc",
    ".js",
    ".jsx",
    ".kt",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
    ".tres",
    ".tscn",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}
MAX_FILE_BYTES = 512_000
TOKEN = re.compile(r"[A-Za-z0-9]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def _inside(project: Path, path: Path) -> bool:
    try:
        path.relative_to(project)
        return True
    except ValueError:
        return False


def _check_no_symlink(project: Path, path: Path) -> None:
    relative = path.relative_to(project)
    node = project
    for part in relative.parts:
        node = node / part
        if node.is_symlink():
            raise ValueError(f"Refusing symlink in indexed path: {relative}")


def _roots(project: Path, names: list[str] | None) -> list[Path]:
    selected: list[Path] = []
    for name in names or DEFAULT_ROOTS:
        raw = Path(name)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError(f"Index root must be relative to the project: {name}")
        path = project / raw
        if not path.exists():
            continue
        _check_no_symlink(project, path)
        resolved = path.resolve()
        if not _inside(project, resolved):
            raise ValueError(f"Index root escapes project: {name}")
        selected.append(resolved)
    if names:
        return sorted(set(selected))
    for name in ROOT_FILES:
        path = project / name
        if path.is_file():
            _check_no_symlink(project, path)
            selected.append(path.resolve())
    return sorted(set(selected))


def _files(project: Path, roots: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            _check_no_symlink(project, path)
            if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in ROOT_FILES):
                continue
            resolved = path.resolve()
            if not _inside(project, resolved):
                raise ValueError(f"Indexed file escapes project: {path}")
            if resolved.stat().st_size <= MAX_FILE_BYTES:
                found.add(resolved)
    return sorted(found, key=lambda path: path.relative_to(project).as_posix())


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


def _chunk_id(relative: str, heading: str, ordinal: int) -> str:
    material = f"{relative}\0{heading}\0{ordinal}".encode()
    return "ctx-" + hashlib.sha256(material).hexdigest()[:16]


def _task_metadata(relative: str) -> dict:
    path = Path(relative)
    parts = path.parts
    lowered = [part.lower() for part in parts]
    if "tasks" in lowered:
        position = lowered.index("tasks")
        if position + 1 < len(parts):
            task_id = parts[position + 1]
            if Path(task_id).suffix:
                task_id = Path(task_id).stem
            return {
                "task_record": True,
                "task_id": task_id,
                "task_artifact": path.stem,
            }
    for collection, artifact in (("plans", "plan"), ("specs", "spec")):
        if collection in lowered and path.suffix.lower() == ".md":
            task_id = path.stem
            if task_id.endswith("-design"):
                task_id = task_id[:-7]
            return {
                "task_record": True,
                "task_id": task_id,
                "task_artifact": artifact,
            }
    return {"task_record": False, "task_id": None, "task_artifact": None}


def _emit_chunk(relative: str, kind: str, heading: str, ordinal: int, start: int, lines: list[str]) -> dict:
    text = "\n".join(lines).strip()
    return {
        "id": _chunk_id(relative, heading, ordinal),
        "path": relative,
        "kind": kind,
        "heading": heading,
        "line_start": start,
        "line_end": start + len(lines) - 1,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
        **_task_metadata(relative),
    }


def _chunks(project: Path, path: Path, chunk_lines: int) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    relative = path.relative_to(project).as_posix()
    kind = _kind(path)
    chunks: list[dict] = []
    heading = ""
    buffer: list[str] = []
    start = 1
    ordinal = 0

    def flush() -> None:
        nonlocal buffer, start, ordinal
        while buffer:
            part, buffer = buffer[:chunk_lines], buffer[chunk_lines:]
            if any(line.strip() for line in part):
                chunks.append(_emit_chunk(relative, kind, heading, ordinal, start, part))
                ordinal += 1
            start += len(part)

    for line_no, line in enumerate(lines, 1):
        is_heading = kind == "markdown" and re.match(r"^#{1,6}\s+\S", line)
        if is_heading and buffer:
            flush()
            start = line_no
        if is_heading:
            heading = line.lstrip("#").strip()
        buffer.append(line)
        if len(buffer) >= chunk_lines:
            flush()
            start = line_no + 1
    flush()
    return chunks


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


def build_index(project: Path, roots: list[str] | None = None, chunk_lines: int = 80) -> list[dict]:
    project = project.resolve()
    if not project.is_dir():
        raise ValueError(f"Not a project directory: {project}")
    if chunk_lines < 10:
        raise ValueError("chunk_lines must be at least 10")
    records: list[dict] = []
    for path in _files(project, _roots(project, roots)):
        records.extend(_chunks(project, path, chunk_lines))
    return records


def _reject_symlinked_output(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    # macOS exposes trusted root-level aliases such as /var -> /private/var and
    # /tmp -> /private/tmp. Normalize only that OS-managed first component;
    # symlinks anywhere beneath it remain forbidden.
    anchor = Path(absolute.anchor)
    relative_parts = absolute.parts[1:]
    if relative_parts:
        first = anchor / relative_parts[0]
        if first.is_symlink():
            absolute = first.resolve() / Path(*relative_parts[1:])
    if absolute.is_symlink():
        raise ValueError(f"Refusing symlink output path: {path}")
    node = absolute.parent
    anchor = Path(absolute.anchor)
    while node != anchor:
        if node.is_symlink():
            raise ValueError(f"Refusing symlink in output path: {node}")
        node = node.parent
    return absolute


def write_jsonl(path: Path, records: list[dict]) -> None:
    path = _reject_symlinked_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _reject_symlinked_output(path)
    parent = path.parent.resolve()
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            os.fchmod(handle.fileno(), 0o600)
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, parent / path.name)
    except Exception:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSONL at {path}:{line_no}") from error
                if not isinstance(record, dict):
                    raise ValueError(f"Expected a JSON object at {path}:{line_no}")
                records.append(record)
    return records


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
    if not task_id or not _TASK_ID.fullmatch(task_id):
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


_PROVENANCE_NOTE = (
    "Record the provenance of each historical constraint: which prior task and artifact "
    "supplied it. Authoritative repository instructions and live code win conflicts."
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


def _terms(value: str) -> set[str]:
    return {term for term in TOKEN.findall(value.lower()) if len(term) > 1 and term not in STOP_WORDS}


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


def search(records: list[dict], query: str, limit: int = 20, task_records_only: bool = False) -> list[dict]:
    query_terms = _terms(query)
    if not query_terms or limit < 1:
        return []
    ranked = []
    for record in records:
        _validate_record(record)
        if task_records_only and record.get("task_record") is not True:
            continue
        if not task_records_only and record.get("task_record") is True:
            continue
        path_hits = query_terms & _terms(record["path"])
        heading = record["heading"]
        heading_hits = query_terms & _terms(heading)
        text_hits = query_terms & _terms(record.get("text", ""))
        score = 3 * len(path_hits) + 2 * len(heading_hits) + len(text_hits)
        normalized_query = " ".join(query.lower().split())
        if normalized_query and normalized_query in " ".join(record.get("text", "").lower().split()):
            score += 4
        if not score:
            continue
        reasons = []
        for label, hits in (("path", path_hits), ("heading", heading_hits), ("text", text_hits)):
            if hits:
                reasons.append(f"{label}:{','.join(sorted(hits))}")
        result = dict(record)
        result["heading"] = heading
        result["score"] = score
        result["reasons"] = reasons
        ranked.append(result)
    ranked.sort(key=lambda item: (-item["score"], item["path"], item["line_start"], item["id"]))
    return ranked[:limit]


def related_tasks(records: list[dict], query: str, limit: int = 8, matches_per_task: int = 3) -> list[dict]:
    if limit < 1 or matches_per_task < 1:
        return []
    matches = search(records, query, max(len(records), 1), task_records_only=True)
    grouped: dict[str, dict] = {}
    for match in matches:
        task_id = match.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        group = grouped.setdefault(
            task_id,
            {"task_id": task_id, "best_score": 0, "total_score": 0, "artifacts": set(), "paths": set(), "matches": []},
        )
        group["best_score"] = max(group["best_score"], match["score"])
        group["total_score"] += match["score"]
        if isinstance(match.get("task_artifact"), str):
            group["artifacts"].add(match["task_artifact"])
        group["paths"].add(match["path"])
        if len(group["matches"]) < matches_per_task:
            group["matches"].append({
                "id": match["id"],
                "path": match["path"],
                "heading": match["heading"],
                "line_start": match["line_start"],
                "line_end": match["line_end"],
                "score": match["score"],
                "reasons": match["reasons"],
            })
    output = []
    for group in grouped.values():
        group["artifacts"] = sorted(group["artifacts"])
        group["paths"] = sorted(group["paths"])
        output.append(group)
    output.sort(key=lambda item: (-item["best_score"], -item["total_score"], item["task_id"]))
    return output[:limit]


if __name__ == "__main__":
    import parser

    raise SystemExit(parser.main())
