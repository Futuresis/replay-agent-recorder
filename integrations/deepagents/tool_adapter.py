from __future__ import annotations

from typing import Any

import replay


def build_adapters(args: Any) -> list[replay.ToolAdapter]:
    """Return Replay tool adapters to install before the target starts."""

    return []
