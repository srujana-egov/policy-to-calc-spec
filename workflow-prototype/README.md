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

The actual interactive CLI. After the questions, it: (1) generates `<code>_preview.html` — a
self-contained, offline-viewable interactive diagram (click any box for its SLA and every
action's roles, click an arrow for that one action's detail) — not a JSON dump; (2) asks for
explicit confirmation before doing anything else; (3) only then writes — a real `POST
/workflow/v3/process/definition` if `DIGIT_SERVER_URL`/`DIGIT_JWT_TOKEN`/`DIGIT_TENANT_ID`/
`DIGIT_USER_ID` are all set in the environment, otherwise a clearly-labeled DRY RUN that prints
exactly what would be sent, with nothing actually transmitted.

Deliberately does **not** use `digitnxt/digit-client-tools`'s own `digit create-workflow` CLI for
the write step — checked its actual source, and its client library's `ActionInput` struct has no
`roles`/`assigneeCheck` fields at all, meaning it silently drops role restrictions on write even
though the real server supports them. `write_process_definition()` in `wizard.py` replicates the
exact same simple HTTP pattern (same headers, same endpoint) directly, preserving `roles` correctly.

## Files

- `models.py` — `ActionInput`/`StateInput`/`ProcessDefinitionInput`, matching the real spec exactly.
- `builder.py` — `WorkflowBuilder`, the testable data-collection layer one method call per wizard
  question. Auto-generates machine-safe `code`s from human-typed names/labels, with collision
  handling.
- `validate.py` — deterministic completeness checks, no AI: exactly one `INITIAL` state, no dead
  ends, every `nextState` resolves, every state reachable from `INITIAL`, no duplicate codes.
- `render.py` — generates the interactive HTML preview (vis-network via CDN for graph layout —
  a solved problem, not reinvented here — with click-to-expand detail panels for roles/SLA).
- `wizard.py` — the interactive CLI: questions → diagram → confirmation → write (real or dry-run).
- `test_workflow_builder.py` — the real example plus one test per completeness check.

## What this doesn't do (out of scope, not forgotten)

- `EscalationConfig` (per-state SLA escalation rules, Layer 3 in `workflow.yaml`) isn't modeled —
  this prototype covers process/state/action authoring only.
- The diagram's graph layout is whatever `vis-network`'s hierarchical layout produces — fine for
  the examples tested so far, not manually verified readable for an arbitrarily large/tangled
  workflow.
