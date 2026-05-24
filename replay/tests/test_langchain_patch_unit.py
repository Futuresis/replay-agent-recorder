from __future__ import annotations

import json

from replay.langchain_patch import _tool_record_inputs


class ToolRuntimeLike:
    pass


def test_tool_record_inputs_drop_runtime_from_dict_input() -> None:
    result = _tool_record_inputs(
        "ainvoke", ({"todos": [], "runtime": ToolRuntimeLike()},), {}
    )

    assert "runtime" not in result["input"]
    assert result["input"]["todos"] == []
    json.dumps(result, ensure_ascii=False)
