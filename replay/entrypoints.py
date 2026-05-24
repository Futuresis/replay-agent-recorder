from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import runpy
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

from replay.langgraph_config import (
    ResolvedGraphRef,
    import_path_symbol,
    load_langgraph_config,
    resolve_langgraph_graph,
    resolve_langgraph_http_app,
)


EntryKind = Literal[
    "auto",
    "script",
    "module",
    "import",
    "factory",
    "runnable",
    "langgraph-json",
    "asgi",
]
EntryMethod = Literal[
    "auto",
    "call",
    "invoke",
    "ainvoke",
    "stream",
    "astream",
    "serve",
]
FrameworkMode = Literal["auto", "none", "langchain", "langgraph", "both"]
DEFAULT_ASGI_RUN_ID_TEMPLATE = "{method}-{path}-{request_id}"


class _Missing:
    pass


MISSING = _Missing()
CONFIG_PARAM_NAMES = {"config", "runnable_config", "run_config"}


def _safe_current_path() -> Path:
    try:
        return Path.cwd()
    except FileNotFoundError:
        return Path(".").resolve()


@dataclass(frozen=True)
class TargetEntry:
    """Normalized target agent entry description."""

    entry: str
    kind: EntryKind = "auto"
    target_root: Path = field(default_factory=_safe_current_path)
    target_cwd: Path | None = None
    method: EntryMethod = "auto"
    graph: str | None = None
    raw_entry: str | None = None


@dataclass(frozen=True)
class TargetInvocation:
    """Normalized inputs used to execute a target entry."""

    input_value: Any = MISSING
    config: dict[str, Any] | None = None
    factory_config: dict[str, Any] | None = None
    call_args: tuple[Any, ...] = ()
    call_kwargs: dict[str, Any] | None = None
    invoke_kwargs: dict[str, Any] | None = None
    target_args: tuple[str, ...] = ()
    collect_stream: bool = True
    serve: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    run_id_template: str = DEFAULT_ASGI_RUN_ID_TEMPLATE
    request_header_run_id: str | None = None
    replay_config: Any | None = None


@dataclass(frozen=True)
class ResolvedEntryRef:
    kind: EntryKind
    ref: str
    method: EntryMethod = "auto"
    graph: str | None = None


class EntryPointError(RuntimeError):
    pass


class UnsupportedEntryError(EntryPointError):
    pass


class EntryImportError(EntryPointError):
    pass


class EntryInvocationError(EntryPointError):
    pass


class _StoreWithExplicitFlag(argparse.Action):
    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        self._explicit_dest = kwargs.pop("explicit_dest")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, self._explicit_dest, True)


class _StoreTrueWithExplicitFlag(argparse._StoreTrueAction):
    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        self._explicit_dest = kwargs.pop("explicit_dest")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        super().__call__(parser, namespace, values, option_string)
        setattr(namespace, self._explicit_dest, True)


def add_target_entry_arguments(
    parser: argparse.ArgumentParser,
    *,
    defaults: dict[str, Any] | None = None,
) -> None:
    """Add generic target-entry execution flags to a generated runner parser."""

    defaults = defaults or {}
    parser.set_defaults(_replay_target_defaults=dict(defaults))
    parser.set_defaults(
        _replay_entry_explicit=False,
        _replay_target_script_explicit=False,
        _replay_graph_explicit=False,
        _replay_entry_kind_explicit=False,
        _replay_method_explicit=False,
    )
    parser.add_argument(
        "--target-root",
        type=Path,
        default=Path(defaults["target_root"]) if defaults.get("target_root") else Path.cwd(),
        help="Repository or working directory of the wrapped agent.",
    )
    parser.add_argument("--target-cwd", type=Path, default=None, help="Target working directory; reserved for target environment handling.")
    parser.add_argument("--no-chdir", action="store_true", help="Do not change into the target working directory.")
    parser.add_argument("--env-file", action="append", type=Path, default=None, help="Dotenv file to load before importing target code; repeatable.")
    parser.add_argument("--env-override", action="store_true", help="Allow dotenv values to overwrite existing environment variables.")
    parser.add_argument("--no-src-pythonpath", action="store_true", help="Do not automatically add target_root/src to sys.path.")
    parser.add_argument("--pythonpath", action="append", type=Path, default=None, help="Extra path to add before importing target code; repeatable.")
    parser.add_argument(
        "--entry",
        action=_StoreWithExplicitFlag,
        explicit_dest="_replay_entry_explicit",
        default=defaults.get("entry"),
        help="Target entry reference, e.g. script:src/main.py, package.agent:agent, or langgraph.json#agent.",
    )
    parser.add_argument(
        "--graph",
        action=_StoreWithExplicitFlag,
        explicit_dest="_replay_graph_explicit",
        default=defaults.get("graph"),
        help="LangGraph graph name used with langgraph.json entries.",
    )
    parser.add_argument(
        "--framework",
        choices=("auto", "none", "langchain", "langgraph", "both"),
        default=defaults.get("framework", "auto"),
        help="Framework patches to install before importing the target agent.",
    )
    parser.add_argument(
        "--target-script",
        type=Path,
        action=_StoreWithExplicitFlag,
        explicit_dest="_replay_target_script_explicit",
        default=None,
        help="Legacy script entry point. Equivalent to --entry script:<path>.",
    )
    parser.add_argument(
        "--entry-kind",
        action=_StoreWithExplicitFlag,
        explicit_dest="_replay_entry_kind_explicit",
        choices=("auto", "script", "module", "import", "factory", "runnable", "langgraph-json", "asgi"),
        default=defaults.get("entry_kind", "auto"),
        help="Override inferred target entry kind.",
    )
    parser.add_argument(
        "--method",
        action=_StoreWithExplicitFlag,
        explicit_dest="_replay_method_explicit",
        choices=("auto", "call", "invoke", "ainvoke", "stream", "astream", "serve"),
        default=defaults.get("method", "auto"),
        help="Callable or runnable invocation method.",
    )
    parser.add_argument("--serve", action=_StoreTrueWithExplicitFlag, default=False, explicit_dest="_replay_method_explicit", help="Serve an ASGI target instead of invoking it once.")
    parser.add_argument("--host", default="127.0.0.1", help="Host used by ASGI serve mode.")
    parser.add_argument("--port", type=int, default=8000, help="Port used by ASGI serve mode.")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload in ASGI serve mode.")
    parser.add_argument(
        "--run-id-template",
        default=DEFAULT_ASGI_RUN_ID_TEMPLATE,
        help="Run id template used for ASGI request sessions.",
    )
    parser.add_argument(
        "--request-header-run-id",
        default=None,
        help="HTTP header whose value should be used as the ASGI request run id.",
    )
    parser.add_argument("--input-json", default=None, help="JSON value passed as runnable or callable input.")
    parser.add_argument("--input-file", type=Path, default=None, help="File containing the JSON input value.")
    parser.add_argument("--config-json", default=None, help="JSON object passed as runnable config.")
    parser.add_argument("--config-file", type=Path, default=None, help="File containing runnable config JSON object.")
    parser.add_argument("--factory-config-json", default=None, help="JSON object passed to factory functions.")
    parser.add_argument("--factory-config-file", type=Path, default=None, help="File containing factory config JSON object.")
    parser.add_argument("--call-args-json", default=None, help="JSON array passed as positional callable or factory args.")
    parser.add_argument("--call-args-file", type=Path, default=None, help="File containing call args JSON array.")
    parser.add_argument("--call-kwargs-json", default=None, help="JSON object passed as callable or factory kwargs.")
    parser.add_argument("--call-kwargs-file", type=Path, default=None, help="File containing call kwargs JSON object.")
    parser.add_argument("--invoke-kwargs-json", default=None, help="JSON object passed as extra runnable kwargs.")
    parser.add_argument("--invoke-kwargs-file", type=Path, default=None, help="File containing extra runnable kwargs JSON object.")
    stream_group = parser.add_mutually_exclusive_group()
    stream_group.add_argument("--collect-stream", dest="collect_stream", action="store_true", default=True)
    stream_group.add_argument("--no-collect-stream", dest="collect_stream", action="store_false")
    parser.add_argument("--result-output-file", type=Path, default=None, help="Write target result to this file.")
    parser.add_argument("--no-print-result", action="store_true", help="Do not print the target result to stdout.")
    parser.add_argument(
        "target_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to script/module entries. Prefix with -- when needed.",
    )


def load_json_option(
    raw: str | None,
    file_path: Path | str | None,
    *,
    label: str,
    expected_type: type | tuple[type, ...] | None = None,
) -> Any:
    """Load JSON from direct option or file."""

    if raw is not None and file_path is not None:
        raise ValueError(f"--{label}-json and --{label}-file are mutually exclusive.")
    if file_path is not None:
        try:
            raw = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"Failed to read --{label}-file {file_path!r}: {exc}") from exc
    if raw is None:
        return MISSING
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--{label} must be valid JSON: {exc}") from exc

    if expected_type is not None and not isinstance(value, expected_type):
        raise ValueError(f"--{label} must decode to a JSON {_json_type_name(expected_type)}.")
    return value


def load_replay_target_defaults(path: Path | str) -> dict[str, Any]:
    """Load generated replay_target.json defaults."""

    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return {}
    target_root = value.get("target_root")
    if isinstance(target_root, str):
        target_root_path = Path(target_root)
        if not target_root_path.is_absolute():
            value["target_root"] = str((config_path.parent / target_root_path).resolve())
    return value


def target_entry_from_args(args: argparse.Namespace) -> TargetEntry:
    """Build TargetEntry from argparse namespace."""

    defaults = getattr(args, "_replay_target_defaults", {}) or {}
    raw_entry = getattr(args, "entry", None)
    target_script = getattr(args, "target_script", None)
    entry_explicit = getattr(args, "_replay_entry_explicit", False)
    target_script_explicit = getattr(args, "_replay_target_script_explicit", False)
    graph_explicit = getattr(args, "_replay_graph_explicit", False)
    entry_kind_explicit = getattr(args, "_replay_entry_kind_explicit", False)
    method_explicit = getattr(args, "_replay_method_explicit", False)
    entry_from_defaults = not entry_explicit and not target_script_explicit
    if entry_explicit and target_script_explicit:
        raise ValueError("--entry and --target-script are mutually exclusive.")
    if target_script_explicit:
        raw_entry = f"script:{target_script}"
    elif not entry_explicit:
        raw_entry = defaults.get("entry")
    if raw_entry is None:
        raise ValueError("Target entry is required. Pass --entry or --target-script.")

    resolved = parse_entry_ref(str(raw_entry))
    graph_option = getattr(args, "graph", None)
    if not graph_explicit and not entry_from_defaults:
        graph_option = None
    elif graph_option is None and entry_from_defaults:
        graph_option = defaults.get("graph")
    graph = _resolve_graph_option(resolved.graph, graph_option)

    entry_kind = getattr(args, "entry_kind", "auto")
    if not entry_kind_explicit and not entry_from_defaults:
        entry_kind = "auto"
    elif entry_kind is None and entry_from_defaults:
        entry_kind = defaults.get("entry_kind")
    entry_kind = entry_kind or "auto"

    method = getattr(args, "method", "auto")
    if not method_explicit and not entry_from_defaults:
        method = "auto"
    elif method is None and entry_from_defaults:
        method = defaults.get("method")
    method = method or "auto"
    if getattr(args, "serve", False):
        method = "serve"
    kind = resolved.kind if entry_kind == "auto" else entry_kind
    if graph and kind == "auto":
        kind = "langgraph-json"
    return TargetEntry(
        entry=resolved.ref,
        kind=kind,
        target_root=Path(getattr(args, "target_root", Path.cwd())).resolve(),
        target_cwd=getattr(args, "target_cwd", None),
        method=resolved.method if method == "auto" else method,
        graph=graph,
        raw_entry=str(raw_entry),
    )


def target_invocation_from_args(args: argparse.Namespace) -> TargetInvocation:
    """Build TargetInvocation from argparse namespace."""

    input_value = load_json_option(getattr(args, "input_json", None), getattr(args, "input_file", None), label="input")
    config = load_json_option(
        getattr(args, "config_json", None),
        getattr(args, "config_file", None),
        label="config",
        expected_type=dict,
    )
    factory_config = load_json_option(
        getattr(args, "factory_config_json", None),
        getattr(args, "factory_config_file", None),
        label="factory-config",
        expected_type=dict,
    )
    call_args = load_json_option(
        getattr(args, "call_args_json", None),
        getattr(args, "call_args_file", None),
        label="call-args",
        expected_type=list,
    )
    call_kwargs = load_json_option(
        getattr(args, "call_kwargs_json", None),
        getattr(args, "call_kwargs_file", None),
        label="call-kwargs",
        expected_type=dict,
    )
    invoke_kwargs = load_json_option(
        getattr(args, "invoke_kwargs_json", None),
        getattr(args, "invoke_kwargs_file", None),
        label="invoke-kwargs",
        expected_type=dict,
    )
    target_args = tuple(_clean_remainder(getattr(args, "target_args", ())))
    return TargetInvocation(
        input_value=input_value,
        config=None if config is MISSING else config,
        factory_config=None if factory_config is MISSING else factory_config,
        call_args=() if call_args is MISSING else tuple(call_args),
        call_kwargs=None if call_kwargs is MISSING else call_kwargs,
        invoke_kwargs=None if invoke_kwargs is MISSING else invoke_kwargs,
        target_args=target_args,
        collect_stream=bool(getattr(args, "collect_stream", True)),
        serve=bool(getattr(args, "serve", False)),
        host=str(getattr(args, "host", "127.0.0.1")),
        port=int(getattr(args, "port", 8000)),
        reload=bool(getattr(args, "reload", False)),
        run_id_template=str(getattr(args, "run_id_template", DEFAULT_ASGI_RUN_ID_TEMPLATE)),
        request_header_run_id=getattr(args, "request_header_run_id", None),
    )


def framework_install_flags(mode: FrameworkMode) -> tuple[bool, bool]:
    """Return (langchain, langgraph) booleans for replay.install."""

    if mode in {"auto", "both"}:
        return True, True
    if mode == "none":
        return False, False
    if mode == "langchain":
        return True, False
    if mode == "langgraph":
        return False, True
    raise ValueError(f"Unsupported framework mode: {mode!r}")


def target_env_files_from_args(args: argparse.Namespace, entry: TargetEntry) -> tuple[Path, ...]:
    """Return .env files explicitly requested plus env implied by langgraph.json."""

    files: list[Path] = []
    for item in getattr(args, "env_file", None) or ():
        _append_unique_path(files, Path(item))

    if entry.kind == "langgraph-json":
        app = load_langgraph_config(entry.target_root, config_path=entry.entry)
        if app.env:
            _append_unique_path(files, Path(app.env))

    return tuple(files)


def parse_entry_ref(value: str) -> ResolvedEntryRef:
    """Parse compact user entry syntax into kind/ref."""

    if not value:
        raise ValueError("Entry must not be empty.")
    for prefix, kind in (
        ("script:", "script"),
        ("module:", "module"),
        ("factory:", "factory"),
        ("runnable:", "runnable"),
        ("asgi:", "asgi"),
    ):
        if value.startswith(prefix):
            method: EntryMethod = "serve" if kind == "asgi" else "auto"
            return ResolvedEntryRef(kind=kind, ref=value[len(prefix) :], method=method)
    if value.startswith("langgraph:"):
        graph = value[len("langgraph:") :] or None
        return ResolvedEntryRef(kind="langgraph-json", ref="langgraph.json", graph=graph)
    if "#" in value:
        config_path, graph = value.split("#", 1)
        if (config_path or "langgraph.json").endswith("langgraph.json"):
            return ResolvedEntryRef(
                kind="langgraph-json",
                ref=config_path or "langgraph.json",
                graph=graph or None,
            )
    if value.endswith("langgraph.json"):
        return ResolvedEntryRef(kind="langgraph-json", ref=value)
    if value.endswith(".py"):
        return ResolvedEntryRef(kind="script", ref=value)
    if ":" in value:
        return ResolvedEntryRef(kind="import", ref=value)
    if _looks_like_path(value):
        return ResolvedEntryRef(kind="script", ref=value)
    return ResolvedEntryRef(kind="module", ref=value)


def import_symbol(ref: str, *, target_root: Path | None = None) -> Any:
    """Import 'module:symbol.path' and return the object."""

    del target_root
    if ":" not in ref:
        raise EntryImportError(f"Entry ref {ref!r} must use 'module:symbol' syntax.")
    module_name, attr_path = ref.split(":", 1)
    if not module_name or not attr_path:
        raise EntryImportError(f"Entry ref {ref!r} must include both module and symbol.")
    try:
        obj = importlib.import_module(module_name)
    except Exception as exc:
        raise EntryImportError(f"Failed to import module for entry {ref!r}: {exc}") from exc
    try:
        for name in attr_path.split("."):
            obj = getattr(obj, name)
    except AttributeError as exc:
        raise EntryImportError(f"Failed to resolve symbol for entry {ref!r}: {exc}") from exc
    return obj


def is_runnable(obj: Any) -> bool:
    """Return True if obj looks like LangChain/LangGraph runnable."""

    return any(hasattr(obj, name) for name in ("ainvoke", "invoke", "astream", "stream"))


async def maybe_await(value: Any) -> Any:
    """Await value if inspect.isawaitable(value), otherwise return it."""

    if inspect.isawaitable(value):
        return await value
    return value


async def run_target_entry(entry: TargetEntry, invocation: TargetInvocation) -> Any:
    """Execute a normalized target entry."""

    entry = _resolve_auto_entry(entry)
    if entry.kind == "script":
        return _run_script_entry(entry, invocation)
    if entry.kind == "module":
        return _run_module_entry(entry, invocation)
    if entry.kind in {"import", "factory", "runnable"}:
        return await _run_imported_entry(entry, invocation)
    if entry.kind == "langgraph-json":
        return await _run_langgraph_json_entry(entry, invocation)
    if entry.kind == "asgi":
        from .asgi import run_asgi_entry

        return await run_asgi_entry(
            entry,
            invocation,
            host=invocation.host,
            port=invocation.port,
            reload=invocation.reload,
            run_id_template=invocation.run_id_template,
            request_header_run_id=invocation.request_header_run_id,
        )
    raise UnsupportedEntryError(f"Entry {entry.raw_entry or entry.entry!r} resolved to unsupported kind={entry.kind!r}.")


def run_target_entry_blocking(entry: TargetEntry, invocation: TargetInvocation) -> Any:
    """Execute target entry from synchronous runners.

    Script and module entries run without an outer event loop so target code may
    call asyncio.run(...). Imported and langgraph.json entries use the blocking
    path so sync runnable invoke/stream methods stay outside any outer event loop.
    """

    entry = _resolve_auto_entry(entry)
    if entry.kind == "script":
        return _run_script_entry(entry, invocation)
    if entry.kind == "module":
        return _run_module_entry(entry, invocation)
    if entry.kind in {"import", "factory", "runnable"}:
        return _run_imported_entry_blocking(entry, invocation)
    if entry.kind == "langgraph-json":
        return _run_langgraph_json_entry_blocking(entry, invocation)
    return asyncio.run(run_target_entry(entry, invocation))


def _resolve_auto_entry(entry: TargetEntry) -> TargetEntry:
    if entry.kind == "auto":
        resolved = parse_entry_ref(entry.entry)
        return TargetEntry(
            entry=resolved.ref,
            kind=resolved.kind,
            target_root=entry.target_root,
            target_cwd=entry.target_cwd,
            method=entry.method if entry.method != "auto" else resolved.method,
            graph=entry.graph or resolved.graph,
            raw_entry=entry.raw_entry or entry.entry,
        )
    return entry


def _find_factory_config_parameter(
    parameters: list[inspect.Parameter],
) -> inspect.Parameter | None:
    for parameter in parameters:
        if parameter.name not in CONFIG_PARAM_NAMES:
            continue
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return parameter
    return None


async def _call_factory_with_config_parameter(
    factory: Any,
    parameter: inspect.Parameter,
    config: dict[str, Any],
) -> Any:
    if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
        return await maybe_await(factory(**{parameter.name: config}))
    return await maybe_await(factory(config))


def _call_factory_with_config_parameter_blocking(
    factory: Any,
    parameter: inspect.Parameter,
    config: dict[str, Any],
) -> Any:
    if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
        return maybe_await_blocking(factory(**{parameter.name: config}))
    return maybe_await_blocking(factory(config))


async def call_factory(factory: Any, invocation: TargetInvocation) -> Any:
    """Call a sync/async factory function and return its product."""

    if not callable(factory):
        raise EntryInvocationError(f"Factory entry resolved to a non-callable object: {factory!r}.")
    call_kwargs = invocation.call_kwargs or {}
    if invocation.call_args or call_kwargs:
        return await maybe_await(factory(*invocation.call_args, **call_kwargs))

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return await maybe_await(factory())

    factory_config = invocation.factory_config
    if factory_config is None:
        factory_config = invocation.config

    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    parameters = list(signature.parameters.values())
    config_parameter = _find_factory_config_parameter(parameters)
    if config_parameter is not None and factory_config is not None:
        return await _call_factory_with_config_parameter(factory, config_parameter, factory_config)
    if not required:
        return await maybe_await(factory())
    if len(required) == 1:
        required_parameter = required[0]
        if config_parameter is not None and required_parameter is config_parameter:
            return await _call_factory_with_config_parameter(factory, required_parameter, factory_config or {})
        if required_parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            return await maybe_await(factory(**{required_parameter.name: factory_config or {}}))
        return await maybe_await(factory(factory_config or {}))
    raise EntryInvocationError(
        "Factory requires unsupported arguments. Pass --call-args-json or --call-kwargs-json."
    )


def call_factory_blocking(factory: Any, invocation: TargetInvocation) -> Any:
    """Call a sync/async factory function without wrapping sync invoke in an outer event loop."""

    if not callable(factory):
        raise EntryInvocationError(f"Factory entry resolved to a non-callable object: {factory!r}.")
    call_kwargs = invocation.call_kwargs or {}
    if invocation.call_args or call_kwargs:
        return maybe_await_blocking(factory(*invocation.call_args, **call_kwargs))

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return maybe_await_blocking(factory())

    factory_config = invocation.factory_config
    if factory_config is None:
        factory_config = invocation.config

    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    parameters = list(signature.parameters.values())
    config_parameter = _find_factory_config_parameter(parameters)
    if config_parameter is not None and factory_config is not None:
        return _call_factory_with_config_parameter_blocking(factory, config_parameter, factory_config)
    if not required:
        return maybe_await_blocking(factory())
    if len(required) == 1:
        required_parameter = required[0]
        if config_parameter is not None and required_parameter is config_parameter:
            return _call_factory_with_config_parameter_blocking(factory, required_parameter, factory_config or {})
        if required_parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            return maybe_await_blocking(factory(**{required_parameter.name: factory_config or {}}))
        return maybe_await_blocking(factory(factory_config or {}))
    raise EntryInvocationError(
        "Factory requires unsupported arguments. Pass --call-args-json or --call-kwargs-json."
    )


async def call_imported_callable(fn: Any, invocation: TargetInvocation) -> Any:
    """Call a non-runnable imported callable."""

    if not callable(fn):
        raise EntryInvocationError(f"Imported entry resolved to a non-callable object: {fn!r}.")
    call_kwargs = invocation.call_kwargs or {}
    if invocation.call_args or call_kwargs:
        return await maybe_await(fn(*invocation.call_args, **call_kwargs))
    if invocation.input_value is not MISSING:
        return await maybe_await(fn(invocation.input_value))
    return await maybe_await(fn())


def call_imported_callable_blocking(fn: Any, invocation: TargetInvocation) -> Any:
    """Call a non-runnable imported callable from the blocking runner."""

    if not callable(fn):
        raise EntryInvocationError(f"Imported entry resolved to a non-callable object: {fn!r}.")
    call_kwargs = invocation.call_kwargs or {}
    if invocation.call_args or call_kwargs:
        return maybe_await_blocking(fn(*invocation.call_args, **call_kwargs))
    if invocation.input_value is not MISSING:
        return maybe_await_blocking(fn(invocation.input_value))
    return maybe_await_blocking(fn())


async def invoke_runnable(runnable: Any, method: EntryMethod, invocation: TargetInvocation) -> Any:
    """Invoke a LangChain/LangGraph runnable-like object."""

    runnable = _wrap_runnable(runnable)
    if invocation.input_value is MISSING:
        raise EntryInvocationError(
            "Entry resolved to a runnable, but no input was provided. Pass --input-json or --input-file."
        )
    selected = _select_runnable_method(runnable, method)
    kwargs = _runnable_kwargs(invocation)

    if selected == "ainvoke":
        return await runnable.ainvoke(invocation.input_value, **kwargs)
    if selected == "invoke":
        return runnable.invoke(invocation.input_value, **kwargs)
    if selected == "astream":
        chunks = []
        async for chunk in runnable.astream(invocation.input_value, **kwargs):
            if invocation.collect_stream:
                chunks.append(chunk)
        return chunks
    if selected == "stream":
        if invocation.collect_stream:
            return list(runnable.stream(invocation.input_value, **kwargs))
        for _chunk in runnable.stream(invocation.input_value, **kwargs):
            pass
        return []
    raise UnsupportedEntryError(f"Runnable method {method!r} is not implemented in P0.")


def invoke_runnable_blocking(runnable: Any, method: EntryMethod, invocation: TargetInvocation) -> Any:
    """Synchronously invoke a runnable when the blocking runner selects sync methods."""

    runnable = _wrap_runnable(runnable)
    if invocation.input_value is MISSING:
        raise EntryInvocationError(
            "Entry resolved to a runnable, but no input was provided. Pass --input-json or --input-file."
        )
    selected = _select_runnable_method(runnable, method)
    kwargs = _runnable_kwargs(invocation)

    if selected == "invoke":
        return runnable.invoke(invocation.input_value, **kwargs)
    if selected == "stream":
        if invocation.collect_stream:
            return list(runnable.stream(invocation.input_value, **kwargs))
        for _chunk in runnable.stream(invocation.input_value, **kwargs):
            pass
        return []
    return asyncio.run(invoke_runnable(runnable, method, invocation))


def print_entry_result(
    result: Any,
    *,
    output_file: Path | None = None,
    print_result: bool = True,
) -> None:
    """Print or write target execution result."""

    if result is None:
        return
    try:
        from replay.normalization import normalize_for_json

        value = normalize_for_json(result)
    except Exception:
        value = result
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        rendered = repr(result)
    if output_file is not None:
        Path(output_file).write_text(rendered + "\n", encoding="utf-8")
    if print_result:
        print(rendered)


def _run_script_entry(entry: TargetEntry, invocation: TargetInvocation) -> None:
    script_path = Path(entry.entry)
    if not script_path.is_absolute():
        script_path = entry.target_root / script_path
    if not script_path.exists():
        raise EntryInvocationError(f"Entry {entry.raw_entry or entry.entry!r} script does not exist: {script_path}")
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(script_path), *invocation.target_args]
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv
    return None


def _run_module_entry(entry: TargetEntry, invocation: TargetInvocation) -> None:
    old_argv = sys.argv[:]
    try:
        sys.argv = [entry.entry, *invocation.target_args]
        runpy.run_module(entry.entry, run_name="__main__", alter_sys=True)
    finally:
        sys.argv = old_argv
    return None


async def _run_imported_entry(entry: TargetEntry, invocation: TargetInvocation) -> Any:
    obj = import_symbol(entry.entry, target_root=entry.target_root)
    if entry.kind == "runnable":
        return await invoke_runnable(obj, entry.method, invocation)
    if entry.kind == "factory":
        produced = await call_factory(obj, invocation)
        if is_runnable(produced):
            return await invoke_runnable(produced, entry.method, invocation)
        return produced
    if is_runnable(obj):
        return await invoke_runnable(obj, entry.method, invocation)
    if callable(obj):
        if _callable_looks_like_config_factory(obj):
            result = await call_factory(obj, invocation)
        else:
            result = await call_imported_callable(obj, invocation)
        if is_runnable(result):
            return await invoke_runnable(result, entry.method, invocation)
        return result
    raise UnsupportedEntryError(
        f"Entry {entry.raw_entry or entry.entry!r} resolved to kind={entry.kind} but is not runnable or callable."
    )


def _run_imported_entry_blocking(entry: TargetEntry, invocation: TargetInvocation) -> Any:
    obj = import_symbol(entry.entry, target_root=entry.target_root)
    if entry.kind == "runnable":
        return invoke_runnable_blocking(obj, entry.method, invocation)
    if entry.kind == "factory":
        produced = call_factory_blocking(obj, invocation)
        if is_runnable(produced):
            return invoke_runnable_blocking(produced, entry.method, invocation)
        return produced
    if is_runnable(obj):
        return invoke_runnable_blocking(obj, entry.method, invocation)
    if callable(obj):
        if _callable_looks_like_config_factory(obj):
            result = call_factory_blocking(obj, invocation)
        else:
            result = call_imported_callable_blocking(obj, invocation)
        if is_runnable(result):
            return invoke_runnable_blocking(result, entry.method, invocation)
        return result
    raise UnsupportedEntryError(
        f"Entry {entry.raw_entry or entry.entry!r} resolved to kind={entry.kind} but is not runnable or callable."
    )


async def _run_langgraph_json_entry(entry: TargetEntry, invocation: TargetInvocation) -> Any:
    """Resolve langgraph.json graph then execute it as import/factory/runnable."""

    app = load_langgraph_config(entry.target_root, config_path=entry.entry)
    if entry.graph == "http":
        resolved_http = _resolve_langgraph_http_target(app)
        delegated = TargetEntry(
            entry=resolved_http.raw_ref if resolved_http.path is not None else resolved_http.import_ref,
            kind="asgi",
            target_root=entry.target_root,
            target_cwd=entry.target_cwd,
            method="serve",
            raw_entry=entry.raw_entry or entry.entry,
        )
        from .asgi import run_asgi_entry

        return await run_asgi_entry(
            delegated,
            invocation,
            host=invocation.host,
            port=invocation.port,
            reload=invocation.reload,
            run_id_template=invocation.run_id_template,
            request_header_run_id=invocation.request_header_run_id,
        )
    resolved = resolve_langgraph_graph(app, entry.graph)
    delegated = TargetEntry(
        entry=resolved.import_ref,
        kind="import",
        target_root=entry.target_root,
        target_cwd=entry.target_cwd,
        method=entry.method,
        raw_entry=entry.raw_entry or entry.entry,
    )
    with _temporary_sys_path(resolved.pythonpath_hints), _temporary_import_modules(resolved.import_ref):
        try:
            return await _run_imported_entry(delegated, invocation)
        except EntryImportError:
            if resolved.path is None or resolved.symbol is None:
                raise

        obj = import_path_symbol(
            resolved.path,
            resolved.symbol,
            module_name_hint=_langgraph_file_module_name_hint(resolved),
        )
        return await _run_loaded_langgraph_object(obj, delegated, invocation)


def _run_langgraph_json_entry_blocking(entry: TargetEntry, invocation: TargetInvocation) -> Any:
    """Resolve langgraph.json graph then execute it through the blocking imported-entry path."""

    app = load_langgraph_config(entry.target_root, config_path=entry.entry)
    if entry.graph == "http":
        resolved_http = _resolve_langgraph_http_target(app)
        delegated = TargetEntry(
            entry=resolved_http.raw_ref if resolved_http.path is not None else resolved_http.import_ref,
            kind="asgi",
            target_root=entry.target_root,
            target_cwd=entry.target_cwd,
            method="serve",
            raw_entry=entry.raw_entry or entry.entry,
        )
        from .asgi import run_asgi_entry

        return asyncio.run(
            run_asgi_entry(
                delegated,
                invocation,
                host=invocation.host,
                port=invocation.port,
                reload=invocation.reload,
                run_id_template=invocation.run_id_template,
                request_header_run_id=invocation.request_header_run_id,
            )
        )
    resolved = resolve_langgraph_graph(app, entry.graph)
    delegated = TargetEntry(
        entry=resolved.import_ref,
        kind="import",
        target_root=entry.target_root,
        target_cwd=entry.target_cwd,
        method=entry.method,
        raw_entry=entry.raw_entry or entry.entry,
    )
    with _temporary_sys_path(resolved.pythonpath_hints), _temporary_import_modules(resolved.import_ref):
        try:
            return _run_imported_entry_blocking(delegated, invocation)
        except EntryImportError:
            if resolved.path is None or resolved.symbol is None:
                raise

        obj = import_path_symbol(
            resolved.path,
            resolved.symbol,
            module_name_hint=_langgraph_file_module_name_hint(resolved),
        )
        return _run_loaded_langgraph_object_blocking(obj, delegated, invocation)


async def _run_loaded_langgraph_object(obj: Any, entry: TargetEntry, invocation: TargetInvocation) -> Any:
    if is_runnable(obj):
        return await invoke_runnable(obj, entry.method, invocation)
    if callable(obj):
        if _callable_looks_like_config_factory(obj):
            result = await call_factory(obj, invocation)
        else:
            result = await call_imported_callable(obj, invocation)
        if is_runnable(result):
            return await invoke_runnable(result, entry.method, invocation)
        return result
    raise UnsupportedEntryError(
        f"Entry {entry.raw_entry or entry.entry!r} resolved to kind={entry.kind} but is not runnable or callable."
    )


def _run_loaded_langgraph_object_blocking(obj: Any, entry: TargetEntry, invocation: TargetInvocation) -> Any:
    if is_runnable(obj):
        return invoke_runnable_blocking(obj, entry.method, invocation)
    if callable(obj):
        if _callable_looks_like_config_factory(obj):
            result = call_factory_blocking(obj, invocation)
        else:
            result = call_imported_callable_blocking(obj, invocation)
        if is_runnable(result):
            return invoke_runnable_blocking(result, entry.method, invocation)
        return result
    raise UnsupportedEntryError(
        f"Entry {entry.raw_entry or entry.entry!r} resolved to kind={entry.kind} but is not runnable or callable."
    )


def _resolve_langgraph_http_target(app: Any) -> ResolvedGraphRef:
    try:
        resolved = resolve_langgraph_http_app(app)
    except ValueError as exc:
        raise EntryInvocationError(str(exc)) from exc

    if resolved.path is not None and resolved.symbol is not None:
        _import_langgraph_resolved_object(resolved)
    return resolved


def _import_langgraph_resolved_object(resolved: ResolvedGraphRef) -> Any:
    with _temporary_sys_path(resolved.pythonpath_hints), _temporary_import_modules(resolved.import_ref):
        try:
            return import_symbol(resolved.import_ref)
        except EntryImportError:
            if resolved.path is None or resolved.symbol is None:
                raise

        return import_path_symbol(
            resolved.path,
            resolved.symbol,
            module_name_hint=_langgraph_file_module_name_hint(resolved),
        )


def _select_runnable_method(runnable: Any, method: EntryMethod) -> EntryMethod:
    if method == "auto":
        for candidate in ("ainvoke", "invoke", "astream", "stream"):
            if hasattr(runnable, candidate):
                return candidate  # type: ignore[return-value]
    if method == "call":
        raise UnsupportedEntryError("method=call is for plain callables, not runnable objects.")
    if method == "serve":
        raise UnsupportedEntryError("method=serve is only supported for ASGI entries.")
    if hasattr(runnable, method):
        return method
    raise EntryInvocationError(f"Runnable does not support method={method!r}.")


def _callable_looks_like_config_factory(fn: Any) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    parameters = list(signature.parameters.values())
    return bool(parameters and parameters[0].name in CONFIG_PARAM_NAMES)


def _wrap_runnable(runnable: Any) -> Any:
    try:
        import replay

        return replay.wrap_runnable(runnable)
    except Exception:
        return runnable


def _runnable_kwargs(invocation: TargetInvocation) -> dict[str, Any]:
    kwargs = dict(invocation.invoke_kwargs or {})
    if invocation.config is not None:
        if "config" in kwargs:
            raise EntryInvocationError(
                "Runnable invocation received both --config-json/--config-file and invoke_kwargs['config']; "
                "pass config in only one place."
            )
        kwargs["config"] = invocation.config
    return kwargs


def maybe_await_blocking(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith(".")


def _resolve_graph_option(entry_graph: str | None, option_graph: str | None) -> str | None:
    if entry_graph and option_graph and entry_graph != option_graph:
        raise ValueError(
            f"--graph must match graph in --entry. Entry graph is {entry_graph!r}; --graph is {option_graph!r}."
        )
    return entry_graph or option_graph


def _append_unique_path(values: list[Path], path: Path) -> None:
    if path not in values:
        values.append(path)


@contextmanager
def _temporary_sys_path(paths: tuple[Path, ...]) -> Iterator[None]:
    if not paths:
        yield
        return
    old_path = sys.path[:]
    try:
        for path in reversed(paths):
            path_text = str(path)
            if path_text not in sys.path:
                sys.path.insert(0, path_text)
        yield
    finally:
        sys.path[:] = old_path


@contextmanager
def _temporary_import_modules(import_ref: str) -> Iterator[None]:
    module_name = import_ref.split(":", 1)[0]
    root_name = module_name.split(".", 1)[0]
    if not root_name:
        yield
        return

    def matching_modules() -> list[str]:
        return [
            name
            for name in sys.modules
            if name == root_name or name.startswith(f"{root_name}.")
        ]

    old_modules = {name: sys.modules[name] for name in matching_modules()}
    for name in old_modules:
        sys.modules.pop(name, None)
    try:
        yield
    finally:
        for name in matching_modules():
            sys.modules.pop(name, None)
        sys.modules.update(old_modules)


def _clean_remainder(values: tuple[str, ...] | list[str]) -> list[str]:
    result = list(values)
    if result and result[0] == "--":
        return result[1:]
    return result


def _json_type_name(expected_type: type | tuple[type, ...]) -> str:
    if expected_type is dict:
        return "object"
    if expected_type is list:
        return "array"
    if isinstance(expected_type, tuple):
        return " or ".join(_json_type_name(item) for item in expected_type)
    return expected_type.__name__


def _langgraph_file_module_name_hint(resolved: Any) -> str | None:
    if getattr(resolved, "name", None):
        return f"_replay_langgraph_{resolved.name}"
    return None
