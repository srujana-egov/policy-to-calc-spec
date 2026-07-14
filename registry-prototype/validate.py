"""Deterministic completeness checks for a SchemaRequest -- no AI, mirroring
../workflow-prototype/validate.py's role: catch the same kinds of authoring mistakes a human
reviewer would, before a preview or a write would mean anything.
"""

from __future__ import annotations

import re

from models import SchemaRequest

_SCHEMA_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def validate_schema_request(schema: SchemaRequest) -> list[str]:
    errors = []
    definition = schema.definition

    if not schema.schemaCode:
        errors.append("schemaCode is empty")
    elif not _SCHEMA_CODE_RE.match(schema.schemaCode):
        errors.append(f"schemaCode '{schema.schemaCode}' should start with a letter and contain "
                       "only letters, numbers, '-' or '_' -- no spaces")

    if not definition.properties:
        errors.append("no fields defined -- a schema needs at least one")

    field_names = set(definition.properties.keys())

    for req in definition.required:
        if req not in field_names:
            errors.append(f"'{req}' is listed as required but is not a defined field")

    for constraint in definition.x_unique or []:
        for field in constraint:
            if field not in field_names:
                errors.append(f"unique constraint references '{field}', which is not a defined field")

    for index in definition.x_indexes or []:
        if index.fieldPath not in field_names:
            errors.append(f"index references '{index.fieldPath}', which is not a defined field")

    for name, prop in definition.properties.items():
        if prop.enum and prop.type not in ("string", "integer", "number"):
            errors.append(f"'{name}' has an enum but type '{prop.type}' -- enum values only make "
                           "sense for string/integer/number fields")

    return errors
