from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetEnvironmentInfo:
    target_root: Path
    target_cwd: Path
    pythonpath_entries: tuple[Path, ...]
    env_files_loaded: tuple[Path, ...]


@contextmanager
def target_environment(
    *,
    target_root: Path | str,
    target_cwd: Path | str | None = None,
    chdir: bool = True,
    pythonpath: Iterable[Path | str] = (),
    env_files: Iterable[Path | str] = (),
    env_override: bool = False,
    include_src: bool = True,
) -> Iterator[TargetEnvironmentInfo]:
    """Temporarily prepare cwd, sys.path, and environment for importing a target project."""

    root = Path(target_root).resolve()
    cwd = _resolve_target_cwd(root, target_cwd)
    old_cwd = Path.cwd()
    old_path = sys.path[:]
    env_previous: dict[str, str | None] = {}

    entries = _pythonpath_entries(root, pythonpath=pythonpath, include_src=include_src)
    resolved_env_files = tuple(_resolve_env_file(root, path) for path in env_files)
    loaded_env_files: list[Path] = []
    try:
        for path in reversed(entries):
            path_text = str(path)
            if path_text in sys.path:
                sys.path.remove(path_text)
            sys.path.insert(0, path_text)

        for path in resolved_env_files:
            values = parse_env_file(path)
            changed = apply_env(values, override=env_override)
            env_previous.update(changed)
            loaded_env_files.append(path)

        if chdir:
            os.chdir(cwd)

        yield TargetEnvironmentInfo(
            target_root=root,
            target_cwd=cwd,
            pythonpath_entries=entries,
            env_files_loaded=tuple(loaded_env_files),
        )
    finally:
        restore_env(env_previous)
        sys.path[:] = old_path
        os.chdir(old_cwd)


def parse_env_file(path: Path | str) -> dict[str, str]:
    env_path = Path(path)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Failed to read env file {env_path}: {exc}") from exc

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid env file line in {env_path}: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid empty env key in {env_path}: {raw_line!r}")
        values[key] = _strip_env_quotes(value)
    return values


def load_env_file(path: Path | str, *, override: bool = False) -> dict[str, str]:
    """Load a simple dotenv file into os.environ."""

    values = parse_env_file(path)
    previous = apply_env(values, override=override)
    return {key: os.environ[key] for key in previous if key in os.environ}


def apply_env(values: dict[str, str], *, override: bool) -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key, value in values.items():
        if key in os.environ and not override:
            continue
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def restore_env(previous: dict[str, str | None]) -> None:
    for key, old_value in reversed(list(previous.items())):
        if old_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_value


def _pythonpath_entries(
    root: Path,
    *,
    pythonpath: Iterable[Path | str],
    include_src: bool,
) -> tuple[Path, ...]:
    entries: list[Path] = [root]
    src = root / "src"
    if include_src and src.exists():
        entries.append(src.resolve())
    for path in pythonpath:
        item = Path(path)
        if not item.is_absolute():
            item = root / item
        entries.append(item.resolve())
    return tuple(entries)


def _resolve_env_file(root: Path, path: Path | str) -> Path:
    item = Path(path)
    if not item.is_absolute():
        item = root / item
    return item.resolve()


def _resolve_target_cwd(root: Path, target_cwd: Path | str | None) -> Path:
    if target_cwd is None:
        return root

    cwd = Path(target_cwd)
    if not cwd.is_absolute():
        cwd = root / cwd
    return cwd.resolve()


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
