from __future__ import annotations

import ast
import json
import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entrypoints import EntryKind, EntryMethod
from .langgraph_config import load_langgraph_config, split_graph_ref


@dataclass(frozen=True)
class EntryCandidate:
    """A possible target entry discovered in a project."""

    entry: str
    kind: EntryKind
    method: EntryMethod = "auto"
    confidence: float = 0.0
    reason: str = ""
    source_path: Path | None = None
    symbol: str | None = None
    graph_name: str | None = None
    framework: str = "auto"
    requires_input: bool = True
    requires_factory_config: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScaffoldDetectionResult:
    target_root: Path
    candidates: tuple[EntryCandidate, ...]
    selected: EntryCandidate | None
    warnings: tuple[str, ...] = ()


def detect_integration_targets(
    target_root: Path | str,
    *,
    max_python_files: int = 500,
) -> ScaffoldDetectionResult:
    """Detect likely Replay runner entries in a target project."""

    root = Path(target_root).resolve()
    candidates = [
        *detect_langgraph_json(root),
        *detect_pyproject_scripts(root),
        *detect_python_ast_entries(root, max_files=max_python_files),
        *detect_main_scripts(root),
    ]
    selected = select_default_candidate(candidates)
    return ScaffoldDetectionResult(
        target_root=root,
        candidates=tuple(candidates),
        selected=selected,
    )


def detect_langgraph_json(target_root: Path | str) -> tuple[EntryCandidate, ...]:
    """Detect graph entries from target_root/langgraph.json."""

    root = Path(target_root).resolve()
    path = root / "langgraph.json"
    if not path.exists():
        return ()

    app = load_langgraph_config(root)
    single = len(app.graphs) == 1
    candidates: list[EntryCandidate] = []
    for graph_name, graph_ref in app.graphs.items():
        lower_name = graph_name.lower()
        if single:
            confidence = 0.98
            reason = "langgraph.json contains a single graph"
        elif lower_name == "agent":
            confidence = 0.97
            reason = "langgraph.json graph named agent"
        elif lower_name in {"reviewer", "review_style_analyzer"}:
            confidence = 0.88
            reason = "langgraph.json auxiliary graph"
        else:
            confidence = 0.90
            reason = "langgraph.json graph"
        symbol = _graph_symbol(graph_ref)
        candidates.append(
            EntryCandidate(
                entry=f"langgraph.json#{graph_name}",
                kind="langgraph-json",
                confidence=confidence,
                reason=reason,
                source_path=path,
                symbol=symbol,
                graph_name=graph_name,
                framework="auto",
                requires_factory_config=symbol.startswith("get_") or ":get_agent" in graph_ref,
                metadata={"graph_ref": graph_ref},
            )
        )
    return tuple(candidates)


def detect_pyproject_scripts(target_root: Path | str) -> tuple[EntryCandidate, ...]:
    """Detect [project.scripts] console scripts from pyproject.toml."""

    root = Path(target_root).resolve()
    path = root / "pyproject.toml"
    if not path.exists():
        return ()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    scripts = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict):
        return ()
    candidates = []
    for name, value in scripts.items():
        if not isinstance(value, str) or ":" not in value:
            continue
        candidates.append(
            EntryCandidate(
                entry=value,
                kind="import",
                method="call",
                confidence=0.65,
                reason=f"pyproject.toml console script {name}",
                source_path=path,
                requires_input=False,
                metadata={"script_name": name},
            )
        )
    return tuple(candidates)


def detect_python_ast_entries(
    target_root: Path | str,
    *,
    max_files: int = 500,
) -> tuple[EntryCandidate, ...]:
    """Scan Python files for common LangGraph/DeepAgents entry patterns."""

    root = Path(target_root).resolve()
    candidates: list[EntryCandidate] = []
    for path in _iter_python_files(root, max_files=max_files):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = python_path_to_module(root, path)
        candidates.extend(_detect_assignments(root, path, module, tree))
        candidates.extend(_detect_factories(root, path, module, tree))
    return tuple(candidates)


def detect_main_scripts(target_root: Path | str) -> tuple[EntryCandidate, ...]:
    """Find files with if __name__ == '__main__'."""

    root = Path(target_root).resolve()
    candidates: list[EntryCandidate] = []
    for path in _iter_python_files(root, max_files=500):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        if not any(_is_main_guard(node) for node in ast.walk(tree) if isinstance(node, ast.If)):
            continue
        relative = path.relative_to(root)
        candidates.append(
            EntryCandidate(
                entry=f"script:{relative.as_posix()}",
                kind="script",
                method="call",
                confidence=0.50,
                reason="Python file has if __name__ == '__main__'",
                source_path=path,
                requires_input=False,
            )
        )
    return tuple(candidates)


def select_default_candidate(candidates: Iterable[EntryCandidate]) -> EntryCandidate | None:
    """Select the best default candidate."""

    items = list(candidates)
    if not items:
        return None
    return max(items, key=_candidate_sort_key)


def write_replay_target_config(
    path: Path | str,
    result: ScaffoldDetectionResult,
    *,
    target_root: Path | str | None = None,
) -> Path:
    """Write replay_target.json from detection result."""

    selected = result.selected
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "entry": selected.entry if selected is not None else None,
        "entry_kind": selected.kind if selected is not None else "auto",
        "method": selected.method if selected is not None else "auto",
        "framework": selected.framework if selected is not None else "auto",
        "graph": selected.graph_name if selected is not None else None,
        "requires_input": selected.requires_input if selected is not None else True,
        "requires_factory_config": selected.requires_factory_config if selected is not None else False,
        "detected": _candidate_to_json(selected, result.target_root) if selected is not None else None,
        "candidates": [_candidate_to_json(candidate, result.target_root) for candidate in result.candidates],
    }
    if target_root is not None:
        relative_target_root = os.path.relpath(Path(target_root).resolve(), start=output.parent.resolve())
        data["target_root"] = Path(relative_target_root).as_posix()
    output.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return output


def python_path_to_module(target_root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(target_root.resolve()).with_suffix("")
    parts = relative.parts
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def _detect_assignments(
    root: Path,
    path: Path,
    module: str,
    tree: ast.AST,
) -> list[EntryCandidate]:
    del root
    candidates: list[EntryCandidate] = []
    stategraph_builder_names = _collect_stategraph_builder_names(tree)
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, ast.Assign):
            continue
        symbols = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not symbols:
            continue
        value = _strip_with_config_call(node.value)
        for symbol in symbols:
            if _is_call_named(value, {"create_deep_agent"}):
                candidates.append(
                    EntryCandidate(
                        entry=f"{module}:{symbol}",
                        kind="runnable",
                        confidence=0.85,
                        reason="module-level create_deep_agent(...) assignment",
                        source_path=path,
                        symbol=symbol,
                        metadata={"ast_pattern": "create_deep_agent_assignment"},
                    )
                )
            elif _is_call_named(value, {"create_agent"}):
                candidates.append(
                    EntryCandidate(
                        entry=f"{module}:{symbol}",
                        kind="runnable",
                        confidence=0.78,
                        reason="module-level create_agent(...) assignment",
                        source_path=path,
                        symbol=symbol,
                        metadata={"ast_pattern": "create_agent_assignment"},
                    )
                )
            elif _is_langgraph_compile_call(value, stategraph_builder_names):
                candidates.append(
                    EntryCandidate(
                        entry=f"{module}:{symbol}",
                        kind="runnable",
                        confidence=0.80,
                        reason="module-level assignment to .compile() looks like a compiled LangGraph graph",
                        source_path=path,
                        symbol=symbol,
                        metadata={"ast_pattern": "compiled_graph_assignment"},
                    )
                )
    return candidates


def _detect_factories(root: Path, path: Path, module: str, tree: ast.AST) -> list[EntryCandidate]:
    del root
    candidates: list[EntryCandidate] = []
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns_agent_or_compile = _function_returns_agent_or_compile(node)
        if not _factory_name(node.name) and not returns_agent_or_compile:
            continue
        if not returns_agent_or_compile:
            continue
        required = _has_required_params(node)
        confidence = 0.84 if node.name == "get_agent" else 0.82
        candidates.append(
            EntryCandidate(
                entry=f"factory:{module}:{node.name}",
                kind="factory",
                confidence=confidence,
                reason=f"{node.name}(...) returns an agent or compiled graph",
                source_path=path,
                symbol=node.name,
                requires_factory_config=required,
                metadata={"ast_pattern": "factory_function"},
            )
        )
    return candidates


def _iter_python_files(root: Path, *, max_files: int) -> list[Path]:
    excluded = {".venv", "venv", "env", "node_modules", ".git", "__pycache__", "build", "dist"}
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in excluded for part in path.relative_to(root).parts):
            continue
        files.append(path)
        if len(files) >= max_files:
            break
    return files


def _candidate_sort_key(candidate: EntryCandidate) -> tuple[float, int, int, int, int]:
    graph_name = (candidate.graph_name or "").lower()
    langgraph_bonus = 1 if candidate.kind == "langgraph-json" else 0
    agent_bonus = 1 if graph_name == "agent" else 0
    non_aux_bonus = 0 if graph_name in {"reviewer", "review_style_analyzer"} else 1
    runnable_bonus = 1 if candidate.kind in {"runnable", "factory"} else 0
    return (candidate.confidence, langgraph_bonus, agent_bonus, non_aux_bonus, runnable_bonus)


def _candidate_to_json(candidate: EntryCandidate, root: Path) -> dict[str, Any]:
    source_path = None
    if candidate.source_path is not None:
        try:
            source_path = candidate.source_path.resolve().relative_to(root).as_posix()
        except ValueError:
            source_path = str(candidate.source_path)
    return {
        "entry": candidate.entry,
        "kind": candidate.kind,
        "method": candidate.method,
        "confidence": candidate.confidence,
        "reason": candidate.reason,
        "source_path": source_path,
        "symbol": candidate.symbol,
        "graph_name": candidate.graph_name,
        "framework": candidate.framework,
        "requires_input": candidate.requires_input,
        "requires_factory_config": candidate.requires_factory_config,
        "metadata": candidate.metadata,
    }


def _graph_symbol(graph_ref: str) -> str:
    try:
        _left, symbol = split_graph_ref(graph_ref)
        return symbol
    except ValueError:
        return ""


def _is_call_named(value: ast.AST, names: set[str]) -> bool:
    if not isinstance(value, ast.Call):
        return False
    return _callee_leaf_name(value) in names


def _factory_name(name: str) -> bool:
    return name in {"get_agent", "build_agent", "create_agent"} or name.endswith("_agent")


def _callee_leaf_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _strip_with_config_call(node: ast.AST) -> ast.AST:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "with_config"
    ):
        return node.func.value
    return node


def _is_stategraph_constructor_call(node: ast.AST) -> bool:
    return _is_call_named(node, {"StateGraph"})


def _collect_stategraph_builder_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    body = tree.body if isinstance(tree, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)) else []
    for node in body:
        if isinstance(node, ast.Assign):
            if not _is_stategraph_constructor_call(node.value):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if not _is_stategraph_constructor_call(node.value):
                continue
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _is_langgraph_compile_call(node: ast.AST, stategraph_builder_names: set[str]) -> bool:
    node = _strip_with_config_call(node)
    if not isinstance(node, ast.Call):
        return False
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "compile"):
        return False
    compile_target = node.func.value
    if _is_stategraph_constructor_call(compile_target):
        return True
    return isinstance(compile_target, ast.Name) and compile_target.id in stategraph_builder_names


def _assignment_target_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    return names


def _function_returns_agent_or_compile(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    stategraph_builder_names = _collect_stategraph_builder_names(node)
    assignment_matches: set[str] = set()
    for child in ast.walk(node):
        value = None
        if isinstance(child, ast.Assign):
            value = child.value
        elif isinstance(child, ast.AnnAssign):
            value = child.value
        if value is None:
            continue
        value = _strip_with_config_call(value)
        if _is_call_named(value, {"create_deep_agent", "create_agent"}) or _is_langgraph_compile_call(
            value, stategraph_builder_names
        ):
            assignment_matches.update(_assignment_target_names(child))

    for child in ast.walk(node):
        if not isinstance(child, ast.Return) or child.value is None:
            continue
        value = _strip_with_config_call(child.value)
        if _is_call_named(value, {"create_deep_agent", "create_agent"}) or _is_langgraph_compile_call(
            value, stategraph_builder_names
        ):
            return True
        if isinstance(child.value, ast.Name) and child.value.id in assignment_matches:
            return True
        if (
            isinstance(child.value, ast.Call)
            and isinstance(child.value.func, ast.Attribute)
            and child.value.func.attr == "with_config"
            and isinstance(child.value.func.value, ast.Name)
            and child.value.func.value.id in assignment_matches
        ):
            return True
    return False


def _has_required_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = list(node.args.posonlyargs) + list(node.args.args)
    defaults = list(node.args.defaults)
    required_positional = max(0, len(args) - len(defaults))
    if required_positional:
        return True
    return bool(node.args.kwonlyargs) and len(node.args.kw_defaults) < len(node.args.kwonlyargs)


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    left = test.left
    if not isinstance(left, ast.Name) or left.id != "__name__":
        return False
    if not any(isinstance(op, ast.Eq) for op in test.ops):
        return False
    return any(isinstance(comparator, ast.Constant) and comparator.value == "__main__" for comparator in test.comparators)
