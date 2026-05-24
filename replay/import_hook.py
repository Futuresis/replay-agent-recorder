from __future__ import annotations

from importlib import abc, machinery
import fnmatch
from pathlib import Path
import sys
import sysconfig
import tokenize
from types import ModuleType
from typing import Iterable

from .instrument import instrument_source
from .semantic_runtime import RUNTIME


EXCLUDED_PATH_PARTS = {
    "site-packages",
    "dist-packages",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
}


def read_python_source(path: Path) -> str:
    with tokenize.open(path) as handle:
        return handle.read()


class InstrumentingLoader(abc.Loader):
    def __init__(self, fullname: str, origin: str):
        self.fullname = fullname
        self.origin = str(origin)

    def create_module(self, spec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        path = Path(self.origin)
        code = instrument_source(read_python_source(path), str(path))
        module.__file__ = str(path)
        module.__loader__ = self
        exec(code, module.__dict__)


class InstrumentingFinder(abc.MetaPathFinder):
    def __init__(self, project_root: str | Path, include: Iterable[str] | None = None, exclude: Iterable[str] | None = None):
        self.project_root = Path(project_root).resolve()
        self.include = tuple(include or ())
        self.exclude = tuple(exclude or ())
        self.previous_runtime_enabled = RUNTIME.enabled
        self.replay_root = Path(__file__).resolve().parent
        self.stdlib_paths = self._stdlib_paths()

    def _stdlib_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for key in ("stdlib", "platstdlib"):
            value = sysconfig.get_paths().get(key)
            if value:
                paths.append(Path(value).resolve())
        return tuple(paths)

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _matches_any(self, rel: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)

    def should_instrument(self, origin: str | None) -> bool:
        if not origin:
            return False
        try:
            path = Path(origin).resolve()
        except (OSError, RuntimeError):
            return False
        if path.suffix != ".py":
            return False
        if not self._is_relative_to(path, self.project_root):
            return False
        if self._is_relative_to(path, self.replay_root):
            return False
        if any(self._is_relative_to(path, root) for root in self.stdlib_paths):
            return False
        rel = path.relative_to(self.project_root).as_posix()
        parts = path.relative_to(self.project_root).parts
        if any(part in EXCLUDED_PATH_PARTS or part.endswith("_cache") or part == ".cache" for part in parts):
            return False
        if self.include and not self._matches_any(rel, self.include):
            return False
        if self.exclude and self._matches_any(rel, self.exclude):
            return False
        return True

    def find_spec(self, fullname: str, path=None, target=None):
        spec = machinery.PathFinder.find_spec(fullname, path)
        if not spec or not spec.origin or not spec.loader:
            return None
        if not self.should_instrument(spec.origin):
            return None
        spec.loader = InstrumentingLoader(fullname, spec.origin)
        return spec


def install_import_hook(project_root: str | Path, include: Iterable[str] | None = None, exclude: Iterable[str] | None = None) -> InstrumentingFinder:
    finder = InstrumentingFinder(project_root, include=include, exclude=exclude)
    RUNTIME.enable()
    sys.meta_path.insert(0, finder)
    return finder


def uninstall_import_hook(token: InstrumentingFinder) -> None:
    while token in sys.meta_path:
        sys.meta_path.remove(token)
    RUNTIME.enabled = token.previous_runtime_enabled
    RUNTIME.pc_stack.clear()
