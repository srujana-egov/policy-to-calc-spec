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
"""

from typing import Any, Literal, Optional

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
