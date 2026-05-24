from __future__ import annotations

from replay.semantic_runtime import RUNTIME


def test_runtime_kind_and_span_metadata_are_context_local() -> None:
    token = RUNTIME.enter_context(enabled=True)
    try:
        assert RUNTIME.current_kinds_snapshot() == []
        RUNTIME.push_kind("llm")
        assert RUNTIME.current_kinds_snapshot() == ["llm"]
        assert RUNTIME.pop_kind() == "llm"
        assert RUNTIME.current_kinds_snapshot() == []

        with RUNTIME.context_span(
            "langgraph_node",
            "planner",
            {"graph_id": "g1", "identity_hint": "g1/planner"},
        ):
            assert RUNTIME.span_stack_snapshot() == [
                {
                    "kind": "langgraph_node",
                    "name": "planner",
                    "metadata": {"graph_id": "g1", "identity_hint": "g1/planner"},
                }
            ]
            assert RUNTIME.merge_metadata(
                {"framework": "langchain"},
                semantic_hint="chat:planner",
            ) == {
                "framework": "langchain",
                "spans": [
                    {
                        "kind": "langgraph_node",
                        "name": "planner",
                        "metadata": {"graph_id": "g1", "identity_hint": "g1/planner"},
                    }
                ],
                "semantic": {"callsite_fingerprint": "chat:planner"},
            }
            assert RUNTIME.current_record_metadata() == {
                "spans": [
                    {
                        "kind": "langgraph_node",
                        "name": "planner",
                        "metadata": {"graph_id": "g1", "identity_hint": "g1/planner"},
                    }
                ]
            }
            assert RUNTIME.current_record_semantic_hint() == "g1/planner"

        assert RUNTIME.span_stack_snapshot() == []
        assert RUNTIME.current_record_metadata() is None
    finally:
        RUNTIME.exit_context(token)
