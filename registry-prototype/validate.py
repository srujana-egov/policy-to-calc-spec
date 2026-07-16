"""Deterministic completeness checks for a SchemaRequest -- no AI, mirroring
../workflow-prototype/validate.py's role: catch the same kinds of authoring mistakes a human
reviewer would, before a preview or a write would mean anything.
"""

from __future__ import annotations

import re

import jsonschema

from models import PropertyDef, SchemaRequest

# Allows a dot, confirmed necessary by a real schema code found in the wild:
# examples/pgr/pgr-schemas/pgr-service-category-schema.yaml uses "PGR.ServiceCategory". The
# original regex (no dots) would have rejected a schema code DIGIT itself ships as an example.
_SCHEMA_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def is_valid_schema_code(code: str) -> bool:
    """Shared with wizard.py's schema-name prompt, so a clearly-wrong name (typed by mistake, or
    by someone unfamiliar with the tool at a live demo) gets caught immediately at the point it's
    typed, with a chance to redo it -- instead of only surfacing much later, deep into drafting,
    as one line in a wall of validation errors."""
    return bool(code) and bool(_SCHEMA_CODE_RE.match(code))


def _validate_property(name: str, prop, path: str) -> list[str]:
    """Checks that apply to any property, one level deep or top-level -- called recursively for
    a nested object's own sub-properties. `prop` may be a raw dict rather than a PropertyDef --
    the "any possible JSON Schema" escape hatch for constructs PropertyDef can't represent
    (oneOf/anyOf, etc, see models.py) -- no PropertyDef-shaped checks apply to those, so skip
    rather than crash on attribute access a dict doesn't have."""
    errors = []
    label = f"{path}.{name}" if path else name

    if isinstance(prop, dict):
        return errors

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


def validate_json_schema_syntax(definition: dict) -> list[str]:
    """Checks that `definition` is itself a technically valid JSON Schema document -- the same
    class of check a JSON Schema validator's compile step (Ajv, in JavaScript; jsonschema here,
    the closest Python equivalent) performs: malformed regex, wrong keyword types, structurally
    illegal keyword combinations. This is a different, complementary check to
    validate_schema_request's referential-integrity checks below, not a replacement for them: a
    $ref pointing at a $defs name that doesn't exist is syntactically legal JSON Schema (this
    catches nothing there -- render.py's own resolution already handles that gracefully), and a
    stray non-standard key sitting in a property definition is syntactically legal too (the JSON
    Schema spec itself says unrecognized keywords are ignored, not rejected -- catching that kind
    of mistake needs the sanitization already built into SchemaBuilder, not a validator)."""
    errors = []
    try:
        jsonschema.Draft202012Validator.check_schema(definition)
    except jsonschema.exceptions.SchemaError as e:
        path = "/".join(str(p) for p in e.path) or "(top level)"
        errors.append(f"not a technically valid JSON Schema at {path}: {e.message}")
    return errors


def validate_schema_request(schema: SchemaRequest) -> list[str]:
    errors = []
    definition = schema.definition

    if not schema.schemaCode:
        errors.append("schemaCode is empty")
    elif not is_valid_schema_code(schema.schemaCode):
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

    for field, requires in (definition.dependentRequired or {}).items():
        if field not in field_names:
            errors.append(f"dependentRequired references '{field}', which is not a defined field")
        for req in requires:
            if req not in field_names:
                errors.append(f"dependentRequired['{field}'] references '{req}', which is not a defined field")

    for block in definition.allOf or []:
        for req in (block.get("then") or {}).get("required") or []:
            if req not in field_names:
                errors.append(f"a conditional rule's 'then' requires '{req}', which is not a defined field")

    for name, prop in definition.properties.items():
        errors.extend(_validate_property(name, prop, ""))

    return errors
