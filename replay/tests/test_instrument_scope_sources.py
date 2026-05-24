from __future__ import annotations

import ast
from textwrap import dedent

import pytest

from replay.instrument import instrument_source, instrument_tree
from replay.semantic_runtime import RUNTIME, Source


def _source(name: str) -> Source:
    return Source(run_id="scope-fix", record_uid=f"rec-{name}", kind="tool", path_id=f"root/{name}")


def _exec_plain(source: str, namespace: dict | None = None) -> dict:
    globals_dict = {"__name__": "__scope_test__"}
    if namespace:
        globals_dict.update(namespace)
    exec(compile(dedent(source), "<scope-test>", "exec"), globals_dict)
    return globals_dict


def _exec_instrumented(source: str, namespace: dict | None = None, *, enter_runtime: bool = True) -> dict:
    globals_dict = {"__name__": "__scope_test__"}
    if namespace:
        globals_dict.update(namespace)
    if not enter_runtime:
        exec(instrument_source(dedent(source), "<scope-test>"), globals_dict)
        return globals_dict
    token = RUNTIME.enter_context(enabled=True)
    try:
        exec(instrument_source(dedent(source), "<scope-test>"), globals_dict)
        return globals_dict
    finally:
        RUNTIME.exit_context(token)


def _assert_same_result(source: str, namespace: dict | None = None) -> tuple[dict, dict]:
    plain = _exec_plain(source, namespace)
    instrumented = _exec_instrumented(source, namespace)
    assert RUNTIME.plain_value(instrumented["RESULT"]) == plain["RESULT"]
    return plain, instrumented


def _instrumented_unparse(source: str) -> str:
    return ast.unparse(instrument_tree(ast.parse(dedent(source), "<scope-test>")))


def test_list_comprehension_target_is_not_captured() -> None:
    _assert_same_result(
        """
        titles = ["Alpha", "Beta"]
        RESULT = any([title.lower() for title in titles])
        """
    )


def test_generator_expression_target_is_not_captured() -> None:
    _assert_same_result(
        """
        titles = ["Alpha", "Beta"]
        RESULT = any(title.lower() for title in titles)
        """
    )


def test_comprehension_preserves_outer_iterable_dependency() -> None:
    iterable_source = _source("iterable")
    token = RUNTIME.enter_context(enabled=True)
    try:
        namespace = {"x": RUNTIME.seed_value([1, 2, 3], iterable_source)}
        instrumented = _exec_instrumented("RESULT = [x for x in x]", namespace, enter_runtime=False)
        assert instrumented["RESULT"] == [1, 2, 3]
        assert RUNTIME.get_provenance(instrumented["RESULT"]) == {iterable_source}
    finally:
        RUNTIME.exit_context(token)


def test_comprehension_filter_keeps_outer_dependencies_only() -> None:
    xs_source = _source("xs")
    predicate_source = _source("predicate")

    def predicate(value: int) -> bool:
        return value % 2 == 1

    token = RUNTIME.enter_context(enabled=True)
    try:
        namespace = {
            "xs": RUNTIME.seed_value([1, 2, 3], xs_source),
            "predicate": RUNTIME.seed_value(predicate, predicate_source),
        }
        instrumented = _exec_instrumented("RESULT = [x for x in xs if predicate(x)]", namespace, enter_runtime=False)
        assert instrumented["RESULT"] == [1, 3]
        visible_sources = RUNTIME.get_provenance(instrumented["RESULT"]) | RUNTIME.get_control_provenance(
            instrumented["RESULT"]
        )
        assert visible_sources == {xs_source, predicate_source}
    finally:
        RUNTIME.exit_context(token)


def test_short_circuit_false_and_missing_name_does_not_raise() -> None:
    _assert_same_result(
        """
        RESULT = "else"
        if False and missing_name:
            RESULT = "then"
        """
    )


def test_lambda_body_names_are_not_collected() -> None:
    _assert_same_result(
        """
        RESULT = "else"
        if lambda: missing_name:
            RESULT = "then"
        """
    )


def test_lambda_default_still_evaluates_normally() -> None:
    _assert_same_result(
        """
        y = 7
        RESULT = (lambda x=y: x)()
        """
    )


def test_nested_function_body_names_are_not_collected() -> None:
    source = """
    def inner():
        return missing_name

    RESULT = "else"
    if inner:
        RESULT = "then"
    """
    instrumented_source = _instrumented_unparse(source)
    assert "_replay_sem_rt.source(lambda: missing_name" not in instrumented_source
    _assert_same_result(source)


def test_nested_class_body_names_are_not_collected() -> None:
    source = """
    class Inner:
        def method(self):
            return missing_name

    RESULT = "else"
    if Inner:
        RESULT = "then"
    """
    instrumented_source = _instrumented_unparse(source)
    assert "_replay_sem_rt.source(lambda: missing_name" not in instrumented_source
    _assert_same_result(source)


def test_runtime_source_missing_name_is_safe_and_untracked() -> None:
    token = RUNTIME.enter_context(enabled=True)
    try:
        value = RUNTIME.source(lambda: missing_name, "missing_name")
        assert RUNTIME.get_provenance(value) == set()
        assert RUNTIME.get_control_provenance(value) == set()
    finally:
        RUNTIME.exit_context(token)


def test_class_body_local_lookup_still_works() -> None:
    source = """
    class C:
        X = 1
        if X:
            Y = 2

    RESULT = (C.X, C.Y)
    """
    instrumented_source = _instrumented_unparse(source)
    assert "_replay_sem_rt.cond" in instrumented_source
    assert "_replay_sem_rt.source" in instrumented_source
    _assert_same_result(source)


def test_chained_comparison_short_circuits_later_operands() -> None:
    _assert_same_result(
        """
        RESULT = "else"
        if 3 < 2 < missing_name:
            RESULT = "then"
        """
    )


@pytest.mark.parametrize(("x", "expected"), [(False, True), (True, False)])
def test_unary_not_semantics_are_preserved(x: bool, expected: bool) -> None:
    plain, instrumented = _assert_same_result(
        """
        RESULT = False
        if not x:
            RESULT = True
        """,
        {"x": x},
    )
    assert plain["RESULT"] is expected
    assert instrumented["RESULT"] is expected


@pytest.mark.parametrize("x", [3, -2, 0])
def test_other_unary_ops_semantics_are_preserved(x: int) -> None:
    _assert_same_result(
        """
        RESULT = (-x, +x, ~x)
        """,
        {"x": x},
    )


def test_chained_comparison_evaluates_middle_operand_once() -> None:
    _assert_same_result(
        """
        calls = 0

        class Probe:
            def __init__(self, value):
                self.value = value

            def __gt__(self, other):
                return self.value > other

            def __lt__(self, other):
                return self.value < other

        def middle():
            global calls
            calls += 1
            return Probe(2)

        RESULT = 1 < middle() < 3
        RESULT = (RESULT, calls)
        """
    )


def test_named_expression_preserves_semantics() -> None:
    source = """
    value = 2
    if (m := value) and m > 1:
        RESULT = m
    else:
        RESULT = 0
    """
    instrumented_source = _instrumented_unparse(source)
    assert "_replay_sem_rt.source(lambda: m" not in instrumented_source
    _assert_same_result(source)


def test_named_expression_in_comprehension_preserves_scope_behavior() -> None:
    source = """
    xs = [1, 2, 3]
    if [(y := x) for x in xs] and y > 0:
        RESULT = y
    else:
        RESULT = 0
    """
    instrumented_source = _instrumented_unparse(source)
    assert "_replay_sem_rt.source(lambda: x, 'x')" not in instrumented_source
    _assert_same_result(source)


def test_instrumented_compare_keeps_operand_control_provenance() -> None:
    operand_control = _source("operand-control")

    token = RUNTIME.enter_context(enabled=True)
    try:
        x = RUNTIME.set_control_provenance(1, operand_control)
        namespace = {"x": x}
        instrumented = _exec_instrumented("RESULT = x == 1", namespace, enter_runtime=False)
        assert RUNTIME.plain_value(instrumented["RESULT"]) is True
        assert RUNTIME.get_provenance(instrumented["RESULT"]) == {operand_control}
    finally:
        RUNTIME.exit_context(token)


def test_instrumented_chained_compare_keeps_operand_control_provenance() -> None:
    left_control = _source("left-control")
    middle_control = _source("middle-control")

    token = RUNTIME.enter_context(enabled=True)
    try:
        namespace = {
            "left": RUNTIME.set_control_provenance(1, left_control),
            "middle": RUNTIME.set_control_provenance(2, middle_control),
        }
        instrumented = _exec_instrumented("RESULT = left < middle < 3", namespace, enter_runtime=False)
        assert RUNTIME.plain_value(instrumented["RESULT"]) is True
        assert RUNTIME.get_provenance(instrumented["RESULT"]) == {left_control, middle_control}
    finally:
        RUNTIME.exit_context(token)


def test_cond_prefers_value_provenance_over_fallback_sources() -> None:
    value_source = _source("value")
    fallback_source = _source("fallback")
    control_source = _source("control")

    token = RUNTIME.enter_context(enabled=True)
    try:
        value = RUNTIME.seed_value(True, value_source)
        fallback = RUNTIME.seed_value("fallback", fallback_source)
        value = RUNTIME.set_control_provenance(value, control_source)
        fallback = RUNTIME.set_control_provenance(fallback, _source("ignored-fallback-control"))
        expected = {value_source, control_source}
        assert RUNTIME.cond(value, fallback).provenance == expected
        assert RUNTIME.compare(value, fallback).provenance == expected
    finally:
        RUNTIME.exit_context(token)


def test_cond_and_compare_use_fallback_sources_when_value_has_none() -> None:
    fallback_source = _source("fallback")
    fallback_control_source = _source("fallback-control")

    token = RUNTIME.enter_context(enabled=True)
    try:
        fallback = RUNTIME.seed_value("fallback", fallback_source)
        fallback = RUNTIME.set_control_provenance(fallback, fallback_control_source)
        expected = {fallback_source, fallback_control_source}
        assert RUNTIME.cond(True, fallback).provenance == expected
        assert RUNTIME.compare(True, fallback).provenance == expected
    finally:
        RUNTIME.exit_context(token)
