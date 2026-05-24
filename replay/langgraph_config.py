from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LangGraphAppConfig:
    """Parsed langgraph.json metadata needed by Replay runner."""

    path: Path
    root: Path
    graphs: dict[str, str]
    env: str | None = None
    http_app: str | None = None
    dependencies: tuple[str, ...] = ()
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResolvedGraphRef:
    name: str
    raw_ref: str
    import_ref: str
    pythonpath_hints: tuple[Path, ...] = ()
    path: Path | None = None
    symbol: str | None = None


def load_langgraph_config(
    target_root: Path | str,
    *,
    config_path: Path | str = "langgraph.json",
) -> LangGraphAppConfig:
    """Load and validate langgraph.json from target_root."""

    root = Path(target_root).resolve()
    config = Path(config_path)
    path = config if config.is_absolute() else root / config
    path = path.resolve()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read langgraph.json at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse langgraph.json at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"langgraph.json at {path} must contain a JSON object.")

    raw_graphs = raw.get("graphs")
    if not isinstance(raw_graphs, dict):
        raise ValueError(f"langgraph.json at {path} field 'graphs' must be an object.")
    graphs = {str(name): str(ref) for name, ref in raw_graphs.items()}

    env = raw.get("env")
    if env is not None and not isinstance(env, str):
        raise ValueError(f"langgraph.json at {path} field 'env' must be a string.")

    http_app = None
    http = raw.get("http", {})
    if http is not None:
        if not isinstance(http, dict):
            raise ValueError(f"langgraph.json at {path} field 'http' must be an object.")
        http_app = http.get("app")
        if http_app is not None and not isinstance(http_app, str):
            raise ValueError(f"langgraph.json at {path} field 'http.app' must be a string.")

    dependencies = _parse_dependencies(raw.get("dependencies"), path)

    return LangGraphAppConfig(
        path=path,
        root=root,
        graphs=graphs,
        env=env,
        http_app=http_app,
        dependencies=dependencies,
        raw=raw,
    )


def select_langgraph_graph(
    app: LangGraphAppConfig,
    graph_name: str | None,
) -> tuple[str, str]:
    """Return (graph_name, graph_ref)."""

    if graph_name:
        try:
            return graph_name, app.graphs[graph_name]
        except KeyError as exc:
            available = ", ".join(sorted(app.graphs))
            raise ValueError(
                f"langgraph.json at {app.path} does not define graph {graph_name!r}. "
                f"Available graphs: {available}."
            ) from exc
    if len(app.graphs) == 1:
        return next(iter(app.graphs.items()))
    available = ", ".join(sorted(app.graphs))
    raise ValueError(
        f"langgraph.json at {app.path} defines multiple graphs. "
        f"Pass --graph or use langgraph.json#GraphName. Available graphs: {available}."
    )


def split_graph_ref(ref: str) -> tuple[str, str]:
    """Split '<module-or-path>:<symbol>' into two parts."""

    left, separator, symbol = ref.partition(":")
    if not separator or not left or not symbol:
        raise ValueError(f"Graph ref {ref!r} must use '<module-or-path>:<symbol>' syntax.")
    return left, symbol


def is_path_graph_ref(ref: str) -> bool:
    """Return True if left side of graph ref looks like a .py file path."""

    left, _symbol = split_graph_ref(ref)
    return left.startswith(".") or left.startswith("/") or left.endswith(".py")


def resolve_graph_ref_to_import_ref(app: LangGraphAppConfig, graph_ref: str) -> str:
    """Convert a langgraph.json graph ref into importable 'module:symbol' syntax."""

    return _resolve_graph_ref(app, "", graph_ref).import_ref


def resolve_langgraph_graph(
    app: LangGraphAppConfig,
    graph_name: str | None,
) -> ResolvedGraphRef:
    """Select and resolve a langgraph.json graph ref."""

    name, graph_ref = select_langgraph_graph(app, graph_name)
    return _resolve_graph_ref(app, name, graph_ref)


def resolve_langgraph_http_app(app: LangGraphAppConfig) -> ResolvedGraphRef:
    """Resolve langgraph.json http.app into a ResolvedGraphRef."""

    if not app.http_app:
        raise ValueError(f"langgraph.json at {app.path} does not define http.app.")
    return _resolve_graph_ref(app, "http", app.http_app)


def import_path_symbol(path: Path, symbol: str, *, module_name_hint: str | None = None) -> Any:
    """Import `symbol` from an arbitrary Python file path."""

    resolved_path = path.resolve()
    if resolved_path.suffix != ".py":
        raise ImportError(f"Failed to import symbol {symbol!r} from file {resolved_path}: expected a .py file.")

    module_name = module_name_hint or _path_module_name(resolved_path)
    spec = importlib.util.spec_from_file_location(module_name, resolved_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to import symbol {symbol!r} from file {resolved_path}: could not create module spec.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ImportError(f"Failed to import symbol {symbol!r} from file {resolved_path}: {exc}") from exc

    obj: Any = module
    try:
        for name in symbol.split("."):
            obj = getattr(obj, name)
    except AttributeError as exc:
        raise ImportError(
            f"Failed to import symbol {symbol!r} from file {resolved_path}: module has no attribute {name!r}."
        ) from exc
    return obj


def _resolve_graph_ref(app: LangGraphAppConfig, name: str, graph_ref: str) -> ResolvedGraphRef:
    left, symbol = split_graph_ref(graph_ref)
    if not is_path_graph_ref(graph_ref):
        return ResolvedGraphRef(name=name, raw_ref=graph_ref, import_ref=graph_ref, symbol=symbol)

    path = Path(left)
    if not path.is_absolute():
        path = app.root / path
    path = path.resolve()

    try:
        relative = path.relative_to(app.root)
    except ValueError as exc:
        raise ValueError(f"Graph ref {graph_ref!r} in {app.path} points outside target root {app.root}.") from exc

    if relative.suffix != ".py":
        raise ValueError(f"Graph ref {graph_ref!r} in {app.path} must point to a .py file.")

    parts = relative.with_suffix("").parts
    if parts and parts[0] == "src":
        module_parts = parts[1:]
        pythonpath_hints = (app.root / "src",)
    else:
        module_parts = parts
        pythonpath_hints = (app.root,)
    if not module_parts:
        raise ValueError(f"Graph ref {graph_ref!r} in {app.path} cannot be converted to a module name.")

    module = ".".join(module_parts)
    return ResolvedGraphRef(
        name=name,
        raw_ref=graph_ref,
        import_ref=f"{module}:{symbol}",
        pythonpath_hints=tuple(path.resolve() for path in pythonpath_hints),
        path=path,
        symbol=symbol,
    )


def _parse_dependencies(raw_dependencies: Any, path: Path) -> tuple[str, ...]:
    if raw_dependencies is None:
        return ()
    if isinstance(raw_dependencies, str):
        return (raw_dependencies,)
    if isinstance(raw_dependencies, list) and all(isinstance(item, str) for item in raw_dependencies):
        return tuple(raw_dependencies)
    raise ValueError(f"langgraph.json at {path} field 'dependencies' must be a string or list of strings.")


def _path_module_name(path: Path) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    stem = "".join(ch if ch.isalnum() else "_" for ch in path.stem)
    return f"_replay_langgraph_{stem}_{digest}"
