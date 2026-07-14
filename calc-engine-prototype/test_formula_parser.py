"""Tests for the deterministic arithmetic-expression-to-JSON-Logic parser (formula_parser.py) --
no eval(), no LLM, just an ast walk. Covers every supported operator, precedence/parentheses,
and every rejection path (unsupported operator, undeclared variable, unparseable syntax)."""

from formula_parser import FormulaParseError, parse_formula

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def test_01_simple_addition():
    check("01-addition", parse_formula("base + rate", {"base", "rate"}) ==
          {"+": [{"var": "base"}, {"var": "rate"}]})


def test_02_multiplication_precedence():
    check("02-precedence", parse_formula("200 + 15 * size", {"size"}) ==
          {"+": [200, {"*": [15, {"var": "size"}]}]})


def test_03_parentheses_override_precedence():
    check("03-parens", parse_formula("(200 + 15) * size", {"size"}) ==
          {"*": [{"+": [200, 15]}, {"var": "size"}]})


def test_04_subtraction_and_division():
    check("04-sub-div", parse_formula("a - b / c", {"a", "b", "c"}) ==
          {"-": [{"var": "a"}, {"/": [{"var": "b"}, {"var": "c"}]}]})


def test_05_unary_minus():
    check("05-unary-minus", parse_formula("-x + 5", {"x"}) ==
          {"+": [{"-": [0, {"var": "x"}]}, 5]})


def test_06_bare_number():
    check("06-bare-number", parse_formula("42", set()) == 42)


def test_07_bare_variable():
    check("07-bare-variable", parse_formula("size", {"size"}) == {"var": "size"})


def test_08_undeclared_variable_rejected():
    raised = False
    try:
        parse_formula("foo + 1", {"bar"})
    except FormulaParseError as e:
        raised = True
        check("08-error-names-undeclared-var", "foo" in str(e))
        check("08-error-lists-known-vars", "bar" in str(e))
    check("08-raised", raised)


def test_09_unsupported_operator_rejected():
    raised = False
    try:
        parse_formula("size ** 2", {"size"})
    except FormulaParseError:
        raised = True
    check("09-power-operator-rejected", raised)


def test_10_function_call_rejected():
    raised = False
    try:
        parse_formula("max(a, b)", {"a", "b"})
    except FormulaParseError:
        raised = True
    check("10-function-call-rejected", raised)


def test_11_comparison_rejected():
    raised = False
    try:
        parse_formula("a > b", {"a", "b"})
    except FormulaParseError:
        raised = True
    check("11-comparison-rejected", raised)


def test_12_invalid_syntax_rejected():
    raised = False
    try:
        parse_formula("a + + ", {"a"})
    except FormulaParseError:
        raised = True
    check("12-invalid-syntax-rejected", raised)


def test_13_no_eval_used():
    """Confirms this really is an AST walk, not eval() in disguise -- a malicious-looking
    expression with no matching AST node type must be rejected, not executed."""
    raised = False
    try:
        parse_formula("__import__('os').system('echo pwned')", set())
    except FormulaParseError:
        raised = True
    check("13-no-code-execution-possible", raised)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll formula_parser.py checks passed -- deterministic, no eval(), no code execution.")
