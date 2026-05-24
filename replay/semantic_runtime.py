from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass, field
import inspect
import operator
import threading
from typing import Any, Iterable
import weakref


@dataclass(frozen=True)
class Source:
    run_id: str
    record_uid: str
    kind: str
    path_id: str | None = None


class _WeakProvenanceEntry:
    _MISSING = object()

    def __init__(self, sources: set[Source], value: Any = _MISSING):
        self.ref: weakref.ReferenceType[Any] | None = None
        self.value = value
        self.sources = sources

    def matches(self, value: Any) -> bool:
        if self.ref is not None:
            return self.ref() is value
        return self.value is value


@dataclass
class _RuntimeState:
    state_id: int
    enabled: bool = False
    generation: int = 0
    weak_provenance: dict[int, _WeakProvenanceEntry] = field(default_factory=dict)
    weak_control_provenance: dict[int, _WeakProvenanceEntry] = field(default_factory=dict)


@dataclass(frozen=True)
class _RuntimeContextToken:
    state: contextvars.Token
    pc_stack: contextvars.Token
    comp_stack: contextvars.Token
    kinds: contextvars.Token
    spans: contextvars.Token


_PROVENANCE_ATTR = "_replay_provenance"
_CONTROL_PROVENANCE_ATTR = "_replay_control_provenance"
_MISSING_SOURCE = object()


@dataclass
class _EmbeddedProvenance:
    state_id: int
    generation: int
    sources: set[Source]


class Condition:
    def __init__(self, value: Any, provenance: Iterable[Source] | None = None):
        self.value = value.value if isinstance(value, Condition) else value
        self.provenance = set(provenance or set())

    def __bool__(self) -> bool:
        return bool(self.value)

    def __repr__(self) -> str:
        return repr(self.value)


class _TrackedNone:
    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: Any) -> bool:
        return other is None or isinstance(other, _TrackedNone)

    def __hash__(self) -> int:
        return hash(None)

    def __repr__(self) -> str:
        return "None"

    __str__ = __repr__


class _TrackedBool:
    def __init__(self, value: Any):
        self.value = bool(value)

    def __bool__(self) -> bool:
        return self.value

    def __eq__(self, other: Any) -> bool:
        other_value = other.value if isinstance(other, _TrackedBool) else other
        return self.value == other_value

    def __hash__(self) -> int:
        return hash(self.value)

    def __int__(self) -> int:
        return int(self.value)

    def __index__(self) -> int:
        return int(self.value)

    def __repr__(self) -> str:
        return repr(self.value)

    __str__ = __repr__


class _TrackedStr(str):
    pass


class _TrackedBytes(bytes):
    pass


class _TrackedInt(int):
    pass


class _TrackedFloat(float):
    pass


class _TrackedComplex(complex):
    pass


class _TrackedTuple(tuple):
    pass


class _TrackedFrozenSet(frozenset):
    pass


_TRACKED_SCALARS = {
    str: _TrackedStr,
    bytes: _TrackedBytes,
    int: _TrackedInt,
    float: _TrackedFloat,
    complex: _TrackedComplex,
    tuple: _TrackedTuple,
    frozenset: _TrackedFrozenSet,
}

_TRACKED_TYPES = (
    _TrackedNone,
    _TrackedBool,
    _TrackedStr,
    _TrackedBytes,
    _TrackedInt,
    _TrackedFloat,
    _TrackedComplex,
    _TrackedTuple,
    _TrackedFrozenSet,
)


def _embedded_provenance(value: Any, state: _RuntimeState) -> set[Source]:
    if not isinstance(value, _TRACKED_TYPES):
        return set()
    try:
        provenance = getattr(value, _PROVENANCE_ATTR, None)
    except Exception:
        return set()
    if (
        not isinstance(provenance, _EmbeddedProvenance)
        or provenance.state_id != state.state_id
        or provenance.generation != state.generation
    ):
        return set()
    return set(provenance.sources)


def _embedded_control_provenance(value: Any, state: _RuntimeState) -> set[Source]:
    if not isinstance(value, _TRACKED_TYPES):
        return set()
    try:
        provenance = getattr(value, _CONTROL_PROVENANCE_ATTR, None)
    except Exception:
        return set()
    if (
        not isinstance(provenance, _EmbeddedProvenance)
        or provenance.state_id != state.state_id
        or provenance.generation != state.generation
    ):
        return set()
    return set(provenance.sources)


def _add_embedded_provenance(value: Any, state: _RuntimeState, sources: set[Source]) -> None:
    existing = getattr(value, _PROVENANCE_ATTR, None)
    if (
        not isinstance(existing, _EmbeddedProvenance)
        or existing.state_id != state.state_id
        or existing.generation != state.generation
    ):
        setattr(value, _PROVENANCE_ATTR, _EmbeddedProvenance(state.state_id, state.generation, set(sources)))
    else:
        existing.sources.update(sources)


def _add_embedded_control_provenance(value: Any, state: _RuntimeState, sources: set[Source]) -> None:
    existing = getattr(value, _CONTROL_PROVENANCE_ATTR, None)
    if (
        not isinstance(existing, _EmbeddedProvenance)
        or existing.state_id != state.state_id
        or existing.generation != state.generation
    ):
        setattr(value, _CONTROL_PROVENANCE_ATTR, _EmbeddedProvenance(state.state_id, state.generation, set(sources)))
    else:
        existing.sources.update(sources)


class _ContextStack:
    def __init__(self, var: contextvars.ContextVar[tuple[Any, ...]]):
        self.var = var

    def _items(self) -> tuple[Any, ...]:
        return self.var.get()

    def append(self, item: Any) -> None:
        self.var.set(self._items() + (item,))

    def pop(self) -> Any:
        items = self._items()
        if not items:
            raise IndexError("pop from empty provenance context stack")
        self.var.set(items[:-1])
        return items[-1]

    def clear(self) -> None:
        self.var.set(())

    def __iter__(self):
        return iter(self._items())

    def __bool__(self) -> bool:
        return bool(self._items())


class Runtime:
    def __init__(self, *, enabled: bool = False):
        self._lock = threading.RLock()
        self._next_state_id = 1
        self._root_state = _RuntimeState(state_id=self._allocate_state_id(), enabled=enabled)
        self._state_var = contextvars.ContextVar(
            f"replay_semantic_state_{id(self)}",
            default=self._root_state,
        )
        self._pc_stack_var = contextvars.ContextVar(f"replay_semantic_pc_stack_{id(self)}", default=())
        self._comp_stack_var = contextvars.ContextVar(f"replay_semantic_comp_stack_{id(self)}", default=())
        self._current_kinds_var = contextvars.ContextVar(f"replay_semantic_current_kinds_{id(self)}", default=())
        self._span_stack_var = contextvars.ContextVar(f"replay_semantic_span_stack_{id(self)}", default=())
        self.pc_stack = _ContextStack(self._pc_stack_var)

    def _state(self) -> _RuntimeState:
        return self._state_var.get()

    def _allocate_state_id(self) -> int:
        state_id = self._next_state_id
        self._next_state_id += 1
        return state_id

    @property
    def enabled(self) -> bool:
        return self._state().enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._state().enabled = bool(value)

    def enter_context(self, *, enabled: bool | None = None) -> _RuntimeContextToken:
        state = _RuntimeState(
            state_id=self._allocate_state_id(),
            enabled=self.enabled if enabled is None else bool(enabled),
        )
        return _RuntimeContextToken(
            state=self._state_var.set(state),
            pc_stack=self._pc_stack_var.set(()),
            comp_stack=self._comp_stack_var.set(()),
            kinds=self._current_kinds_var.set(()),
            spans=self._span_stack_var.set(()),
        )

    def exit_context(self, token: _RuntimeContextToken) -> None:
        self._span_stack_var.reset(token.spans)
        self._current_kinds_var.reset(token.kinds)
        self._comp_stack_var.reset(token.comp_stack)
        self._pc_stack_var.reset(token.pc_stack)
        self._state_var.reset(token.state)

    def reset(self, *, enabled: bool | None = None) -> "Runtime":
        with self._lock:
            state = self._state()
            state.weak_provenance = {}
            state.weak_control_provenance = {}
            state.generation += 1
            self.pc_stack.clear()
            self._comp_stack_var.set(())
            self._current_kinds_var.set(())
            self._span_stack_var.set(())
            if enabled is not None:
                state.enabled = bool(enabled)
        return self

    def enable(self) -> "Runtime":
        self.enabled = True
        return self

    def disable(self) -> "Runtime":
        self.enabled = False
        self.pc_stack.clear()
        self._comp_stack_var.set(())
        self._current_kinds_var.set(())
        self._span_stack_var.set(())
        return self

    def _normalize_sources(self, sources: Any) -> set[Source]:
        if sources is None:
            return set()
        if isinstance(sources, Source):
            return {sources}
        if isinstance(sources, (set, frozenset, list, tuple)):
            return set(sources)
        return {sources}

    def _track_value(self, value: Any, provenance: set[Source]) -> Any:
        if isinstance(value, _TRACKED_TYPES):
            return value
        if value is None:
            return _TrackedNone()
        if isinstance(value, bool):
            return _TrackedBool(value)
        tracker = _TRACKED_SCALARS.get(type(value))
        if tracker is not None:
            return tracker(value)
        return value

    def _track_mutable_value(self, value: Any) -> Any:
        return value

    def _set_weak_provenance(self, value: Any, provenance: set[Source]) -> None:
        state = self._state()
        value_id = id(value)
        entry = state.weak_provenance.get(value_id)
        if entry is not None and entry.matches(value):
            entry.sources.update(provenance)
            return

        entry = _WeakProvenanceEntry(set(provenance))

        def cleanup(_ref, *, owner_state=state, owner_id=value_id, owner_entry=entry):
            with self._lock:
                if owner_state.weak_provenance.get(owner_id) is owner_entry:
                    owner_state.weak_provenance.pop(owner_id, None)

        try:
            entry.ref = weakref.ref(value, cleanup)
        except TypeError:
            entry.value = value
        state.weak_provenance[value_id] = entry

    def _set_weak_control_provenance(self, value: Any, provenance: set[Source]) -> None:
        state = self._state()
        value_id = id(value)
        entry = state.weak_control_provenance.get(value_id)
        if entry is not None and entry.matches(value):
            entry.sources.update(provenance)
            return

        entry = _WeakProvenanceEntry(set(provenance))

        def cleanup(_ref, *, owner_state=state, owner_id=value_id, owner_entry=entry):
            with self._lock:
                if owner_state.weak_control_provenance.get(owner_id) is owner_entry:
                    owner_state.weak_control_provenance.pop(owner_id, None)

        try:
            entry.ref = weakref.ref(value, cleanup)
        except TypeError:
            entry.value = value
        state.weak_control_provenance[value_id] = entry

    def _direct_provenance(self, value: Any) -> set[Source]:
        state = self._state()
        out = _embedded_provenance(value, state)
        entry = state.weak_provenance.get(id(value))
        if entry is not None and entry.matches(value):
            out.update(entry.sources)
        return out

    def _direct_control_provenance(self, value: Any) -> set[Source]:
        state = self._state()
        out = _embedded_control_provenance(value, state)
        entry = state.weak_control_provenance.get(id(value))
        if entry is not None and entry.matches(value):
            out.update(entry.sources)
        return out

    def _attach_provenance(self, value: Any, provenance: set[Source]) -> None:
        if not provenance:
            return
        if isinstance(value, _TRACKED_TYPES):
            _add_embedded_provenance(value, self._state(), provenance)
        else:
            self._set_weak_provenance(value, provenance)

    def _attach_control_provenance(self, value: Any, provenance: set[Source]) -> None:
        if not provenance:
            return
        if isinstance(value, _TRACKED_TYPES):
            _add_embedded_control_provenance(value, self._state(), provenance)
        else:
            self._set_weak_control_provenance(value, provenance)

    def set_provenance(self, value: Any, sources: Any) -> Any:
        if not self.enabled:
            return value
        provenance = self._normalize_sources(sources)
        if not provenance:
            return value
        value = self._track_value(value, provenance)
        with self._lock:
            self._attach_provenance(value, provenance)
        return value

    set_prov = set_provenance

    def set_control_provenance(self, value: Any, sources: Any) -> Any:
        if not self.enabled:
            return value
        provenance = self._normalize_sources(sources)
        if not provenance:
            return value
        value = self._track_value(value, provenance)
        with self._lock:
            self._attach_control_provenance(value, provenance)
        return value

    def seed_value(self, value: Any, sources: Any) -> Any:
        return self.set_provenance(value, sources)

    def current_kinds_snapshot(self) -> list[str]:
        return list(self._current_kinds_var.get())

    def push_kind(self, kind: str) -> None:
        self._current_kinds_var.set(self._current_kinds_var.get() + (kind,))

    def pop_kind(self) -> str:
        items = self._current_kinds_var.get()
        if not items:
            raise IndexError("pop from empty runtime kind stack")
        self._current_kinds_var.set(items[:-1])
        return items[-1]

    def span_stack_snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._span_stack_var.get()]

    @contextmanager
    def context_span(self, kind: str, name: str, metadata: dict[str, Any] | None = None):
        span = {"kind": kind, "name": name, "metadata": dict(metadata or {})}
        token = self._span_stack_var.set(self._span_stack_var.get() + (span,))
        try:
            yield span
        finally:
            self._span_stack_var.reset(token)

    def merge_metadata(
        self,
        metadata: dict[str, Any] | None = None,
        *,
        semantic_hint: str | None = None,
    ) -> dict[str, Any]:
        merged = dict(metadata or {})
        spans = self.span_stack_snapshot()
        if spans:
            merged["spans"] = spans
        if semantic_hint:
            semantic = merged.get("semantic") if isinstance(merged.get("semantic"), dict) else {}
            semantic = dict(semantic)
            semantic["callsite_fingerprint"] = semantic_hint
            merged["semantic"] = semantic
        return merged

    def current_record_metadata(self) -> dict[str, Any] | None:
        spans = self.span_stack_snapshot()
        if not spans:
            return None
        return {"spans": spans}

    def current_record_semantic_hint(self) -> str | None:
        for span in reversed(self._span_stack_var.get()):
            metadata = span.get("metadata") if isinstance(span, dict) else None
            if isinstance(metadata, dict) and isinstance(metadata.get("identity_hint"), str):
                return metadata["identity_hint"]
        return None

    def seed_response(self, response: Any, source: Source | Iterable[Source]) -> Any:
        return self.set_provenance(response, source)

    def get_provenance(self, value: Any) -> set[Source]:
        if not self.enabled:
            return set()
        seen: set[int] = set()

        def collect(item: Any) -> set[Source]:
            item_id = id(item)
            if item_id in seen:
                return set()
            seen.add(item_id)
            with self._lock:
                out = self._direct_provenance(item)
            if isinstance(item, Condition):
                out.update(item.provenance)
                out.update(collect(item.value))
            elif isinstance(item, dict):
                for key, val in item.items():
                    out.update(collect(key))
                    out.update(collect(val))
            elif isinstance(item, (list, tuple, set, frozenset)):
                for child in item:
                    out.update(collect(child))
            return out

        return collect(value)

    get_prov = get_provenance

    def get_control_provenance(self, value: Any) -> set[Source]:
        if not self.enabled:
            return set()
        seen: set[int] = set()

        def collect(item: Any) -> set[Source]:
            item_id = id(item)
            if item_id in seen:
                return set()
            seen.add(item_id)
            with self._lock:
                out = self._direct_control_provenance(item)
            if isinstance(item, Condition):
                out.update(collect(item.value))
            elif isinstance(item, dict):
                for key, val in item.items():
                    out.update(collect(key))
                    out.update(collect(val))
            elif isinstance(item, (list, tuple, set, frozenset)):
                for child in item:
                    out.update(collect(child))
            return out

        return collect(value)

    def plain_value(self, value: Any) -> Any:
        return self._plain_value(value, set())

    def _plain_value(self, value: Any, seen: set[int]) -> Any:
        if isinstance(value, Condition):
            return self._plain_value(value.value, seen)
        if isinstance(value, _TrackedNone):
            return None
        if isinstance(value, _TrackedBool):
            return value.value
        if isinstance(value, _TrackedStr):
            return str(value)
        if isinstance(value, _TrackedBytes):
            return bytes(value)
        if isinstance(value, _TrackedInt):
            return int(value)
        if isinstance(value, _TrackedFloat):
            return float(value)
        if isinstance(value, _TrackedComplex):
            return complex(value)
        if isinstance(value, _TrackedTuple):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return tuple(self._plain_value(item, seen) for item in value)
            finally:
                seen.remove(value_id)
        if isinstance(value, _TrackedFrozenSet):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return frozenset(self._plain_value(item, seen) for item in value)
            finally:
                seen.remove(value_id)
        if isinstance(value, tuple):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return tuple(self._plain_value(item, seen) for item in value)
            finally:
                seen.remove(value_id)
        if isinstance(value, frozenset):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return frozenset(self._plain_value(item, seen) for item in value)
            finally:
                seen.remove(value_id)
        return value

    def plain_snapshot(self, value: Any) -> Any:
        return self._plain_snapshot(value, set())

    def _plain_snapshot(self, value: Any, seen: set[int]) -> Any:
        value = self.plain_value(value)
        if isinstance(value, dict):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return {
                    self._plain_snapshot(key, seen): self._plain_snapshot(val, seen)
                    for key, val in value.items()
                }
            finally:
                seen.remove(value_id)
        if isinstance(value, list):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return [self._plain_snapshot(item, seen) for item in value]
            finally:
                seen.remove(value_id)
        if isinstance(value, tuple):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return tuple(self._plain_snapshot(item, seen) for item in value)
            finally:
                seen.remove(value_id)
        if isinstance(value, set):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return {self._plain_snapshot(item, seen) for item in value}
            finally:
                seen.remove(value_id)
        if isinstance(value, frozenset):
            value_id = id(value)
            if value_id in seen:
                return value
            seen.add(value_id)
            try:
                return frozenset(self._plain_snapshot(item, seen) for item in value)
            finally:
                seen.remove(value_id)
        return value

    def data_provenance(self, *values: Any) -> set[Source]:
        out: set[Source] = set()
        for value in values:
            out.update(self.get_provenance(value))
        return out

    collect_provenance = data_provenance

    def current_control_provenance(self) -> set[Source]:
        out: set[Source] = set()
        for item in self.pc_stack:
            out.update(self.get_provenance(item))
            out.update(self.get_control_provenance(item))
        return out

    current_pc = current_control_provenance

    def combine(self, *values: Any) -> set[Source]:
        return self.data_provenance(*values) | self.current_control_provenance()

    def _condition_provenance(self, value: Any, *sources: Any) -> set[Source]:
        current_control = self.current_control_provenance()
        value_data = self.get_provenance(value)
        value_control = self.get_control_provenance(value)
        if value_data:
            return value_data | value_control | current_control
        fallback_data = self.data_provenance(*sources)
        fallback_control: set[Source] = set()
        for source in sources:
            fallback_control.update(self.get_control_provenance(source))
        return fallback_data | fallback_control | current_control

    def _comparison_provenance(self, *operands: Any) -> set[Source]:
        provenance = self.data_provenance(*operands) | self.current_control_provenance()
        for operand in operands:
            provenance.update(self.get_control_provenance(operand))
        return provenance

    def capture_input_provenance(self, *values: Any, args: Any = None, kwargs: Any = None) -> dict[str, set[Source]]:
        data_values = list(values)
        if args is not None:
            data_values.append(args)
        if kwargs is not None:
            data_values.append(kwargs)
        control_sources = self.current_control_provenance()
        for value in data_values:
            control_sources.update(self.get_control_provenance(value))
        return {
            "data_sources": self.data_provenance(*data_values) - control_sources,
            "control_sources": control_sources,
        }

    def assign(self, value: Any) -> Any:
        value = self.set_provenance(value, self.combine(value))
        return self.set_control_provenance(value, self.get_control_provenance(value))

    def pack(self, value: Any, *sources: Any) -> Any:
        if self.enabled:
            value = self._track_mutable_value(value)
        value = self.set_provenance(value, self.combine(value, *sources))
        control_sources = self.get_control_provenance(value)
        for source in sources:
            control_sources.update(self.get_control_provenance(source))
        return self.set_control_provenance(value, control_sources)

    def unpack(self, values: Any) -> Any:
        if not self.enabled:
            return values
        provenance = self.combine(values)
        control_provenance = self.get_control_provenance(values)
        items = [
            self.set_control_provenance(
                self.set_provenance(item, self.get_provenance(item) | provenance),
                control_provenance,
            )
            for item in values
        ]
        return tuple(items) if isinstance(values, tuple) else items

    def binop(self, op: str, left: Any, right: Any) -> Any:
        operations = {
            "+": operator.add,
            "-": operator.sub,
            "*": operator.mul,
            "/": operator.truediv,
            "//": operator.floordiv,
            "%": operator.mod,
            "**": operator.pow,
            "<<": operator.lshift,
            ">>": operator.rshift,
            "|": operator.or_,
            "&": operator.and_,
            "^": operator.xor,
            "@": operator.matmul,
        }
        result = operations[op](self.plain_value(left), self.plain_value(right))
        result = self.set_provenance(result, self.combine(left, right))
        return self.set_control_provenance(
            result,
            self.get_control_provenance(left) | self.get_control_provenance(right),
        )

    def unaryop(self, op: str, operand: Any) -> Any:
        operations = {
            "not": operator.not_,
            "+": operator.pos,
            "-": operator.neg,
            "~": operator.invert,
        }
        result = operations[op](self.plain_value(operand))
        result = self.set_provenance(result, self.combine(operand))
        return self.set_control_provenance(result, self.get_control_provenance(operand))

    def joinedstr(self, *parts: Any) -> Any:
        text: list[str] = []
        sources: list[Any] = []
        for part in parts:
            tag = part[0]
            if tag == "lit":
                text.append(str(part[1]))
                continue
            value = part[1]
            conversion = part[2] if len(part) > 2 else -1
            format_spec = part[3] if len(part) > 3 else None
            plain = self.plain_value(value)
            if conversion == 115:
                converted = str(plain)
            elif conversion == 114:
                converted = repr(plain)
            elif conversion == 97:
                converted = ascii(plain)
            else:
                converted = plain
            spec = "" if format_spec is None else str(self.plain_value(format_spec))
            text.append(format(converted, spec))
            sources.append(value)
            if format_spec is not None:
                sources.append(format_spec)
        result = self.set_provenance("".join(text), self.combine(*sources))
        control_sources: set[Source] = set()
        for source in sources:
            control_sources.update(self.get_control_provenance(source))
        return self.set_control_provenance(result, control_sources)

    def format_call(self, template: Any, /, *args: Any, **kwargs: Any) -> Any:
        result = self.plain_value(template).format(*self.plain_value(args), **self.plain_value(kwargs))
        result = self.set_provenance(result, self.combine(template, args, kwargs))
        return self.set_control_provenance(
            result,
            self.get_control_provenance(template)
            | self.get_control_provenance(args)
            | self.get_control_provenance(kwargs),
        )

    def join_call(self, separator: Any, iterable: Any, /) -> Any:
        items = list(iterable)
        result = self.plain_value(separator).join(self.plain_value(item) for item in items)
        result = self.set_provenance(result, self.combine(separator, items))
        return self.set_control_provenance(
            result,
            self.get_control_provenance(separator)
            | self.get_control_provenance(iterable)
            | self.get_control_provenance(items),
        )

    def compare(self, value: Any, *sources: Any) -> Condition:
        return Condition(value, self._condition_provenance(value, value, *sources))

    def cond(self, value: Any, *sources: Any) -> Condition:
        return Condition(value, self._condition_provenance(value, value, *sources))

    def compare_op(self, op: str, left: Any, right: Any) -> Condition:
        operations = {
            "==": operator.eq,
            "!=": operator.ne,
            "<": operator.lt,
            "<=": operator.le,
            ">": operator.gt,
            ">=": operator.ge,
            "in": lambda a, b: a in b,
            "not in": lambda a, b: a not in b,
            "is": lambda a, b: a is b,
            "is not": lambda a, b: a is not b,
        }
        plain_left = self.plain_value(left)
        plain_right = self.plain_value(right)
        return Condition(operations[op](plain_left, plain_right), self._comparison_provenance(left, right))

    def compare_chain(self, left_thunk: Any, ops: Iterable[str], *comparator_thunks: Any) -> Condition:
        op_names = tuple(ops)
        left = left_thunk()
        right = comparator_thunks[0]()
        condition = self.compare_op(op_names[0], left, right)
        for op_name, thunk in zip(op_names[1:], comparator_thunks[1:]):
            if not condition:
                return condition
            prior_sources = self.get_provenance(condition)
            with self.pc(condition):
                next_right = thunk()
            next_right = self.set_control_provenance(next_right, prior_sources)
            left = right
            right = next_right
            next_condition = self.compare_op(op_name, left, right)
            condition = Condition(
                next_condition.value,
                self.get_provenance(next_condition)
                | self.get_control_provenance(next_condition)
                | prior_sources,
            )
        return condition

    def bool_and(self, *thunks: Any) -> Any:
        if not thunks:
            return True
        result = thunks[0]()
        condition = self.cond(result)
        for thunk in thunks[1:]:
            if not condition:
                return result
            prior_sources = self.get_provenance(condition)
            with self.pc(condition):
                result = thunk()
            result = self.set_control_provenance(result, prior_sources)
            condition = self.cond(result, condition)
        return result

    def bool_or(self, *thunks: Any) -> Any:
        if not thunks:
            return False
        result = thunks[0]()
        condition = self.cond(result)
        for thunk in thunks[1:]:
            if condition:
                return result
            prior_sources = self.get_provenance(condition)
            with self.pc(condition):
                result = thunk()
            result = self.set_control_provenance(result, prior_sources)
            condition = self.cond(result, condition)
        return result

    def ifexp(self, condition_thunk: Any, body_thunk: Any, else_thunk: Any) -> Any:
        condition = self.cond(condition_thunk())
        with self.pc(condition):
            result = body_thunk() if condition else else_thunk()
        return self.set_control_provenance(result, self.get_provenance(condition))

    @contextmanager
    def comp_context(self):
        if not self.enabled:
            yield set()
            return
        bucket: set[Source] = set()
        token = self._comp_stack_var.set(self._comp_stack_var.get() + (bucket,))
        try:
            yield bucket
        finally:
            self._comp_stack_var.reset(token)

    def comp_cond(self, condition: Any) -> Condition:
        condition = self.cond(condition)
        if self.enabled:
            stack = self._comp_stack_var.get()
            if stack:
                stack[-1].update(self.get_provenance(condition))
        return condition

    def comprehension(self, thunk: Any) -> Any:
        if not self.enabled:
            return thunk()
        with self.comp_context() as sources:
            result = thunk()
        return self.set_control_provenance(result, sources)

    @contextmanager
    def pc(self, condition: Any):
        if not self.enabled:
            yield
            return
        self.pc_stack.append(condition)
        try:
            yield
        finally:
            self.pc_stack.pop()

    def iterate(self, iterable: Any):
        if not self.enabled:
            yield from iterable
            return
        provenance = self.combine(iterable)
        for item in iterable:
            item_sources = self.get_provenance(item) | provenance
            tracked_item = self.set_provenance(item, item_sources)
            if isinstance(item, (list, tuple)):
                yield self.unpack(tracked_item)
            else:
                yield tracked_item

    def attr(self, value: Any, name: str) -> Any:
        result = getattr(self.plain_value(value), name)
        if not self.enabled:
            return result
        result = self.set_provenance(result, self.get_provenance(result) | self.combine(value))
        return self.set_control_provenance(result, self.get_control_provenance(value))

    def subscript(self, value: Any, key: Any) -> Any:
        result = self.plain_value(value)[self.plain_value(key)]
        if not self.enabled:
            return result
        result = self.set_provenance(result, self.get_provenance(result) | self.combine(value, key))
        return self.set_control_provenance(
            result,
            self.get_control_provenance(value) | self.get_control_provenance(key),
        )

    def setitem(self, obj: Any, key: Any, value: Any) -> None:
        target = self.plain_value(obj)
        target[self.plain_value(key)] = value
        if not self.enabled:
            return
        input_provenance = self.combine(obj, key, value)
        with self._lock:
            self._attach_provenance(target, input_provenance)
            self._attach_control_provenance(
                target,
                self.get_control_provenance(obj)
                | self.get_control_provenance(key)
                | self.get_control_provenance(value),
            )

    def delitem(self, obj: Any, key: Any) -> None:
        target = self.plain_value(obj)
        del target[self.plain_value(key)]
        if not self.enabled:
            return
        input_provenance = self.combine(obj, key)
        with self._lock:
            self._attach_provenance(target, input_provenance)
            self._attach_control_provenance(
                target,
                self.get_control_provenance(obj) | self.get_control_provenance(key),
            )

    def _is_runtime_callable(self, fn: Any) -> bool:
        return getattr(fn, "__self__", None) is self

    def should_unwrap_call_args(self, fn: Any) -> bool:
        if self._is_runtime_callable(fn):
            return False
        base = getattr(fn, "__func__", fn)
        if getattr(base, "__module__", None) == "replay.openai_patch":
            return False
        if getattr(base, "__module__", None) == "replay.tools" and getattr(base, "__name__", None) in {
            "invoke_tool",
            "invoke_tool_sync",
        }:
            return False
        if getattr(base, "_replay_tool_wrapper", False):
            return False
        globals_dict = getattr(base, "__globals__", None)
        if isinstance(globals_dict, dict):
            if any(name.startswith("_replay_sem_rt") and value is self for name, value in globals_dict.items()):
                return False
        return True

    def invocation_args(self, fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any], /) -> tuple[tuple[Any, ...], dict[str, Any]]:
        if self.should_unwrap_call_args(fn):
            return self.plain_call_args(args, kwargs)
        return args, kwargs

    def plain_call_args(self, args: tuple[Any, ...], kwargs: dict[str, Any], /) -> tuple[tuple[Any, ...], dict[str, Any]]:
        return (
            tuple(self.plain_value(arg) for arg in args),
            {self.plain_value(key): self.plain_value(value) for key, value in kwargs.items()},
        )

    def call(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        callable_obj = self.plain_value(fn)
        input_provenance = self.combine(fn, args, kwargs)
        call_args, call_kwargs = self.invocation_args(callable_obj, args, kwargs)
        result = callable_obj(*call_args, **call_kwargs)
        result = self.set_provenance(result, self.get_provenance(result) | input_provenance)
        return self.set_control_provenance(
            result,
            self.get_control_provenance(fn)
            | self.get_control_provenance(args)
            | self.get_control_provenance(kwargs),
        )

    def call_method(self, obj: Any, name: str, /, *args: Any, **kwargs: Any) -> Any:
        input_provenance = self.combine(obj, args, kwargs)
        method = getattr(obj, name)
        if self.should_preserve_mutation_args(obj, name):
            call_args, call_kwargs = args, kwargs
        else:
            call_args, call_kwargs = self.invocation_args(method, args, kwargs)
        result = method(*call_args, **call_kwargs)
        if self.should_track_mutation(obj, name):
            with self._lock:
                self._attach_provenance(obj, input_provenance)
                self._attach_control_provenance(
                    obj,
                    self.get_control_provenance(obj)
                    | self.get_control_provenance(args)
                    | self.get_control_provenance(kwargs),
                )
        result = self.set_provenance(result, self.get_provenance(result) | input_provenance)
        return self.set_control_provenance(
            result,
            self.get_control_provenance(obj)
            | self.get_control_provenance(args)
            | self.get_control_provenance(kwargs),
        )

    def should_preserve_mutation_args(self, obj: Any, name: str) -> bool:
        if isinstance(obj, list):
            return name in {"append", "extend", "insert"}
        if isinstance(obj, set):
            return name in {"add", "update"}
        if isinstance(obj, dict):
            return name in {"setdefault", "update"}
        return False

    def source(self, thunk: Any, name: str) -> Any:
        try:
            return thunk()
        except NameError:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                if caller is not None and name in caller.f_locals:
                    return caller.f_locals[name]
            finally:
                del frame
            return _MISSING_SOURCE

    def should_track_mutation(self, obj: Any, name: str) -> bool:
        if isinstance(obj, list):
            return name in {"append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse"}
        if isinstance(obj, set):
            return name in {"add", "update", "discard", "remove", "clear", "pop"}
        if isinstance(obj, dict):
            return name in {"setdefault", "update", "pop", "popitem", "clear"}
        return False


RUNTIME = Runtime(enabled=False)


def get_provenance(value: Any) -> set[Source]:
    return RUNTIME.get_provenance(value)


def set_provenance(value: Any, sources: Any) -> Any:
    return RUNTIME.set_provenance(value, sources)


def seed_value(value: Any, sources: Any) -> Any:
    return RUNTIME.seed_value(value, sources)


def seed_response(response: Any, source: Source | Iterable[Source]) -> Any:
    return RUNTIME.seed_response(response, source)


def capture_input_provenance(*values: Any, args: Any = None, kwargs: Any = None) -> dict[str, set[Source]]:
    return RUNTIME.capture_input_provenance(*values, args=args, kwargs=kwargs)
