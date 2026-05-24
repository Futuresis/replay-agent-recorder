from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any


ToolFn = Callable[[dict[str, Any]], Any]


def words(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())


def score_payload(args: dict[str, Any]) -> dict[str, Any]:
    texts = [str(item) for item in args.get("texts", [])]
    token_count = sum(len(words(text)) for text in texts)
    return {
        "score": min(100, 12 + token_count * 3),
        "token_count": token_count,
        "text_count": len(texts),
    }


async def async_digest(args: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0)
    text = str(args.get("text") or "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"sha256": digest, "prefix": digest[:10], "length": len(text)}


def normalize_text(args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text") or "")
    tokens = words(text)
    return {
        "normalized": " ".join(tokens),
        "token_count": len(tokens),
        "unique_tokens": sorted(set(tokens))[:12],
    }


async def topic_tags(args: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0)
    text = str(args.get("text") or "")
    markers = {
        "llm": ["llm", "agent", "model", "replay"],
        "ops": ["deploy", "monitor", "incident", "support"],
        "risk": ["risk", "failure", "rollback", "unsafe"],
        "data": ["data", "dataset", "json", "file"],
    }
    lower = text.lower()
    tags = [name for name, keys in markers.items() if any(key in lower for key in keys)]
    return {"tags": tags or ["general"], "source_length": len(text)}


def unstable_gate(args: dict[str, Any]) -> dict[str, Any]:
    label = str(args.get("label") or "gate")
    if args.get("should_fail", True):
        raise ValueError(f"planned failure from {label}")
    return {"ok": True, "label": label}


MAPPING_TOOLS: dict[str, ToolFn] = {
    "normalize_text": normalize_text,
    "topic_tags": topic_tags,
    "unstable_gate": unstable_gate,
}


class WorkspaceToolClient:
    """Small method-style file tool client for MethodToolAdapter coverage."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.live_call_count = 0

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.live_call_count += 1
        args = arguments or {}
        await asyncio.sleep(0)

        if name == "inventory":
            return self._inventory()
        if name == "write_text":
            return self._write_text(str(args.get("path") or ""), str(args.get("text") or ""))
        if name == "append_text":
            return self._append_text(str(args.get("path") or ""), str(args.get("text") or ""))
        if name == "delete_file":
            return self._delete_file(str(args.get("path") or ""))
        raise ValueError(f"unknown workspace tool: {name}")

    def _inventory(self) -> dict[str, Any]:
        files = []
        if self.root.exists():
            for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
                rel = path.relative_to(self.root).as_posix()
                preview = path.read_text(encoding="utf-8")[:80]
                files.append(
                    {
                        "path": rel,
                        "size": len(preview.encode("utf-8")),
                        "preview": preview,
                    }
                )
        return {"files": files}

    def _write_text(self, rel_path: str, text: str) -> dict[str, Any]:
        target = self._safe_path(rel_path)
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {"path": rel_path, "operation": "modify" if existed else "create", "bytes": len(text.encode("utf-8"))}

    def _append_text(self, rel_path: str, text: str) -> dict[str, Any]:
        target = self._safe_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return {"path": rel_path, "operation": "append", "bytes": len(text.encode("utf-8"))}

    def _delete_file(self, rel_path: str) -> dict[str, Any]:
        target = self._safe_path(rel_path)
        if not target.exists():
            return {"path": rel_path, "operation": "missing"}
        target.unlink()
        return {"path": rel_path, "operation": "delete"}

    def _safe_path(self, rel_path: str) -> Path:
        parsed = PurePosixPath(rel_path)
        has_unsafe_part = False
        for part in parsed.parts:
            if part in {"", ".", ".."}:
                has_unsafe_part = True
                break
        if not rel_path or parsed.is_absolute() or has_unsafe_part:
            raise ValueError(f"unsafe sandbox path: {rel_path!r}")
        target = self.root.joinpath(*parsed.parts)
        target.resolve(strict=False).relative_to(self.root)
        return target
