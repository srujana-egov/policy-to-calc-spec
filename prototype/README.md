# Monday demo prototype

Proves the core pipeline end to end for one document (Chennai Schedule I). See `../DESIGN.md`'s
"Monday demo scope" section for what this is and isn't.

## What's runnable right now, no API key needed

```
python3 run_demo.py
```

Validates the hand-authored `fixtures_generated/chennai_schedule_I_rules.json` (representing what
the LLM extraction+synthesis stages should produce) against `validate.py` — a direct
implementation of `calculation-engine-3.0.0.yaml`'s `x-businessRules` — then runs three synthetic
payloads through `simulate.py` — a reimplementation of the engine's documented evaluation order —
and prints business-readable results plus the extraction's stated assumptions.

## What needs `ANTHROPIC_API_KEY` to run live

```
export ANTHROPIC_API_KEY=sk-...
.venv/bin/python extract.py                                    # doc -> PolicyRule[] (structured output)
.venv/bin/python synthesize.py                                 # PolicyRule[] -> CalculationRule[] (structured output + validation reflection pass)
.venv/bin/python run_demo.py fixtures_generated/synthesized_calculation_rules.json
```

`extract.py` and `synthesize.py` use Claude's native Structured Outputs (`client.messages.parse`
with a Pydantic `output_format`) — the response is guaranteed schema-conformant, no hand-rolled
JSON parsing/retry. `synthesize.py` runs one reflection pass through `validate.py` if the first
attempt fails validation, then reports remaining errors rather than looping indefinitely.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install anthropic
```

(Already done in this checkout — `.venv/` exists with `anthropic` installed.)

## Honest limits of this prototype

- Scoped to Chennai Schedule I only — no "locate relevant spans" step, no multi-schedule
  `tradeCategory` handling, no Bissau case.
- No MCP, confirmation gate, audit log, or Temporal — see `DESIGN.md` for where those fit later.
- `simulate.py` is not full engine parity (no persistence, no tenant isolation) — it's the
  documented evaluation algorithm only, enough to prove a spec computes what the policy says.
- `extract.py`/`synthesize.py` are untested against the live API in this environment (no API key
  available) — verified instead by confirming the Pydantic schemas build correctly and both
  scripts import cleanly. Run them with a real key before the demo to confirm end to end.
