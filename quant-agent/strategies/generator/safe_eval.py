"""A restricted expression evaluator for `StrategySpec` signal logic.

Spec section 42 ("Code generation safety") requires generated strategy
code to be sandboxed, resource-limited, and unable to access unauthorized
systems. Rather than `exec()`-ing LLM/agent-generated Python, every
`SignalLogic` condition is a short boolean expression string (e.g.
`"ofi > threshold"`) that is parsed with `ast` and evaluated against a
whitelist of node types and function names only. There is no code path
here that can reach the filesystem, network, or process — expressions
that reference anything outside the provided variable namespace raise
`UnsafeExpressionError` instead of silently failing open.
"""
from __future__ import annotations

import ast
import math
from typing import Any

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Not, ast.UnaryOp, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
    ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.Name, ast.Load, ast.Constant, ast.Call,
)

_ALLOWED_FUNCS = {
    "abs": abs, "min": min, "max": max, "round": round,
    "sqrt": math.sqrt, "tanh": math.tanh, "exp": math.exp, "log": math.log,
}


class UnsafeExpressionError(ValueError):
    pass


def _check(node: ast.AST) -> None:
    if not isinstance(node, _ALLOWED_NODES):
        raise UnsafeExpressionError(f"Disallowed syntax in strategy expression: {type(node).__name__}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise UnsafeExpressionError(f"Disallowed function call in strategy expression: {ast.dump(node)}")
    for child in ast.iter_child_nodes(node):
        _check(child)


def compile_expression(expr: str) -> ast.Expression:
    """Parse + validate. Raises UnsafeExpressionError for anything outside
    the whitelist (attribute access, subscripting, comprehensions, imports,
    lambdas, string literals used as code, etc.)."""
    tree = ast.parse(expr, mode="eval")
    _check(tree)
    return tree


def eval_expression(expr: str, namespace: dict[str, Any]) -> Any:
    tree = compile_expression(expr)
    code = compile(tree, filename="<strategy_expr>", mode="eval")
    safe_globals = {"__builtins__": {}}
    safe_globals.update(_ALLOWED_FUNCS)
    return eval(code, safe_globals, dict(namespace))  # noqa: S307 - sandboxed via AST whitelist above


def eval_signal_logic(all_of: list[str], any_of: list[str], namespace: dict[str, Any]) -> bool:
    all_ok = all(bool(eval_expression(e, namespace)) for e in all_of) if all_of else True
    any_ok = any(bool(eval_expression(e, namespace)) for e in any_of) if any_of else True
    return all_ok and any_ok
