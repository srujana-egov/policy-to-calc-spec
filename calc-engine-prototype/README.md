# Calculation rule wizard prototype

A guided-question wizard for authoring `CalculationRule[]` **fresh** — someone deciding a fee/tax
schedule from scratch, not extracting one from an existing policy document. Same shape as
`../workflow-prototype/` and `../registry-prototype/`: a testable builder layer driven by both the
interactive CLI and automated tests, a deterministic validate step, a table preview a non-technical
user can actually read, an explicit confirmation gate, and a real-or-dry-run write.

This is a *third*, complementary path alongside `../prototype/`'s existing
`PolicyRule[] -> CalculationRule[]` pipeline (built for the backlog of already-written policy
documents — see `../CONFIG-PIPELINE.md`'s "Why `PolicyRule` stays as an intermediate" section for
why that one keeps an intermediate stage). This prototype has no such intermediate, because there's
no existing document to extract from — the wizard's own questions are the source of truth, exactly
like the other two prototypes.

## Spec found and verified

**Update: `calculation-engine-3.0.0.yaml` — the real OpenAPI spec, confirmed from the platform
team — has been found and this prototype has been re-verified against it field by field.** This
closes the one gap `../workflow-prototype/` and `../registry-prototype/` never had (both were
always verified against real Go source in `digitnxt/digit3`) — for most of this project's life, no
Calculation Engine service could be found anywhere in the digitnxt org, and this model/validator
were inherited from `../prototype/models.py`'s own earlier reconstruction, unverified. The real
spec is now checked into `fixtures/real_world/calculation-engine-3.0.0.yaml`.

**What the re-verification confirmed** (already correct, now checked rather than assumed):
- The evaluation order in `simulate.py` (AGGREGATION → RATE_MATRIX → ADJUSTMENT →
  PENALTY/INTEREST/TAX in `dependsOn` order) matches the spec's `/{module}/estimate` description
  word for word.
- `PERCENTAGE`'s `value` is percentage points (`9` = 9%, divided by 100) and `PER_UNIT`'s is a
  plain multiplier (no division) — both match `CalculationType`'s own description.
- `SLAB`'s `rate` divides by 100, exactly like `PERCENTAGE` — the spec's own `Slab.x-businessRules`
  restates the identical worked example (`700000` against `[0-500000 @ 0.5][500000+ @ 1]` → `2500 +
  2000`) already found and fixed via `calculation-rule-examples.pdf` (see below) — doubly confirmed.
- `AttributeCondition`/`AttributeBinding`'s "exactly one of jsonPath-or-equivalent" shape, the
  `AttributePath.Conflict` registration rule, `Slab`'s "only the final tier may omit `to`" rule, the
  `dependsOn` DAG requirement, and "most-specific-match wins" for `RATE_MATRIX` all match exactly.
- AGGREGATION rules genuinely have no `calculationType`/`value` — the spec's own "aggregation"
  example under `POST /{module}/rules` omits both, matching what
  `calculation-rule-examples.pdf`'s examples #22–24 already showed.

**Two real, confirmed bugs found and fixed by this re-verification** (in `wizard.py`'s
`write_rules()`):
1. **The real write path is `/calculation/v3/{module}/rules`, not bare `/{module}/rules`.** The
   spec's own `servers:` block declares the base as `.../calculation/v3` — the same convention
   already verified for registry (`/registry/v3`) and workflow (`/workflow/v3`). This prototype's
   write path omitted the prefix entirely until now.
2. **`POST` creates one rule per call — the request body is a single `CalculationRule` object, not
   an array.** This prototype previously sent the *entire* rule set as one bulk-array body in a
   single request. Fixed to loop, one `POST` per rule, matching the spec's `requestBody` schema and
   its `201` response (a single created rule with `id`/`version`/`configVersion`/`auditDetail`).
3. Separately: the spec requires `security: [BearerAuth: []]` on every operation with no
   alternative — unlike registry/workflow, where a JWT is optional best-effort. `DIGIT_JWT_TOKEN`
   is now required (not optional) to attempt a real write here; its absence forces a dry run.

**One interesting spec-internal inconsistency, worth naming rather than silently working around:**
the formal JSON-Schema `required` arrays are looser than the spec's own worked examples show —
`CalculationRule.required` lists `calculationType` unconditionally, yet the spec's own
"aggregation" example omits it; `AttributeCondition.required` lists `jsonPath` unconditionally, yet
the spec's own "bulkSurchargeOnDerivedTotal" example sets only `derivedFrom`. Both times, the
concrete example (and the prose `x-businessRules`) is right and the bare `required` array is
overly strict boilerplate — this prototype follows the examples/prose, matching what
`calculation-rule-examples.pdf` already independently confirmed.

**What's still genuinely uncertain:** the exact literal header names behind the spec's
`ClientId`/`ClientSecret`/`TimeStamp`/`RequestIdHeader`/`CorrelationIdHeader` parameters — they're
`$ref`'d to a `digit-specs` `v3.0.0/common.yaml` this project doesn't have a confirmed local copy
of. Not guessed; `wizard.py`'s `_calc_engine_headers()` sends only what's independently confirmed
(`X-Tenant-Id`, `X-User-Id`, `Authorization: Bearer`) and documents this gap in its own docstring.

The `$.` field-reference mechanism (below) was already independently verified regardless — it
calls the real, already-proven `GET /registry/v3/schema/:schemaCode` route from
`../registry-prototype/`.

**Also part of this verification history: `calculation-rule-examples.pdf`** (30 real rule bodies,
surfaced before the OpenAPI spec was found) — see "Stress test against 30 real examples" below for
what it confirmed and corrected on its own.

## What's runnable right now, no API key or live service needed

```
python3 test_calc_rule_builder.py     # CalculationRuleBuilder + validate.py, 25 checks
python3 test_formula_parser.py        # the arithmetic-to-JSON-Logic parser, 15 checks
python3 test_example_generator.py     # the worked-examples generator, 16 checks
python3 test_wizard.py                # the interactive layer itself, 24 checks
python3 test_render.py                # the table preview + worked examples, 24 checks
python3 test_write_path.py            # real HTTP paths against a throwaway local server, 17 checks
python3 test_real_world_examples.py   # stress test against 30 real rule bodies, 324 checks
```

```
python3 wizard.py
```

The actual interactive CLI: module name → (optional) pick a registry schema to reference fields
from → one or more rules, each authored via a plain-language "what kind of charge is this?" menu
→ table preview **with worked examples** → explicit confirmation → write (real
`POST /{module}/rules` or a clearly-labeled dry run). Same "fix one thing, don't restart
everything" pattern as the other two prototypes: saying no offers a menu to redo/add/delete a
rule or rename the module, rather than starting over.

## The `$.` field-reference mechanism

Every `CalculationRule.jsonPath` (in a condition, `appliesOn`, `sourceAttribute`, or a
`formulaVariables` entry) needs to name a real field on the entity these rules apply to. Rather
than asking someone to type a raw JSONPath by hand — exactly the kind of "looks right, isn't"
mistake that shipped twice already in `../registry-prototype/` (a wrong data-write URL, and
`x-unique`/`x-indexes` silently landing in the wrong place) — `registry_lookup.py` fetches the real
registry schema (Step 2's output) via the same verified `GET /registry/v3/schema/:schemaCode` route
`../registry-prototype/add_data.py` uses, flattens its fields (one level of nesting, matching that
prototype's own nesting limit), and lets the wizard's questions *pick* a field, generating the
exact `$.path` deterministically (`field_to_json_path()`).

This isn't a new convention invented for this prototype — `$.`-prefixed paths already appear
throughout this project's earlier work: `../prototype/simulate.py`'s own docstring
(`"$.tradeLicenseDetail.premisesArea"`), `../DEMO.md`, and the real
`../prototype/fixtures_generated/chennai_schedule_I_rules.json` fixture. If the fetch fails (no
server configured, network error, unknown schema code), the wizard falls back to manual path entry
rather than crashing the session — confirmed by `test_wiz_11` in `test_wizard.py`.

## The mechanism menu (`../reference/calculation-rule-vocabulary.md`, made interactive)

| Wizard option | `CalculationRule` shape | Builder method |
|---|---|---|
| A flat amount every time | `calculationType: FLAT` | `add_flat_rule` |
| A rate × some field | `calculationType: PER_UNIT`, `appliesOn.jsonPath` | `add_per_unit_rule` |
| Per item in a repeating list | `scope: SUBENTITY`, `subEntityPath` | `add_per_item_rule` |
| Tiered/marginal bands | `calculationType: SLAB`, `slabs` | `add_slab_rule` |
| A percentage of another fee | `calculationType: PERCENTAGE`, `appliesOn.componentRef` + `dependsOn` | `add_percentage_rule` |
| A rebate/deduction | `ruleType: ADJUSTMENT`, `appliesOn.componentRef` always required | `add_adjustment_rule` |
| Total a repeating list | `ruleType: AGGREGATION`, `scope: SUBENTITY`, low `priority` | `add_aggregation_rule` |
| Real math | `calculationType: FORMULA`, `formulaVariables` + `formulaLogic` | `add_formula_rule` |

A real bug found while wiring up AGGREGATION, twice over: this prototype's first draft set
`calculationType: FORMULA` for aggregation rules, invented rather than checked. That was then
"fixed" to `calculationType: FLAT, value: 0` — an inert placeholder, matching
`../prototype/synthesize.py`'s own (unverified) convention. `calculation-rule-examples.pdf`'s real
examples #22–24 later showed neither is right: the real engine omits `calculationType`/`value`
entirely for AGGREGATION (it derives an attribute, it doesn't compute a billable amount). `models.py`
now makes `calculationType` optional; `add_aggregation_rule` sets neither field.

## Worked examples in the preview, not just a rule table

`CONFIG-PIPELINE.md`'s own design for this pipeline calls for the business-user review to include
"a few representative worked examples," not just the generated rules — a rule table still requires
someone to mentally simulate the rules to know whether they're right. The confirmation preview now
does this for real: `example_generator.py` picks up to 15 scenarios, each *targeted* at one
specific decision the wizard's answers made (a condition boundary, a slab tier, an aggregation
threshold) — not random values — and `simulate.py` (adapted from the already-proven
`../prototype/simulate.py`) actually computes each one's result.

This is the concrete version of "oh, so at exactly 1000 sq ft this shop pays 2000, but at 1000.01
it jumps to 5000 — did I mean to draw the line there?" — seeing a real number expose a boundary
mistake, where a rule table showing `to: 1000` and `from: 1000.01` side by side does not make that
jump nearly as obvious.

Building this surfaced several real, previously-untested bugs — none caught by `validate.py`,
because none of them are structural:

- **Sub-entity paths need to be *relative*, not root-`$.`-prefixed.** `AGGREGATION`'s
  `sourceAttribute` and `PER_ITEM_IN_LIST`'s `appliesOn`/conditions all apply *inside* one element
  of a repeating list — the vocabulary reference's own words: "relative to one array element." The
  wizard's first draft ran every field through the same `$.`-prefixing picker regardless, which
  silently failed to resolve during simulation (an aggregation's sum came back as `0` every time).
  `ask_field_reference()` now takes a `relative` flag; `configure_per_item()`/
  `configure_aggregation()` use it.
- **A `derivedFrom` condition names an aggregation's *component*, not its `targetAttribute`.**
  `../prototype/simulate.py`'s own `derived` dict was keyed by `targetAttribute` but looked up by
  the condition's own (arbitrary) dict key — correct only if someone happened to name their
  condition identically to the aggregation's result name. Fixed to key by `component`, matching
  what `derivedFrom` (per the vocabulary reference: `derivedFrom: <aggregationComponent>`) and this
  prototype's own wizard actually store there.
- **A FORMULA variable reading an AGGREGATION's result via `componentRef` crashed.** AGGREGATION
  rules never produce a real line-item amount (their `calculationType`/`value` are an inert
  `FLAT`/`0` placeholder — see below); their result only ever lands in `derived`. `_amount_of()`
  now falls back to `derived` when a `componentRef` target isn't a computed line item.
- **`Slab.rate` needs `/100` — `simulate.py` (inherited, unchanged) originally multiplied it
  directly, no division.** A first test scenario using `rate: 20` for an intended "20% bracket"
  produced a **100x-too-large** result once actually simulated. Without real ground truth at the
  time, this got "fixed" by telling the wizard's users to pre-divide by 100 themselves
  (`ask_slabs()`, "enter 0.05, not 5") — compensating for the bug rather than fixing it.
  `calculation-rule-examples.pdf`'s example #14 later confirmed the *engine* divides by 100
  (matching `PERCENTAGE`'s own convention): its own prose ("0.5% on the first 500,000, 1% on the
  remaining 200,000") only reproduces its stated result (4500) with `/100`. Properly fixed in
  `_compute_slab`; the wizard's question reworded back to the correct convention (enter `5` for a
  5% bracket, not `0.05`) — see "Stress test against 30 real examples" below.

None of these would have been caught by schema validation alone — they're exactly the class of
mistake worked-example simulation exists to catch, including in the tool that generates them.

## Stress test against 30 real examples

`calculation-rule-examples.pdf` — 30 real `CalculationRule` bodies, ordered simple-to-complex, each
introducing one new concept — surfaced partway through this project. Unlike everything else in this
prototype (inherited from `../prototype/`'s own reconstruction, never independently checked — see
the caveat above), this document is treated as ground truth: its own worked arithmetic (e.g. "700,000
pays 0.5% on the first 500,000 and 1% on the remaining 200,000") is checked directly against what
`simulate.py` computes, not just structural shape.

All 30 examples are in `fixtures/real_world/calculation_rule_examples.json`;
`test_real_world_examples.py` (324 checks) verifies three things per example: it round-trips through
`CalculationRule` with every field the doc set intact, realistic per-module groupings validate clean
under `validate.py`, and `simulate.py` computes the exact numbers the doc's prose describes. This
found two real bugs, both now fixed and covered by regression tests:

- **`Slab.rate` needed `/100`** (see above) — confirmed, not just fixed on suspicion, by example
  #14's own stated result.
- **AGGREGATION rules don't have `calculationType`/`value` at all** (see above) — examples #22-24
  omit both fields entirely; this prototype had twice guessed a value for a field the real contract
  doesn't have.

It also confirmed several design decisions already made without real evidence at the time: the
FORMULA parser's deliberate exclusion of `if`/comparison operators (the doc's own "Common mistakes"
section recommends two plain conditional rules over a hidden branch, exactly this prototype's
stance) — though `simulate.py`'s `_eval_formula` now *evaluates* `if`/`==`/`!=`/comparisons anyway
(restored from `../prototype/simulate.py`, dropped during adaptation), since example #28 shows the
real engine accepts this even if this wizard won't help someone author it. Also confirmed: CGST/SGST
"identical twin" tax pairs, `dependsOn` required purely for sequencing even on a `FLAT` `TAX` rule
with nothing to read (`CANCER_CESS`), and a 3-deep `PROPERTY_TAX -> INTEREST -> PENALTY` dependency
chain with `formulaVariables` mixing `componentRef` and `jsonPath` in one rule — all of which this
prototype's model/validator/builder already handled correctly, no changes needed.

## The FORMULA question: deterministic, not an LLM call

`../prototype/synthesize.py` deliberately left `FORMULA`/`TIME_BASED` unimplemented — formalizing a
free-text `formulaHint` into JSON Logic was "the one non-deterministic gap," because that hint came
from *inferring* math out of messy prose. This wizard's version is different in kind, not just
degree: the user types an arithmetic expression (`"200 + 15 * size"`) over variable names *they
just declared moments ago* in the same session. That's parseable, not inferable, so
`formula_parser.py` does it with a plain `ast` walk — no `eval()`, so no arbitrary code ever
executes (`test_13_no_eval_used` in `test_formula_parser.py` confirms this directly). Supports `+`,
`-`, `*`, `/`, unary minus, and parentheses; rejects anything else (function calls, comparisons)
with a message safe to show a non-technical user, rather than silently guessing.

## Files

- `models.py` — `CalculationRule`/`Slab`/`AttributeCondition`/`AttributeBinding`/
  `CalculationRuleSet`, inherited from `../prototype/models.py` (see the caveat above).
  `Union[int, float]` on every numeric field, not bare `float` — preserves e.g. the real Chennai
  fixture's `"to": 1000` rather than coercing to `1000.0` (the same fix already needed once in
  `../registry-prototype/models.py`).
- `registry_lookup.py` — the `$.` field-reference mechanism: fetch a registry schema, flatten its
  fields, turn a picked field into a `$.path`.
- `formula_parser.py` — deterministic arithmetic-to-JSON-Logic parser for FORMULA rules.
- `builder.py` — `CalculationRuleBuilder`, one method per mechanism shape (not one generic
  `add_field`-style method — `CalculationRule`'s shape varies too much by `calculationType` for
  that to make sense, the same reasoning `WorkflowBuilder.add_action_to_new_state` already used).
- `validate.py` — adapted from `../prototype/validate.py`'s already-proven business-rule checks
  (attribute-path registry conflicts, `dependsOn` DAG, overlapping bands, `AttributeBinding` shape,
  SLAB tier validation, FORMULA variable-reference checks). Found and fixed one real gap while
  adapting: the per-condition check required `jsonPath` unconditionally, with no exception for
  `derivedFrom` even though the model and vocabulary reference both treat it as a real, valid
  alternative — `test_14`/`test_15` in `test_calc_rule_builder.py` cover this.
- `render.py` — a self-contained HTML table preview (one row per rule, not a JSON dump) plus a
  "Worked examples" section when scenario results are passed in, zero external dependencies. Click
  a rule row for its exact definition.
- `simulate.py` — offline evaluator reimplementing the engine's documented evaluation order
  (AGGREGATION first, then RATE_MATRIX, ADJUSTMENT, then PENALTY/INTEREST/TAX in `dependsOn`
  order), adapted from `../prototype/simulate.py`. Several real bugs found and fixed here — see
  "Worked examples in the preview" and "Stress test against 30 real examples" above.
- `example_generator.py` — picks up to 15 targeted worked-example scenarios (one per condition
  band/boundary, slab tier, aggregation threshold) and runs them through `simulate.py`.
- `wizard.py` — the interactive CLI: module → registry lookup → rules (looped) → table preview
  with worked examples → confirm → write. `run_session()` returns the built rule set, separate
  from the write step, so tests can drive it directly.
- `test_calc_rule_builder.py` — the real Chennai Schedule I fixture (already proven in
  `../prototype/fixtures_generated/`), reproduced exactly through builder calls, plus one test per
  completeness check.
- `test_formula_parser.py` — every supported operator, precedence, and every rejection path.
- `test_example_generator.py` — confirms scenarios are genuinely targeted (not random) and that
  simulating them produces numerically sane results, including regression tests for each of the
  four bugs found while building this feature (see below).
- `test_wizard.py` — the interactive layer, driven via a mocked `input()`, against real fixtures
  and edge cases (cancel, invalid retries, redo/add/delete-a-rule, rename the module, the registry
  field-picker against a mock server, and its fetch-failure fallback).
- `test_render.py` — offline-safety and structural correctness, including the worked-examples
  section.
- `test_write_path.py` — the real-POST path (not just dry-run) against a throwaway local HTTP
  server, plus the registry-schema `GET` fetch. Covers the two write-path bugs found via the real
  spec (missing `/calculation/v3` prefix, bulk-array body instead of one-`POST`-per-rule) and the
  mandatory-Bearer-token requirement (see "Spec found and verified" above).
- `test_real_world_examples.py` — stress test against all 30 examples in
  `calculation-rule-examples.pdf`: structural round-trip, per-module `validate.py` checks, and
  computed-arithmetic assertions matching the doc's own worked numbers (see "Stress test against 30
  real examples" above).
- `fixtures/` — `flat_percentage_session.txt`/`slab_aggregation_formula_session.txt` (exact wizard
  answers covering all 8 mechanisms across two sessions), `*_golden.json` (verified output),
  `real_world/chennai_schedule_I_rules.json` (copied from `../prototype/fixtures_generated/`),
  `real_world/calculation_rule_examples.json` (all 30 examples from the PDF, transcribed verbatim),
  `real_world/calculation-engine-3.0.0.yaml` (the real OpenAPI spec, confirmed from the platform
  team).

## What this doesn't do (out of scope, not forgotten)

- No schema *updates* — this prototype only creates new rule sets, matching
  `../registry-prototype/`'s own create-only scope. The real spec does have `PUT`/`DELETE
  /{module}/rules/{id}`, plus `GET .../attribute-paths`, `/estimate`, `/recalculate`, `/confirm`,
  `/cancel`, and calculation-record search — none of those are implemented here; this prototype
  only covers rule *authoring* (`POST /{module}/rules`), not the full calculation lifecycle.
- `TIME_BASED` (interest/penalty reading day-count fields like `daysDelayed`) isn't a separate
  wizard option — per `../reference/calculation-rule-vocabulary.md`'s own note, the engine expects
  the caller to supply day-count fields directly; this prototype doesn't do date arithmetic.
- The FORMULA *authoring* parser (`formula_parser.py`, what the wizard uses) supports `+`/`-`/`*`/`/`
  and parentheses only — no conditionals (`if`), no functions. Per the vocabulary reference (and
  `calculation-rule-examples.pdf`'s own "Common mistakes to avoid"), "a hidden if-branch formula" is
  discouraged in favor of two plain conditional rules anyway, so this isn't a gap so much as a
  deliberate nudge toward the clearer pattern. `simulate.py`'s *evaluator* is not this limited,
  though — it does support `if`/`==`/`!=`/comparisons, since the real engine accepts a rule
  authored that way even if this wizard won't help someone build one (see the stress-test section
  above).
- Worked-example scenarios vary one thing at a time (a single condition's field, one slab tier,
  one aggregation's total) — they don't attempt every *combination* of conditions across multiple
  rules at once, which would grow combinatorially past the ~15-scenario cap this prototype
  deliberately keeps the preview readable at.
