# Monday demo prototype

Proves the core pipeline end to end for one document (Chennai Schedule I). See
`../DEMO-2026-07-13.md`'s "Architecture (as built for the demo)" section for what this is and
isn't.

## What's runnable right now, no API key needed

```
python3 run_demo.py
```

Validates the hand-authored `fixtures_generated/chennai_schedule_I_rules.json` (representing what
the LLM extraction+synthesis stages should produce) against `validate.py` — a direct
implementation of `calculation-engine-3.0.0.yaml`'s `x-businessRules` — then runs three synthetic
payloads through `simulate.py` — a reimplementation of the engine's documented evaluation order —
and prints business-readable results plus the extraction's stated assumptions.

## What needs an API key to run live

Works with **either** `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` — `llm_client.py` picks whichever
is set (Anthropic preferred if both are). Set the real key value yourself in your own terminal,
not by pasting it into a chat:

```
export ANTHROPIC_API_KEY=sk-ant-...    # or: export OPENAI_API_KEY=sk-...
cd prototype
.venv/bin/python extract.py --schedule SCHEDULE-I ../fixtures/chennai-trade-license-schedule.md
.venv/bin/python synthesize.py
.venv/bin/python run_demo.py fixtures_generated/synthesized_calculation_rules.json
```

`extract.py` and `synthesize.py` use structured outputs (Claude's `client.messages.parse` with a
Pydantic `output_format`, or OpenAI's `client.chat.completions.parse` with `response_format` —
same idea, different SDK) — the response is guaranteed schema-conformant, no hand-rolled JSON
parsing/retry. `synthesize.py` runs one reflection pass through `validate.py` if the first attempt
fails validation, then reports remaining errors rather than looping indefinitely.

Override the model via `ANTHROPIC_MODEL` / `OPENAI_MODEL` env vars if the defaults
(`claude-sonnet-5` / `gpt-5.6`) aren't right for your account.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install anthropic openai
```

(Already done in this checkout — `.venv/` exists with both installed.)

## Honest limits of this prototype

- Scoped to Chennai Schedule I only — no "locate relevant spans" step, no multi-schedule
  `tradeCategory` handling, no Bissau case.
- No MCP, confirmation gate, audit log, or Temporal — see `../DEMO-2026-07-13.md` for where those
  fit later.
- `simulate.py` is not full engine parity (no persistence, no tenant isolation) — it's the
  documented evaluation algorithm only, enough to prove a spec computes what the policy says.
- `extract.py`/`synthesize.py` are untested against a live API in this environment (no working
  key available here) — verified instead by confirming the Pydantic schemas build correctly and
  both scripts import cleanly. Run them yourself with a real key before the demo to confirm end
  to end — and note the OpenAI path specifically is even less proven than the Anthropic path,
  since this prototype was originally built and reasoned about against Claude's behavior.
