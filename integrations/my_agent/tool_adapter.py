from __future__ import annotations

from typing import Any

import replay


def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    """Patch a framework-owned class method shaped like call_tool(name, arguments)."""

    # from target_agent.tools import ToolClient
    # return [
    #     replay.ClassMethodToolAdapter(
    #         ToolClient,
    #         "call_tool",
    #         namespace="local",
    #         version="v1",
    #         tool_filter=None,
    #     )
    # ]
    return []
