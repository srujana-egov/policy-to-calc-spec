"""Regression test: does PolicyRule actually have a field for every one of the 30 examples in
calculation-rule-examples.pdf? Each function below constructs one example as a PolicyRule and
asserts it validates. This is the reusable answer to "what happens when the target schema grows":
add one more function here and run this file -- a red test tells you immediately which pattern
the schema can't hold yet, instead of manually re-reading all 30 (or 40, or 50) examples again.

Run: .venv/bin/python -m pytest test_policy_rule_coverage.py -v
(or just .venv/bin/python test_policy_rule_coverage.py to run without pytest)
"""

from models import PolicyRule, PolicyCondition, PolicyRuleVariant, PolicyValueSource

PASSED = []
FAILED = []


def check(tier_example: str, rule: PolicyRule):
    PASSED.append(tier_example)  # construction succeeding IS the check -- Pydantic raises on failure


def test_01_flat_fee_always_applies():
    check("01", PolicyRule(scheduleId="EX01", tradeNames=["Any licence holder"], mechanism="FLAT_OR_BANDED",
        variants=[PolicyRuleVariant(conditions=[], amount=500)],
        sourceText="flat fee 500", confidence=0.95))


def test_02_03_percentage_tax_twins():
    for name, comp in [("EX02", "SGST"), ("EX03", "CGST")]:
        check(name, PolicyRule(scheduleId=name, tradeNames=["Any licence holder"], mechanism="PERCENTAGE_OF_COMPONENT",
            referencesComponents=["LICENSE_FEE"], amountIsPercentage=True,
            variants=[PolicyRuleVariant(conditions=[], amount=18)],
            sourceText=f"18% {comp} on license fee", confidence=0.9))


def test_04_exact_match_condition():
    check("04", PolicyRule(scheduleId="EX04", tradeNames=["Category x"], mechanism="FLAT_OR_BANDED",
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="category", suggestedJsonPath="$.category", equals="x")], amount=1000)],
        sourceText="category x pays 1000", confidence=0.9))


def test_05_boolean_condition():
    check("05", PolicyRule(scheduleId="EX05", tradeNames=["Liquor licensees"], mechanism="FLAT_OR_BANDED",
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="hasLiquorLicense", suggestedJsonPath="$.certificateDetail.hasLiquorLicense", equals=True)],
            amount=2000)],
        sourceText="liquor add-on fee 2000", confidence=0.9))


def test_06_flat_rebate():
    check("06", PolicyRule(scheduleId="EX06", tradeNames=["Army-owned property"], mechanism="REBATE_OF_COMPONENT",
        amountIsPercentage=False, referencesComponents=["PROPERTY_TAX"],
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="ownerType", suggestedJsonPath="$.ownerType", equals="ARMY")], amount=-500)],
        sourceText="army owners get 500 rebate", confidence=0.9))


def test_07_08_numeric_bands():
    check("07-08", PolicyRule(scheduleId="EX07-08", tradeNames=["Staffed establishments"], mechanism="FLAT_OR_BANDED",
        variants=[
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="employeeCount", suggestedJsonPath="$.certificateDetail.employeeCount", **{"from": 10, "to": 24})], amount=1200),
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="employeeCount", suggestedJsonPath="$.certificateDetail.employeeCount", **{"from": 25, "to": 100})], amount=3000),
        ],
        sourceText="10-24 employees: 1200, 25-100: 3000", confidence=0.9))


def test_09_percentage_rebate_open_range():
    check("09", PolicyRule(scheduleId="EX09", tradeNames=["Aged buildings"], mechanism="REBATE_OF_COMPONENT",
        amountIsPercentage=True, referencesComponents=["PROPERTY_TAX"],
        variants=[PolicyRuleVariant(conditions=[PolicyCondition(attributeName="ageOfBuilding", suggestedJsonPath="$.ageOfBuilding", **{"from": 30})], amount=-10)],
        sourceText="30+ year buildings get 10% depreciation", confidence=0.85))


def test_10_two_and_conditions_per_unit():
    check("10", PolicyRule(scheduleId="EX10", tradeNames=["Category y, 100+ employees"], mechanism="PER_UNIT",
        rateAppliesToAttribute="employeeCount",
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="category", suggestedJsonPath="$.category", equals="y"),
            PolicyCondition(attributeName="employeeCount", suggestedJsonPath="$.certificateDetail.employeeCount", **{"from": 100}),
        ], amount=500)],
        sourceText="category y with 100+ employees: 500 per employee", confidence=0.85))


def test_11_three_and_conditions_per_unit():
    check("11", PolicyRule(scheduleId="EX11", tradeNames=["Residential zone A1 ground floor"], mechanism="PER_UNIT",
        rateAppliesToAttribute="area",
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="zoneClass", suggestedJsonPath="$.zoneClass", equals="A1"),
            PolicyCondition(attributeName="floorNo", suggestedJsonPath="$.floorNo", **{"from": 0, "to": 0}),
            PolicyCondition(attributeName="usageType", suggestedJsonPath="$.usageType", equals="RESIDENTIAL"),
        ], amount=2)],
        sourceText="zone A1, ground floor, residential: 2/sqft", confidence=0.85))


def test_12_per_unit_no_conditions():
    check("12", PolicyRule(scheduleId="EX12", tradeNames=["Any property"], mechanism="PER_UNIT",
        rateAppliesToAttribute="area",
        variants=[PolicyRuleVariant(conditions=[], amount=2)],
        sourceText="2 rupees per square foot", confidence=0.9))


def test_13_per_item_in_list():
    check("13", PolicyRule(scheduleId="EX13", tradeNames=["Weighing scale accessory"], mechanism="PER_ITEM_IN_LIST",
        subEntityHint="accessories", rateAppliesToAttribute="quantity",
        variants=[PolicyRuleVariant(conditions=[PolicyCondition(attributeName="accessoryType", suggestedJsonPath="type", equals="WEIGHING_SCALE")], amount=200)],
        sourceText="200 per weighing scale", confidence=0.9))


def test_14_two_tier_slab():
    check("14", PolicyRule(scheduleId="EX14", tradeNames=["All properties"], mechanism="SLAB",
        rateAppliesToAttribute="propertyValue",
        variants=[
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="propertyValue", suggestedJsonPath="$.propertyValue", **{"from": 0, "to": 500000})], amount=0.5),
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="propertyValue", suggestedJsonPath="$.propertyValue", **{"from": 500000})], amount=1),
        ],
        sourceText="0.5% on first 500000, 1% on remainder", confidence=0.85))


def test_15_three_tier_slab_different_module():
    check("15", PolicyRule(scheduleId="EX15", tradeNames=["Water connections"], mechanism="SLAB",
        rateAppliesToAttribute="kilolitersConsumed",
        variants=[
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="kilolitersConsumed", suggestedJsonPath="$.usageDetail.kilolitersConsumed", **{"from": 0, "to": 10})], amount=0),
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="kilolitersConsumed", suggestedJsonPath="$.usageDetail.kilolitersConsumed", **{"from": 10, "to": 50})], amount=20),
            PolicyRuleVariant(conditions=[PolicyCondition(attributeName="kilolitersConsumed", suggestedJsonPath="$.usageDetail.kilolitersConsumed", **{"from": 50})], amount=40),
        ],
        sourceText="0-10kl free, 10-50kl @20, 50+ @40", confidence=0.85))


def test_16_19_tax_cess_stack():
    check("16-CGST", PolicyRule(scheduleId="EX16", tradeNames=["Licence holders"], mechanism="PERCENTAGE_OF_COMPONENT",
        referencesComponents=["LICENSE_FEE"], amountIsPercentage=True,
        variants=[PolicyRuleVariant(conditions=[], amount=9)], sourceText="9% CGST", confidence=0.9))
    check("17-SGST", PolicyRule(scheduleId="EX17", tradeNames=["Licence holders"], mechanism="PERCENTAGE_OF_COMPONENT",
        referencesComponents=["LICENSE_FEE"], amountIsPercentage=True,
        variants=[PolicyRuleVariant(conditions=[], amount=9)], sourceText="9% SGST", confidence=0.9))
    check("18-FIRE_CESS", PolicyRule(scheduleId="EX18", tradeNames=["Licence holders"], mechanism="PERCENTAGE_OF_COMPONENT",
        referencesComponents=["LICENSE_FEE"], amountIsPercentage=True,
        variants=[PolicyRuleVariant(conditions=[], amount=1)], sourceText="1% fire cess", confidence=0.9))
    # The tricky one: FLAT, but still sequenced after LICENSE_FEE with no value read from it
    check("19-CANCER_CESS", PolicyRule(scheduleId="EX19", tradeNames=["Licence holders"], mechanism="FLAT_OR_BANDED",
        referencesComponents=["LICENSE_FEE"],
        variants=[PolicyRuleVariant(conditions=[], amount=50)], sourceText="flat 50 cancer cess, after licence fee", confidence=0.85))


def test_20_second_accessory_type():
    check("20", PolicyRule(scheduleId="EX20", tradeNames=["Generator accessory"], mechanism="PER_ITEM_IN_LIST",
        subEntityHint="accessories", rateAppliesToAttribute="quantity",
        variants=[PolicyRuleVariant(conditions=[PolicyCondition(attributeName="accessoryType", suggestedJsonPath="type", equals="GENERATOR")], amount=500)],
        sourceText="500 per generator", confidence=0.9))


def test_21_subentity_multiple_conditions():
    check("21", PolicyRule(scheduleId="EX21", tradeNames=["Floors 1-3, residential"], mechanism="PER_ITEM_IN_LIST",
        subEntityHint="units", rateAppliesToAttribute="area",
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="floorNo", suggestedJsonPath="floorNo", **{"from": 1, "to": 3}),
            PolicyCondition(attributeName="usageType", suggestedJsonPath="usageType", equals="RESIDENTIAL"),
        ], amount=2.5)],
        sourceText="floors 1-3 residential: 2.5/sqft", confidence=0.85))


def test_22_aggregation_sum():
    check("22", PolicyRule(scheduleId="EX22", tradeNames=["All accessories"], mechanism="AGGREGATION",
        subEntityHint="accessories", aggregateFunctionHint="SUM", aggregationTargetName="numberOfAccessories",
        variants=[], valueSources=[PolicyValueSource(variableName="quantity", suggestedJsonPath="quantity")],
        sourceText="sum of accessory quantities", confidence=0.9))


def test_23_aggregation_count():
    check("23", PolicyRule(scheduleId="EX23", tradeNames=["All floors"], mechanism="AGGREGATION",
        subEntityHint="units", aggregateFunctionHint="COUNT", aggregationTargetName="floorCount",
        variants=[], valueSources=[PolicyValueSource(variableName="floorNo", suggestedJsonPath="floorNo")],
        sourceText="count of floors", confidence=0.9))


def test_24_aggregation_max():
    check("24", PolicyRule(scheduleId="EX24", tradeNames=["All floors"], mechanism="AGGREGATION",
        subEntityHint="units", aggregateFunctionHint="MAX", aggregationTargetName="topFloor",
        variants=[], valueSources=[PolicyValueSource(variableName="floorNo", suggestedJsonPath="floorNo")],
        sourceText="max floor number", confidence=0.9))


def test_25_rebate_on_derived_value():
    check("25", PolicyRule(scheduleId="EX25", tradeNames=["Bulk accessory holders"], mechanism="REBATE_OF_COMPONENT",
        amountIsPercentage=False, referencesComponents=["LICENSE_FEE", "TOTAL_ACCESSORY_COUNT"],
        variants=[PolicyRuleVariant(conditions=[
            PolicyCondition(attributeName="numberOfAccessories", suggestedJsonPath="", derivedFrom="TOTAL_ACCESSORY_COUNT", **{"from": 4})], amount=300)],
        sourceText="4+ accessories: +300 surcharge", confidence=0.8))


def test_26_simple_formula():
    check("26", PolicyRule(scheduleId="EX26", tradeNames=["Premises"], mechanism="FORMULA",
        valueSources=[PolicyValueSource(variableName="size", suggestedJsonPath="$.certificateDetail.sizeSqm")],
        formulaHint="200 + 15*size", variants=[], sourceText="200 + 15/sqm", confidence=0.85))


def test_27_formula_multi_variable():
    check("27", PolicyRule(scheduleId="EX27", tradeNames=["Combined establishments"], mechanism="FORMULA",
        valueSources=[
            PolicyValueSource(variableName="employees", suggestedJsonPath="$.certificateDetail.employeeCount"),
            PolicyValueSource(variableName="size", suggestedJsonPath="$.certificateDetail.sizeSqm"),
        ],
        formulaHint="500 + 10*employees + 5*size", variants=[], sourceText="combined fee formula", confidence=0.8))


def test_28_formula_with_branch():
    check("28", PolicyRule(scheduleId="EX28", tradeNames=["Fire safety rated premises"], mechanism="FORMULA",
        valueSources=[
            PolicyValueSource(variableName="fireSafetyClass", suggestedJsonPath="$.certificateDetail.fireSafetyClass"),
            PolicyValueSource(variableName="size", suggestedJsonPath="$.certificateDetail.sizeSqm"),
        ],
        formulaHint="size*10 if fireSafetyClass=='A' else size*20", variants=[],
        sourceText="fire safety fee, branches on class", confidence=0.75))


def test_29_interest_time_based():
    check("29", PolicyRule(scheduleId="EX29", tradeNames=["Overdue property tax"], mechanism="TIME_BASED",
        referencesComponents=["PROPERTY_TAX"],
        valueSources=[
            PolicyValueSource(variableName="principal", referencesComponent="PROPERTY_TAX"),
            PolicyValueSource(variableName="daysDelayed", suggestedJsonPath="$.paymentDetail.daysDelayed"),
        ],
        formulaHint="principal * 0.00033 * daysDelayed", variants=[],
        sourceText="0.033% per day interest", confidence=0.8))


def test_30_penalty_two_dependencies():
    check("30", PolicyRule(scheduleId="EX30", tradeNames=["Overdue property tax with interest"], mechanism="TIME_BASED",
        referencesComponents=["PROPERTY_TAX", "INTEREST"],
        valueSources=[
            PolicyValueSource(variableName="principal", referencesComponent="PROPERTY_TAX"),
            PolicyValueSource(variableName="interest", referencesComponent="INTEREST"),
        ],
        formulaHint="(principal + interest) * 0.02", variants=[],
        sourceText="2% penalty on tax plus interest", confidence=0.8))


if __name__ == "__main__":
    import sys
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} example(s) constructed and validated -> {PASSED}")
    if FAILED:
        print(f"FAILED: {FAILED}")
        sys.exit(1)
    print("\nAll 30 reference examples (across all 11 tiers) now have a place in PolicyRule.")
