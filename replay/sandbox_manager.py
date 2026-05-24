from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from .errors import SandboxError, SandboxSafetyError
from .filesystem_effects import FilesystemCapture


@dataclass(frozen=True)
class ManagedSandbox:
    """Prepare a fresh work directory from a stable base directory."""

    base_root: str | Path
    work_root: str | Path
    reset: bool = True

    def __enter__(self) -> Path:
        return prepare_sandbox(
            base_root=self.base_root,
            work_root=self.work_root,
            reset=self.reset,
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass(frozen=True)
class ManagedSandboxCapture:
    """Prepare a sandbox and return a FilesystemCapture for its work root."""

    base_root: str | Path
    work_root: str | Path
    reset: bool = True
    max_file_bytes: int = 1_000_000
    encoding: str = "utf-8"

    def __enter__(self) -> FilesystemCapture:
        root = prepare_sandbox(
            base_root=self.base_root,
            work_root=self.work_root,
            reset=self.reset,
        )
        return FilesystemCapture(
            root,
            max_file_bytes=self.max_file_bytes,
            encoding=self.encoding,
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def prepare_sandbox(
    *,
    base_root: str | Path,
    work_root: str | Path,
    reset: bool = True,
) -> Path:
    base = Path(base_root).resolve()
    work = Path(work_root).resolve()

    _validate_sandbox_paths(base, work)
    _validate_no_symlinks(base)

    if work.exists():
        if work.is_symlink():
            raise SandboxSafetyError(f"Managed sandbox work_root cannot be a symlink: {work}")
        if not work.is_dir():
            raise SandboxError(f"Managed sandbox work_root is not a directory: {work}")
        if not reset:
            return work
        _safe_remove_tree(work)

    work.parent.mkdir(parents=True, exist_ok=True)
    _validate_no_symlink_ancestors(work.parent)
    shutil.copytree(base, work)
    return work


def _validate_sandbox_paths(base: Path, work: Path) -> None:
    if not base.exists():
        raise SandboxError(f"Managed sandbox base_root does not exist: {base}")
    if base.is_symlink():
        raise SandboxSafetyError(f"Managed sandbox base_root cannot be a symlink: {base}")
    if not base.is_dir():
        raise SandboxError(f"Managed sandbox base_root is not a directory: {base}")

    if _contains_or_equals(base, work):
        raise SandboxSafetyError("work_root must not be inside base_root or equal to base_root.")
    if _contains_or_equals(work, base):
        raise SandboxSafetyError("work_root must not contain base_root.")

    anchor = Path(work.anchor).resolve()
    if work == anchor:
        raise SandboxSafetyError(f"Refusing to manage filesystem root as work_root: {work}")

    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    protected_roots = [cwd, home]
    for protected in protected_roots:
        if _contains_or_equals(work, protected):
            raise SandboxSafetyError(f"work_root must not contain protected path: {protected}")

    if len(work.parts) < 3:
        raise SandboxSafetyError(f"work_root is too broad to manage safely: {work}")


def _safe_remove_tree(path: Path) -> None:
    _validate_no_symlink_ancestors(path.parent)
    shutil.rmtree(path)


def _validate_no_symlinks(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current_dir = Path(dirpath)
        if current_dir.is_symlink():
            raise SandboxSafetyError(f"Symlink directories are not supported in sandbox base: {current_dir}")

        for dirname in dirnames:
            child_dir = current_dir / dirname
            if child_dir.is_symlink():
                raise SandboxSafetyError(f"Symlink directories are not supported in sandbox base: {child_dir}")

        for filename in filenames:
            file_path = current_dir / filename
            if file_path.is_symlink():
                raise SandboxSafetyError(f"Symlink files are not supported in sandbox base: {file_path}")


def _validate_no_symlink_ancestors(path: Path) -> None:
    resolved = path.resolve(strict=False)
    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise SandboxSafetyError(f"Managed sandbox path cannot pass through a symlink: {current}")


def _contains_or_equals(parent: Path, child: Path) -> bool:
    if parent == child:
        return True
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
