import pytest

from strategies.generator.safe_eval import UnsafeExpressionError, eval_expression, eval_signal_logic


def test_basic_arithmetic_and_comparison():
    assert eval_expression("2 + 3 * 4", {}) == 14
    assert eval_expression("close > 100", {"close": 150}) is True
    assert eval_expression("close > 100", {"close": 50}) is False


def test_allowed_functions():
    assert eval_expression("abs(-5)", {}) == 5
    assert eval_expression("max(1, 2, 3)", {}) == 3


def test_signal_logic_all_and_any():
    ns = {"a": 1, "b": 2}
    assert eval_signal_logic(["a > 0", "b > 0"], [], ns) is True
    assert eval_signal_logic(["a > 0", "b < 0"], [], ns) is False
    assert eval_signal_logic([], ["a < 0", "b > 0"], ns) is True


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo pwned')",
    "open('/etc/passwd').read()",
    "().__class__.__bases__",
    "[x for x in range(10)]",
    "lambda: 1",
    "a.b.c",
])
def test_rejects_unsafe_expressions(expr):
    with pytest.raises(UnsafeExpressionError):
        eval_expression(expr, {"a": object()})
