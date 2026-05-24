from __future__ import annotations

from typing import Any

from .context import get_current_session
from .normalization import normalize_for_json
from .semantic_runtime import RUNTIME
from .tool_calls import build_tool_call_records


def _noop_restore():
    return None


_INSTALL_STATE = {}
_UNSERIALIZABLE = object()
_DROP_FROM_TOOL_RECORD = object()


def _primitive(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            converted = _primitive(item)
            if converted is not _UNSERIALIZABLE:
                out.append(converted)
        return out
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            converted = _primitive(item)
            if converted is not _UNSERIALIZABLE:
                out[str(key)] = converted
        return out
    return _UNSERIALIZABLE


def _put(metadata, key, value):
    converted = _primitive(value)
    if converted is not _UNSERIALIZABLE:
        metadata[key] = converted


def _lc_id(obj):
    try:
        value = obj.lc_id()
    except Exception:
        value = None
    converted = _primitive(value)
    return converted if converted is not _UNSERIALIZABLE else None


def _model_name(obj):
    for name in ("model_name", "model", "model_id", "deployment_name"):
        value = getattr(obj, name, None)
        if isinstance(value, (str, int, float, bool)):
            return value
    return None


def _class_metadata(obj):
    cls = obj.__class__
    metadata = {
        "class_name": cls.__name__,
        "class_module": cls.__module__,
    }
    lc_id = _lc_id(obj)
    if lc_id is not None:
        metadata["lc_id"] = lc_id
    return metadata


def _chat_name(model):
    return model.__class__.__name__


def _safe_path_part(value):
    return str(value).replace("/", ".")


def _span_prefix():
    for span in reversed(RUNTIME.span_stack_snapshot()):
        if span.get("kind") != "langgraph_node":
            continue
        span_metadata = span.get("metadata") or {}
        prefix = span_metadata.get("identity_hint")
        if prefix:
            return str(prefix).rstrip("/")
    return None


def _with_active_identity_prefix(hint):
    prefix = _span_prefix()
    return f"{prefix}/{hint}" if prefix else hint


def _chat_identity_hint(model):
    return _with_active_identity_prefix(
        f"langchain/chat_model/{_safe_path_part(_model_name(model) or model.__class__.__name__)}"
    )


def _chat_metadata(model, method):
    metadata = {
        "framework": "langchain",
        "component": "chat_model",
        "method": method,
    }
    metadata.update(_class_metadata(model))
    _put(metadata, "model", _model_name(model))
    return metadata


def _normalize_chat_record_kwargs(args, kwargs):
    from langchain_core.messages import (
        BaseMessage,
        HumanMessage,
        convert_to_messages,
        messages_to_dict,
    )

    record_kwargs: dict[str, Any] = {}
    if args:
        first_arg = RUNTIME.plain_value(args[0])
        if isinstance(first_arg, str):
            record_kwargs["messages"] = messages_to_dict(
                [HumanMessage(content=first_arg)]
            )
        elif isinstance(first_arg, BaseMessage):
            record_kwargs["messages"] = messages_to_dict([first_arg])
        else:
            record_kwargs["messages"] = messages_to_dict(convert_to_messages(first_arg))
    for key, value in kwargs.items():
        if key in _CHAT_CONFIG_KWARGS or value is None:
            continue
        if key == "messages":
            if isinstance(value, str):
                record_kwargs["messages"] = messages_to_dict(
                    [HumanMessage(content=value)]
                )
            elif isinstance(value, BaseMessage):
                record_kwargs["messages"] = messages_to_dict([value])
            else:
                record_kwargs["messages"] = messages_to_dict(convert_to_messages(value))
            continue
        if key in {
            "response_format",
            "ls_structured_output_format",
            "tools",
            "tool_choice",
            "model_kwargs",
            "extra_body",
        }:
            record_kwargs[key] = normalize_for_json(RUNTIME.plain_value(value))
            continue
        record_kwargs[key] = RUNTIME.plain_value(value)
    return record_kwargs


def _chat_record_kwargs(args, kwargs):
    return _normalize_chat_record_kwargs(args, kwargs)


def _tool_name(tool):
    return getattr(tool, "name", None) or tool.__class__.__name__


def _tool_call_payload(args):
    if not args or not isinstance(args[0], dict):
        return None
    payload = args[0]
    if payload.get("type") == "tool_call" and "name" in payload and "id" in payload:
        return payload
    return None


def _tool_identity_hint(tool, args=None):
    hint = f"langchain/tool/{_safe_path_part(_tool_name(tool))}"
    payload = _tool_call_payload(args or ())
    if payload is not None:
        hint = f"{hint}/tool_call/{_safe_path_part(RUNTIME.plain_value(payload.get('id')))}"
    return _with_active_identity_prefix(hint)


def _tool_metadata(tool, method, args=None):
    metadata = {
        "framework": "langchain",
        "component": "tool",
        "method": method,
        "tool_name": _tool_name(tool),
    }
    payload = _tool_call_payload(args or ())
    if payload is not None:
        metadata["tool_call_id"] = payload.get("id")
    metadata.update(_class_metadata(tool))
    return metadata


def _is_runtime_record_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return False
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        return False

    descriptors = [
        f"{cls.__module__}.{cls.__qualname__}".lower()
        for cls in getattr(value.__class__, "__mro__", (value.__class__,))
    ]
    class_names = [
        cls.__qualname__.lower()
        for cls in getattr(value.__class__, "__mro__", (value.__class__,))
    ]
    if any(name.startswith("toolruntime") for name in class_names):
        return True

    framework_modules = ("langchain", "langchain_core", "langgraph", "deepagents")
    if not any(descriptor.startswith(framework_modules) for descriptor in descriptors):
        return False

    internal_fragments = (
        "toolruntime",
        "runtime",
        "runcontrol",
        "pregel",
        "callback",
        "executor",
        "streamwriter",
        "stream_writer",
        "configurableruntime",
        "configurable_runtime",
    )
    if any(
        fragment in descriptor
        for descriptor in descriptors
        for fragment in internal_fragments
    ):
        return True

    manager_fragments = (
        "callbackmanager",
        "callback_manager",
        "runmanager",
        "run_manager",
    )
    if any(
        fragment in descriptor
        for descriptor in descriptors
        for fragment in manager_fragments
    ):
        return True

    store_fragments = (
        ".store.",
        "basestore",
        "base_store",
        "checkpointstore",
        "checkpoint_store",
    )
    return any(
        descriptor.startswith(framework_modules)
        and any(fragment in descriptor for fragment in store_fragments)
        for descriptor in descriptors
    )


def _sanitize_tool_record_value(value):
    def sanitize(item, seen):
        if isinstance(item, dict):
            item_id = id(item)
            if item_id in seen:
                return {"__kind__": "cycle", "type": "dict"}
            seen.add(item_id)
            try:
                out = {}
                for key, child in item.items():
                    converted = sanitize(child, seen)
                    if converted is not _DROP_FROM_TOOL_RECORD:
                        out[str(RUNTIME.plain_value(key))] = converted
                return normalize_for_json(out)
            finally:
                seen.remove(item_id)

        if isinstance(item, (list, tuple, set, frozenset)):
            item_id = id(item)
            if item_id in seen:
                return {"__kind__": "cycle", "type": type(item).__name__}
            seen.add(item_id)
            try:
                out = []
                for child in item:
                    converted = sanitize(child, seen)
                    if converted is not _DROP_FROM_TOOL_RECORD:
                        out.append(converted)
                return normalize_for_json(out)
            finally:
                seen.remove(item_id)

        item = RUNTIME.plain_value(item)
        if _is_runtime_record_value(item):
            return _DROP_FROM_TOOL_RECORD
        return normalize_for_json(item)

    return sanitize(value, set())


def _tool_record_inputs(method, args, kwargs):
    record_kwargs = {}

    def put_record_value(key, value):
        converted = _sanitize_tool_record_value(value)
        if converted is not _DROP_FROM_TOOL_RECORD:
            record_kwargs[key] = converted

    for key, value in kwargs.items():
        if key in _TOOL_CONFIG_KWARGS:
            continue
        put_record_value(key, value)
    if args:
        record_kwargs = dict(record_kwargs)
        raw_first_arg = args[0]
        first_arg = RUNTIME.plain_value(raw_first_arg)
        if method in {"invoke", "ainvoke"} and isinstance(first_arg, dict):
            record_value = (
                raw_first_arg if isinstance(raw_first_arg, dict) else first_arg
            )
            if first_arg.get("type") == "tool_call":
                put_record_value("tool_call", record_value)
            else:
                put_record_value("input", record_value)
        elif method in {"run", "arun"}:
            put_record_value("input", raw_first_arg)
    return record_kwargs


def _coerce_toolnode_response(response, args):
    payload = _tool_call_payload(args)
    if payload is None:
        return response
    if response.__class__.__name__ in {"ToolMessage", "Command"}:
        return response
    if _is_tool_message_like_record(response):
        return _tool_message_from_record(response, payload)
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return response
    sources = RUNTIME.get_prov(response)
    content = RUNTIME.plain_value(response)
    if not isinstance(content, list):
        content = str(content)
    message = ToolMessage(
        content=content,
        name=RUNTIME.plain_value(payload.get("name")),
        tool_call_id=RUNTIME.plain_value(payload.get("id")),
    )
    return RUNTIME.seed_value(message, sources)


def _tool_call_records_factory(session):
    def build(record: dict[str, Any]) -> list[dict[str, Any]]:
        return build_tool_call_records(record, next_record_uid=session.next_record_uid)

    return build


def _is_tool_message_like_record(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") == "tool"
        and "content" in value
        and "tool_call_id" in value
    )


def _tool_message_from_record(value: dict[str, Any], payload: dict[str, Any]):
    from langchain_core.messages import ToolMessage

    status = value.get("status")
    if status not in {"success", "error"}:
        status = "success"
    sources = RUNTIME.get_prov(value)
    message = ToolMessage(
        content=value.get("content", ""),
        name=value.get("name") or RUNTIME.plain_value(payload.get("name")),
        tool_call_id=value.get("tool_call_id") or RUNTIME.plain_value(payload.get("id")),
        additional_kwargs=value.get("additional_kwargs") or {},
        response_metadata=value.get("response_metadata") or {},
        status=status,
    )
    return RUNTIME.seed_value(message, sources)


def _in_external_boundary():
    kinds = RUNTIME.current_kinds_snapshot()
    return bool(kinds and kinds[-1] in {"llm", "tool"})


def _resolve_targets(modules):
    if modules is not None:
        if isinstance(modules, dict):
            return modules.get("BaseChatModel"), modules.get("BaseTool")
        if isinstance(modules, (list, tuple)):
            chat = modules[0] if len(modules) > 0 else None
            tool = modules[1] if len(modules) > 1 else None
            return chat, tool
        return getattr(modules, "BaseChatModel", None), getattr(
            modules, "BaseTool", None
        )
    try:
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.tools import BaseTool
    except Exception:
        return None, None
    return BaseChatModel, BaseTool


def _active_session(state):
    if not state["order"]:
        return None
    return get_current_session()


def install_langchain_patch(modules=None, module=None):
    if modules is None:
        modules = module
    BaseChatModel, BaseTool = _resolve_targets(modules)
    if BaseChatModel is None and BaseTool is None:
        return _noop_restore

    key = (BaseChatModel, BaseTool)
    state = _INSTALL_STATE.get(key)
    if state is None:
        state = {
            "installs": set(),
            "order": [],
            "originals": {},
        }
        _INSTALL_STATE[key] = state

        if BaseChatModel is not None:
            state["originals"].update(
                {
                    "chat_invoke": BaseChatModel.invoke,
                    "chat_ainvoke": BaseChatModel.ainvoke,
                    "chat_stream": BaseChatModel.stream,
                    "chat_astream": BaseChatModel.astream,
                }
            )

            def invoke(self, *args, **kwargs):
                session = _active_session(state)
                original = state["originals"]["chat_invoke"]
                if session is None or _in_external_boundary():
                    return original(self, *args, **kwargs)
                return session.handle_sync_llm_boundary(
                    name=_chat_name(self),
                    record_kwargs=_chat_record_kwargs(args, kwargs),
                    invoke=lambda: original(self, *args, **kwargs),
                    metadata=_chat_metadata(self, "invoke"),
                    semantic_hint=_chat_identity_hint(self),
                    provider="langchain",
                    api="chat_model.invoke",
                    extra_records_factory=_tool_call_records_factory(session),
                )

            async def ainvoke(self, *args, **kwargs):
                session = _active_session(state)
                original = state["originals"]["chat_ainvoke"]
                if session is None or _in_external_boundary():
                    return await original(self, *args, **kwargs)
                return await session.handle_async_llm_boundary(
                    name=_chat_name(self),
                    record_kwargs=_chat_record_kwargs(args, kwargs),
                    invoke=lambda: original(self, *args, **kwargs),
                    metadata=_chat_metadata(self, "ainvoke"),
                    semantic_hint=_chat_identity_hint(self),
                    provider="langchain",
                    api="chat_model.ainvoke",
                    extra_records_factory=_tool_call_records_factory(session),
                )

            def stream(self, *args, **kwargs):
                session = _active_session(state)
                original = state["originals"]["chat_stream"]
                if session is None or _in_external_boundary():
                    return original(self, *args, **kwargs)
                chunks = session.handle_sync_llm_boundary(
                    name=_chat_name(self),
                    record_kwargs=_chat_record_kwargs(args, kwargs),
                    invoke=lambda: list(original(self, *args, **kwargs)),
                    metadata=_chat_metadata(self, "stream"),
                    semantic_hint=_chat_identity_hint(self),
                    provider="langchain",
                    api="chat_model.stream",
                    extra_records_factory=_tool_call_records_factory(session),
                )
                return iter(chunks)

            async def astream(self, *args, **kwargs):
                session = _active_session(state)
                original = state["originals"]["chat_astream"]
                if session is None or _in_external_boundary():
                    async for chunk in original(self, *args, **kwargs):
                        yield chunk
                    return

                async def collect_chunks():
                    return [chunk async for chunk in original(self, *args, **kwargs)]

                chunks = await session.handle_async_llm_boundary(
                    name=_chat_name(self),
                    record_kwargs=_chat_record_kwargs(args, kwargs),
                    invoke=collect_chunks,
                    metadata=_chat_metadata(self, "astream"),
                    semantic_hint=_chat_identity_hint(self),
                    provider="langchain",
                    api="chat_model.astream",
                    extra_records_factory=_tool_call_records_factory(session),
                )
                for chunk in chunks:
                    yield chunk

            BaseChatModel.invoke = invoke
            BaseChatModel.ainvoke = ainvoke
            BaseChatModel.stream = stream
            BaseChatModel.astream = astream

        if BaseTool is not None:
            state["originals"].update(
                {
                    "tool_invoke": BaseTool.invoke,
                    "tool_ainvoke": BaseTool.ainvoke,
                    "tool_run": BaseTool.run,
                    "tool_arun": BaseTool.arun,
                }
            )

            def _record_tool(tool, method, original_key, args, kwargs):
                session = _active_session(state)
                original = state["originals"][original_key]
                if session is None or _in_external_boundary():
                    return original(tool, *args, **kwargs)
                record_input = {
                    "tool_name": _tool_name(tool),
                    "arguments": _tool_record_inputs(method, args, kwargs),
                    "framework": "langchain",
                    "component": "tool",
                    "method": method,
                }
                response = session.handle_sync_tool_boundary(
                    tool_name=_tool_name(tool),
                    input_record=record_input,
                    invoke=lambda: _coerce_toolnode_response(
                        original(tool, *args, **kwargs), args
                    ),
                    metadata=_tool_metadata(tool, method, args),
                    semantic_hint=_tool_identity_hint(tool, args),
                    input_arguments=record_input["arguments"],
                )
                return _coerce_toolnode_response(response, args)

            async def _record_tool_async(tool, method, original_key, args, kwargs):
                session = _active_session(state)
                original = state["originals"][original_key]
                if session is None or _in_external_boundary():
                    return await original(tool, *args, **kwargs)

                async def invoke_tool():
                    response = original(tool, *args, **kwargs)
                    if hasattr(response, "__await__"):
                        response = await response
                    return _coerce_toolnode_response(response, args)

                record_input = {
                    "tool_name": _tool_name(tool),
                    "arguments": _tool_record_inputs(method, args, kwargs),
                    "framework": "langchain",
                    "component": "tool",
                    "method": method,
                }
                response = await session.handle_async_tool_boundary(
                    tool_name=_tool_name(tool),
                    input_record=record_input,
                    invoke=invoke_tool,
                    metadata=_tool_metadata(tool, method, args),
                    semantic_hint=_tool_identity_hint(tool, args),
                    input_arguments=record_input["arguments"],
                )
                return _coerce_toolnode_response(response, args)

            def tool_invoke(self, *args, **kwargs):
                return _record_tool(self, "invoke", "tool_invoke", args, kwargs)

            async def tool_ainvoke(self, *args, **kwargs):
                return await _record_tool_async(
                    self, "ainvoke", "tool_ainvoke", args, kwargs
                )

            def run(self, *args, **kwargs):
                return _record_tool(self, "run", "tool_run", args, kwargs)

            async def arun(self, *args, **kwargs):
                return await _record_tool_async(self, "arun", "tool_arun", args, kwargs)

            BaseTool.invoke = tool_invoke
            BaseTool.ainvoke = tool_ainvoke
            BaseTool.run = run
            BaseTool.arun = arun

    token = object()
    state["installs"].add(token)
    state["order"].append(token)
    restored = False

    def restore():
        nonlocal restored
        if restored:
            return
        restored = True
        current_state = _INSTALL_STATE.get(key)
        if current_state is None:
            return
        current_state["installs"].discard(token)
        current_state["order"] = [
            item for item in current_state["order"] if item is not token
        ]
        if current_state["order"]:
            return
        originals = current_state["originals"]
        if BaseChatModel is not None:
            BaseChatModel.invoke = originals["chat_invoke"]
            BaseChatModel.ainvoke = originals["chat_ainvoke"]
            BaseChatModel.stream = originals["chat_stream"]
            BaseChatModel.astream = originals["chat_astream"]
        if BaseTool is not None:
            BaseTool.invoke = originals["tool_invoke"]
            BaseTool.ainvoke = originals["tool_ainvoke"]
            BaseTool.run = originals["tool_run"]
            BaseTool.arun = originals["tool_arun"]
        del _INSTALL_STATE[key]

    return restore


_CHAT_CONFIG_KWARGS = {
    "callbacks",
    "config",
    "configurable",
    "metadata",
    "recursion_limit",
    "run_id",
    "run_name",
    "run_manager",
    "tags",
}


_TOOL_CONFIG_KWARGS = {
    "callbacks",
    "config",
    "configurable",
    "metadata",
    "recursion_limit",
    "run_id",
    "run_name",
    "run_manager",
    "tags",
}
