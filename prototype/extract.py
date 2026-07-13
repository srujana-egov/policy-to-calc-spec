"""Stage 2: Extract & Normalize. Two passes, not one:

Pass A (free text): read the WHOLE document and reason about it before committing to any
structure — find every fee-relevant table, actively search for narrative sentences elsewhere in
the document that describe fee methodology (not just the first table found), cross-reference the
two, and flag anything that points at an external document this model can't see. This is
deliberately modeled on how this pipeline's design conversation actually worked: reading the
Chennai PDF's merged-cell table correctly, and cross-referencing Bissau's scattered narrative
confirmations against its fee tables, both happened via free-form reasoning across a whole
document — not a single constrained extraction call.

Pass B (structured output): given Pass A's analysis as grounding, extract PolicyRule[] in the
guaranteed-conformant shape. Structured outputs and extended thinking are not compatible on the
same call (confirmed against current docs) — this two-pass split is how you get both: real
reasoning room in Pass A, guaranteed-valid JSON in Pass B.

Requires ANTHROPIC_API_KEY or OPENAI_API_KEY in the environment (see llm_client.py — whichever is
set is used). Usage: python extract.py [source_file] [--schedule SCHEDULE-I]
"""

import sys
from pathlib import Path

from llm_client import free_text, structured
from models import PolicyExtraction

DEFAULT_SOURCE = Path(__file__).parent.parent / "fixtures" / "chennai-trade-license-schedule.md"
OUTPUT_PATH = Path(__file__).parent / "fixtures_generated" / "extracted_policy_rules.json"

ANALYSIS_SYSTEM_PROMPT = """You are analyzing a government fee policy document before any
structured extraction happens. Read the ENTIRE document — do not stop at the first table you
find. Write a free-form analysis covering:

1. Every distinct fee pattern, including tables where ONE fee block visually spans many named
   rows above/below it (a merged-cell style table) — state explicitly which named items share
   which fee pattern, don't treat each row as independent unless the document actually gives each
   one a different amount.
2. Actively search the REST of the document (not just tables) for sentences that describe fee
   methodology, classification systems, or which attribute drives the fee. Documents often state
   this in prose separately from the table itself.
3. Cross-reference (2) against (1) explicitly — if a narrative statement clarifies, contradicts,
   or simplifies what a table implies (e.g. "there is no classification system for X"), say so and
   explain how it changes the extraction.
4. Any reference to an external document this model cannot see (an amendment number, a gazette
   citation, "as per G.O. Ms No. ...") — flag these, do not guess what they might contain.
5. Anything genuinely ambiguous (a boundary condition, a missing effective date, an item that
   doesn't clearly belong to one fee pattern) — name it explicitly rather than silently picking an
   interpretation.

This is reasoning, not the final output — write prose, not JSON."""

EXTRACTION_SYSTEM_PROMPT = """Using the analysis provided (and the original document for
reference), extract PolicyRule records — a normalized intermediate format, NOT the final
CalculationRule schema, that mapping happens in a later stage. For each distinct fee pattern the
analysis identified, first decide which of these 9 mechanisms it actually is, then fill in only
the fields that mechanism needs:

- FLAT_OR_BANDED: a fixed or banded amount, where exactly ONE matching band's amount applies to
  the WHOLE value. variants = one row per band, each with 0+ simultaneous conditions (use `equals`
  for an exact match on a category/boolean, `from`/`to` for a numeric range — never both on the
  same condition) and the amount for that row. A flat fee with no banding is a single variant with
  no conditions. Set referencesComponents even here if the source text says this must be computed
  after another component purely for ordering, without actually reading that component's value
  (e.g. a flat cess still listed after the base licence fee in a tax/cess stack).
- PER_UNIT: a rate multiplied by one raw numeric field, no repeating array. Set
  rateAppliesToAttribute to that field's name; variants holds the rate as `amount` (banded rates,
  like "different rate per size range", still use multiple variants here).
- PER_ITEM_IN_LIST: charged once per element of a repeating array (accessories, floors, taps).
  Set subEntityHint to the array's name and rateAppliesToAttribute to the per-element field the
  rate multiplies; variants' conditions match on per-element attributes (e.g. accessory type).
- SLAB: true marginal tiers over ONE field — do NOT confuse with FLAT_OR_BANDED. The tell-tale
  sign: the document describes a rate that applies only to the PORTION of a value within each
  band, with every band the value reaches contributing its own portion (e.g. "0.5% on the first
  500,000, 1% on the remainder" — not "pick whichever band matches and apply its rate to the whole
  amount"). Set rateAppliesToAttribute to the field being tiered; each variant is one tier, with a
  single condition on that field (from/to = the tier's bounds) and `amount` = that tier's rate.
- PERCENTAGE_OF_COMPONENT: a tax/cess computed as a percentage of another component's amount. Set
  referencesComponents to the component(s) it reads; variants' amount is the percentage.
  amountIsPercentage is always true here (set it, don't leave the default).
- REBATE_OF_COMPONENT: a rebate/deduction/surcharge adjusting another component. Set
  amountIsPercentage explicitly: false if the amount is a flat currency deduction (e.g. -500),
  true if it's a percentage deduction (e.g. -10%) — these look similar in the source text but are
  different mechanically, do not default this without checking the actual wording. If the
  condition bands on an already-derived total (not a raw field), set that condition's derivedFrom
  to the AGGREGATION rule's scheduleId instead of a jsonPath-based from/to.
- AGGREGATION: derives one attribute by summing/counting/etc. over a repeating array. Set
  subEntityHint (the array), aggregateFunctionHint (SUM/COUNT/MAX/MIN/AVG), aggregationTargetName
  (what the derived attribute should be called), and valueSources with the one field being
  aggregated. No variants needed.
- FORMULA: real math combining more than one input. Set valueSources (each a raw field or a
  componentRef) and formulaHint as a short, plain description of the math (e.g. "200 + 15*size",
  or "size*10 if fireSafetyClass=='A' else size*20") — do not write JSON Logic yourself, that's
  the next stage's job.
- TIME_BASED: interest/penalty. Same as FORMULA, but valueSources typically includes a
  componentRef principal plus a raw time field (e.g. daysDelayed); referencesComponents should
  list every component this depends on for sequencing, including ones only used for ordering.

Always set: scheduleId, tradeNames (every trade/item this pattern applies to), sourceText (the
verbatim quote), confidence (0-1). Also populate documentNotes with anything that doesn't belong
to one specific rule: external references you couldn't resolve, and cross-cutting clarifications
(e.g. "no classification system exists, so no category condition should be added anywhere").
Only extract what the analysis actually found — do not invent trade names, amounts, or components
that aren't in the source text."""


def analyze(document_text: str, schedule_filter: str | None) -> str:
    instruction = "Analyze this document" + (f", focusing on {schedule_filter}" if schedule_filter else "")
    instruction += f":\n\n{document_text}"
    return free_text(ANALYSIS_SYSTEM_PROMPT, instruction)


def extract(document_text: str, analysis: str, schedule_filter: str | None) -> PolicyExtraction:
    instruction = f"Analysis:\n{analysis}\n\nOriginal document:\n{document_text}"
    if schedule_filter:
        instruction += f"\n\nOnly extract PolicyRules for {schedule_filter}."
    return structured(
        EXTRACTION_SYSTEM_PROMPT,
        [{"role": "user", "content": instruction}],
        PolicyExtraction,
    )


def main():
    args = sys.argv[1:]
    schedule_filter = None
    if "--schedule" in args:
        idx = args.index("--schedule")
        schedule_filter = args[idx + 1]
        del args[idx:idx + 2]
    source_path = Path(args[0]) if args else DEFAULT_SOURCE

    document_text = source_path.read_text()

    print("Pass A: analyzing whole document...")
    analysis = analyze(document_text, schedule_filter)
    print(analysis)
    print("\nPass B: structured extraction, grounded in the analysis above...")
    result = extract(document_text, analysis, schedule_filter)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(result.model_dump_json(indent=2))
    print(f"\nExtracted {len(result.rules)} PolicyRule(s) -> {OUTPUT_PATH}")
    for r in result.rules:
        print(f"  - {r.scheduleId}: {len(r.tradeNames)} trade(s), confidence={r.confidence}")
    if result.documentNotes:
        print("Document-level notes:")
        for note in result.documentNotes:
            print(f"  - {note}")


if __name__ == "__main__":
    main()
