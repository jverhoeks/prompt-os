from __future__ import annotations

import ast
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Callable


_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "ceil": math.ceil,
    "cos": math.cos,
    "degrees": math.degrees,
    "exp": math.exp,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "radians": math.radians,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_POINTS = 2000
_MIN_POINTS = 2


def evaluate(expression: str) -> dict[str, str]:
    tree = _parse(expression)
    _assert_supported(tree, names=set())
    if _uses_names_or_calls(tree):
        value = _eval_float(tree.body, {})
        return {"expression": expression, "value": _format_float(value)}
    value = _eval_decimal(tree.body)
    return {"expression": expression, "value": format(value, "f")}


def sample(
    expression: str,
    *,
    start: str | int | float,
    end: str | int | float,
    points: int = 240,
    variable: str = "x",
) -> dict[str, Any]:
    if not isinstance(points, int) or isinstance(points, bool):
        raise ValueError(f"points must be between {_MIN_POINTS} and {_MAX_POINTS}")
    if not _MIN_POINTS <= points <= _MAX_POINTS:
        raise ValueError(f"points must be between {_MIN_POINTS} and {_MAX_POINTS}")
    if not isinstance(variable, str) or not variable.isidentifier() or variable in _FUNCTIONS:
        raise ValueError("variable must be a usable identifier")
    start_value = _bound(start)
    end_value = _bound(end)
    if start_value == end_value:
        raise ValueError("start and end must differ")
    tree = _parse(expression)
    _assert_supported(tree, names={variable})
    series: list[dict[str, float | None]] = []
    span = end_value - start_value
    for index in range(points):
        x = start_value + span * index / (points - 1)
        try:
            y = _eval_float(tree.body, {variable: x})
            if not math.isfinite(y):
                y = None
        except ValueError:
            y = None
        series.append({"x": x, "y": y})
    return {
        "expression": expression,
        "variable": variable,
        "start": start_value,
        "end": end_value,
        "count": points,
        "points": series,
    }


def _parse(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("expression contains an unsupported operation") from exc
    if not isinstance(tree, ast.Expression):
        raise ValueError("expression contains an unsupported operation")
    return tree


def _uses_names_or_calls(tree: ast.AST) -> bool:
    return any(isinstance(node, (ast.Call, ast.Name)) for node in ast.walk(tree))


def _assert_supported(tree: ast.AST, *, names: set[str]) -> None:
    allowed_names = names | set(_CONSTANTS) | set(_FUNCTIONS)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Attribute, ast.Subscript, ast.List, ast.Dict, ast.Starred)
        ):
            raise ValueError("expression contains an unsupported operation")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _FUNCTIONS
            or node.keywords
        ):
            raise ValueError("expression contains an unsupported operation")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError("expression contains an unsupported operation")


def _bound(value: str | int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("interval bound must be a finite number or expression")
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = value.strip()
        try:
            result = float(Decimal(text))
        except (InvalidOperation, ValueError):
            result = _eval_float(_parse(text).body, {})
    if not math.isfinite(result):
        raise ValueError("interval bound must be a finite number or expression")
    return result


def _eval_decimal(node: ast.AST) -> Decimal:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_decimal(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _eval_decimal(node.left)
        right = _eval_decimal(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow) and right == int(right) and abs(right) <= 20:
            return left ** int(right)
    raise ValueError("expression contains an unsupported operation")


def _eval_float(node: ast.AST, env: dict[str, float]) -> float:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id in env:
            return env[node.id]
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValueError(f"unknown name {node.id!r}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_float(node.operand, env)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _eval_float(node.left, env)
        right = _eval_float(node.right, env)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("division by zero")
            return left / right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise ValueError("division by zero")
            return left % right
        if isinstance(node.op, ast.Pow) and abs(right) <= 20:
            return math.pow(left, right)
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
            and not node.keywords
        ):
            function = _FUNCTIONS[node.func.id]
            arguments = [_eval_float(argument, env) for argument in node.args]
            try:
                result = function(*arguments)
            except (TypeError, ValueError) as exc:
                raise ValueError("expression contains an unsupported operation") from exc
            if not isinstance(result, (int, float)) or isinstance(result, bool):
                raise ValueError("expression contains an unsupported operation")
            return float(result)
    raise ValueError("expression contains an unsupported operation")


def _format_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("expression did not produce a finite value")
    text = format(Decimal(str(value)).normalize(), "f")
    return "0" if text in {"-0", ""} else text
