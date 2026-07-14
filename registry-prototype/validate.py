"""Deterministic completeness checks for a SchemaRequest -- no AI, mirroring
../workflow-prototype/validate.py's role: catch the same kinds of authoring mistakes a human
reviewer would, before a preview or a write would mean anything.
"""

from __future__ import annotations

import re

from models import PropertyDef, SchemaRequest

# Allows a dot, confirmed necessary by a real schema code found in the wild:
# examples/pgr/pgr-schemas/pgr-service-category-schema.yaml uses "PGR.ServiceCategory". The
# original regex (no dots) would have rejected a schema code DIGIT itself ships as an example.
_SCHEMA_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def _validate_property(name: str, prop: PropertyDef, path: str) -> list[str]:
    """Checks that apply to any property, one level deep or top-level -- called recursively for
    a nested object's own sub-properties."""
    errors = []
    label = f"{path}.{name}" if path else name

    if prop.enum and prop.type not in ("string", "integer", "number"):
        errors.append(f"'{label}' has an enum but type '{prop.type}' -- enum values only make "
                       "sense for string/integer/number fields")
    if prop.pattern is not None and prop.type != "string":
        errors.append(f"'{label}' has a pattern but type '{prop.type}' -- patterns only make "
                       "sense for text fields")
    if (prop.minimum is not None or prop.maximum is not None) and prop.type not in ("integer", "number"):
        errors.append(f"'{label}' has a minimum/maximum but type '{prop.type}' -- those only make "
                       "sense for number fields")
    if prop.minimum is not None and prop.maximum is not None and prop.minimum > prop.maximum:
        errors.append(f"'{label}' has minimum ({prop.minimum}) greater than maximum ({prop.maximum})")
    if (prop.minLength is not None or prop.maxLength is not None) and prop.type != "string":
        errors.append(f"'{label}' has a minLength/maxLength but type '{prop.type}' -- those only "
                       "make sense for text fields")
    if prop.minLength is not None and prop.maxLength is not None and prop.minLength > prop.maxLength:
        errors.append(f"'{label}' has minLength ({prop.minLength}) greater than maxLength ({prop.maxLength})")

    if prop.properties or prop.required:
        if prop.type != "object":
            errors.append(f"'{label}' has sub-fields but type '{prop.type}' -- sub-fields only "
                           "make sense for a group-of-fields (object) type")
        sub_names = set((prop.properties or {}).keys())
        for req in prop.required or []:
            if req not in sub_names:
                errors.append(f"'{req}' is listed as required under '{label}' but is not one of its sub-fields")
        for sub_name, sub_prop in (prop.properties or {}).items():
            errors.extend(_validate_property(sub_name, sub_prop, label))

    return errors


def validate_schema_request(schema: SchemaRequest) -> list[str]:
    errors = []
    definition = schema.definition

    if not schema.schemaCode:
        errors.append("schemaCode is empty")
    elif not _SCHEMA_CODE_RE.match(schema.schemaCode):
        errors.append(f"schemaCode '{schema.schemaCode}' should start with a letter and contain "
                       "only letters, numbers, '.', '-' or '_' -- no spaces")

    if not definition.properties:
        errors.append("no fields defined -- a schema needs at least one")

    field_names = set(definition.properties.keys())

    for req in definition.required:
        if req not in field_names:
            errors.append(f"'{req}' is listed as required but is not a defined field")

    for constraint in schema.x_unique or []:
        for field in constraint:
            if field not in field_names:
                errors.append(f"unique constraint references '{field}', which is not a defined field")

    for index in schema.x_indexes or []:
        if index.fieldPath not in field_names:
            errors.append(f"index references '{index.fieldPath}', which is not a defined field")

    for name, prop in definition.properties.items():
        errors.extend(_validate_property(name, prop, ""))

    return errors
