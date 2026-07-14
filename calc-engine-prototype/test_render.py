"""Stress tests for render.py -- offline-safety and structural correctness as a permanent
regression check, mirroring the same checks in ../workflow-prototype/test_render.py and
../registry-prototype/test_render.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from builder import CalculationRuleBuilder
from example_generator import generate_scenarios, run_scenarios
from render import render_ruleset_preview

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


EXTERNAL_REF_PATTERNS = ["http://", "https://", "@import", "fetch(", "XMLHttpRequest"]


def _sample_ruleset():
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("BASE_FEE", 2000, conditions={"area": {"jsonPath": "$.area", "to": 1000}},
                     effectiveFrom="2024-01-01")
    b.add_percentage_rule("CESS", 5.0, "BASE_FEE", effectiveFrom="2024-01-01")
    b.add_slab_rule("INCOME_TAX", "$.income",
                     [{"from": 0, "to": 250000, "rate": 0}, {"from": 250000, "rate": 20}],
                     effectiveFrom="2024-01-01")
    b.add_assumption("effectiveFrom defaulted to 2024-01-01; confirm with business user.")
    return b.build()


def test_render_01_no_external_references():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(_sample_ruleset(), str(out))
        html = out.read_text()
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render01-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_02_one_row_per_rule():
    rule_set = _sample_ruleset()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(rule_set, str(out))
        html = out.read_text()
    check("render02-row-count", html.count('class="rule-row"') == len(rule_set.rules),
          (html.count('class="rule-row"'), len(rule_set.rules)))
    for rule in rule_set.rules:
        check(f"render02-has-{rule.component}", rule.component in html)


def test_render_03_assumptions_shown():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(_sample_ruleset(), str(out))
        html = out.read_text()
    check("render03-assumption-text-present", "confirm with business user" in html)


def test_render_04_slab_and_percentage_summaries_readable():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(_sample_ruleset(), str(out))
        html = out.read_text()
    check("render04-slab-tier-count-shown", "2 tier(s)" in html, html)
    check("render04-percentage-shown", "5.0% of BASE_FEE" in html, html)


def test_render_05_does_not_crash_on_formula_rule():
    b = CalculationRuleBuilder("x")
    b.add_formula_rule("COMPLEX_FEE", {"+": [{"var": "a"}, {"var": "b"}]},
                        {"a": {"jsonPath": "$.a"}, "b": {"jsonPath": "$.b"}},
                        effectiveFrom="2024-01-01")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(b.build(), str(out))
        html = out.read_text()
    check("render05-formula-rendered", "formula (see detail)" in html, html)
    check("render05-formula-logic-in-detail-json", '"formulaLogic"' in html)


def test_render_06_worked_examples_section_shows_computed_totals():
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 2000, conditions={"area": {"jsonPath": "$.tradeLicenseDetail.premisesArea", "to": 1000}},
                     effectiveFrom="2024-01-01")
    b.add_flat_rule("FEE", 5000, conditions={"area": {"jsonPath": "$.tradeLicenseDetail.premisesArea", "from": 1000.01}},
                     effectiveFrom="2024-01-01")
    rule_set = b.build()
    raw = [r.model_dump(by_alias=True, exclude_none=True) for r in rule_set.rules]
    scenario_results = run_scenarios(raw, generate_scenarios(raw))

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(rule_set, str(out), scenario_results=scenario_results)
        html = out.read_text()

    check("render06-worked-examples-heading", "Worked examples" in html)
    check("render06-boundary-scenario-shown", "exactly the upper boundary" in html, html)
    check("render06-two-totals-shown-2000-and-5000", 'class="total-cell">2000' in html and
          'class="total-cell">5000' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render06-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_07_no_scenarios_omits_examples_section_cleanly():
    """render_ruleset_preview() must still work with no scenario_results at all (backward
    compatible call shape) -- doesn't crash, doesn't render an empty/broken examples section."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "preview.html"
        render_ruleset_preview(_sample_ruleset(), str(out))
        html = out.read_text()
    check("render07-no-crash-without-scenarios", "BASE_FEE" in html)
    check("render07-no-worked-examples-heading", "Worked examples" not in html)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll render.py checks passed -- offline-safe and structurally correct.")
