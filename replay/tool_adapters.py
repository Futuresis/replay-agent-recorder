from __future__ import annotations

import functools
import inspect
from collections.abc import Iterable, MutableMapping
from typing import Any, Callable, Protocol

from .filesystem_effects import FilesystemCapture
from .tools import invoke_tool, invoke_tool_sync


class ToolAdapter(Protocol):
    """Protocol for user-provided tool system adapters."""

    def install(self) -> None:
        """Attach replay recording/replay to the target tool system."""

    def uninstall(self) -> None:
        """Restore the target tool system to its original call behavior."""


class BaseToolAdapter:
    """Base class for adapters that translate tool calls into replay events."""

    namespace: str | None = None
    version: str | None = None
    fs_capture: FilesystemCapture | None = None

    def tool_name(self, name: str) -> str:
        if self.namespace:
            return f"{self.namespace}:{name}"
        return name

    async def invoke_async(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        invoke: Callable[[], Any],
    ) -> Any:
        return await invoke_tool(
            self.tool_name(name),
            arguments,
            invoke,
            namespace=self.namespace,
            version=self.version,
            fs_capture=self.fs_capture,
        )

    def invoke_sync(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        invoke: Callable[[], Any],
    ) -> Any:
        return invoke_tool_sync(
            self.tool_name(name),
            arguments,
            invoke,
            namespace=self.namespace,
            version=self.version,
            fs_capture=self.fs_capture,
        )


class MappingToolAdapter(BaseToolAdapter):
    """Wrap a mutable mapping of tool name to callable.

    This adapter is useful for custom registries shaped like
    {"search": search_fn, "calculator": calculator_fn}. Each wrapped callable
    is expected to receive one JSON-like arguments mapping.
    """

    def __init__(
        self,
        registry: MutableMapping[str, Callable[..., Any]],
        *,
        namespace: str | None = None,
        version: str | None = None,
        fs_capture: FilesystemCapture | None = None,
    ) -> None:
        self.registry = registry
        self.namespace = namespace
        self.version = version
        self.fs_capture = fs_capture
        self._originals: dict[str, Callable[..., Any]] = {}

    def install(self) -> None:
        for name, fn in list(self.registry.items()):
            if name in self._originals:
                continue
            self._originals[name] = fn
            self.registry[name] = self._wrap(name, fn)

    def uninstall(self) -> None:
        for name, fn in self._originals.items():
            self.registry[name] = fn
        self._originals.clear()

    def _wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None)):

            @functools.wraps(fn)
            async def async_wrapper(arguments: dict[str, Any] | None = None) -> Any:
                return await self.invoke_async(name, arguments or {}, lambda: fn(arguments or {}))

            async_wrapper._replay_tool_wrapper = True
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(arguments: dict[str, Any] | None = None) -> Any:
            return self.invoke_sync(name, arguments or {}, lambda: fn(arguments or {}))

        sync_wrapper._replay_tool_wrapper = True
        return sync_wrapper


class MethodToolAdapter(BaseToolAdapter):
    """Wrap an object method shaped like call_tool(name, arguments)."""

    def __init__(
        self,
        target: Any,
        method_name: str,
        *,
        namespace: str | None = None,
        version: str | None = None,
        fs_capture: FilesystemCapture | None = None,
        name_arg: int = 0,
        arguments_arg: int = 1,
    ) -> None:
        self.target = target
        self.method_name = method_name
        self.namespace = namespace
        self.version = version
        self.fs_capture = fs_capture
        self.name_arg = name_arg
        self.arguments_arg = arguments_arg
        self._original: Callable[..., Any] | None = None

    def install(self) -> None:
        if self._original is not None:
            return

        original = getattr(self.target, self.method_name)
        self._original = original
        setattr(self.target, self.method_name, self._wrap(original))

    def uninstall(self) -> None:
        if self._original is None:
            return
        setattr(self.target, self.method_name, self._original)
        self._original = None

    def _wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None)):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tool_name, arguments = self._extract_call(args, kwargs)
                return await self.invoke_async(tool_name, arguments, lambda: fn(*args, **kwargs))

            async_wrapper._replay_tool_wrapper = True
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name, arguments = self._extract_call(args, kwargs)
            return self.invoke_sync(tool_name, arguments, lambda: fn(*args, **kwargs))

        sync_wrapper._replay_tool_wrapper = True
        return sync_wrapper

    def _extract_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if "name" in kwargs:
            raw_name = kwargs["name"]
        elif "tool_name" in kwargs:
            raw_name = kwargs["tool_name"]
        elif len(args) > self.name_arg:
            raw_name = args[self.name_arg]
        else:
            raise TypeError(f"{self.method_name} call is missing a tool name.")

        if "arguments" in kwargs:
            raw_arguments = kwargs["arguments"]
        elif "args" in kwargs:
            raw_arguments = kwargs["args"]
        elif len(args) > self.arguments_arg:
            raw_arguments = args[self.arguments_arg]
        else:
            raw_arguments = {}

        if raw_arguments is None:
            raw_arguments = {}
        if not isinstance(raw_arguments, dict):
            raise TypeError(
                f"{self.method_name} tool arguments must be a dict, "
                f"got {type(raw_arguments).__name__}."
            )
        return str(raw_name), raw_arguments


class ClassMethodToolAdapter(BaseToolAdapter):
    """Wrap a class method shaped like call_tool(name, arguments).

    This adapter is for framework-owned clients where wrapper code cannot easily
    access the concrete client instance before the agent starts. It patches the
    method on the class and restores the original method on uninstall.
    """

    def __init__(
        self,
        target_cls: type[Any],
        method_name: str,
        *,
        namespace: str | None = None,
        version: str | None = None,
        fs_capture: FilesystemCapture | None = None,
        name_arg: int = 1,
        arguments_arg: int = 2,
        name_kwarg: str | tuple[str, ...] = ("name", "tool_name"),
        arguments_kwarg: str | tuple[str, ...] = ("arguments", "args"),
        tool_filter: Callable[[str, dict[str, Any]], bool] | Iterable[str] | None = None,
        arguments_factory: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.target_cls = target_cls
        self.method_name = method_name
        self.namespace = namespace
        self.version = version
        self.fs_capture = fs_capture
        self.name_arg = name_arg
        self.arguments_arg = arguments_arg
        self.name_kwarg = _as_tuple(name_kwarg)
        self.arguments_kwarg = _as_tuple(arguments_kwarg)
        self.tool_filter = _normalize_tool_filter(tool_filter)
        self.arguments_factory = arguments_factory
        self._original: Callable[..., Any] | None = None

    def install(self) -> None:
        if self._original is not None:
            return

        original = getattr(self.target_cls, self.method_name)
        self._original = original
        setattr(self.target_cls, self.method_name, self._wrap(original))

    def uninstall(self) -> None:
        if self._original is None:
            return
        setattr(self.target_cls, self.method_name, self._original)
        self._original = None

    def _wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(getattr(fn, "__call__", None)):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tool_name, arguments = self._extract_call(args, kwargs)
                invoke = lambda: fn(*args, **kwargs)
                if not self._should_record(tool_name, arguments):
                    return await invoke()
                return await self.invoke_async(tool_name, arguments, invoke)

            async_wrapper._replay_tool_wrapper = True
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name, arguments = self._extract_call(args, kwargs)
            invoke = lambda: fn(*args, **kwargs)
            if not self._should_record(tool_name, arguments):
                return invoke()
            return self.invoke_sync(tool_name, arguments, invoke)

        sync_wrapper._replay_tool_wrapper = True
        return sync_wrapper

    def _extract_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raw_name = _first_kwarg(kwargs, self.name_kwarg)
        if raw_name is _MISSING:
            if len(args) <= self.name_arg:
                raise TypeError(f"{self.method_name} call is missing a tool name.")
            raw_name = args[self.name_arg]

        if self.arguments_factory is not None:
            raw_arguments = self.arguments_factory(args, kwargs)
        else:
            raw_arguments = _first_kwarg(kwargs, self.arguments_kwarg)
            if raw_arguments is _MISSING:
                raw_arguments = args[self.arguments_arg] if len(args) > self.arguments_arg else {}

        if raw_arguments is None:
            raw_arguments = {}
        if not isinstance(raw_arguments, dict):
            raise TypeError(
                f"{self.method_name} tool arguments must be a dict, "
                f"got {type(raw_arguments).__name__}."
            )
        return str(raw_name), raw_arguments

    def _should_record(self, name: str, arguments: dict[str, Any]) -> bool:
        if self.tool_filter is None:
            return True
        if callable(self.tool_filter):
            return bool(self.tool_filter(name, arguments))
        return name in self.tool_filter


_MISSING = object()


def _as_tuple(value: str | tuple[str, ...]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else value


def _first_kwarg(kwargs: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in kwargs:
            return kwargs[name]
    return _MISSING


def _normalize_tool_filter(
    tool_filter: Callable[[str, dict[str, Any]], bool] | Iterable[str] | None,
) -> Callable[[str, dict[str, Any]], bool] | frozenset[str] | None:
    if tool_filter is None or callable(tool_filter):
        return tool_filter
    if isinstance(tool_filter, str):
        return frozenset({tool_filter})
    return frozenset(tool_filter)
