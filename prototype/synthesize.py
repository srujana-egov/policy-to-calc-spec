"""Stage 4: Rule Synthesis & Validation. Maps PolicyRule[] -> CalculationRule[] via structured
outputs, then runs the *deterministic* validator (validate.py) against the real schema's business
rules. If validation fails, one reflection pass is sent back to the model with the concrete
errors (the "reflection-guardrail" pattern) — not a full self-consistency ensemble, which would
be overkill for this scale. Requires ANTHROPIC_API_KEY or OPENAI_API_KEY (see llm_client.py).
"""

from pathlib import Path

from llm_client import structured
from models import CalculationRuleSet
from validate import validate_rule_set

INPUT_PATH = Path(__file__).parent / "fixtures_generated" / "extracted_policy_rules.json"
VOCAB_PATH = Path(__file__).parent.parent / "reference" / "calculation-rule-vocabulary.md"
OUTPUT_PATH = Path(__file__).parent / "fixtures_generated" / "synthesized_calculation_rules.json"

SYSTEM_PROMPT = """You map normalized PolicyRule records into CalculationRule records for the
DIGIT Calculation Engine. Use ONLY the patterns in the vocabulary reference below — do not invent
mechanisms outside it. Every rule needs: ruleType, component, scope, calculationType,
effectiveFrom. Set a real `jsonPath` per condition using the PolicyRule's suggestedJsonPath (never
both `equals` and `from`/`to` on the same condition — but leaving both unset is valid too, meaning
the condition only requires that attribute to be present, no specific value; carry that through
as-is, don't invent an `equals`/`from`/`to` value that wasn't in the PolicyCondition).

Set CalculationRuleSet.module ONCE for the whole batch (e.g. "trade-license") — infer it from what
this document's fees are actually for. Do NOT set a `module` field on individual CalculationRule
objects — the real schema has no such field there; `module` is resolved from the `{module}` path
segment when a batch is written (`POST /{module}/rules`), never repeated per rule.

Map each PolicyRule's `mechanism` onto the vocabulary reference as follows:
- FLAT_OR_BANDED -> one RATE_MATRIX/FLAT rule per variant, sharing one `component` name, each
  variant's conditions carried over as-is (0, 1, or several ANDed conditions per rule). If
  referencesComponents is set here even though this isn't a percentage/rebate, it's a pure
  sequencing dependency — carry it into `dependsOn` only, do NOT set appliesOn.componentRef for it.
- PER_UNIT / PER_ITEM_IN_LIST -> RATE_MATRIX/PER_UNIT, `appliesOn.jsonPath` = rateAppliesToAttribute.
  PER_ITEM_IN_LIST additionally sets scope=SUBENTITY and subEntityPath from subEntityHint (turn
  the hint into a real JSONPath ending in `[*]`, e.g. "accessories" -> a plausible
  `$.<module>Detail.accessories[*]` given the rest of the document's field-naming style).
- SLAB -> one RATE_MATRIX rule, calculationType SLAB, `appliesOn.jsonPath` = rateAppliesToAttribute,
  `slabs` built from the variants IN ORDER (each variant's condition from/to become that slab's
  from/to, its amount becomes that slab's rate) — this is one rule with a slabs array, NOT one
  rule per variant the way FLAT_OR_BANDED is. Only the final slab may omit `to`.
- PERCENTAGE_OF_COMPONENT -> TAX (or RATE_MATRIX if the source clearly isn't a statutory tax),
  calculationType PERCENTAGE, `appliesOn.componentRef` and `dependsOn` from referencesComponents.
- REBATE_OF_COMPONENT -> ADJUSTMENT, `appliesOn.componentRef` and `dependsOn` from
  referencesComponents, calculationType FLAT if amountIsPercentage is false, PERCENTAGE if true —
  check this flag, do not assume FLAT by default. Value/variant amounts kept negative as given. A
  condition with `derivedFrom` set bands on an AGGREGATION component's output instead of a raw field.
- AGGREGATION -> ruleType AGGREGATION, scope=SUBENTITY, subEntityPath from subEntityHint,
  aggregateFunction from aggregateFunctionHint, sourceAttribute from valueSources[0],
  targetAttribute from aggregationTargetName. Give it a low priority so it runs before dependents.
- FORMULA / TIME_BASED -> calculationType FORMULA (ruleType FORMULA-bearing rules use RATE_MATRIX/
  INTEREST/PENALTY as appropriate), formulaVariables built from valueSources (jsonPath or
  componentRef per source), and formulaLogic as real JSON Logic formalizing formulaHint's
  plain-text math description. dependsOn must list every componentRef used.

Every `appliesOn`, `sourceAttribute`, and each entry in `formulaVariables` must set EXACTLY ONE of
`jsonPath` or `componentRef` — never both, never neither. Use the matching PolicyValueSource field
(`suggestedJsonPath` vs `referencesComponent`) to decide which one applies; don't guess or fill in
a placeholder for whichever one you don't have.

Populate the `assumptions` list with every non-obvious judgment call you made (boundary
inclusivity, effectiveFrom when the document doesn't state one, module name, roundOff default,
how you turned a subEntityHint into a real JSONPath, how you formalized a formulaHint into JSON
Logic) so a business user can review and override them — do not silently decide these without
recording them.

Vocabulary reference:
{vocab}
"""


def synthesize(policy_rules_json: str, max_reflection_passes: int = 1) -> CalculationRuleSet:
    vocab = VOCAB_PATH.read_text()
    system = SYSTEM_PROMPT.format(vocab=vocab)

    messages = [{"role": "user", "content": f"PolicyRules to map:\n\n{policy_rules_json}"}]

    for attempt in range(max_reflection_passes + 1):
        result: CalculationRuleSet = structured(system, messages, CalculationRuleSet)
        rules_as_dicts = [r.model_dump(by_alias=True, exclude_none=True) for r in result.rules]
        errors = validate_rule_set(rules_as_dicts)

        if not errors:
            return result

        if attempt < max_reflection_passes:
            print(f"Reflection pass {attempt + 1}: {len(errors)} validation error(s), retrying...")
            messages.append({"role": "assistant", "content": result.model_dump_json()})
            messages.append({
                "role": "user",
                "content": "That output failed validation against the real schema's business "
                            "rules:\n" + "\n".join(errors) + "\n\nFix these specific errors and "
                            "return a corrected CalculationRuleSet.",
            })
        else:
            print(f"WARNING: {len(errors)} validation error(s) remain after reflection:")
            for e in errors:
                print(f"  - {e}")

    return result


def main():
    policy_rules_json = INPUT_PATH.read_text()
    result = synthesize(policy_rules_json)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(result.model_dump_json(indent=2, by_alias=True))
    print(f"Synthesized {len(result.rules)} CalculationRule(s) -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
