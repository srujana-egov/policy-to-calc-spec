# Registry configuration prototype

Step 2 of the config pipeline (see `../DEMO-2026-07-15.md`) — a guided-question wizard that
authors a DIGIT Registry schema, then a second phase that enters data records against it, entirely
offline, no external API or LLM required. Same shape as `../workflow-prototype/`: a testable
builder layer driven by both the interactive CLI and automated tests, a deterministic validate
step, a preview a non-technical user can actually read, an explicit confirmation gate, and a
real-or-dry-run write.

Contract verified directly against the real Go source in `digitnxt/digit3`
(`src/services/registry/internal/models`, handlers, DB migrations) — not just `swagger.yaml`,
because the spec and the implementation disagree in several places (below).

Also stress-tested against every registry schema/data example found by scraping the **entire
digitnxt GitHub org** (all 8 repos, not just `digit3`) — `digit-specs`, `digit-client-tools`,
`examples`, `license-certificate`, `digit-trial`, `decision` — searched via GitHub code search
plus full repo tree listings for `registry`/`schema` filenames. Real schemas found this way
(`fixtures/real_world/`) turned up patterns this prototype didn't support yet (nested objects,
`pattern`, `minimum`/`maximum`) and a schema code containing a dot that the original validation
regex would have rejected — see "Real discrepancies found" below.

## What's runnable right now, no API key or live service needed

```
python3 test_schema_builder.py   # SchemaBuilder + validate.py, 39 checks
python3 test_wizard.py           # the interactive layer itself, 37 checks
python3 test_render.py           # the two table previews, 37 checks
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

## Real schemas found by scraping the whole org, and what they revealed

- **`digit-cli`'s own `test-registry-schema.yaml`/`test-registry-data.yaml`** are, byte for byte,
  the exact `test-license-registry` schema built through this wizard earlier — good independent
  confirmation the wizard's output matches what the DIGIT ecosystem itself considers a valid
  example.
- **`digit-cli`'s own `create-registry` Go command has the identical `x-unique`/`x-indexes`
  placement bug**, independently confirming the fix above was right: its
  `RegistrySchemaDefinition` YAML-parsing struct and `RegistrySchemaRequest` HTTP struct
  (`client-libraries/digit-library/digit/registry.go`) only have `SchemaCode`/`Definition` fields
  — there's nowhere for `x-unique`/`x-indexes` to go even if the YAML has them at the top level
  (which `digit-cli/test-registry-schema.yaml` does) or nested (which `digit-cli/example-schema.yaml`,
  an older/different-draft example, does) — either way, the official CLI silently drops them.
  Separately, its `CreateRegistryData` function POSTs to `/registry/v3/data?schemaCode=X` (query
  param), while its own `SeedRegistryData` function in the same file POSTs to
  `/registry/v3/{schemaCode}/data` (path param, matching the verified real route) — two different
  URL shapes for the same operation in one file; the query-param one contradicts the actual router
  and is almost certainly broken the same way this prototype's data-write bug was.
- **`digit-specs/v3.0.0/registry.yaml`'s own canonical example schema (`trade-license`) confirms
  `x-unique`/`x-indexes` belong at the top level** — independent confirmation from the spec repo
  itself, not just the Go source. It also uses a nested `address` object with a required `city`
  sub-field and two unique constraints (one single-field, one compound) — see `test_13`/
  `test_wiz_02b` below.
- **`docs/tutorials/backend/pgr2-registry-schema.yaml` (an official tutorial) has the identical
  `x-indexes`-nested-in-`definition` bug**, yet another independent instance of the same
  documentation mistake. `fixtures/real_world/pgr2.json` corrects this rather than reproducing the
  tutorial's own bug (see the `_comment_x_indexes_moved` note in that file). This tutorial is also
  where `pattern` (10-digit mobile, 6-digit pincode), `minimum`/`maximum` (lat/long bounds), and
  nested objects were found in the wild, prompting `models.py`/`builder.py`/`wizard.py` to support
  them (previously unsupported).
- **`examples/pgr/pgr-schemas/pgr-service-category-schema.yaml` uses a schema code containing a
  dot** (`PGR.ServiceCategory`) — the original `_SCHEMA_CODE_RE` (letters/numbers/`-`/`_` only)
  would have rejected a schema code DIGIT itself ships as an example. Fixed to allow dots.
- **Nested object properties are real and common, not hypothetical** — found independently in
  three unrelated sources (the `pgr2` tutorial, `digit-specs`' own canonical example, and
  `examples/pgr/pgr-schemas/registry-schema.yaml`), always for the same kind of field (an
  `address` or `auditDetails` group). Support was added one level deep, matching every real
  example except one: `pgr2`'s own `address` field nests a *second* level
  (`address.auditDetails`, itself with sub-fields) — deeper than the wizard's interactive flow
  goes (a deliberate scope decision, not an oversight: arbitrary recursive prompting was judged
  not worth the added complexity for a pattern seen at only one level everywhere else). The
  underlying model (`PropertyDef.properties: Optional[dict[str, PropertyDef]]`) has no depth
  limit — `test_14`/`test_wiz_02b`-`02d` confirm the model reproduces `pgr2`'s full two-level
  structure exactly, while the *wizard's own UI* caps at one level (`configure_nested_fields()` in
  `wizard.py`; a sub-field can't itself be a group, only `ask_field()` at the top level offers
  that choice).
- **`digit-trial`'s `services/common/registry/` is a different, unrelated "registry" service**
  (different routes entirely — `/registry/database`, `/registry/{name}/{version}/data`) — found
  during the scrape, explicitly not conflated with the `digit3` registry service this prototype
  targets.
- **`digit-cli/example-schema.yaml` is stale, broken cruft, not an alternate valid format** —
  confirmed by parsing it: its only top-level YAML key is `schema` (wrapping `code`/
  `description`/`definition`), but `createRegistry.go`'s `RegistrySchemaDefinition` struct only
  recognizes top-level `schemaCode`/`definition` keys. Running `digit create-registry --file
  example-schema.yaml` today would immediately fail the CLI's own `"definition is required"`
  check (`registryDef.Definition` would be `nil` since `definition` never appears at the level the
  parser looks for it). Confirmed via commit history: it was added in the same initial commit as
  the *working* `test-registry-schema.yaml`, then never updated when `createRegistry.go` was later
  changed — dead documentation left in the current tree, not a legacy-but-supported shape. Not
  modeled here, correctly.
- **`minLength`/`maxLength` are modeled on weaker evidence than `pattern`/`minimum`/`maximum`** --
  see models.py's docstring for the full account. `license-certificate/Schema-Registry-3.0.0.yaml`
  uses them repeatedly, but that file has no `schemaCode`/`x-indexes` anywhere — it's a different
  OpenAPI spec (that project's own module/UI-config API), and the usages found are on OpenAPI
  *request parameters*, not registry schema fields. Still standard JSON Schema 2020-12 keywords
  (the exact dialect the registry declares), so supporting them is low-risk and consistent with
  the other constraints, just not backed by a "found literally inside a real registry schema"
  example the way `pattern`/`minimum`/`maximum` are. `test_24` uses a synthetic (clearly labeled,
  not scraped) example rather than overstating this as a real-schema fixture.

## Files

- `models.py` — `SchemaRequest`/`SchemaDefinition`/`PropertyDef`/`IndexDef`/`DataRequest`,
  matching the real Go structs. Schema definitions are genuine JSON Schema draft 2020-12, not a
  custom DIGIT format — confirmed against real example payloads, not assumed. `PropertyDef`
  supports `pattern`/`minimum`/`maximum`/`minLength`/`maxLength` and arbitrary-depth nested `properties`/`required`
  (self-referencing, `model_rebuild()`'d) — see "Real schemas found" above.
- `builder.py` — `SchemaBuilder`, one method call per wizard question. Auto-generates camelCase
  field names from human-typed labels (`camel_field_name`), the same reasoning as `slugify()` in
  the workflow builder. `add_nested_field()` adds one level of sub-fields under an object-type
  field.
- `validate.py` — deterministic completeness checks: schema code present and well-formed (letters/
  numbers/`.`/`-`/`_`), at least one field, every `required`/unique-constraint/index reference
  resolves to a real field (recursively, for nested groups too), `enum`/`pattern`/`minimum`/
  `maximum` only on types where they make sense, `minimum` not greater than `maximum`.
- `render.py` — two self-contained HTML table previews (schema fields; data records), zero
  external dependencies. Click a field row for its exact JSON Schema fragment (including nested
  sub-fields, at whatever depth the model has); the table itself shows constraint hints
  (pattern/min/max, "group of N field(s)") for top-level fields.
- `wizard.py` — phase 1 interactive CLI: fields → constraints → table preview → confirm → write.
  `run_schema_session()` returns the built schema, separate from the write step, so tests can
  drive it directly. `ask_field_type()` offers a 7th option ("a group of related fields") that
  triggers `configure_nested_fields()` for one level of sub-questions; text fields can optionally
  get a digit-count pattern (phone/pincode-style, expressed in plain language, not raw regex);
  number fields can optionally get a min/max bound.
- `data_entry.py` — phase 2: one question per field per record, repeatable, table preview →
  confirm → write. Imports `ask`/`ask_yes_no`/`_registry_headers` from `wizard.py`.
  `ask_nested_record_value()` asks a group's sub-fields as their own set of questions; an optional
  group can be skipped entirely.
- `add_data.py` — standalone entry point for adding records to a schema that already exists on
  the server: fetches it via `GET /registry/v3/schema/:schemaCode`, then reuses `data_entry.py`'s
  flow. No schema authoring, no dry-run form.
- `test_schema_builder.py` — real examples (`license-registry`, `trade-license`, `pgr2`,
  `PGR.ServiceCategory`) plus one test per completeness check. `canonicalize_real_world()`/
  `canonicalize_built()` strip fields this prototype deliberately doesn't model before comparing.
- `test_wizard.py` — the interactive layer (both phases), driven via a mocked `input()`, against
  the same real fixtures and edge cases (cancel, invalid retries, redo/add/delete-and-fix-the-
  fallout for fields, redo/add/delete for records, the real interactive nested-object flow against
  `trade-license`).
- `test_render.py` — offline-safety and structural correctness for both previews, including
  nested-group display and pattern/min-max hints.
- `test_write_path.py` — the real-POST paths (not just dry-run) against a throwaway local HTTP
  server, asserting the exact path/headers/body sent. Added after a live write 404'd because of
  the missing-`/schema/`-segment bug above -- dry runs alone can't catch a URL-construction bug
  since they never send a real request.
- `fixtures/` — `license_registry_schema_session.txt`/`license_registry_data_session.txt`/
  `trade_license_session.txt` (exact wizard answers), `*_golden.json` (verified output),
  `real_world/` (schemas scraped from across the digitnxt org, used as fidelity fixtures rather
  than hand-invented examples).

## What this doesn't do (out of scope, not forgotten)

- `x-ref-schema` (cross-schema field references) and `webhook` (on-write callbacks) are real
  fields on the schema definition but aren't modeled — this prototype covers property/constraint/
  index authoring and record entry, not cross-schema linking or webhook wiring.
- Schema *updates* (`PUT /registry/v3/schema/:schemaCode`, which bumps `version`) aren't modeled
  — this prototype only creates new schemas.
- Async-persistence mode (see above) isn't handled by the write step.
- The wizard's interactive flow only goes one level deep for nested object fields (a deliberate
  scope decision — see "Real schemas found" above for the one real counterexample). The
  underlying model has no such limit.
