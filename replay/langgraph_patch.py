from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from functools import wraps
import asyncio
import contextvars
import inspect


_STATE_PROV = contextvars.ContextVar("replay_langgraph_state_prov", default=None)
_GRAPH_ID_ATTR = "_replay_langgraph_graph_id"
_CONTROL_KEY = ("__replay_langgraph_control__",)
_INSTALL_STATE = {}


def _noop_restore():
    return None


def _resolve_targets(modules):
    if modules is not None:
        if isinstance(modules, dict):
            return modules.get("StateGraph")
        return getattr(modules, "StateGraph", None)
    try:
        from langgraph.graph.state import StateGraph
    except Exception:
        return None
    return StateGraph


def _schema_name(schema):
    if schema is None:
        return "StateGraph"
    module = getattr(schema, "__module__", None)
    qualname = getattr(schema, "__qualname__", None) or getattr(
        schema, "__name__", None
    )
    if module and qualname:
        return f"{module}.{qualname}"
    return str(schema)


def _safe_path_part(value):
    return str(value).replace("/", ".")


def _graph_id(graph):
    existing = getattr(graph, _GRAPH_ID_ATTR, None)
    if existing:
        return existing
    graph_id = f"langgraph/graph/{_safe_path_part(_schema_name(getattr(graph, 'state_schema', None)))}"
    setattr(graph, _GRAPH_ID_ATTR, graph_id)
    return graph_id


def _state_prov():
    existing = _STATE_PROV.get()
    if existing is None:
        existing = {}
        _STATE_PROV.set(existing)
    return existing


@contextmanager
def _graph_run_state():
    token = _STATE_PROV.set({})
    try:
        yield
    finally:
        _STATE_PROV.reset(token)


def _node_name(node, action):
    if isinstance(node, str):
        return node, action
    action = node
    get_name = getattr(action, "get_name", None)
    if callable(get_name):
        name = get_name()
    else:
        name = getattr(action, "__name__", action.__class__.__name__)
    return name, action


def _is_async_callable(action):
    return inspect.iscoroutinefunction(action) or inspect.iscoroutinefunction(
        getattr(action, "__call__", None)
    )


def _looks_like_runnable(action):
    return hasattr(action, "invoke") and hasattr(action, "ainvoke")


try:
    from langchain_core.runnables import Runnable
except Exception:
    Runnable = None


def _state_key(runtime, key):
    try:
        return runtime.plain_value(key)
    except Exception:
        return key


def _seed_state(runtime, state, state_prov):
    with runtime._lock:
        control_sources = set(state_prov.get(_CONTROL_KEY, set()))
    if isinstance(state, Mapping):
        seeded = dict(state)
        incoming_sources = set(runtime.get_prov(state)) | control_sources
        for key, value in state.items():
            lookup_key = _state_key(runtime, key)
            with runtime._lock:
                stored_sources = set(state_prov.get(lookup_key, set()))
            sources = stored_sources | runtime.get_prov(key) | runtime.get_prov(value)
            incoming_sources |= sources
            if sources:
                seeded[key] = runtime.seed_value(value, sources)
        if incoming_sources:
            seeded = runtime.seed_value(seeded, incoming_sources)
        return seeded, incoming_sources

    incoming_sources = set(runtime.get_prov(state)) | control_sources
    with runtime._lock:
        stored_values = list(state_prov.values())
    for sources in stored_values:
        incoming_sources |= set(sources)
    if incoming_sources:
        state = runtime.seed_value(state, incoming_sources)
    return state, incoming_sources


@contextmanager
def _node_context(runtime, node_name, graph_id, incoming_sources):
    identity_hint = f"{graph_id}/{_safe_path_part(node_name)}"
    metadata = {
        "framework": "langgraph",
        "langgraph_node": node_name,
        "graph_id": graph_id,
        "identity_hint": identity_hint,
    }
    pc_carrier = runtime.seed_value(
        {"__replay_langgraph_pc__": node_name}, incoming_sources
    )
    with runtime.context_span("langgraph_node", node_name, metadata):
        with runtime.pc(pc_carrier):
            yield


def _merge_state_prov(runtime, state_prov, key, sources):
    sources = set(sources)
    if not sources:
        return
    with runtime._lock:
        state_prov[key] = set(state_prov.get(key, set())) | sources


def _capture_mapping_output(runtime, out, state_prov, pc_sources):
    for key, value in out.items():
        _merge_state_prov(
            runtime,
            state_prov,
            _state_key(runtime, key),
            runtime.get_prov(value) | pc_sources,
        )


def _as_sequence(value):
    if value is None or value == ():
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


def _capture_send(runtime, send, state_prov, pc_sources):
    node_sources = runtime.get_prov(getattr(send, "node", None))
    arg = getattr(send, "arg", None)
    arg_sources = runtime.get_prov(arg) | pc_sources
    _merge_state_prov(runtime, state_prov, _CONTROL_KEY, node_sources | arg_sources)
    if isinstance(arg, Mapping):
        _capture_mapping_output(runtime, arg, state_prov, pc_sources)


def _capture_command(runtime, out, state_prov, pc_sources):
    update = getattr(out, "update", None)
    if isinstance(update, Mapping):
        _capture_mapping_output(runtime, update, state_prov, pc_sources)
    elif update is not None:
        _merge_state_prov(
            runtime, state_prov, _CONTROL_KEY, runtime.get_prov(update) | pc_sources
        )

    goto = getattr(out, "goto", None)
    _merge_state_prov(
        runtime, state_prov, _CONTROL_KEY, runtime.get_prov(goto) | pc_sources
    )
    for item in _as_sequence(goto):
        if item.__class__.__name__ == "Send":
            _capture_send(runtime, item, state_prov, pc_sources)
        else:
            _merge_state_prov(
                runtime, state_prov, _CONTROL_KEY, runtime.get_prov(item) | pc_sources
            )


def _capture_output(runtime, out, state_prov):
    pc_sources = runtime.current_pc()
    if isinstance(out, Mapping):
        _capture_mapping_output(runtime, out, state_prov, pc_sources)
        return
    if out.__class__.__name__ == "Command":
        _capture_command(runtime, out, state_prov, pc_sources)
        return
    if out.__class__.__name__ == "Send":
        _capture_send(runtime, out, state_prov, pc_sources)
        return
    for item in _as_sequence(out):
        if item.__class__.__name__ == "Send":
            _capture_send(runtime, item, state_prov, pc_sources)


def _plain_graph_output(runtime, out):
    return runtime.plain_value(out)


def _wrap_callable(graph, node_name, action):
    graph_id = _graph_id(graph)

    if _is_async_callable(action):

        @wraps(action)
        async def async_wrapped(input_state, *args, **kwargs):
            from .semantic_runtime import RUNTIME

            state_prov = _state_prov()
            seeded_state, incoming_sources = _seed_state(
                RUNTIME, input_state, state_prov
            )
            with _node_context(RUNTIME, node_name, graph_id, incoming_sources):
                out = await action(seeded_state, *args, **kwargs)
                _capture_output(RUNTIME, out, state_prov)
                return _plain_graph_output(RUNTIME, out)

        return async_wrapped

    @wraps(action)
    def wrapped(input_state, *args, **kwargs):
        from .semantic_runtime import RUNTIME

        state_prov = _state_prov()
        seeded_state, incoming_sources = _seed_state(RUNTIME, input_state, state_prov)
        with _node_context(RUNTIME, node_name, graph_id, incoming_sources):
            out = action(seeded_state, *args, **kwargs)
            _capture_output(RUNTIME, out, state_prov)
            return _plain_graph_output(RUNTIME, out)

    return wrapped


def _wrap_runnable(graph, node_name, action):
    if Runnable is None:
        return action

    graph_id = _graph_id(graph)

    class _WrappedRunnable(Runnable):
        def get_name(self, *args, **kwargs):
            return node_name

        def invoke(self, input_state, config=None, **kwargs):
            from .semantic_runtime import RUNTIME

            state_prov = _state_prov()
            seeded_state, incoming_sources = _seed_state(
                RUNTIME, input_state, state_prov
            )
            with _node_context(RUNTIME, node_name, graph_id, incoming_sources):
                out = action.invoke(seeded_state, config, **kwargs)
                _capture_output(RUNTIME, out, state_prov)
                return _plain_graph_output(RUNTIME, out)

        async def ainvoke(self, input_state, config=None, **kwargs):
            from .semantic_runtime import RUNTIME

            state_prov = _state_prov()
            seeded_state, incoming_sources = _seed_state(
                RUNTIME, input_state, state_prov
            )
            with _node_context(RUNTIME, node_name, graph_id, incoming_sources):
                out = await action.ainvoke(seeded_state, config, **kwargs)
                _capture_output(RUNTIME, out, state_prov)
                return _plain_graph_output(RUNTIME, out)

        def __getattr__(self, name):
            return getattr(action, name)

    return _WrappedRunnable()


def _wrap_compiled_graph(compiled):
    if getattr(compiled, "__replay_langgraph_run_wrapper__", False):
        return compiled

    original_invoke = getattr(compiled, "invoke", None)
    if original_invoke is not None:

        @wraps(original_invoke)
        def invoke(input_state, *args, **kwargs):
            config = _extract_config(args, kwargs)
            with _maybe_auto_session(compiled, input_state, config):
                with _graph_run_state():
                    return original_invoke(input_state, *args, **kwargs)

        try:
            compiled.invoke = invoke
        except Exception:
            pass

    original_ainvoke = getattr(compiled, "ainvoke", None)
    if original_ainvoke is not None:

        @wraps(original_ainvoke)
        async def ainvoke(input_state, *args, **kwargs):
            config = _extract_config(args, kwargs)
            with _maybe_auto_session(compiled, input_state, config):
                with _graph_run_state():
                    return await original_ainvoke(input_state, *args, **kwargs)

        try:
            compiled.ainvoke = ainvoke
        except Exception:
            pass

    original_stream = getattr(compiled, "stream", None)
    if original_stream is not None:

        @wraps(original_stream)
        def stream(input_state, *args, **kwargs):
            config = _extract_config(args, kwargs)
            with _maybe_auto_session(compiled, input_state, config):
                ctx = contextvars.copy_context()
                ctx.run(_STATE_PROV.set, {})
                iterator = ctx.run(
                    lambda: iter(original_stream(input_state, *args, **kwargs))
                )
                try:
                    while True:
                        try:
                            yield ctx.run(next, iterator)
                        except StopIteration:
                            return
                finally:
                    close = getattr(iterator, "close", None)
                    if callable(close):
                        ctx.run(close)

        try:
            compiled.stream = stream
        except Exception:
            pass

    original_astream = getattr(compiled, "astream", None)
    if original_astream is not None:

        @wraps(original_astream)
        async def astream(input_state, *args, **kwargs):
            config = _extract_config(args, kwargs)
            with _maybe_auto_session(compiled, input_state, config):
                ctx = contextvars.copy_context()
                ctx.run(_STATE_PROV.set, {})
                iterator = ctx.run(
                    lambda: original_astream(input_state, *args, **kwargs).__aiter__()
                )
                try:
                    while True:
                        try:
                            yield await ctx.run(
                                lambda: asyncio.create_task(iterator.__anext__())
                            )
                        except StopAsyncIteration:
                            return
                finally:
                    aclose = getattr(iterator, "aclose", None)
                    if callable(aclose):
                        await ctx.run(lambda: asyncio.create_task(aclose()))

        try:
            compiled.astream = astream
        except Exception:
            pass

    try:
        compiled.__replay_langgraph_run_wrapper__ = True
    except Exception:
        pass
    return compiled


def _extract_config(args, kwargs):
    if "config" in kwargs:
        return kwargs.get("config")
    if args:
        candidate = args[0]
        if isinstance(candidate, Mapping):
            return candidate
    return None


@contextmanager
def _maybe_auto_session(compiled, input_state, config):
    try:
        from .autosession import maybe_replay_session_for_config
    except Exception:
        yield
        return
    with maybe_replay_session_for_config(
        runnable=compiled,
        input_value=input_state,
        config=config,
        graph_name=getattr(compiled, "_replay_graph_name", None),
    ):
        yield


def wrap_compiled_graph(compiled):
    """Public wrapper for already-compiled LangGraph graph objects."""

    try:
        return _wrap_compiled_graph(compiled)
    except Exception:
        return compiled


def install_langgraph_patch(modules=None, module=None):
    if modules is None:
        modules = module
    StateGraph = _resolve_targets(modules)
    if StateGraph is None or not hasattr(StateGraph, "add_node"):
        return _noop_restore

    state = _INSTALL_STATE.get(StateGraph)
    if state is None:
        original_add_node = StateGraph.add_node
        original_compile = getattr(StateGraph, "compile", None)
        state = {
            "original_add_node": original_add_node,
            "original_compile": original_compile,
            "installs": set(),
            "order": [],
        }
        _INSTALL_STATE[StateGraph] = state

        def add_node(
            self,
            node,
            action=None,
            *,
            defer=False,
            metadata=None,
            input_schema=None,
            retry_policy=None,
            cache_policy=None,
            destinations=None,
            **kwargs,
        ):
            node_name, node_action = _node_name(node, action)
            if node_action is not None and _looks_like_runnable(node_action):
                node_action = _wrap_runnable(self, node_name, node_action)
            elif node_action is not None and callable(node_action):
                node_action = _wrap_callable(self, node_name, node_action)
            forwarded = {
                "defer": defer,
                "metadata": metadata,
                "input_schema": input_schema,
                "retry_policy": retry_policy,
                "cache_policy": cache_policy,
                "destinations": destinations,
                **kwargs,
            }
            signature = inspect.signature(original_add_node)
            accepts_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in signature.parameters.values()
            )
            if not accepts_kwargs:
                forwarded = {
                    key: value
                    for key, value in forwarded.items()
                    if key in signature.parameters
                }
            return original_add_node(
                self,
                node_name if node_action is not None else node,
                node_action,
                **forwarded,
            )

        StateGraph.add_node = add_node

        if original_compile is not None:

            def compile(self, *args, **kwargs):
                return wrap_compiled_graph(original_compile(self, *args, **kwargs))

            StateGraph.compile = compile

    token = object()
    state["installs"].add(token)
    state["order"].append(token)
    restored = False

    def restore():
        nonlocal restored
        if restored:
            return
        restored = True
        current_state = _INSTALL_STATE.get(StateGraph)
        if current_state is None:
            return
        current_state["installs"].discard(token)
        current_state["order"] = [
            item for item in current_state["order"] if item is not token
        ]
        if current_state["order"]:
            return
        StateGraph.add_node = current_state["original_add_node"]
        if current_state["original_compile"] is not None:
            StateGraph.compile = current_state["original_compile"]
        del _INSTALL_STATE[StateGraph]

    return restore
