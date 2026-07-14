"""Pydantic models matching the real DIGIT Registry service contract -- verified against actual
Go source (`internal/models/models.go`) in digitnxt/digit3, not just swagger.yaml, the same way
../workflow-prototype/models.py was checked against the real workflow service.

Two real discrepancies found during that verification, both worth remembering here:
- The real API is mounted at `/registry/v3/...` -- swagger.yaml and README both say `/registry/v1`.
- The real auth header the middleware reads is `X-User-Id` -- swagger.yaml, the README, and even
  the project's own Postman collection all send `X-Client-Id`, which is silently ignored (you get
  a 400 "X-User-Id header is required"). write_schema()/write_records() in wizard.py send the
  header that actually works, not the documented one.

`x-ref-schema` (cross-schema references) and `webhook` are real fields on the schema definition
but out of scope here, same reasoning as EscalationConfig being out of scope for the workflow
wizard -- this prototype covers property/constraint/index authoring and record entry, not
cross-schema linking or webhook wiring.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PropertyType = Literal["string", "integer", "number", "boolean", "array", "object"]
IndexMethod = Literal["btree", "gin"]


class PropertyDef(BaseModel):
    type: PropertyType
    format: str | None = None
    enum: list[str] | None = None
    description: str | None = None


class IndexDef(BaseModel):
    name: str | None = None
    fieldPath: str
    method: IndexMethod = "btree"


class SchemaDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="https://json-schema.org/draft/2020-12/schema", alias="$schema")
    type: Literal["object"] = "object"
    additionalProperties: bool = False
    properties: dict[str, PropertyDef] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    x_unique: list[list[str]] | None = Field(default=None, alias="x-unique")
    x_indexes: list[IndexDef] | None = Field(default=None, alias="x-indexes")


class SchemaRequest(BaseModel):
    schemaCode: str
    definition: SchemaDefinition


class DataRequest(BaseModel):
    version: int | None = None  # required only when updating an existing record, not on create
    data: dict[str, Any]
