"""
Arithmetic for decoding a PID, expressed as data rather than as code.

A J1979 PID decodes with a one-line formula over its data bytes — engine speed is
`(256*A + B) / 4`, coolant temperature is `A - 40`. Keeping those as text is what lets a
vehicle profile add a manufacturer PID without shipping Python, and it is also the
notation the Torque CSV format already uses, so an imported file needs no translation.

Formulas arrive from files the user supplies, so they are **interpreted, never executed**.
The expression is parsed to a syntax tree, every node is checked against a whitelist at
construction time, and evaluation walks that tree. Nothing is compiled and `eval` is never
called, so a profile cannot reach the filesystem, the network or the interpreter however
it is written.

Bytes are named A, B, C, D… after the PID echo, which is the convention of both SAE J1979
worked examples and the Torque CSV format.
"""

from __future__ import annotations

import ast
from typing import Any, Callable, Dict, FrozenSet, Mapping

# Data bytes are addressed by letter, A being the first byte after the PID echo.
BYTE_NAMES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class FormulaError(ValueError):
    """A formula could not be parsed, or could not be evaluated against these bytes."""


def signed(value: int, bits: int = 8) -> int:
    """Reinterpret an unsigned value as two's complement, for a signed PID."""
    limit = 1 << bits
    return value - limit if value >= limit >> 1 else value


def bit(value: int, index: int) -> int:
    """Extract one bit, counted from the least significant. Returns 0 or 1."""
    return (int(value) >> int(index)) & 1


# The only callables a formula may use. Everything else is refused at parse time.
FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "signed": signed,
    "bit": bit,
}

_BINARY_OPERATORS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
    ast.BitAnd: lambda a, b: int(a) & int(b),
    ast.BitOr: lambda a, b: int(a) | int(b),
    ast.BitXor: lambda a, b: int(a) ^ int(b),
    ast.LShift: lambda a, b: int(a) << int(b),
    ast.RShift: lambda a, b: int(a) >> int(b),
}

_UNARY_OPERATORS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
    ast.Invert: lambda a: ~int(a),
    ast.Not: lambda a: not a,
}

_COMPARISONS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}

# Exponents are bounded because a formula is user-supplied text: `9**9**9` parses to a
# perfectly valid tree that would then occupy the process indefinitely.
MAX_EXPONENT = 64


class Formula:
    """
    One decoding expression, validated once and then evaluated against data bytes.

    Args:
        expression: The formula text, e.g. `(256*A + B) / 4`.

    Raises:
        FormulaError: If the text is not an expression, or uses anything outside the
            whitelist of operators, names and functions.
    """

    def __init__(self, expression: str):
        self.expression = str(expression).strip()
        if not self.expression:
            raise FormulaError("a formula cannot be empty")
        try:
            tree = ast.parse(self.expression, mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"{self.expression!r} is not a valid expression: {exc.msg}") from exc
        self._tree = tree.body
        self._names = self._validate(self._tree)

    @property
    def variables(self) -> FrozenSet[str]:
        """The byte names the formula reads, e.g. {'A', 'B'}."""
        return self._names

    @property
    def required_bytes(self) -> int:
        """How many data bytes the formula needs to evaluate."""
        if not self._names:
            return 0
        return max(BYTE_NAMES.index(name) for name in self._names) + 1

    def _validate(self, node: ast.AST) -> FrozenSet[str]:
        """Walk the tree once, refusing anything outside the whitelist."""
        names = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Store) or child.id in FUNCTIONS:
                    if child.id not in FUNCTIONS:
                        raise FormulaError(f"{self.expression!r} assigns to {child.id}, which is not allowed")
                    continue
                if child.id not in BYTE_NAMES or len(child.id) != 1:
                    raise FormulaError(
                        f"{self.expression!r} refers to {child.id!r}, which is neither a data byte "
                        f"(A, B, C…) nor one of {', '.join(sorted(FUNCTIONS))}"
                    )
                names.add(child.id)
            elif isinstance(child, ast.Call):
                if not isinstance(child.func, ast.Name) or child.func.id not in FUNCTIONS:
                    raise FormulaError(f"{self.expression!r} calls something that is not an allowed function")
                if child.keywords:
                    raise FormulaError(f"{self.expression!r} uses keyword arguments, which are not allowed")
            elif isinstance(child, ast.Constant):
                if not isinstance(child.value, (int, float)) or isinstance(child.value, bool):
                    raise FormulaError(f"{self.expression!r} contains a non-numeric constant")
            elif isinstance(
                child,
                (ast.Expression, ast.BinOp, ast.UnaryOp, ast.IfExp, ast.Compare, ast.BoolOp, ast.And, ast.Or, ast.Load),
            ):
                continue
            elif isinstance(child, tuple(_BINARY_OPERATORS) + tuple(_UNARY_OPERATORS) + tuple(_COMPARISONS)):
                continue
            else:
                raise FormulaError(
                    f"{self.expression!r} uses {type(child).__name__}, which a decoding formula may not contain"
                )
        return frozenset(names)

    def __call__(self, data: bytes) -> float:
        """
        Evaluate the formula against a PID's data bytes.

        Args:
            data: The bytes following the PID echo, A being the first of them.

        Raises:
            FormulaError: If the data is shorter than the formula needs.
        """
        needed = self.required_bytes
        if len(data) < needed:
            raise FormulaError(f"{self.expression!r} needs {needed} data byte(s) but the response carried {len(data)}")
        environment = {BYTE_NAMES[index]: value for index, value in enumerate(data[: len(BYTE_NAMES)])}
        return self._evaluate(self._tree, environment)

    def _evaluate(self, node: ast.AST, environment: Mapping[str, int]) -> Any:
        """Walk the validated tree. Every branch here matches something _validate allowed."""
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            return environment[node.id]

        if isinstance(node, ast.BinOp):
            left = self._evaluate(node.left, environment)
            right = self._evaluate(node.right, environment)
            if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
                raise FormulaError(f"{self.expression!r} raises to the power of {right}, which is refused")
            try:
                return _BINARY_OPERATORS[type(node.op)](left, right)
            except ZeroDivisionError:
                raise FormulaError(f"{self.expression!r} divided by zero on this response") from None

        if isinstance(node, ast.UnaryOp):
            return _UNARY_OPERATORS[type(node.op)](self._evaluate(node.operand, environment))

        if isinstance(node, ast.Call):
            arguments = [self._evaluate(argument, environment) for argument in node.args]
            function = FUNCTIONS[node.func.id]  # type: ignore[attr-defined]
            try:
                return function(*arguments)
            except (TypeError, ValueError) as exc:
                raise FormulaError(f"{self.expression!r} called {node.func.id} badly: {exc}") from exc  # type: ignore[attr-defined]

        if isinstance(node, ast.IfExp):
            chosen = node.body if self._evaluate(node.test, environment) else node.orelse
            return self._evaluate(chosen, environment)

        if isinstance(node, ast.Compare):
            left = self._evaluate(node.left, environment)
            for operator, comparator in zip(node.ops, node.comparators):
                right = self._evaluate(comparator, environment)
                if not _COMPARISONS[type(operator)](left, right):
                    return False
                left = right
            return True

        if isinstance(node, ast.BoolOp):
            values = [self._evaluate(value, environment) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)

        raise FormulaError(f"{self.expression!r} cannot be evaluated")

    def __repr__(self) -> str:
        return f"Formula({self.expression!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Formula) and other.expression == self.expression

    def __hash__(self) -> int:
        return hash(self.expression)
