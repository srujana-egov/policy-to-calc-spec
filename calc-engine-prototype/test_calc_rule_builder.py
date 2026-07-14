"""Tests: CalculationRuleBuilder + validate_rule_set, mirroring
../workflow-prototype/test_workflow_builder.py and ../registry-prototype/test_schema_builder.py's
role -- a real example (the actual Chennai Schedule I fixture, already proven in
../prototype/fixtures_generated/) built entirely through builder calls, plus one test per
completeness check.
"""

import json
from pathlib import Path

from builder import CalculationRuleBuilder
from models import AttributeBinding, AttributeCondition, CalculationRule, CalculationRuleSet, Slab
from validate import validate_rule_set, validate_rule_set_models

FIXTURES = Path(__file__).parent / "fixtures"
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def canonicalize_real_world(real: dict) -> dict:
    """Strips fields this prototype doesn't produce from the real fixture before comparing:
    sourceDocument (provenance metadata, not part of the CalculationRule contract) and any
    per-rule fields the real fixture simply omits because they equal the schema default
    (isActive, dependsOn) -- comparing rule-by-rule on the fields that actually matter."""
    return {"module": real["module"], "rules": real["rules"]}


def canonical_rules(rule_set) -> list[dict]:
    dumped = [r.model_dump(by_alias=True, exclude_none=True) for r in rule_set.rules]
    for r in dumped:
        r.pop("isActive", None)
        if r.get("dependsOn") == []:
            r.pop("dependsOn")
    return dumped


def build_chennai_schedule_i() -> CalculationRuleBuilder:
    """The real fixture from ../prototype/fixtures_generated/chennai_schedule_I_rules.json,
    reproduced entirely through builder calls -- FLAT_OR_BANDED mechanism, two variants sharing
    one component, per the mapping table in ../CONFIG-PIPELINE.md."""
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule(
        "MICRO_COTTAGE_LICENSE_FEE", 2000,
        conditions={"premisesArea": {"jsonPath": "$.tradeLicenseDetail.premisesArea", "to": 1000}},
        priority=10, effectiveFrom="2008-07-30")
    b.add_flat_rule(
        "MICRO_COTTAGE_LICENSE_FEE", 5000,
        conditions={"premisesArea": {"jsonPath": "$.tradeLicenseDetail.premisesArea", "from": 1000.01}},
        priority=10, effectiveFrom="2008-07-30")
    return b


def test_01_chennai_schedule_i_matches_real_fixture():
    rule_set = build_chennai_schedule_i().build()
    errors = validate_rule_set_models(rule_set)
    check("01-validates-clean", not errors, errors)

    real = canonicalize_real_world(json.loads((FIXTURES / "real_world" / "chennai_schedule_I_rules.json").read_text()))
    check("01-module-matches", rule_set.module == real["module"])
    check("01-rules-match", canonical_rules(rule_set) == real["rules"], canonical_rules(rule_set))


def test_02_all_eight_mechanisms_build_and_validate_clean():
    b = CalculationRuleBuilder("test-module")
    b.add_flat_rule("FLAT_FEE", 100, effectiveFrom="2024-01-01")
    b.add_per_unit_rule("PER_SQFT_FEE", 5, "$.area", effectiveFrom="2024-01-01")
    # "quantity"/"area" below are relative to one item in the list (simulate.py resolves them
    # against each sub-entity dict directly, per the vocabulary reference's "relative to one
    # array element") -- not '$.accessories[*].quantity', a root-absolute path that would
    # silently fail to resolve. Real bug found and fixed while wiring up worked-example
    # simulation -- see wizard.py's configure_aggregation()/configure_per_item().
    b.add_per_item_rule("PER_ACCESSORY_FEE", 50, "$.accessories[*]", "quantity",
                         effectiveFrom="2024-01-01")
    b.add_slab_rule("TIERED_FEE", "$.income",
                     [{"from": 0, "to": 250000, "rate": 0}, {"from": 250000, "rate": 10}],
                     effectiveFrom="2024-01-01")
    b.add_percentage_rule("CESS", 5, "FLAT_FEE", effectiveFrom="2024-01-01")
    b.add_adjustment_rule("SENIOR_REBATE", -10, "FLAT_FEE", is_percentage=True, effectiveFrom="2024-01-01")
    b.add_aggregation_rule("TOTAL_AREA", "SUM", "$.floors[*]", "area", "totalArea",
                            effectiveFrom="2024-01-01")
    b.add_formula_rule("COMPLEX_FEE", {"+": [{"var": "base"}, {"*": [{"var": "rate"}, {"var": "size"}]}]},
                        {"base": {"jsonPath": "$.baseFee"}, "rate": {"jsonPath": "$.rate"},
                         "size": {"jsonPath": "$.area"}}, effectiveFrom="2024-01-01")
    errors = validate_rule_set_models(b.build())
    check("02-eight-mechanisms-no-errors", not errors, errors)
    check("02-eight-rules-built", len(b.rules) == 8, len(b.rules))


def test_03_missing_required_field_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "value": 1}  # missing effectiveFrom
    errors = validate_rule_set([rule])
    check("03-missing-field-caught", any("missing required field 'effectiveFrom'" in e for e in errors), errors)


def test_04_unknown_ruletype_caught():
    rule = {"ruleType": "BOGUS", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "value": 1, "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("04-unknown-ruletype-caught", any("unknown ruleType 'BOGUS'" in e for e in errors), errors)


def test_05_subentity_requires_path_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "SUBENTITY", "calculationType": "FLAT",
            "value": 1, "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("05-subentity-path-required-caught", any("scope=SUBENTITY requires subEntityPath" in e for e in errors), errors)


def test_06_flat_requires_value_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("06-value-required-caught", any("calculationType=FLAT requires 'value'" in e for e in errors), errors)


def test_07_adjustment_requires_applies_on_component_ref():
    rule = {"ruleType": "ADJUSTMENT", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "value": -1, "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("07-adjustment-appliesOn-required", any("ruleType=ADJUSTMENT requires appliesOn.componentRef" in e for e in errors), errors)


def test_08_attribute_binding_both_set_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "PER_UNIT",
            "value": 1, "appliesOn": {"jsonPath": "$.a", "componentRef": "Y"}, "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("08-binding-both-set-caught", any("appliesOn sets both 'jsonPath' and 'componentRef'" in e for e in errors), errors)


def test_09_attribute_binding_neither_set_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "PER_UNIT",
            "value": 1, "appliesOn": {}, "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("09-binding-neither-set-caught", any("appliesOn sets neither" in e for e in errors), errors)


def test_10_slab_overlap_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "SLAB",
            "appliesOn": {"jsonPath": "$.a"},
            "slabs": [{"from": 0, "to": 100, "rate": 1}, {"from": 50, "to": 200, "rate": 2}],
            "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("10-slab-overlap-caught", any("slabs overlap" in e for e in errors), errors)


def test_11_formula_undeclared_variable_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FORMULA",
            "formulaLogic": {"var": "ghost"}, "formulaVariables": {"real": {"jsonPath": "$.a"}},
            "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("11-formula-undeclared-var-caught", any("undeclared variable" in e for e in errors), errors)


def test_12_aggregation_requirements_caught():
    """No calculationType/value here -- AGGREGATION rules omit both entirely (see models.py)."""
    rule = {"ruleType": "AGGREGATION", "component": "X", "scope": "ENTITY", "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("12-aggregation-scope-caught", any("ruleType=AGGREGATION requires scope=SUBENTITY" in e for e in errors), errors)
    check("12-aggregation-func-caught", any("requires a valid aggregateFunction" in e for e in errors), errors)
    check("12-aggregation-source-caught", any("requires sourceAttribute.jsonPath" in e for e in errors), errors)
    check("12-aggregation-target-caught", any("requires targetAttribute" in e for e in errors), errors)


def test_13_condition_equals_and_range_together_caught():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "value": 1, "conditions": {"a": {"jsonPath": "$.a", "equals": 1, "from": 0}},
            "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("13-equals-and-range-caught", any("sets both 'equals' and 'from'/'to'" in e for e in errors), errors)


def test_14_condition_missing_jsonpath_and_derivedfrom_caught():
    """A real gap found while adapting ../prototype/validate.py: its own per-condition check
    only ever required 'jsonPath', with no exception for 'derivedFrom' -- even though the model,
    _validate_attribute_path_registry, and ../reference/calculation-rule-vocabulary.md all treat
    derivedFrom as a real, valid alternative (banding on a previous AGGREGATION's result). Fixed
    here to require either."""
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "value": 1, "conditions": {"a": {}}, "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("14-missing-jsonpath-or-derivedfrom-caught",
          any("needs either 'jsonPath' or 'derivedFrom'" in e for e in errors), errors)


def test_15_derived_from_condition_is_valid_no_false_positive():
    rule = {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
            "value": 1, "conditions": {"a": {"derivedFrom": "SOME_AGGREGATION"}},
            "effectiveFrom": "2024-01-01"}
    errors = validate_rule_set([rule])
    check("15-derivedfrom-condition-no-false-positive", not errors, errors)


def test_16_attribute_path_conflict_caught():
    rules = [
        {"ruleType": "RATE_MATRIX", "component": "X", "scope": "ENTITY", "calculationType": "FLAT",
         "value": 1, "conditions": {"a": {"jsonPath": "$.a"}}, "effectiveFrom": "2024-01-01"},
        {"ruleType": "RATE_MATRIX", "component": "Y", "scope": "ENTITY", "calculationType": "FLAT",
         "value": 1, "conditions": {"a": {"jsonPath": "$.different"}}, "effectiveFrom": "2024-01-01"},
    ]
    errors = validate_rule_set(rules)
    check("16-attribute-path-conflict-caught", any("409 AttributePath.Conflict" in e for e in errors), errors)


def test_17_dependson_cycle_caught():
    rules = [
        {"ruleType": "RATE_MATRIX", "component": "A", "scope": "ENTITY", "calculationType": "FLAT",
         "value": 1, "dependsOn": ["B"], "effectiveFrom": "2024-01-01"},
        {"ruleType": "RATE_MATRIX", "component": "B", "scope": "ENTITY", "calculationType": "FLAT",
         "value": 1, "dependsOn": ["A"], "effectiveFrom": "2024-01-01"},
    ]
    errors = validate_rule_set(rules)
    check("17-dependson-cycle-caught", any("cycle detected" in e for e in errors), errors)


def test_18_overlapping_bands_caught():
    rules = [
        {"ruleType": "RATE_MATRIX", "component": "FEE", "scope": "ENTITY", "calculationType": "FLAT",
         "value": 100, "conditions": {"area": {"jsonPath": "$.area", "to": 1000}},
         "effectiveFrom": "2024-01-01"},
        {"ruleType": "RATE_MATRIX", "component": "FEE", "scope": "ENTITY", "calculationType": "FLAT",
         "value": 200, "conditions": {"area": {"jsonPath": "$.area", "from": 500}},
         "effectiveFrom": "2024-01-01"},
    ]
    errors = validate_rule_set(rules)
    check("18-overlapping-bands-caught", any("overlapping" in e and "area" in e for e in errors), errors)


def test_19_no_false_positives_on_chennai_fixture():
    errors = validate_rule_set_models(build_chennai_schedule_i().build())
    check("19-no-false-positives", not errors, errors)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll CalculationRuleBuilder + completeness checks verified against the real Chennai fixture.")
