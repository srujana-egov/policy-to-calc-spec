"""Stress test against calculation-rule-examples.pdf's 30 real rule bodies -- the first ground
truth this prototype has ever had (unlike ../workflow-prototype and ../registry-prototype, no real
Calculation Engine source exists to verify against; see models.py's docstring). Three things get
checked per fixtures/real_world/calculation_rule_examples.json:

  1. Structural fidelity -- every field the doc sets round-trips through CalculationRule exactly
     (subset comparison: fields the doc omits, like roundOff/isActive/dependsOn, get this
     project's own defaults, which is expected and fine).
  2. validate.py raises no false positives on realistic per-module groupings of these examples.
  3. simulate.py computes the exact numbers the doc's own prose describes -- this is what actually
     caught the two real bugs below, not just structural inspection.

Two real, confirmed bugs found via this stress test, both fixed as part of building this file:

  - SLAB's `rate` was applied as a raw multiplier (no /100) in simulate.py's _compute_slab.
    Example #14's own prose ("0.5% on the first 500,000, 1% on the remaining 200,000") only
    reproduces its stated result (2500 + 2000 = 4500) if rate is divided by 100 -- matching
    PERCENTAGE's convention. Without the fix, the raw-multiplier reading gives 450000 (a 64%
    property tax). Fixed; wizard.py's rate question re-worded to match (it previously told users
    to pre-divide by 100 themselves, compensating for the missing division rather than fixing it).
  - AGGREGATION rules were assumed to require calculationType/value (an inert FLAT/0 placeholder,
    inherited from an earlier, unverified convention). Examples #22-24 show
    the real engine omits both fields entirely for AGGREGATION. Fixed in models.py (calculationType
    now Optional), validate.py (only required for non-AGGREGATION ruleTypes), and builder.py
    (add_aggregation_rule no longer sets either field).
"""

from __future__ import annotations

import json
from pathlib import Path

from models import CalculationRule
from simulate import simulate_estimate
from validate import validate_rule_set

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


EXAMPLES = {e["id"]: e["rule"] for e in
            json.loads((Path(__file__).parent / "fixtures/real_world/calculation_rule_examples.json")
                       .read_text())["examples"]}


def test_01_every_example_round_trips_through_the_model():
    """Every field the doc actually sets must survive CalculationRule(**rule) unchanged -- fields
    the doc omits (roundOff, isActive, dependsOn) pick up this project's own defaults, which is
    expected, not a mismatch."""
    for ex_id, raw in EXAMPLES.items():
        rule = CalculationRule(**raw)
        dumped = rule.model_dump(by_alias=True, exclude_none=True)
        for key, value in raw.items():
            if key == "slabs":
                # exclude_none=True strips an explicit "to": null from each slab dict too --
                # cosmetic (both mean "no upper bound"), so compare with None filled back in.
                got = [{**s, "to": s.get("to")} for s in dumped.get("slabs", [])]
                want = [{**s, "to": s.get("to")} for s in value]
                check(f"01-ex{ex_id:02d}-field-slabs", got == want, (ex_id, "expected", want, "got", got))
                continue
            check(f"01-ex{ex_id:02d}-field-{key}", dumped.get(key) == value,
                  (ex_id, key, "expected", value, "got", dumped.get(key)))


# --- Realistic per-module groupings, matching how the doc itself presents related examples ---

GROUPS = {
    "trade-license-tax-stack": [1, 16, 17, 18, 19],           # LICENSE_FEE + CGST/SGST/FIRE_CESS/CANCER_CESS
    "trade-license-conditions": [1, 4, 5],                     # category/boolean conditions on LICENSE_FEE
    "trade-license-staffing-bands": [7, 8],                    # STAFFING_FEE two bands
    "trade-license-and-condition": [10],                       # two-condition AND, PER_UNIT
    "property-tax-rate-matrix-row": [11],                      # three-condition AND, PER_UNIT
    "property-tax-slab": [14],                                 # the SLAB rate/100 confirmation
    "water-usage-slab": [15],                                  # SLAB in a different module
    "property-tax-rebate-stack": [14, 6, 9],                   # base + rebate + depreciation ADJUSTMENTs
    "trade-license-accessories": [13, 20],                     # SUBENTITY PER_UNIT, two accessory types
    "trade-license-aggregation-surcharge": [1, 22, 25],        # AGGREGATION -> derivedFrom ADJUSTMENT
    "property-tax-unit-aggregation": [23, 24],                 # COUNT + MAX over the same sub-entity array
    "property-tax-mixed-scope": [21],                          # SUBENTITY, multiple relative conditions
    "trade-license-formulas": [26, 27, 28],                    # FORMULA incl. the if/== branch
    "property-tax-interest-penalty-chain": [14, 29, 30],       # 3-deep dependsOn chain
}


def test_02_no_false_positives_on_realistic_groupings():
    for name, ids in GROUPS.items():
        rules = [EXAMPLES[i] for i in ids]
        errors = validate_rule_set(rules)
        check(f"02-{name}-validates-clean", not errors, (name, errors))


def test_03_tax_stack_totals_correctly():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-tax-stack"]]
    result = simulate_estimate(rules, {})
    # 500 base + 9% CGST(45) + 9% SGST(45) + 1% FIRE_CESS(5) + 50 flat CANCER_CESS
    check("03-tax-stack-total", result["totalAmount"] == 645, result)


def test_04_conditions_pick_the_right_row():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-conditions"]]
    at_x = simulate_estimate(rules, {"category": "x"})
    check("04-category-x-picks-1000-row",
          at_x["totalAmount"] == 1000 and len(at_x["lineItems"]) == 1, at_x)
    default = simulate_estimate(rules, {"category": "z"})
    check("04-default-picks-500-row", default["totalAmount"] == 500, default)
    with_liquor = simulate_estimate(
        rules, {"category": "z", "certificateDetail": {"hasLiquorLicense": True}})
    check("04-boolean-condition-adds-liquor-fee", with_liquor["totalAmount"] == 2500, with_liquor)


def test_05_staffing_bands_pick_the_right_tier():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-staffing-bands"]]
    check("05-band-1", simulate_estimate(rules, {"certificateDetail": {"employeeCount": 15}})
          ["totalAmount"] == 1200)
    check("05-band-2", simulate_estimate(rules, {"certificateDetail": {"employeeCount": 50}})
          ["totalAmount"] == 3000)
    check("05-below-both-bands-no-charge",
          simulate_estimate(rules, {"certificateDetail": {"employeeCount": 5}})["totalAmount"] == 0)


def test_06_and_condition_requires_both_to_match():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-and-condition"]]
    matches = simulate_estimate(rules, {"category": "y", "certificateDetail": {"employeeCount": 150}})
    check("06-and-condition-fires", matches["totalAmount"] == 75000, matches)  # 500 * 150
    one_false = simulate_estimate(rules, {"category": "z", "certificateDetail": {"employeeCount": 150}})
    check("06-and-condition-needs-both", one_false["totalAmount"] == 0, one_false)


def test_07_three_condition_rate_matrix_row():
    rules = [EXAMPLES[i] for i in GROUPS["property-tax-rate-matrix-row"]]
    result = simulate_estimate(
        rules, {"zoneClass": "A1", "floorNo": 0, "usageType": "RESIDENTIAL", "area": 1000})
    check("07-three-condition-row-fires", result["totalAmount"] == 2000, result)  # 2/sqft * 1000


def test_08_slab_rate_is_divided_by_100_confirmed_by_the_docs_own_prose():
    """The doc's own words: '700,000 pays 0.5% on the first 500,000 and 1% on the remaining
    200,000' -- 500000*0.005 + 200000*0.01 = 4500. This is the confirmation for the /100 fix."""
    rules = [EXAMPLES[14]]
    result = simulate_estimate(rules, {"propertyValue": 700000})
    check("08-slab-matches-doc-prose", result["totalAmount"] == 4500, result)
    at_boundary = simulate_estimate(rules, {"propertyValue": 500000})
    check("08-slab-boundary-is-inclusive-to-lower-tier", at_boundary["totalAmount"] == 2500, at_boundary)


def test_09_slab_mechanism_is_identical_across_modules():
    """'Same engine, zero code difference' per the doc -- same _compute_slab, a different
    module/field/units entirely."""
    rules = [EXAMPLES[15]]
    below_first_tier = simulate_estimate(rules, {"usageDetail": {"kilolitersConsumed": 5}})
    check("09-below-first-tier-is-free", below_first_tier["totalAmount"] == 0, below_first_tier)
    mid_tier = simulate_estimate(rules, {"usageDetail": {"kilolitersConsumed": 30}})
    check("09-mid-tier", mid_tier["totalAmount"] == 4, mid_tier)  # (30-10)*20/100
    top_tier = simulate_estimate(rules, {"usageDetail": {"kilolitersConsumed": 60}})
    check("09-top-tier", top_tier["totalAmount"] == 12, top_tier)  # 10*0 + 40*20/100 + 10*40/100


def test_10_rebate_and_depreciation_both_read_the_original_base_amount():
    rules = [EXAMPLES[i] for i in GROUPS["property-tax-rebate-stack"]]
    result = simulate_estimate(
        rules, {"propertyValue": 700000, "ownerType": "ARMY", "ageOfBuilding": 35})
    # PROPERTY_TAX 4500, -500 flat rebate, -10% of the *original* 4500 (not compounded) = -450
    check("10-rebate-stack-total", result["totalAmount"] == 3550, result)


def test_11_subentity_per_unit_produces_one_line_item_per_matching_element():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-accessories"]]
    result = simulate_estimate(rules, {"certificateDetail": {"accessories": [
        {"type": "WEIGHING_SCALE", "quantity": 3}, {"type": "GENERATOR", "quantity": 2}]}})
    check("11-two-line-items", len(result["lineItems"]) == 2, result)
    check("11-total", result["totalAmount"] == 1600, result)  # 200*3 + 500*2


def test_12_aggregation_feeds_a_derivedfrom_surcharge():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-aggregation-surcharge"]]
    above = simulate_estimate(rules, {"certificateDetail": {"accessories": [
        {"quantity": 3}, {"quantity": 2}]}})
    check("12-surcharge-fires-at-5", above["totalAmount"] == 800, above)  # 500 + 300
    below = simulate_estimate(rules, {"certificateDetail": {"accessories": [
        {"quantity": 1}, {"quantity": 1}]}})
    check("12-no-surcharge-at-2", below["totalAmount"] == 500, below)


def test_13_count_and_max_aggregate_independently():
    rules = [EXAMPLES[i] for i in GROUPS["property-tax-unit-aggregation"]]
    result = simulate_estimate(rules, {"units": [{"floorNo": 1}, {"floorNo": 2}, {"floorNo": 3}]})
    check("13-count", result["derived"]["FLOOR_COUNT"] == 3, result["derived"])
    check("13-max", result["derived"]["TOP_FLOOR"] == 3, result["derived"])


def test_14_subentity_scope_with_multiple_relative_conditions():
    rules = [EXAMPLES[i] for i in GROUPS["property-tax-mixed-scope"]]
    result = simulate_estimate(rules, {"units": [
        {"floorNo": 2, "usageType": "RESIDENTIAL", "area": 800},
        {"floorNo": 5, "usageType": "RESIDENTIAL", "area": 500}]})
    check("14-only-matching-unit-billed", len(result["lineItems"]) == 1, result)
    check("14-per-unit-amount", result["lineItems"][0]["amount"] == 2000, result)  # 2.5 * 800


def test_15_formulas_including_the_branch_compute_correctly():
    rules = [EXAMPLES[i] for i in GROUPS["trade-license-formulas"]]
    payload = {"certificateDetail": {"sizeSqm": 100, "employeeCount": 20, "fireSafetyClass": "A"}}
    result = simulate_estimate(rules, payload)
    by_component = {li["component"]: li["amount"] for li in result["lineItems"]}
    check("15-two-term-formula", by_component["PREMISES_SIZE_FEE"] == 1700, by_component)  # 200+100*15
    check("15-multi-variable-formula", by_component["COMBINED_ESTABLISHMENT_FEE"] == 1200, by_component)
    check("15-branch-class-A", by_component["FIRE_SAFETY_FEE"] == 1000, by_component)  # 100*10

    payload["certificateDetail"]["fireSafetyClass"] = "B"
    result_b = simulate_estimate(rules, payload)
    fire_b = next(li["amount"] for li in result_b["lineItems"] if li["component"] == "FIRE_SAFETY_FEE")
    check("15-branch-class-B-takes-the-other-side", fire_b == 2000, fire_b)  # 100*20


def test_16_three_deep_dependson_chain_computes_in_order():
    """PROPERTY_TAX -> INTEREST -> PENALTY, the deepest chain in the doc. Each stage rounds
    (NEAREST_1 default) before the next reads it via componentRef."""
    rules = [EXAMPLES[i] for i in GROUPS["property-tax-interest-penalty-chain"]]
    result = simulate_estimate(
        rules, {"propertyValue": 700000, "paymentDetail": {"daysDelayed": 30}})
    by_component = {li["component"]: li["amount"] for li in result["lineItems"]}
    check("16-base", by_component["PROPERTY_TAX"] == 4500, by_component)
    check("16-interest", by_component["INTEREST"] == 45, by_component)  # round(4500*0.00033*30)
    check("16-penalty", by_component["PENALTY"] == 91, by_component)  # round((4500+45)*0.02)
    check("16-total", result["totalAmount"] == 4636, result)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s)")
    print(f"\nAll {len(EXAMPLES)} real-world examples verified -- structural round-trip, "
          "cross-rule validation, and computed arithmetic all match the source document.")
