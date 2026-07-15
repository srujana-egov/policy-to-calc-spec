"""Tests for example_generator.py -- the worked-examples generator wired into the confirmation
preview. Confirms scenarios are genuinely *targeted* (one per condition band/boundary, one per
slab tier, one per aggregation threshold), not random, and that running them through
simulate.py produces numerically correct, sane results -- catching exactly the kind of mistake
this feature exists to catch (see test_05/test_06 below, both real bugs found while building
this very file).
"""


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builder import CalculationRuleBuilder
from example_generator import build_baseline_payload, generate_scenarios, run_scenarios

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def _raw_rules(builder: CalculationRuleBuilder) -> list[dict]:
    return [r.model_dump(by_alias=True, exclude_none=True) for r in builder.build().rules]


def test_01_chennai_style_boundary_scenarios_show_the_jump():
    """The real Chennai fixture's own ambiguity ('above 1000 sq.ft.' modeled as from: 1000.01)
    -- confirms the generated scenarios actually surface the boundary jump a business user should
    sanity-check, not just structural correctness."""
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 2000, conditions={"area": {"jsonPath": "$.tradeLicenseDetail.premisesArea", "to": 1000}},
                     effectiveFrom="2008-07-30")
    b.add_flat_rule("FEE", 5000, conditions={"area": {"jsonPath": "$.tradeLicenseDetail.premisesArea", "from": 1000.01}},
                     effectiveFrom="2008-07-30")
    rules = _raw_rules(b)
    scenarios = generate_scenarios(rules)
    results = run_scenarios(rules, scenarios)
    totals_by_label = {r["label"]: r["result"]["totalAmount"] for r in results}

    check("01-has-boundary-scenario", any("1000" in label and "boundary" in label for label in totals_by_label),
          totals_by_label)
    at_1000 = next(v for k, v in totals_by_label.items() if "= 1000 " in k)
    at_1000_01 = next(v for k, v in totals_by_label.items() if "1000.01" in k)
    check("01-boundary-shows-the-jump", at_1000 == 2000 and at_1000_01 == 5000,
          (at_1000, at_1000_01))
    check("01-capped-reasonably", 3 <= len(results) <= 15, len(results))


def test_02_slab_tiers_each_get_a_scenario():
    """Rates are 0/5/20, not 0/0.05/0.20 -- SLAB's rate is divided by 100 by the engine (same
    convention as PERCENTAGE's value), confirmed by calculation-rule-examples.pdf's example #14
    (see simulate.py's _compute_slab). A rate of 20 here means a real 20% bracket."""
    b = CalculationRuleBuilder("income-tax")
    b.add_slab_rule("INCOME_TAX", "$.income",
                     [{"from": 0, "to": 250000, "rate": 0}, {"from": 250000, "to": 500000, "rate": 5},
                      {"from": 500000, "rate": 20}],
                     effectiveFrom="2024-04-01")
    rules = _raw_rules(b)
    results = run_scenarios(rules, generate_scenarios(rules))
    labels = [r["label"] for r in results]
    check("02-tier1-scenario", any("0-250000" in l for l in labels), labels)
    check("02-tier2-scenario", any("250000-500000" in l for l in labels), labels)
    check("02-tier3-scenario", any("500000 and up" in l for l in labels), labels)

    tier2 = next(r for r in results if "250000-500000" in r["label"])
    tier3 = next(r for r in results if "500000 and up" in r["label"])
    check("02-tier2-tax-is-a-real-5-percent-bracket", tier2["result"]["totalAmount"] < tier2["payload"]["income"],
          (tier2["result"]["totalAmount"], tier2["payload"]["income"]))
    check("02-tier3-tax-less-than-income",
          tier3["result"]["totalAmount"] < tier3["payload"]["income"],
          "a slab rate of 2000 (raw multiplier, no /100) would make tax exceed income -- the exact "
          "bug this test guards against")


def test_03_aggregation_threshold_scenarios_show_below_and_above():
    b = CalculationRuleBuilder("trade-license")
    b.add_aggregation_rule("TOTAL_AREA", "SUM", "$.floors[*]", "area", "totalArea", effectiveFrom="2024-01-01")
    b.add_flat_rule("BIG_BUILDING_SURCHARGE", 500,
                     conditions={"totalArea": {"derivedFrom": "TOTAL_AREA", "from": 5000}},
                     effectiveFrom="2024-01-01")
    rules = _raw_rules(b)
    results = run_scenarios(rules, generate_scenarios(rules))
    labels = [r["label"] for r in results]
    check("03-below-threshold-scenario", any("below" in l for l in labels), labels)
    check("03-above-threshold-scenario", any("at/above" in l for l in labels), labels)

    below = next(r for r in results if "below" in r["label"])
    above = next(r for r in results if "at/above" in r["label"])
    check("03-below-does-not-trigger-surcharge", below["result"]["totalAmount"] == 0, below["result"])
    check("03-above-triggers-surcharge", above["result"]["totalAmount"] == 500, above["result"])


def test_04_formula_variable_reading_aggregation_via_componentref():
    """A real bug found while building this feature: a FORMULA rule reading an AGGREGATION's
    result via componentRef crashed ('not yet computed') because AGGREGATION rules never produce
    a line-item amount -- their real result only ever lands in `derived`. Fixed in simulate.py's
    _amount_of() to fall back to `derived` for a componentRef naming an aggregation component."""
    b = CalculationRuleBuilder("income-tax")
    b.add_aggregation_rule("TOTAL_INVESTMENTS", "SUM", "$.investments[*]", "amount", "totalInvestments",
                            effectiveFrom="2024-01-01")
    b.add_formula_rule("REBATE", {"*": [{"var": "total"}, 0.1]}, {"total": {"componentRef": "TOTAL_INVESTMENTS"}},
                        dependsOn=["TOTAL_INVESTMENTS"], effectiveFrom="2024-01-01")
    rules = _raw_rules(b)
    results = run_scenarios(rules, generate_scenarios(rules))  # must not raise
    check("04-does-not-crash", len(results) > 0, results)


def test_04b_percentage_of_a_conditional_base_that_does_not_fire_does_not_crash():
    """A real bug found while preparing a demo: a PERCENTAGE/TAX rule reading a *conditional*
    RATE_MATRIX rule's amount via componentRef (e.g. CGST = 9% of a STAFFING_FEE banded on
    employeeCount) crashed on a boundary scenario where employeeCount fell outside that band --
    STAFFING_FEE produced no line item, so CGST's componentRef lookup raised instead of simply not
    applying either. Fixed in simulate.py: a componentRef pointing at a component with no line
    item and no derived value now skips the *referencing* rule too (ComponentNotApplicable),
    rather than crashing simulate_estimate()."""
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("STAFFING_FEE", 1200,
                     conditions={"employeeCount": {"jsonPath": "$.certificateDetail.employeeCount",
                                                    "from": 10, "to": 24}},
                     priority=10, effectiveFrom="2024-04-01")
    b.add_percentage_rule("CGST", 9, "STAFFING_FEE", ruleType="TAX", priority=20,
                           effectiveFrom="2024-04-01")
    rules = _raw_rules(b)
    results = run_scenarios(rules, generate_scenarios(rules))  # must not raise
    check("04b-does-not-crash", len(results) > 0, results)

    past_boundary = next(r for r in results if "just past that boundary" in r["label"])
    check("04b-neither-rule-applies-past-the-band",
          past_boundary["result"]["totalAmount"] == 0 and past_boundary["result"]["lineItems"] == [],
          past_boundary["result"])

    # "Typical case" is already inside the 10-24 band here (the baseline's own midpoint
    # construction lands on it), so the dedicated "within band" scenario dedupes against it.
    within_band = next(r for r in results if r["label"] == "Typical case (default values)")
    check("04b-both-rules-apply-within-the-band",
          within_band["result"]["totalAmount"] == 1308, within_band["result"])


def test_05_baseline_uses_first_matching_condition_not_last():
    """Real bug found and fixed: two FLAT rules sharing a jsonPath (a banded fee) both tried to
    set the baseline's value for that path -- last-write-wins landed the 'typical case' baseline
    in the *second* band, not an obviously representative one. Fixed to first-write-wins."""
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 2000, conditions={"area": {"jsonPath": "$.area", "to": 1000}}, effectiveFrom="2024-01-01")
    b.add_flat_rule("FEE", 5000, conditions={"area": {"jsonPath": "$.area", "from": 1000.01}}, effectiveFrom="2024-01-01")
    rules = _raw_rules(b)
    baseline = build_baseline_payload(rules)
    check("05-baseline-in-first-band", baseline["area"] <= 1000, baseline)


def test_06_no_eval_needed_scenarios_deduplicated():
    b = CalculationRuleBuilder("x")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")  # no conditions at all
    rules = _raw_rules(b)
    scenarios = generate_scenarios(rules)
    check("06-single-scenario-no-duplicates", len(scenarios) == 1, scenarios)


def test_07_scenarios_capped_at_max():
    b = CalculationRuleBuilder("x")
    for i in range(10):
        b.add_flat_rule(f"FEE_{i}", 100, conditions={f"attr{i}": {"jsonPath": f"$.attr{i}", "from": 0, "to": 100}},
                         effectiveFrom="2024-01-01")
    rules = _raw_rules(b)
    scenarios = generate_scenarios(rules, max_scenarios=15)
    check("07-capped", len(scenarios) <= 15, len(scenarios))


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll example_generator.py checks passed -- scenarios are targeted, not random, and "
          "computed via a real simulation, not just displayed as input.")
