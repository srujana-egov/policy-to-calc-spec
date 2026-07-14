# Workflow configuration prototype

Step 4 of `../CONFIG-PIPELINE.md` — the guided-question wizard that builds a DIGIT Workflow
Service process definition, entirely offline, no external API or LLM required. Schema verified
directly against `digit-specs` `v3.0.0/workflow.yaml` (`ActionInput`, `StateInput`,
`ProcessDefinitionInput`), not guessed.

## What's runnable right now, no API key or live service needed

```
python3 test_workflow_builder.py
```

Builds the real `trade-license-approval` example from `workflow.yaml`'s own
`createProcessDefinition` example entirely through `WorkflowBuilder` calls, confirms it validates
clean, and confirms every completeness check in `validate.py` actually catches its corresponding
broken case (missing/duplicate `INITIAL`, dead ends, unresolvable `nextState`, duplicate codes,
unreachable states, terminal states with actions) — not just asserted to.

```
python3 wizard.py
```

The actual interactive CLI — answer its questions and it produces a real `ProcessDefinitionInput`
JSON payload, checked against the same `validate.py`, ready for `POST /process/definition`.

## Files

- `models.py` — `ActionInput`/`StateInput`/`ProcessDefinitionInput`, matching the real spec exactly.
- `builder.py` — `WorkflowBuilder`, the testable data-collection layer one method call per wizard
  question. Auto-generates machine-safe `code`s from human-typed names/labels, with collision
  handling.
- `validate.py` — deterministic completeness checks, no AI: exactly one `INITIAL` state, no dead
  ends, every `nextState` resolves, every state reachable from `INITIAL`, no duplicate codes.
- `wizard.py` — the interactive CLI, driving the same `WorkflowBuilder` the tests use.
- `test_workflow_builder.py` — the real example plus one test per completeness check.

## What this doesn't do (out of scope, not forgotten)

- Doesn't call the live Workflow Service — `POST /process/definition` is the natural next step
  once there's a real service to write to, but nothing here needs it to be useful today.
- Doesn't render an actual diagram back to the user — the wizard prints the resulting JSON, not a
  visual graph. `CONFIG-PIPELINE.md`'s "whole-structure preview" mitigation (catching an
  asymmetric-looking workflow by eye) isn't built here yet.
- `EscalationConfig` (per-state SLA escalation rules, Layer 3 in `workflow.yaml`) isn't modeled —
  this prototype covers process/state/action authoring only.
