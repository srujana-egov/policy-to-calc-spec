# Registry configuration prototype

Step 2 of `../CONFIG-PIPELINE.md` — a guided-question wizard that authors a DIGIT Registry schema,
then a second phase that enters data records against it, entirely offline, no external API or
LLM required. Same shape as `../workflow-prototype/`: a testable builder layer driven by both the
interactive CLI and automated tests, a deterministic validate step, a preview a non-technical user
can actually read, an explicit confirmation gate, and a real-or-dry-run write.

Contract verified directly against the real Go source in `digitnxt/digit3`
(`src/services/registry/internal/models`, handlers, DB migrations) — not just `swagger.yaml`,
because the spec and the implementation disagree in several places (below).

## What's runnable right now, no API key or live service needed

```
python3 test_schema_builder.py   # SchemaBuilder + validate.py, 21 checks
python3 test_wizard.py           # the interactive layer itself, 28 checks
python3 test_render.py           # the two table previews, 19 checks
python3 test_write_path.py       # real HTTP paths against a throwaway local server, 20 checks
```

```
python3 wizard.py
```

The actual interactive CLI. Phase 1 (schema authoring): questions → table preview → explicit
confirmation → write (real `POST /registry/v3/schema` or a clearly-labeled dry run). Phase 2, if
you say yes to adding data now: one question per field per record, repeatable, table preview →
confirmation → write (one `POST /registry/v3/<code>/data` per record).

Both phases use the same "fix one thing, don't restart everything" pattern the workflow wizard
was given after finding that "no" at the confirmation gate used to discard the whole session:
saying no offers a menu to redo/add/delete a field (or a record), rather than starting over.

```
python3 add_data.py [schema-code]
```

A separate entry point for a schema that already exists on the server — skips schema authoring
entirely. Fetches the real definition via `GET /registry/v3/schema/:schemaCode` (needs
`DIGIT_SERVER_URL`/`DIGIT_TENANT_ID`/`DIGIT_USER_ID` set; there's no dry-run form of this, since
there's nothing to preview without knowing the real fields), then runs the same record-entry flow
as `wizard.py`'s phase 2.

## Real discrepancies found between swagger.yaml / the README / the Postman collection and the
## actual Go implementation (why this exists instead of trusting the spec)

- **Wrong API version everywhere except the code itself**: `swagger.yaml` and the service's own
  `README.md` both document `/registry/v1/...`. The real Gin router — variable literally named
  `v1` in the source, a copy-paste artifact — mounts everything at `/registry/v3/...`. Every
  request in this prototype uses `v3`.
- **The data route drops the `schema` path segment entirely, contradicting swagger.yaml**: the
  spec documents `/registry/v1/schema/{schemaCode}/data`. The real router
  (`cmd/server/main.go`) mounts `schemaRoutes := v1.Group("/schema")` (schema CRUD) and
  `dataRoutes := v1.Group("/:schemaCode/data")` as two *siblings* directly under `v1`
  (`/registry/v3`) — data routes are **not** nested under `/schema` at all. The real path is
  `/registry/v3/{schemaCode}/data`, not `/registry/v3/schema/{schemaCode}/data`. This one wasn't
  caught by the initial research pass (which read the documented shape rather than verifying this
  specific route against the router registration) and shipped as a real bug — a live write
  returned a bare `404 page not found` (Gin's own "no route matched," not our app's JSON error
  shape) even though the schema itself had been created successfully seconds earlier at the
  correctly-shaped `/registry/v3/schema` endpoint. `write_records()` in `data_entry.py` now uses
  the verified route.
- **`x-unique`/`x-indexes` are top-level fields on the create-schema *request body*, not nested
  inside `definition` — the most consequential mismatch found, because it fails silently.** The
  real `models.SchemaRequest` Go struct has `XUnique`/`XIndexes` as sibling fields of `Definition`
  (which is typed `json.RawMessage` — an opaque blob server-side). `CreateSchema`'s handler does
  `c.ShouldBindJSON(&request)` straight into that struct: anything nested inside the `definition`
  JSON that isn't part of `SchemaDefinition` is inert, never populating the real `XUnique`/
  `XIndexes` fields the server actually reads. The Postman collection's own example nests them
  inside `definition`, which is why this was modeled wrong here initially — **any schema created
  with an earlier version of this tool that included a unique constraint or an index almost
  certainly did not get that constraint/index applied server-side**, despite the create call
  returning `201 Created` (the server doesn't reject or warn about unrecognized keys inside the
  raw `definition` blob, so the request "succeeds" while silently dropping the constraint). If you
  created a schema before this fix and it needs real unique constraints or indexes, re-create it
  or `PUT` an update with the corrected shape. `models.py`'s `SchemaRequest` now has `x_unique`/
  `x_indexes` as top-level fields, matching the real struct; `test_write_path.py` asserts they
  land at the top level of the actual JSON sent, not just that the Python objects compare equal
  (structural-equality tests alone couldn't have caught this, since fixing the code and fixing the
  test assertions together hides exactly this class of bug).
- **Wrong auth header, in three places at once**: `swagger.yaml`, the README, *and* the project's
  own `Registry_Collection.json` (Postman) all document/send `X-Client-Id` as the actor header.
  The real middleware (`internal/middleware/middleware.go`) only reads `X-User-Id`. Sending the
  documented header gets a 400 `"X-User-Id header is required"`. `_registry_headers()` in
  `wizard.py` sends `X-User-Id`, not what any of the docs say.
  - `DIGIT_USER_ID` is the env var this prototype reads for it, matching the workflow wizard's
    naming, not `DIGIT_CLIENT_ID`.
- **A body field that's parsed and then silently discarded**: the `_isExist` endpoint's request
  struct has a `tenantId` field the spec says overrides the header — the handler never reads it,
  always using the header value instead. Not exercised by this prototype (schema/data creation
  only), but a reminder that "the spec says X is optional/overridable" isn't always true.
- **Error response shape doesn't match the documented envelope**: swagger says errors come back
  as `{success, data, error, message}`; the real `writeError()` returns a bare JSON array
  `[{"code":..., "message":...}]`. `write_schema()`/`write_records()` don't try to parse a
  structured error body for this reason — on an HTTP error they print the raw response instead of
  assuming a shape that might not be there.
- **Response shape is config-dependent**: if the server's async-persistence mode is enabled,
  create/update/delete on data return `202 Accepted` with no body at all (fire-and-forget), rather
  than the synchronous `201`/`200` + record body swagger documents. `write_records()` only reads
  `resp["data"]["registryId"]` on success — if you point this at a server running in async mode,
  expect that read to fail; this prototype doesn't currently handle that mode.

## Files

- `models.py` — `SchemaRequest`/`SchemaDefinition`/`PropertyDef`/`IndexDef`/`DataRequest`,
  matching the real Go structs. Schema definitions are genuine JSON Schema draft 2020-12, not a
  custom DIGIT format — confirmed against real example payloads, not assumed.
- `builder.py` — `SchemaBuilder`, one method call per wizard question. Auto-generates camelCase
  field names from human-typed labels (`camel_field_name`), the same reasoning as `slugify()` in
  the workflow builder.
- `validate.py` — deterministic completeness checks: schema code present and well-formed, at
  least one field, every `required`/unique-constraint/index reference resolves to a real field,
  `enum` only on types where it makes sense.
- `render.py` — two self-contained HTML table previews (schema fields; data records), zero
  external dependencies. Click a field row for its exact JSON Schema fragment.
- `wizard.py` — phase 1 interactive CLI: fields → constraints → table preview → confirm → write.
  `run_schema_session()` returns the built schema, separate from the write step, so tests can
  drive it directly.
- `data_entry.py` — phase 2: one question per field per record, repeatable, table preview →
  confirm → write. Imports `ask`/`ask_yes_no`/`_registry_headers` from `wizard.py`.
- `add_data.py` — standalone entry point for adding records to a schema that already exists on
  the server: fetches it via `GET /registry/v3/schema/:schemaCode`, then reuses `data_entry.py`'s
  flow. No schema authoring, no dry-run form.
- `test_schema_builder.py` — a real example (`license-registry`, matching the registry service's
  own Postman collection payload) plus one test per completeness check.
- `test_wizard.py` — the interactive layer (both phases), driven via a mocked `input()`, against
  the same real fixture and edge cases (cancel, invalid retries, redo/add/delete-and-fix-the-
  fallout for fields, redo/add/delete for records).
- `test_render.py` — offline-safety and structural correctness for both previews.
- `test_write_path.py` — the real-POST paths (not just dry-run) against a throwaway local HTTP
  server, asserting the exact path/headers/body sent. Added after a live write 404'd because of
  the missing-`/schema/`-segment bug above -- dry runs alone can't catch a URL-construction bug
  since they never send a real request.
- `fixtures/` — `license_registry_schema_session.txt`/`license_registry_data_session.txt` (the
  exact wizard answers), `license_registry_golden.json`/`license_registry_data_golden.json` (the
  verified output).

## What this doesn't do (out of scope, not forgotten)

- `x-ref-schema` (cross-schema field references) and `webhook` (on-write callbacks) are real
  fields on the schema definition but aren't modeled — this prototype covers property/constraint/
  index authoring and record entry, not cross-schema linking or webhook wiring.
- Schema *updates* (`PUT /registry/v3/schema/:schemaCode`, which bumps `version`) aren't modeled
  — this prototype only creates new schemas.
- Async-persistence mode (see above) isn't handled by the write step.
