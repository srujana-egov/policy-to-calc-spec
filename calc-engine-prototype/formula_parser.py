"""Deterministic arithmetic-expression -> JSON Logic parser for FORMULA rules.

The sibling PolicyRule-extraction pipeline this project also built deliberately left
FORMULA/TIME_BASED unimplemented, calling formalizing a free-text `formulaHint` into real JSON
Logic "the one non-deterministic gap" -- because that hint came from *inferring* math out of messy
prose, which is genuinely ambiguous. This wizard's FORMULA question is different in kind, not just
degree: the user is
typing an arithmetic expression over variable names *they just declared moments ago* in this same
session (e.g. "200 + 15 * size" where "size" is a formulaVariables entry already bound to a real
registry field). That's a constrained, checkable task -- parse it, don't infer it -- so unlike
the prose case, this can be genuinely deterministic. No `eval()` anywhere: parses via Python's
`ast` module and walks the tree by hand, so no arbitrary code ever executes.

Supports +, -, *, /, unary minus, parentheses, numeric literals, and bare variable names. Nothing
else (comparisons, function calls, attribute access) -- rejected with a clear error rather than
silently producing something wrong.
"""

from __future__ import annotations

import ast

_OP_TO_JSON_LOGIC = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}


class FormulaParseError(Exception):
    pass


def parse_formula(expression: str, known_variables: set[str]) -> dict:
    """Parses a plain arithmetic expression into JSON Logic. Raises FormulaParseError (with a
    message safe to show a non-technical user) on anything unsupported or on an undeclared
    variable name."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise FormulaParseError(f"couldn't parse '{expression}' as a formula: {e.msg}")
    return _convert(tree.body, expression, known_variables)


def _convert(node, expression: str, known_variables: set[str]) -> dict:
    if isinstance(node, ast.BinOp):
        op = _OP_TO_JSON_LOGIC.get(type(node.op))
        if op is None:
            raise FormulaParseError(
                f"'{expression}' uses an operator this wizard doesn't support -- only +, -, *, / are allowed")
        return {op: [_convert(node.left, expression, known_variables), _convert(node.right, expression, known_variables)]}

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return {"-": [0, _convert(node.operand, expression, known_variables)]}

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value

    if isinstance(node, ast.Name):
        if node.id not in known_variables:
            raise FormulaParseError(
                f"'{node.id}' in '{expression}' isn't one of the variables you named -- "
                f"known variables: {', '.join(sorted(known_variables)) or '(none yet)'}")
        return {"var": node.id}

    raise FormulaParseError(
        f"'{expression}' has something this wizard doesn't support (only numbers, variable "
        "names, +, -, *, /, and parentheses are allowed)")
