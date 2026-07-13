"""Stage 4: Rule Synthesis. Maps PolicyRule[] -> CalculationRule[] deterministically -- plain
code, one branch per mechanism, no LLM call and no prompt.

This replaces an earlier, LLM-based version of this file. That version existed because the
PolicyRule[] it received came from extract.py's document-reading pipeline, where fields like
suggestedJsonPath/subEntityHint were genuinely inferred guesses needing a model's judgment to
turn into the target schema. That pipeline is out of scope now (see CONFIG-PIPELINE.md) --
PolicyRule[] here comes from a structured form instead, where those same fields are already real,
registry-confirmed values the user picked from a dropdown. There's nothing left to infer, only to
translate field-for-field, which is exactly what plain code does more reliably than a model call.

The one thing this file still cannot produce deterministically: `formulaLogic` for FORMULA/
TIME_BASED mechanisms. Turning `formulaHint`'s plain text ("200 + 15*size") into real, executable
JSON Logic is a judgment call — either a human enters the JSON Logic directly (a small
formula-builder UI, not yet built), or that one narrow case keeps an LLM call. Rather than
silently emit an invalid CalculationRule that fails validate.py later with a confusing error,
that mechanism raises immediately, with a clear reason.
"""

from models import (
    PolicyRule, PolicyCondition, CalculationRule, CalculationRuleSet,
    AttributeCondition, AttributeBinding, Slab,
)


def synthesize(policy_rules: list[PolicyRule], module: str, effective_from: str) -> CalculationRuleSet:
    rules: list[CalculationRule] = []
    assumptions: list[str] = []

    for pr in policy_rules:
        component = pr.scheduleId
        mechanism = pr.mechanism

        if mechanism == "FLAT_OR_BANDED":
            rules.extend(_flat_or_banded(pr, component, effective_from))
        elif mechanism in ("PER_UNIT", "PER_ITEM_IN_LIST"):
            rules.extend(_per_unit(pr, component, effective_from, sub_entity=mechanism == "PER_ITEM_IN_LIST"))
        elif mechanism == "SLAB":
            rules.append(_slab(pr, component, effective_from))
        elif mechanism == "PERCENTAGE_OF_COMPONENT":
            rules.append(_percentage_of_component(pr, component, effective_from))
        elif mechanism == "REBATE_OF_COMPONENT":
            rules.append(_rebate_of_component(pr, component, effective_from))
        elif mechanism == "AGGREGATION":
            rule, assumption = _aggregation(pr, component, effective_from)
            rules.append(rule)
            assumptions.append(assumption)
        elif mechanism in ("FORMULA", "TIME_BASED"):
            rules.append(_formula(pr, component, effective_from, mechanism))
        else:
            raise ValueError(f"unknown mechanism: {mechanism}")

    return CalculationRuleSet(module=module, rules=rules, assumptions=assumptions)


def _cond(c: PolicyCondition) -> AttributeCondition:
    # equals/from/to may all be unset (presence-only) -- carried through as-is, never invented.
    return AttributeCondition(jsonPath=c.suggestedJsonPath, derivedFrom=c.derivedFrom,
                               equals=c.equals, **{"from": c.from_, "to": c.to})


def _flat_or_banded(pr: PolicyRule, component: str, effective_from: str) -> list[CalculationRule]:
    # One CalculationRule PER VARIANT -- unlike SLAB, which collapses variants into one rule.
    return [
        CalculationRule(
            ruleType="RATE_MATRIX", component=component, scope="ENTITY",
            conditions={c.attributeName: _cond(c) for c in v.conditions},
            calculationType="FLAT", value=v.amount,
            # referencesComponents here is sequencing-only (never appliesOn.componentRef) --
            # a plain FLAT_OR_BANDED rule doesn't read another component's value.
            dependsOn=pr.referencesComponents,
            effectiveFrom=effective_from,
        )
        for v in pr.variants
    ]


def _per_unit(pr: PolicyRule, component: str, effective_from: str, sub_entity: bool) -> list[CalculationRule]:
    return [
        CalculationRule(
            ruleType="RATE_MATRIX", component=component,
            scope="SUBENTITY" if sub_entity else "ENTITY",
            # subEntityHint is already a real, form-confirmed JSONPath -- no inference needed,
            # unlike the old LLM prompt which had to *guess* this from a bare hint like "accessories".
            subEntityPath=pr.subEntityHint if sub_entity else None,
            conditions={c.attributeName: _cond(c) for c in v.conditions},
            calculationType="PER_UNIT", value=v.amount,
            appliesOn=AttributeBinding(jsonPath=pr.rateAppliesToAttribute),
            effectiveFrom=effective_from,
        )
        for v in pr.variants
    ]


def _slab(pr: PolicyRule, component: str, effective_from: str) -> CalculationRule:
    slabs = [
        Slab(**{"from": v.conditions[0].from_ or 0}, to=v.conditions[0].to, rate=v.amount)
        for v in pr.variants
    ]
    return CalculationRule(
        ruleType="RATE_MATRIX", component=component, scope="ENTITY",
        calculationType="SLAB", slabs=slabs,
        appliesOn=AttributeBinding(jsonPath=pr.rateAppliesToAttribute),
        effectiveFrom=effective_from,
    )


def _percentage_of_component(pr: PolicyRule, component: str, effective_from: str) -> CalculationRule:
    v = pr.variants[0]
    return CalculationRule(
        ruleType="TAX", component=component, scope="ENTITY",
        calculationType="PERCENTAGE", value=v.amount,
        appliesOn=AttributeBinding(componentRef=pr.referencesComponents[0]),
        dependsOn=pr.referencesComponents,
        effectiveFrom=effective_from,
    )


def _rebate_of_component(pr: PolicyRule, component: str, effective_from: str) -> CalculationRule:
    v = pr.variants[0]
    return CalculationRule(
        ruleType="ADJUSTMENT", component=component, scope="ENTITY",
        conditions={c.attributeName: _cond(c) for c in v.conditions},
        # Checked, never defaulted to FLAT -- a flat -500 and a -10% look similar in source text
        # but are mechanically different.
        calculationType="PERCENTAGE" if pr.amountIsPercentage else "FLAT",
        value=v.amount,
        appliesOn=AttributeBinding(componentRef=pr.referencesComponents[0]),
        dependsOn=pr.referencesComponents,
        effectiveFrom=effective_from,
    )


def _aggregation(pr: PolicyRule, component: str, effective_from: str) -> tuple[CalculationRule, str]:
    # calculationType is in the real schema's *universal* required list even for AGGREGATION,
    # though it does nothing there -- aggregateFunction is what actually drives behavior.
    # Confirmed against calculation-engine-3.0.0.yaml directly, not guessed. But validate.py's
    # "value required when calculationType=FLAT/PERCENTAGE/PER_UNIT" check has no AGGREGATION
    # exception, so *some* value is unavoidable too -- confirmed by actually running this, not
    # assumed. value=0 is inert: simulate.py never reads `value` for AGGREGATION rules.
    rule = CalculationRule(
        ruleType="AGGREGATION", component=component, scope="SUBENTITY",
        subEntityPath=pr.subEntityHint,
        calculationType="FLAT", value=0,
        aggregateFunction=pr.aggregateFunctionHint,
        sourceAttribute=AttributeBinding(jsonPath=pr.valueSources[0].suggestedJsonPath),
        targetAttribute=pr.aggregationTargetName,
        priority=1,  # runs before anything that depends on its output
        effectiveFrom=effective_from,
    )
    assumption = (
        f"{component}: calculationType set to FLAT with value=0 -- a required-but-unused "
        f"placeholder for this AGGREGATION rule, not a real fee amount."
    )
    return rule, assumption


def _formula(pr: PolicyRule, component: str, effective_from: str, mechanism: str) -> CalculationRule:
    raise NotImplementedError(
        f"{component} ({mechanism}): formulaLogic cannot be produced deterministically from "
        f"formulaHint ({pr.formulaHint!r}) yet -- this needs either a human-entered JSON Logic "
        f"expression (a formula-builder UI, not yet built) or a narrow LLM call scoped to just "
        f"this conversion. Raising here, rather than emitting an invalid CalculationRule that "
        f"would fail validate.py later with a less clear error."
    )
