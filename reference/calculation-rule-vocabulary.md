# CalculationRule vocabulary — target reference for the synthesis stage

Condensed from `calculation-rule-examples.pdf` (30 worked examples, simple → complex) and
verified against the authoritative schema in `calculation-engine-3.0.0.yaml`
(`components/schemas/CalculationRule`, `AttributeCondition`, `AttributeBinding`, `Slab`) — both
supplied by Ghanshyam. This is the lookup table Stage 4 (Rule Synthesis) maps every extracted
`PolicyRule` onto — every real policy fee pattern reduces to one or more rows below, combined.
Where the two sources could be read as disagreeing, the YAML schema and its `x-businessRules`
win — the examples doc illustrates the schema, it doesn't extend it.

| Policy pattern | CalculationRule mechanism |
|---|---|
| Charge the same amount every time | `calculationType: FLAT`, `conditions: {}` |
| Charge different amounts depending on one field | one `conditions` key, several rule rows sharing a `component` |
| Charge based on a range (age, count, size) | `from`/`to` in the condition instead of `equals` (`to` inclusive). Either bound may be omitted for an open-ended range (e.g. `from: 30` with no `to` means "30 and above") |
| Require several fields to all match | multiple keys in one `conditions` object — always ANDed, no explicit "and" |
| Charge rate × someField | `calculationType: PER_UNIT`, `appliesOn.jsonPath` |
| Charge tiered/marginal bands of the same field | `calculationType: SLAB`, `slabs` array — each tier's rate applies only to the portion of the value inside that band, never the whole value |
| Add a tax/cess on top of a fee | `calculationType: PERCENTAGE`, `appliesOn.componentRef`, `dependsOn: [thatComponent]` |
| A flat charge that must still be sequenced after another component (e.g. a flat cess after the base fee), without reading that component's value | `dependsOn` naming it, but no `appliesOn.componentRef` — `dependsOn` is a pure ordering hint here, not a signal the rule is a percentage/rebate |
| Give a rebate/deduction | `ADJUSTMENT` ruleType, negative `value`. Schema-verified: `appliesOn.componentRef` is *always* required when `ruleType: ADJUSTMENT` (not optional) — a rebate always names the component it reduces, and that component must also appear in `dependsOn` |
| Charge per item in a repeating list (accessories, floors, taps) | `scope: SUBENTITY`, `subEntityPath` — `jsonPath` inside that rule's `conditions`/`appliesOn` becomes relative to one array element |
| Total up a list into one number | `ruleType: AGGREGATION`, `aggregateFunction: SUM\|COUNT\|MAX\|MIN\|AVG`, `sourceAttribute`, `targetAttribute`, low `priority` (runs first). `sourceAttribute` is required even for `COUNT` — it counts sub-entities where that field is present, even though the value itself isn't summed |
| Band/condition on that derived total | `derivedFrom: <aggregationComponent>` in a condition, instead of `jsonPath` |
| Do real math (not just a rate) | `calculationType: FORMULA`, `formulaVariables` (each bound via `jsonPath`, `componentRef`, or mixed) + `formulaLogic` (JSON Logic) |
| Branch inside one formula | JSON Logic `if` inside `formulaLogic` — only when both branches share the same underlying shape; otherwise prefer two plain conditional rules |
| Chain fee → interest → penalty | `dependsOn` naming each prior component, in order; `INTEREST`/`PENALTY` ruleTypes read prior components via `componentRef` inside `formulaVariables` |

## Specificity tie-break (schema `x-businessRules`, not previously captured from the examples alone)

"When multiple `RATE_MATRIX` rules could match the same context, the engine selects the most
specific match — the rule with the fewest wildcarded condition keys wins." This means the
synthesis stage doesn't strictly need every band within one component to be mutually exclusive by
construction — but relying on this instead of authoring genuinely non-overlapping bands is a
mistake to avoid (see below): it's a safety net in the engine, not a design pattern to lean on.
`ADJUSTMENT` rules are the opposite of this — they're cumulative and stack in ascending `priority`
order rather than picking one winner; `AGGREGATION` rules always run first regardless of
`priority`.

## Common mistakes to avoid (feed these as synthesis-stage validation checks)

- Missing `dependsOn` on a `PERCENTAGE`/`FORMULA` rule that reads a `componentRef` — the engine
  never infers order.
- Setting both `equals` and `from`/`to` on the same condition — pick one, never both.
- Reusing an attribute name with a different `jsonPath` across two rules — the first rule to use
  a name registers its path for that module; a later rule with a different path is a write-time
  `409 AttributePath.Conflict`. Always reuse the exact same `jsonPath`.
- Treating `SLAB` as a bracket lookup instead of true marginal tiers (each `rate` applies only to
  the portion of the value inside that band).
- A hidden `if`-branch formula where two plain conditional rows (see "Charge different amounts
  depending on one field") would be clearer to the next maintainer.

## Note from the reference doc worth flagging upstream

`INTEREST`/`PENALTY` rules expect the caller to compute and supply day-count fields (e.g.
`daysDelayed`) in `entityDetail` — the engine does not do date arithmetic from `asOnDate`
internally. If native day-count support becomes a common need across generated specs, that's a
platform-team ask, not something the generator can work around.
