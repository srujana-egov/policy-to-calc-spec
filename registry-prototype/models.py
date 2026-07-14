"""Pydantic models matching the real DIGIT Registry service contract -- verified against actual
Go source (`internal/models/models.go`) in digitnxt/digit3, not just swagger.yaml, the same way
../workflow-prototype/models.py was checked against the real workflow service.

Real discrepancies found during that verification, worth remembering here:
- The real API is mounted at `/registry/v3/...` -- swagger.yaml and README both say `/registry/v1`.
- The real auth header the middleware reads is `X-User-Id` -- swagger.yaml, the README, and even
  the project's own Postman collection all send `X-Client-Id`, which is silently ignored (you get
  a 400 "X-User-Id header is required"). write_schema()/write_records() in wizard.py send the
  header that actually works, not the documented one.
- `x-unique`/`x-indexes` are top-level fields on the create-schema *request body itself*
  (`models.SchemaRequest` in the real Go source has `XUnique`/`XIndexes` as sibling fields of
  `Definition`, which is typed `json.RawMessage` -- a raw blob), NOT nested inside `definition`.
  The Postman collection's own example nests them inside `definition`, which is why this was
  modeled wrong here initially: `CreateSchema`'s handler does `c.ShouldBindJSON(&request)`
  straight into that struct, so anything nested inside the `definition` JSON blob that isn't part
  of `SchemaDefinition` below is inert -- Go's `encoding/json` only looks for `x-unique`/
  `x-indexes` keys at the top level of the request body.

`x-ref-schema` (cross-schema references) and `webhook` are real fields on the schema definition
but out of scope here, same reasoning as EscalationConfig being out of scope for the workflow
wizard -- this prototype covers property/constraint/index authoring and record entry, not
cross-schema linking or webhook wiring.

Nested object properties (one level deep -- a field like `address` with its own `city`/`pincode`
sub-fields) *are* modeled, added after scraping real schemas across the digitnxt org turned up
the same pattern independently three times: the official `pgr2-registry-schema.yaml` tutorial,
`digit-specs/v3.0.0/registry.yaml`'s own canonical `trade-license` example (an `address` object
with a required `city` sub-field), and `examples/pgr/pgr-schemas/registry-schema.yaml`. Deeper
nesting (an object inside an object inside an object) was never seen in any real example, so
`PropertyDef.properties` isn't itself further restricted, but the wizard only ever asks for one
level -- see `wizard.py`.

`pattern` and `minimum`/`maximum` are modeled for the same reason: the `pgr2` tutorial uses
`pattern` for a 10-digit mobile number and a 6-digit pincode, and `minimum`/`maximum` for
latitude/longitude bounds.

`minLength`/`maxLength` are modeled too, on weaker but still real evidence: `license-certificate/
Schema-Registry-3.0.0.yaml` uses them repeatedly (e.g. an 8-128 character `Idempotency-Key`
header), but on checking closely that file has no `schemaCode`/`x-indexes`/`x-unique` anywhere --
it's a *different* OpenAPI spec for that project's own module/UI-config API, not a registry
schema-authoring contract, and the `minLength`/`maxLength` usages found are on OpenAPI *request
parameters* (headers/paths), not registry `definition.properties` fields. They're still standard
JSON Schema 2020-12 keywords (the exact dialect the registry's own `definition` field declares),
so supporting them is low-risk and consistent with `pattern`/`minimum`/`maximum` -- just not
backed by the same strength of "found literally inside a real registry schema" evidence.
"""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# Deliberately Optional[X], not X | None: Pydantic re-evaluates model field annotations at class
# creation time to build its validators, so the `|` union syntax (PEP 604, Python 3.10+) breaks
# on Python 3.9 even with `from __future__ import annotations` -- that only defers *function*
# signature evaluation, not Pydantic's own field-type resolution. Matches the same fix already
# applied in ../workflow-prototype/models.py.

PropertyType = Literal["string", "integer", "number", "boolean", "array", "object"]
IndexMethod = Literal["btree", "gin"]


class PropertyDef(BaseModel):
    type: PropertyType
    format: Optional[str] = None
    enum: Optional[list[str]] = None
    description: Optional[str] = None
    pattern: Optional[str] = None
    # Union[int, float], not float: pydantic's "smart" union mode keeps an int input (e.g. the
    # real pgr2 example's `minimum: -90`) as an int on output, rather than coercing to `-90.0` and
    # producing JSON that doesn't match the real schema byte-for-byte.
    minimum: Optional[Union[int, float]] = None
    maximum: Optional[Union[int, float]] = None
    minLength: Optional[int] = None
    maxLength: Optional[int] = None
    # One level of nesting only (see module docstring) -- meaningful only when type == "object".
    properties: Optional[dict[str, "PropertyDef"]] = None
    required: Optional[list[str]] = None


PropertyDef.model_rebuild()


class IndexDef(BaseModel):
    name: Optional[str] = None
    fieldPath: str
    method: IndexMethod = "btree"


class SchemaDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="https://json-schema.org/draft/2020-12/schema", alias="$schema")
    type: Literal["object"] = "object"
    additionalProperties: bool = False
    properties: dict[str, PropertyDef] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class SchemaRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schemaCode: str
    definition: SchemaDefinition
    x_unique: Optional[list[list[str]]] = Field(default=None, alias="x-unique")
    x_indexes: Optional[list[IndexDef]] = Field(default=None, alias="x-indexes")


class DataRequest(BaseModel):
    version: Optional[int] = None  # required only when updating an existing record, not on create
    data: dict[str, Any]
