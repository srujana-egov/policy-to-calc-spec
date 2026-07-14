"""SchemaBuilder: the testable data-collection layer behind the wizard, mirroring
../workflow-prototype/builder.py's WorkflowBuilder -- one method per wizard question, driven
identically by the interactive CLI and by automated tests.

Auto-generates a camelCase field name from a human-typed label (collision-checked), the same
reasoning as slugify() in the workflow builder: nobody typing answers into a wizard should have to
invent a machine-safe identifier themselves.
"""

from __future__ import annotations

import re

from models import IndexDef, IndexMethod, PropertyDef, PropertyType, SchemaDefinition, SchemaRequest


def camel_field_name(text: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text.strip()) if w]
    if not words:
        return "field"
    first, *rest = words
    return first[0].lower() + first[1:] + "".join(w[:1].upper() + w[1:] for w in rest)


class SchemaBuilder:
    def __init__(self, schema_code: str):
        self.schema_code = schema_code
        self.properties: dict[str, PropertyDef] = {}
        self.required: list[str] = []
        self.unique_constraints: list[list[str]] = []
        self.indexes: list[IndexDef] = []

    def _dedupe_name(self, candidate: str) -> str:
        if candidate not in self.properties:
            return candidate
        i = 2
        while f"{candidate}{i}" in self.properties:
            i += 1
        return f"{candidate}{i}"

    def add_field(self, label: str, type: PropertyType, required: bool = False,
                  format: str | None = None, enum: list[str] | None = None,
                  description: str | None = None) -> str:
        """'What's the next field called, and what kind of value does it hold?' Returns the
        generated field name."""
        name = self._dedupe_name(camel_field_name(label))
        self.properties[name] = PropertyDef(type=type, format=format, enum=enum, description=description)
        if required:
            self.required.append(name)
        return name

    def remove_field(self, name: str) -> None:
        """Drops a field that turned out to be a mistake. Any unique constraint or index that
        referenced it becomes dangling -- validate.py already catches that, the same composable
        fix-it-then-revalidate loop as WorkflowBuilder.remove_state."""
        del self.properties[name]
        if name in self.required:
            self.required.remove(name)

    def add_unique_constraint(self, field_names: list[str]) -> None:
        """'Should any combination of fields be unique across every record?' e.g. a single field
        like a license number, or a compound key like (year, sequenceNumber)."""
        self.unique_constraints.append(field_names)

    def add_index(self, field_name: str, method: IndexMethod = "btree", name: str | None = None) -> None:
        """'Will this field be searched/filtered on often enough to need an index?'"""
        self.indexes.append(IndexDef(name=name, fieldPath=field_name, method=method))

    def build(self) -> SchemaRequest:
        definition = SchemaDefinition(
            properties=dict(self.properties),
            required=list(self.required),
            **{
                "x-unique": self.unique_constraints or None,
                "x-indexes": self.indexes or None,
            },
        )
        return SchemaRequest(schemaCode=self.schema_code, definition=definition)
