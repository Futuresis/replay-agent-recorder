from __future__ import annotations

import base64
import inspect
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, ForwardRef, get_origin, is_typeddict
from uuid import UUID


def normalize_for_json(value: Any, *, max_depth: int = 12) -> Any:
    return _Normalizer(max_depth=max_depth).normalize(value)


class _Normalizer:
    def __init__(self, *, max_depth: int) -> None:
        self.max_depth = max_depth
        self._seen: set[int] = set()

    def normalize(self, value: Any, *, depth: int = 0) -> Any:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            if value == 0:
                return 0.0
            return float(Decimal(str(value)).normalize())
        if isinstance(value, Decimal):
            return float(value.normalize())
        if depth >= self.max_depth:
            return self._max_depth(value)
        if self._is_langchain_message_sequence(value):
            return self._normalize_langchain_messages(list(value), depth=depth)
        if self._is_langchain_message(value):
            return self._normalize_langchain_message(value, depth=depth)
        if self._is_pydantic_v2_model_class(value):
            return self._normalize_pydantic_model_class(value, version="v2", depth=depth)
        if inspect.isclass(value) and is_dataclass(value):
            return self._normalize_dataclass_class(value)
        if is_dataclass(value):
            return self._normalize_dataclass_instance(value, depth=depth)
        if inspect.isclass(value) and is_typeddict(value):
            return self._normalize_typed_dict_class(value)
        if self._is_langchain_tool(value):
            return self._normalize_langchain_tool(value, depth=depth)
        if self._is_pydantic_v2_model_instance(value):
            return self._normalize_pydantic_model_instance(value, version="v2", depth=depth)
        if self._is_pydantic_v1_model_class(value):
            return self._normalize_pydantic_model_class(value, version="v1", depth=depth)
        if self._is_pydantic_v1_model_instance(value):
            return self._normalize_pydantic_model_instance(value, version="v1", depth=depth)
        if isinstance(value, Enum):
            return {
                "__kind__": "enum",
                "module": value.__class__.__module__,
                "qualname": value.__class__.__qualname__,
                "member": value.name,
                "enum_value": self.normalize(value.value, depth=depth + 1),
            }
        if isinstance(value, datetime):
            return {"__kind__": "datetime", "value": value.isoformat()}
        if isinstance(value, date):
            return {"__kind__": "date", "value": value.isoformat()}
        if isinstance(value, time):
            return {"__kind__": "time", "value": value.isoformat()}
        if isinstance(value, timedelta):
            return {"__kind__": "timedelta", "total_seconds": value.total_seconds()}
        if isinstance(value, UUID):
            return {"__kind__": "uuid", "value": str(value)}
        if isinstance(value, Path):
            return {"__kind__": "path", "value": str(value)}
        if self._is_secret(value):
            return {"__kind__": "secret", "type": value.__class__.__name__, "value": "***"}
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw = bytes(value)
            return {
                "__kind__": "bytes",
                "base64": base64.b64encode(raw).decode("ascii"),
                "length": len(raw),
            }
        if isinstance(value, dict):
            return self._normalize_dict(value, depth=depth)
        if isinstance(value, (list, tuple)):
            return self._normalize_list(value, depth=depth)
        if isinstance(value, (set, frozenset)):
            return self._normalize_set(value, depth=depth)
        if inspect.isclass(value):
            return self._normalize_python_class(value)
        if callable(value):
            return self._normalize_callable(value)
        return self._normalize_object(value)

    def _normalize_dict(self, value: dict[Any, Any], *, depth: int) -> dict[str, Any]:
        marker = self._enter(value)
        if marker is not None:
            return marker
        try:
            normalized = {}
            for key, item in value.items():
                if item is None:
                    continue
                normalized[str(key)] = self.normalize(item, depth=depth + 1)
            return {key: normalized[key] for key in sorted(normalized)}
        finally:
            self._leave(value)

    def _normalize_list(self, value: list[Any] | tuple[Any, ...], *, depth: int) -> list[Any] | dict[str, str]:
        marker = self._enter(value)
        if marker is not None:
            return marker
        try:
            return [self.normalize(item, depth=depth + 1) for item in value]
        finally:
            self._leave(value)

    def _normalize_set(self, value: set[Any] | frozenset[Any], *, depth: int) -> list[Any] | dict[str, str]:
        marker = self._enter(value)
        if marker is not None:
            return marker
        try:
            normalized = [self.normalize(item, depth=depth + 1) for item in value]
            return sorted(normalized, key=self._stable_json)
        finally:
            self._leave(value)

    def _normalize_pydantic_model_instance(self, value: Any, *, version: str, depth: int) -> dict[str, Any]:
        data = value.model_dump(mode="json", exclude_none=True) if version == "v2" else value.dict(exclude_none=True)
        return {
            "__kind__": "pydantic_model",
            "pydantic_version": version,
            "module": value.__class__.__module__,
            "qualname": value.__class__.__qualname__,
            "data": self.normalize(data, depth=depth + 1),
        }

    def _normalize_pydantic_model_class(self, value: type[Any], *, version: str, depth: int) -> dict[str, Any]:
        schema = value.model_json_schema() if version == "v2" else value.schema()
        return {
            "__kind__": "pydantic_model_class",
            "pydantic_version": version,
            "module": value.__module__,
            "qualname": value.__qualname__,
            "annotations": self._normalize_annotations(getattr(value, "__annotations__", {})),
            "schema": self.normalize(schema, depth=depth + 1),
        }

    def _normalize_dataclass_instance(self, value: Any, *, depth: int) -> dict[str, Any]:
        marker = self._enter(value)
        if marker is not None:
            return marker
        try:
            data = {}
            for field in fields(value):
                item = getattr(value, field.name)
                if item is not None:
                    data[field.name] = self.normalize(item, depth=depth + 1)
            normalized_data = {key: data[key] for key in sorted(data)}
        finally:
            self._leave(value)
        return {
            "__kind__": "dataclass",
            "module": value.__class__.__module__,
            "qualname": value.__class__.__qualname__,
            "data": normalized_data,
        }

    def _normalize_dataclass_class(self, value: type[Any]) -> dict[str, Any]:
        return {
            "__kind__": "dataclass_class",
            "module": value.__module__,
            "qualname": value.__qualname__,
            "annotations": self._normalize_annotations(getattr(value, "__annotations__", {})),
        }

    def _normalize_typed_dict_class(self, value: type[Any]) -> dict[str, Any]:
        return {
            "__kind__": "typeddict_class",
            "module": value.__module__,
            "qualname": value.__qualname__,
            "annotations": self._normalize_annotations(getattr(value, "__annotations__", {})),
        }

    def _normalize_langchain_messages(self, value: list[Any], *, depth: int) -> list[Any]:
        marker = self._enter(value)
        if marker is not None:
            return [marker]
        try:
            from langchain_core.messages import messages_to_dict

            return self.normalize(messages_to_dict(value), depth=depth + 1)
        finally:
            self._leave(value)

    def _normalize_langchain_message(self, value: Any, *, depth: int) -> Any:
        marker = self._enter(value)
        if marker is not None:
            return marker
        try:
            from langchain_core.messages import messages_to_dict

            return self.normalize(messages_to_dict([value])[0], depth=depth + 1)
        finally:
            self._leave(value)

    def _normalize_langchain_tool(self, value: Any, *, depth: int) -> dict[str, Any]:
        metadata = {
            "__kind__": "langchain_tool",
            "module": value.__class__.__module__,
            "qualname": value.__class__.__qualname__,
            "name": getattr(value, "name", value.__class__.__name__),
            "description": getattr(value, "description", None),
            "return_direct": getattr(value, "return_direct", None),
        }
        args_schema = getattr(value, "args_schema", None)
        if args_schema is not None:
            metadata["args_schema"] = self.normalize(args_schema, depth=depth + 1)
        func = getattr(value, "func", None)
        if func is not None:
            metadata["func"] = self.normalize(func, depth=depth + 1)
        return {key: item for key, item in metadata.items() if item is not None}

    def _normalize_python_class(self, value: type[Any]) -> dict[str, Any]:
        normalized = {
            "__kind__": "python_class",
            "module": value.__module__,
            "qualname": value.__qualname__,
        }
        annotations = self._normalize_annotations(getattr(value, "__annotations__", {}))
        if annotations:
            normalized["annotations"] = annotations
        return normalized

    def _normalize_callable(self, value: Any) -> dict[str, Any]:
        return {
            "__kind__": "callable",
            "module": getattr(value, "__module__", value.__class__.__module__),
            "qualname": getattr(value, "__qualname__", getattr(value, "__name__", value.__class__.__qualname__)),
        }

    def _normalize_object(self, value: Any) -> dict[str, Any]:
        marker = self._enter(value)
        if marker is not None:
            return marker
        try:
            return {
                "__kind__": "object",
                "module": value.__class__.__module__,
                "qualname": value.__class__.__qualname__,
            }
        finally:
            self._leave(value)

    def _enter(self, value: Any) -> dict[str, str] | None:
        object_id = id(value)
        if object_id in self._seen:
            return {"__kind__": "cycle", "type": self._type_name(value)}
        self._seen.add(object_id)
        return None

    def _leave(self, value: Any) -> None:
        self._seen.discard(id(value))

    def _max_depth(self, value: Any) -> dict[str, str]:
        return {"__kind__": "max_depth", "type": self._type_name(value)}

    def _stable_json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _type_name(self, value: Any) -> str:
        if isinstance(value, dict):
            return "dict"
        if isinstance(value, list):
            return "list"
        if isinstance(value, tuple):
            return "tuple"
        if isinstance(value, set):
            return "set"
        if isinstance(value, frozenset):
            return "frozenset"
        return value.__class__.__name__

    def _normalize_annotations(self, annotations: Mapping[str, Any]) -> dict[str, str]:
        return {
            str(key): self._annotation_name(value)
            for key, value in sorted(annotations.items(), key=lambda item: str(item[0]))
        }

    def _annotation_name(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, ForwardRef):
            return value.__forward_arg__
        if inspect.isclass(value):
            if value.__module__ == "builtins":
                return value.__name__
            return f"{value.__module__}.{value.__qualname__}"
        origin = get_origin(value)
        if origin is not None:
            return str(value).replace("typing.", "")
        return str(value).replace("typing.", "")

    def _is_secret(self, value: Any) -> bool:
        return hasattr(value, "get_secret_value") and "Secret" in value.__class__.__name__

    def _is_langchain_message(self, value: Any) -> bool:
        try:
            from langchain_core.messages import BaseMessage
        except Exception:
            return False
        return isinstance(value, BaseMessage)

    def _is_langchain_message_sequence(self, value: Any) -> bool:
        if not isinstance(value, (list, tuple)) or not value:
            return False
        try:
            from langchain_core.messages import BaseMessage
        except Exception:
            return False
        return all(isinstance(item, BaseMessage) for item in value)

    def _is_langchain_tool(self, value: Any) -> bool:
        try:
            from langchain_core.tools import BaseTool
        except Exception:
            return False
        return isinstance(value, BaseTool)

    def _is_pydantic_v2_model_instance(self, value: Any) -> bool:
        try:
            from pydantic import BaseModel as PydanticBaseModel
        except Exception:
            return False
        return isinstance(value, PydanticBaseModel)

    def _is_pydantic_v2_model_class(self, value: Any) -> bool:
        if not inspect.isclass(value):
            return False
        try:
            from pydantic import BaseModel as PydanticBaseModel
        except Exception:
            return False
        return issubclass(value, PydanticBaseModel)

    def _is_pydantic_v1_model_instance(self, value: Any) -> bool:
        try:
            from pydantic.v1 import BaseModel as PydanticV1BaseModel
        except Exception:
            return False
        return isinstance(value, PydanticV1BaseModel)

    def _is_pydantic_v1_model_class(self, value: Any) -> bool:
        if not inspect.isclass(value):
            return False
        try:
            from pydantic.v1 import BaseModel as PydanticV1BaseModel
        except Exception:
            return False
        return issubclass(value, PydanticV1BaseModel)
