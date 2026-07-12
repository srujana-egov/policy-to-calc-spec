"""Stage 4: Rule Synthesis & Validation. Maps PolicyRule[] -> CalculationRule[] via Claude's
structured outputs, then runs the *deterministic* validator (validate.py) against the real
schema's business rules. If validation fails, one reflection pass is sent back to the model with
the concrete errors (the "reflection-guardrail" pattern) — not a full self-consistency ensemble,
which would be overkill for this scale. Requires ANTHROPIC_API_KEY in the environment.
"""

import json
from pathlib import Path

from anthropic import Anthropic

from models import CalculationRuleSet
from validate import validate_rule_set

INPUT_PATH = Path(__file__).parent / "fixtures_generated" / "extracted_policy_rules.json"
VOCAB_PATH = Path(__file__).parent.parent / "reference" / "calculation-rule-vocabulary.md"
OUTPUT_PATH = Path(__file__).parent / "fixtures_generated" / "synthesized_calculation_rules.json"

SYSTEM_PROMPT = """You map normalized PolicyRule records into CalculationRule records for the
DIGIT Calculation Engine. Use ONLY the patterns in the vocabulary reference below — do not invent
mechanisms outside it. Every rule needs: ruleType, component, scope, calculationType,
effectiveFrom. A banded flat fee becomes one RATE_MATRIX/FLAT rule per band, sharing one
`component` name, each band's condition using from/to (never both from/to and equals on the same
condition). Set a real `jsonPath` per condition using the PolicyRule's suggestedJsonPath. Populate
the `assumptions` list with every non-obvious judgment call you made (boundary inclusivity,
effectiveFrom when the document doesn't state one, module name, roundOff default) so a business
user can review and override them — do not silently decide these without recording them.

Vocabulary reference:
{vocab}
"""


def synthesize(policy_rules_json: str, max_reflection_passes: int = 1) -> CalculationRuleSet:
    client = Anthropic()
    vocab = VOCAB_PATH.read_text()
    system = SYSTEM_PROMPT.format(vocab=vocab)

    messages = [{"role": "user", "content": f"PolicyRules to map:\n\n{policy_rules_json}"}]

    for attempt in range(max_reflection_passes + 1):
        response = client.messages.parse(
            model="claude-sonnet-5",
            max_tokens=4096,
            system=system,
            messages=messages,
            output_format=CalculationRuleSet,
        )
        result: CalculationRuleSet = response.parsed_output
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
