"""CalculationRuleBuilder: the testable data-collection layer behind the wizard, mirroring
../workflow-prototype/builder.py and ../registry-prototype/builder.py -- one method per wizard
question/mechanism-shape, driven identically by the interactive CLI and by automated tests.

Unlike WorkflowBuilder/SchemaBuilder (which mutate one growing object field-by-field),
CalculationRule's shape varies enough by calculationType that each method here takes a whole
rule's worth of already-decided parameters at once (mirroring how
WorkflowBuilder.add_action_to_new_state takes everything needed in one call, not many small
mutations) -- one method per mechanism shape in ../reference/calculation-rule-vocabulary.md,
rather than one generic "add_field"-style method that would need type-specific branches anyway.
"""

from __future__ import annotations

from models import AttributeBinding, AttributeCondition, CalculationRule, CalculationRuleSet, Slab


def _conditions_from(raw: dict) -> dict:
    """raw: {attrName: {jsonPath/derivedFrom, equals?, from?, to?}}"""
    return {name: AttributeCondition(**spec) for name, spec in raw.items()}


class CalculationRuleBuilder:
    def __init__(self, module: str):
        self.module = module
        self.rules: list[CalculationRule] = []
        self.assumptions: list[str] = []

    def add_flat_rule(self, component: str, value: float, ruleType: str = "RATE_MATRIX",
                       conditions: dict | None = None, dependsOn: list[str] | None = None,
                       priority: int = 100, roundOff: str = "NEAREST_1",
                       effectiveFrom: str = "", effectiveTo: str | None = None) -> CalculationRule:
        """'Charge a flat amount' -- optionally 'depending on conditions' (one row per band, all
        sharing one component)."""
        rule = CalculationRule(
            ruleType=ruleType, component=component, scope="ENTITY",
            conditions=_conditions_from(conditions or {}), calculationType="FLAT", value=value,
            dependsOn=dependsOn or [], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_per_unit_rule(self, component: str, rate: float, applies_on_json_path: str,
                           conditions: dict | None = None, dependsOn: list[str] | None = None,
                           priority: int = 100, roundOff: str = "NEAREST_1",
                           effectiveFrom: str = "", effectiveTo: str | None = None) -> CalculationRule:
        """'Charge a rate x some field' -- appliesOn.jsonPath names the field the rate multiplies."""
        rule = CalculationRule(
            ruleType="RATE_MATRIX", component=component, scope="ENTITY",
            conditions=_conditions_from(conditions or {}), calculationType="PER_UNIT", value=rate,
            appliesOn=AttributeBinding(jsonPath=applies_on_json_path),
            dependsOn=dependsOn or [], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_per_item_rule(self, component: str, rate: float, sub_entity_path: str,
                           applies_on_json_path: str, conditions: dict | None = None,
                           dependsOn: list[str] | None = None, priority: int = 100,
                           roundOff: str = "NEAREST_1", effectiveFrom: str = "",
                           effectiveTo: str | None = None) -> CalculationRule:
        """'Charge per item in a repeating list' (accessories, floors, taps) -- scope=SUBENTITY,
        subEntityPath names the array, jsonPath is relative to one element."""
        rule = CalculationRule(
            ruleType="RATE_MATRIX", component=component, scope="SUBENTITY",
            subEntityPath=sub_entity_path, conditions=_conditions_from(conditions or {}),
            calculationType="PER_UNIT", value=rate,
            appliesOn=AttributeBinding(jsonPath=applies_on_json_path),
            dependsOn=dependsOn or [], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_slab_rule(self, component: str, applies_on_json_path: str, slabs: list[dict],
                       conditions: dict | None = None, dependsOn: list[str] | None = None,
                       priority: int = 100, roundOff: str = "NEAREST_1",
                       effectiveFrom: str = "", effectiveTo: str | None = None) -> CalculationRule:
        """'Charge tiered/marginal bands of the same field' -- ONE rule, slabs built from every
        tier in order (never one rule per tier -- that's FLAT_OR_BANDED, a different mechanism)."""
        rule = CalculationRule(
            ruleType="RATE_MATRIX", component=component, scope="ENTITY",
            conditions=_conditions_from(conditions or {}), calculationType="SLAB",
            appliesOn=AttributeBinding(jsonPath=applies_on_json_path),
            slabs=[Slab(**s) for s in slabs],
            dependsOn=dependsOn or [], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_percentage_rule(self, component: str, percentage: float, applies_on_component: str,
                             ruleType: str = "TAX", conditions: dict | None = None,
                             priority: int = 100, roundOff: str = "NEAREST_1",
                             effectiveFrom: str = "", effectiveTo: str | None = None) -> CalculationRule:
        """'Add a tax/cess on top of a fee' -- appliesOn.componentRef + dependsOn on that same
        component (the engine never infers order)."""
        rule = CalculationRule(
            ruleType=ruleType, component=component, scope="ENTITY",
            conditions=_conditions_from(conditions or {}), calculationType="PERCENTAGE",
            value=percentage, appliesOn=AttributeBinding(componentRef=applies_on_component),
            dependsOn=[applies_on_component], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_adjustment_rule(self, component: str, value: float, applies_on_component: str,
                             is_percentage: bool = False, conditions: dict | None = None,
                             priority: int = 100, roundOff: str = "NEAREST_1",
                             effectiveFrom: str = "", effectiveTo: str | None = None) -> CalculationRule:
        """'Give a rebate/deduction' -- ADJUSTMENT ruleType, appliesOn.componentRef is *always*
        required (schema-verified, not optional), that component also always goes in dependsOn."""
        rule = CalculationRule(
            ruleType="ADJUSTMENT", component=component, scope="ENTITY",
            conditions=_conditions_from(conditions or {}),
            calculationType="PERCENTAGE" if is_percentage else "FLAT", value=value,
            appliesOn=AttributeBinding(componentRef=applies_on_component),
            dependsOn=[applies_on_component], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_aggregation_rule(self, component: str, aggregate_function: str, sub_entity_path: str,
                              source_json_path: str, target_attribute: str,
                              priority: int = 1, effectiveFrom: str = "",
                              effectiveTo: str | None = None) -> CalculationRule:
        """'Total up a list into one number' -- scope=SUBENTITY forced, low priority by default
        (AGGREGATION always runs before whatever depends on its result, regardless of priority,
        per the vocabulary reference -- the low default here is just a readability convention).

        No calculationType/value -- an AGGREGATION rule derives an attribute, it doesn't compute
        a billable amount; aggregateFunction is what drives behavior. An earlier version of this
        method set calculationType="FLAT", value=0 as an inert placeholder, following
        ../prototype/synthesize.py's own (unverified) convention -- calculation-rule-examples.pdf's
        real examples #22-24 confirm the real engine omits the field entirely for AGGREGATION."""
        rule = CalculationRule(
            ruleType="AGGREGATION", component=component, scope="SUBENTITY",
            subEntityPath=sub_entity_path,
            aggregateFunction=aggregate_function,
            sourceAttribute=AttributeBinding(jsonPath=source_json_path),
            targetAttribute=target_attribute, priority=priority,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def add_formula_rule(self, component: str, formula_logic: dict, variables: dict,
                          conditions: dict | None = None, dependsOn: list[str] | None = None,
                          priority: int = 100, roundOff: str = "NEAREST_1",
                          effectiveFrom: str = "", effectiveTo: str | None = None) -> CalculationRule:
        """'Do real math (not just a rate)' -- variables: {name: {jsonPath: ...} or {componentRef: ...}}."""
        rule = CalculationRule(
            ruleType="RATE_MATRIX", component=component, scope="ENTITY",
            conditions=_conditions_from(conditions or {}), calculationType="FORMULA",
            formulaLogic=formula_logic,
            formulaVariables={name: AttributeBinding(**spec) for name, spec in variables.items()},
            dependsOn=dependsOn or [], priority=priority, roundOff=roundOff,
            effectiveFrom=effectiveFrom, effectiveTo=effectiveTo)
        self.rules.append(rule)
        return rule

    def remove_rule(self, index: int) -> None:
        del self.rules[index]

    def add_assumption(self, text: str) -> None:
        self.assumptions.append(text)

    def build(self) -> CalculationRuleSet:
        return CalculationRuleSet(module=self.module, rules=list(self.rules), assumptions=list(self.assumptions))
