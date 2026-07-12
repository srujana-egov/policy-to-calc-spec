"""Monday demo: Chennai Schedule I, end to end (minus the live LLM extraction/synthesis calls,
which are in extract.py / synthesize.py and need ANTHROPIC_API_KEY to run).

This script proves the deterministic half of the pipeline — validate + simulate — against a
CalculationRule set shaped exactly like what extract.py -> synthesize.py should produce.
"""

import json
import sys
from pathlib import Path

from validate import validate_rule_set
from simulate import simulate_estimate

DEFAULT_FIXTURE_PATH = Path(__file__).parent / "fixtures_generated" / "chennai_schedule_I_rules.json"

SYNTHETIC_PAYLOADS = [
    {
        "label": "Plastic works, 800 sq.ft.",
        "entityDetail": {"tradeLicenseDetail": {"tradeName": "Plastic works", "premisesArea": 800}},
    },
    {
        "label": "Tailoring Machine, exactly 1000 sq.ft. (boundary case)",
        "entityDetail": {"tradeLicenseDetail": {"tradeName": "Tailoring Machine", "premisesArea": 1000}},
    },
    {
        "label": "Automobile works, 1500 sq.ft.",
        "entityDetail": {"tradeLicenseDetail": {"tradeName": "Automobile works", "premisesArea": 1500}},
    },
]


def main():
    fixture_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURE_PATH
    fixture = json.loads(fixture_path.read_text())
    rules = fixture["rules"]

    print(f"=== Validating {len(rules)} generated CalculationRule(s) ===")
    errors = validate_rule_set(rules)
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return
    print("All rules valid against calculation-engine-3.0.0.yaml's business rules.\n")

    print("=== Assumptions made during extraction (business user reviews these) ===")
    for a in fixture["assumptions"]:
        print(f"  - {a}")
    print()

    print("=== Synthetic payload simulation (offline evaluator) ===")
    for case in SYNTHETIC_PAYLOADS:
        result = simulate_estimate(rules, case["entityDetail"])
        print(f"\n{case['label']}")
        for li in result["lineItems"]:
            print(f"  {li['component']}: Rs. {li['amount']:.0f}")
        print(f"  -> Total: Rs. {result['totalAmount']:.0f}")


if __name__ == "__main__":
    main()
