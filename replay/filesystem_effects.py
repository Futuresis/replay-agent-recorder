from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import (
    FilesystemCaptureError,
    FilesystemReplayConflictError,
    FilesystemSandboxEscapeError,
)


@dataclass(frozen=True)
class FilesystemCapture:
    """Opt-in filesystem effect capture rooted at a sandbox directory."""

    root: str | Path
    max_file_bytes: int = 1_000_000
    encoding: str = "utf-8"

    def resolved_root(self) -> Path:
        return Path(self.root).resolve()


@dataclass(frozen=True)
class FileState:
    path: str
    sha256: str
    size: int
    text: str


def snapshot_filesystem(capture: FilesystemCapture) -> dict[str, FileState]:
    root = capture.resolved_root()
    if capture.max_file_bytes < 0:
        raise FilesystemCaptureError("max_file_bytes must be non-negative.")

    if not root.exists():
        return {}
    if not root.is_dir():
        raise FilesystemCaptureError(f"Filesystem capture root is not a directory: {root}")
    if root.is_symlink():
        raise FilesystemCaptureError(f"Filesystem capture root cannot be a symlink: {root}")

    snapshot: dict[str, FileState] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current_dir = Path(dirpath)
        if current_dir.is_symlink():
            raise FilesystemCaptureError(f"Symlink directories are not supported: {current_dir}")

        for dirname in list(dirnames):
            child_dir = current_dir / dirname
            if child_dir.is_symlink():
                raise FilesystemCaptureError(f"Symlink directories are not supported: {child_dir}")

        for filename in filenames:
            file_path = current_dir / filename
            if file_path.is_symlink():
                raise FilesystemCaptureError(f"Symlink files are not supported: {file_path}")
            if not file_path.is_file():
                continue

            size = file_path.stat().st_size
            if size > capture.max_file_bytes:
                raise FilesystemCaptureError(
                    f"Captured file exceeds max_file_bytes={capture.max_file_bytes}: {file_path}"
                )

            raw = file_path.read_bytes()
            try:
                text = raw.decode(capture.encoding)
            except UnicodeDecodeError as exc:
                raise FilesystemCaptureError(
                    f"Captured file is not {capture.encoding} text: {file_path}"
                ) from exc

            rel_path = file_path.relative_to(root).as_posix()
            snapshot[rel_path] = FileState(
                path=rel_path,
                sha256=_sha256(raw),
                size=size,
                text=text,
            )

    return {path: snapshot[path] for path in sorted(snapshot)}


def build_filesystem_effect(
    capture: FilesystemCapture,
    before: dict[str, FileState],
    after: dict[str, FileState],
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for rel_path in sorted(set(before) | set(after)):
        before_state = before.get(rel_path)
        after_state = after.get(rel_path)

        if before_state is None and after_state is not None:
            changes.append(
                {
                    "type": "create",
                    "path": rel_path,
                    "before_sha256": None,
                    "after_sha256": after_state.sha256,
                    "after_text": after_state.text,
                }
            )
        elif before_state is not None and after_state is None:
            changes.append(
                {
                    "type": "delete",
                    "path": rel_path,
                    "before_sha256": before_state.sha256,
                    "after_sha256": None,
                }
            )
        elif before_state is not None and after_state is not None:
            if before_state.sha256 == after_state.sha256:
                continue
            changes.append(
                {
                    "type": "modify",
                    "path": rel_path,
                    "before_sha256": before_state.sha256,
                    "after_sha256": after_state.sha256,
                    "after_text": after_state.text,
                }
            )

    return {
        "root": str(capture.root),
        "encoding": capture.encoding,
        "changes": changes,
    }


def apply_filesystem_effect(
    effect: dict[str, Any],
    capture: FilesystemCapture,
) -> None:
    changes = effect.get("changes")
    if not isinstance(changes, list):
        raise FilesystemCaptureError("Filesystem effect must contain a changes list.")
    if not changes:
        return

    root = capture.resolved_root()
    if root.exists() and not root.is_dir():
        raise FilesystemCaptureError(f"Filesystem capture root is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise FilesystemCaptureError(f"Filesystem capture root cannot be a symlink: {root}")

    resolved_changes = [_resolve_change(root, change) for change in changes]
    _validate_current_state(resolved_changes)

    for change, target in sorted(
        resolved_changes,
        key=lambda item: (0 if item[0]["type"] == "delete" else 1, -len(target_parts(item[1]))),
    ):
        if change["type"] != "delete":
            continue
        target.unlink()
        _remove_empty_parents(root, target.parent)

    for change, target in resolved_changes:
        if change["type"] not in {"create", "modify"}:
            continue
        after_text = change.get("after_text")
        if not isinstance(after_text, str):
            raise FilesystemCaptureError(
                f"Filesystem {change['type']} change is missing text content: {change['path']}"
            )
        _ensure_no_symlink_ancestors(root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding=capture.encoding, newline="") as file:
            file.write(after_text)

    _verify_after_state(resolved_changes)


def filesystem_effect_has_changes(record: dict[str, Any]) -> bool:
    filesystem = filesystem_effect_from_record(record)
    return bool(filesystem and filesystem.get("changes"))


def filesystem_effect_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    effects = record.get("effects")
    if not isinstance(effects, dict):
        return None
    filesystem = effects.get("filesystem")
    return filesystem if isinstance(filesystem, dict) else None


def _resolve_change(root: Path, change: Any) -> tuple[dict[str, Any], Path]:
    if not isinstance(change, dict):
        raise FilesystemCaptureError("Filesystem change entries must be objects.")

    change_type = change.get("type")
    if change_type not in {"create", "modify", "delete"}:
        raise FilesystemCaptureError(f"Unsupported filesystem change type: {change_type!r}")

    rel_path = change.get("path")
    if not isinstance(rel_path, str) or not rel_path:
        raise FilesystemCaptureError("Filesystem changes require a non-empty relative path.")

    target = _safe_child_path(root, rel_path)
    return change, target


def _safe_child_path(root: Path, rel_path: str) -> Path:
    if "\\" in rel_path:
        raise FilesystemSandboxEscapeError(f"Backslashes are not allowed in captured paths: {rel_path!r}")

    parsed = PurePosixPath(rel_path)
    if parsed.is_absolute():
        raise FilesystemSandboxEscapeError(f"Absolute paths are not allowed: {rel_path!r}")

    parts = parsed.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise FilesystemSandboxEscapeError(f"Unsafe captured path: {rel_path!r}")
    if any(":" in part for part in parts):
        raise FilesystemSandboxEscapeError(f"Drive-like path segments are not allowed: {rel_path!r}")

    target = root.joinpath(*parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise FilesystemSandboxEscapeError(f"Captured path escapes sandbox: {rel_path!r}") from exc
    return target


def _validate_current_state(resolved_changes: list[tuple[dict[str, Any], Path]]) -> None:
    for change, target in resolved_changes:
        change_type = change["type"]
        before_sha256 = change.get("before_sha256")

        _ensure_no_symlink_ancestors_for_existing(target)
        if target.exists() and target.is_symlink():
            raise FilesystemCaptureError(f"Symlink files are not supported: {target}")

        if change_type == "create":
            if before_sha256 is not None:
                raise FilesystemCaptureError(f"Create change must have before_sha256=null: {change['path']}")
            if target.exists():
                raise FilesystemReplayConflictError(
                    f"Cannot replay create; path already exists: {change['path']}"
                )
            continue

        if before_sha256 is None:
            raise FilesystemCaptureError(f"{change_type} change requires before_sha256: {change['path']}")
        if not target.is_file():
            raise FilesystemReplayConflictError(
                f"Cannot replay {change_type}; file is missing: {change['path']}"
            )

        current_sha256 = _sha256(target.read_bytes())
        if current_sha256 != before_sha256:
            raise FilesystemReplayConflictError(
                "Cannot replay filesystem effect; file hash changed "
                f"for {change['path']}: expected {before_sha256}, got {current_sha256}"
            )


def _verify_after_state(resolved_changes: list[tuple[dict[str, Any], Path]]) -> None:
    for change, target in resolved_changes:
        after_sha256 = change.get("after_sha256")
        if change["type"] == "delete":
            if after_sha256 is not None:
                raise FilesystemCaptureError(f"Delete change must have after_sha256=null: {change['path']}")
            if target.exists():
                raise FilesystemReplayConflictError(f"Delete replay left path in place: {change['path']}")
            continue

        if after_sha256 is None:
            raise FilesystemCaptureError(f"{change['type']} change requires after_sha256: {change['path']}")
        if not target.is_file():
            raise FilesystemReplayConflictError(
                f"{change['type']} replay did not create a regular file: {change['path']}"
            )
        current_sha256 = _sha256(target.read_bytes())
        if current_sha256 != after_sha256:
            raise FilesystemReplayConflictError(
                "Filesystem replay produced the wrong file hash "
                f"for {change['path']}: expected {after_sha256}, got {current_sha256}"
            )


def _ensure_no_symlink_ancestors(root: Path, target: Path) -> None:
    current = root
    for part in target.relative_to(root).parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise FilesystemCaptureError(f"Symlink directories are not supported: {current}")
    if target.exists() and target.is_symlink():
        raise FilesystemCaptureError(f"Symlink files are not supported: {target}")


def _ensure_no_symlink_ancestors_for_existing(target: Path) -> None:
    current = target.anchor
    if not current:
        return
    path = Path(current)
    for part in target.parts[1:-1]:
        path = path / part
        if path.exists() and path.is_symlink():
            raise FilesystemCaptureError(f"Symlink directories are not supported: {path}")


def _remove_empty_parents(root: Path, path: Path) -> None:
    while path != root and root in path.parents:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def target_parts(path: Path) -> tuple[str, ...]:
    return path.parts


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()
