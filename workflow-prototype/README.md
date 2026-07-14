# Workflow configuration prototype

Step 4 of `../CONFIG-PIPELINE.md` — the guided-question wizard that builds a DIGIT Workflow
Service process definition, entirely offline, no external API or LLM required. Schema verified
directly against `digit-specs` `v3.0.0/workflow.yaml` (`ActionInput`, `StateInput`,
`ProcessDefinitionInput`), not guessed.

## What's runnable right now, no API key or live service needed

```
python3 test_workflow_builder.py   # WorkflowBuilder + validate.py, 16 checks
python3 test_wizard.py             # the interactive layer itself, 39 checks
python3 test_render.py             # the diagram renderer, 17 checks
```

`test_workflow_builder.py` builds the real `trade-license-approval` example from `workflow.yaml`'s
own `createProcessDefinition` example entirely through `WorkflowBuilder` calls, confirms it
validates clean, and confirms every completeness check in `validate.py` actually catches its
corresponding broken case (missing/duplicate `INITIAL`, dead ends, unresolvable `nextState`,
duplicate codes, unreachable states, terminal states with actions, marking the `INITIAL` state
terminal) — not just asserted to.

`test_wizard.py` drives `wizard.py`'s actual `input()`-driven code (via a mocked `input()`, not
reimplemented) — the layer every real bug in this project has lived in, which the builder tests
above never touch. Replays three real production configs (`BPA`, `BPA_LOW`, `PGR67`) from
`fixtures/*_session_input.txt`, diffing the result against the real source config
(`fixtures/bpa_original.json`) or a prior verified snapshot, plus edge cases: cancel-via-quit,
invalid-input retries, self-loops, and the whole fix-one-thing-not-everything flow (redo a state,
edit the workflow's own fields, delete a state and then fix the dangling reference it leaves
behind, deleting the `INITIAL` state, an unknown fix target).

`test_render.py` checks the diagram is genuinely dependency-free (greps the rendered HTML for
`http://`/`https://`/`@import`/`fetch(`/`XMLHttpRequest`, all absent) and structurally correct
(the SVG parses as well-formed XML, node/edge counts match the process, self-loops route into the
backward lane without breaking anything) — previously only checked ad hoc during manual
verification, now a permanent regression check.

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
- `render.py` — generates the interactive HTML preview: hand-rolled SVG layout (BFS column
  layering from `INITIAL`, backward/self-loop edges routed into their own lane below the
  diagram), zero external dependencies — click-to-expand detail panels for roles/SLA.
- `wizard.py` — the interactive CLI: questions → diagram → confirmation → write (real or dry-run).
  `run_session()` holds the whole question sequence and returns the built process, separate from
  `main()`'s write step, so tests can drive it directly.
- `test_workflow_builder.py` — the real example plus one test per completeness check.
- `test_wizard.py` — the interactive layer, driven via a mocked `input()`, against real fixtures
  and edge cases.
- `test_render.py` — the diagram renderer: offline-safety and structural correctness.
- `fixtures/` — real production workflow configs used as regression fixtures: `bpa_original.json`
  (the actual DIGIT `businessService` config, in its native `state`/`actions`/`roles` shape),
  `*_session_input.txt` (the exact wizard answers that reproduce each one), `*_golden.json`
  (a prior verified wizard run's output, for configs without an independent source doc to diff
  against).

## What this doesn't do (out of scope, not forgotten)

- `EscalationConfig` (per-state SLA escalation rules, Layer 3 in `workflow.yaml`) isn't modeled —
  this prototype covers process/state/action authoring only.
- The BFS column layout is verified correct up to the real examples tested (14 states, self-loops
  and backward edges included) — not manually verified readable for an arbitrarily large or
  densely tangled workflow beyond that.
- The real-POST path in `write_process_definition()` (as opposed to the dry-run path, which every
  test exercises) has no automated test — it would need a mock HTTP server to check headers/body
  without a live DIGIT environment.
