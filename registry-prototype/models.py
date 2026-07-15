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
    # extra="forbid", not the default "ignore": a real bug found via add_raw_property -- a raw
    # dict like {"type": "array", "prefixItems": [...]} has a "type" value that's a legal
    # PropertyType, so Pydantic's Union[PropertyDef, dict] elsewhere in this file would otherwise
    # happily validate it AS a PropertyDef and silently drop "prefixItems" (Pydantic's default
    # is to ignore, not reject, unmodeled keys). With extra="forbid", any key PropertyDef doesn't
    # explicitly model makes that Union member fail to validate, so Pydantic correctly falls
    # through to the raw `dict` variant instead -- the fix has to live here, not in the Union
    # field's ordering, since the union tries the "best match," not strictly left-to-right.
    model_config = ConfigDict(extra="forbid")

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
    # "The value must NOT match this schema" -- e.g. {"not": {"pattern": "^-"}} on a string field
    # means "anything except values starting with a dash." Raw dict, not further modeled: the
    # negated schema could itself be arbitrarily complex, same reasoning as the properties escape
    # hatch on SchemaDefinition below. Trailing underscore + alias, matching schema_/$schema --
    # `not` is a Python keyword and can't be a field name directly.
    not_: Optional[dict] = Field(default=None, alias="not")


PropertyDef.model_rebuild()


class IndexDef(BaseModel):
    name: Optional[str] = None
    fieldPath: str
    method: IndexMethod = "btree"


class SchemaDefinition(BaseModel):
    # extra="allow", not the default "ignore": no builder tool sets an unmodeled top-level
    # keyword today, but if one ever does (a future "import an existing schema" path, a new LLM
    # tool), the default "ignore" would silently drop it before it ever reaches the registry --
    # exactly the failure mode this project's whole "preview gap" feature exists to prevent, just
    # one layer lower (at ingestion, not at rendering). "allow" round-trips it byte-for-byte
    # instead, same reasoning as the properties/allOf/etc. raw-dict escape hatches below.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    schema_: str = Field(default="https://json-schema.org/draft/2020-12/schema", alias="$schema")
    type: Literal["object"] = "object"
    additionalProperties: bool = False
    # Union[PropertyDef, dict], not just PropertyDef: this is the escape hatch that makes "any
    # possible JSON Schema" real rather than aspirational -- a property using a construct
    # PropertyDef can't represent (oneOf/anyOf, no top-level "type") simply won't validate as a
    # PropertyDef (whose "type" field is required), so Pydantic's union falls back to storing it
    # as a raw dict, preserved byte-for-byte through to the real create-schema request.
    properties: dict[str, Union[PropertyDef, dict]] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    # Conditional rules ("if applicantType is Individual, then aadhaarNumber is required"),
    # stored as raw if/then dicts rather than modeled -- same reasoning as the properties escape
    # hatch above. Left as None (not an eager []) so a schema with no conditionals round-trips
    # with no "allOf" key at all, matching every real example seen so far.
    allOf: Optional[list[dict]] = None
    # {"fieldA": ["fieldB", "fieldC"]} -- "if fieldA is present at all, fieldB/fieldC become
    # required." Simple enough to model directly rather than as a raw passthrough.
    dependentRequired: Optional[dict[str, list[str]]] = None
    # {"fieldA": {"properties": {...}, "required": [...]}} -- dependentRequired's more general
    # cousin: presence of fieldA pulls in a whole extra sub-schema (new properties, not just new
    # required entries), not just "these already-declared fields become required." Raw dict
    # value, same escape-hatch reasoning as allOf.
    dependentSchemas: Optional[dict[str, dict]] = None
    # {pattern: schema} -- "any property whose *name* matches this regex must conform to this
    # schema," for open-ended/dynamic fields a fixed properties list can't express (e.g. "any
    # field starting with x- is a free-form string"). Raw dict values, same reasoning as allOf.
    patternProperties: Optional[dict[str, dict]] = None
    # Reusable sub-schema definitions, referenced from elsewhere in the same document via
    # {"$ref": "#/$defs/name"} -- lets one sub-shape (e.g. "Address") be defined once and reused
    # across multiple fields instead of duplicated. Only *internal* refs are resolved by this
    # prototype (see render.py) -- an external/cross-document $ref stays out of scope, same
    # reasoning as x-ref-schema above, and for the added reason that resolving one would require
    # a network fetch, breaking this project's offline-safety guarantee.
    defs_: Optional[dict[str, dict]] = Field(default=None, alias="$defs")


class SchemaRequest(BaseModel):
    # extra="allow", same reasoning as SchemaDefinition above -- x-ref-schema/webhook are real,
    # deliberately out-of-scope fields (see module docstring); "allow" means if either ever shows
    # up on a request this prototype didn't originate, it still round-trips instead of vanishing.
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    schemaCode: str
    definition: SchemaDefinition
    x_unique: Optional[list[list[str]]] = Field(default=None, alias="x-unique")
    x_indexes: Optional[list[IndexDef]] = Field(default=None, alias="x-indexes")


class DataRequest(BaseModel):
    version: Optional[int] = None  # required only when updating an existing record, not on create
    data: dict[str, Any]
