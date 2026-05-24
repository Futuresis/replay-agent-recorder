from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


class JsonlWriter:
    def __init__(self, path: Path, *, overwrite: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if overwrite else "a"
        self.path = path
        self._file = path.open(mode, encoding="utf-8", newline="\n")

    def write(self, item: dict[str, Any]) -> None:
        line = json.dumps(item, ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def run_path(log_dir: Path, run_id: str) -> Path:
    return log_dir / f"{run_id}.jsonl"


def make_record_uid(index: int) -> str:
    return f"rec_{index:06d}"


def load_records(
    path: Path,
    *,
    kind: str | None = None,
    kinds: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(f"Replay log not found: {path}")
    if kind is not None and kinds is not None:
        raise ValueError("Pass either kind or kinds, not both.")
    allowed_kinds = {kind} if kind is not None else set(kinds) if kinds is not None else None

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
            if allowed_kinds is None or item.get("kind") in allowed_kinds:
                records.append(item)
    return records


def load_replay_records(path: Path) -> list[dict[str, Any]]:
    return load_records(path, kinds={"llm", "tool"})


def load_llm_records(path: Path) -> list[dict[str, Any]]:
    return load_records(path, kind="llm")


def next_fork_run_id(log_dir: Path, base_run: str) -> str:
    for index in range(1, 10000):
        fork_run = f"{base_run}_fork_{index:03d}"
        if not run_path(log_dir, fork_run).exists():
            return fork_run
    raise RuntimeError(f"Unable to allocate fork run id for {base_run}.")
