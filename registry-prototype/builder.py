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


def _dedupe_name_in(properties: dict, candidate: str) -> str:
    if candidate not in properties:
        return candidate
    i = 2
    while f"{candidate}{i}" in properties:
        i += 1
    return f"{candidate}{i}"


# What's actually legal inside a JSON Schema property fragment. A real gap found via a live LLM
# run: the model, used to add_field's own required/required_stated/details_stated arguments,
# sometimes carries those same keys into a oneOf alternative's sub-property definitions too --
# this whitelist strips anything that isn't real JSON Schema vocabulary before it can land in the
# schema that gets rendered and ultimately sent to the registry service. Shared by every builder
# method that accepts a raw dict of named sub-properties from a caller (an LLM, in practice) --
# oneOf alternatives, dependentSchemas' extra properties -- not just oneOf.
_RAW_PROPERTY_ALLOWED_KEYS = {
    "type", "format", "enum", "description", "pattern", "minimum", "maximum",
    "minLength", "maxLength", "properties", "required", "oneOf", "anyOf",
}


def _sanitize_raw_property(prop_def: dict) -> dict:
    cleaned = {k: v for k, v in prop_def.items() if k in _RAW_PROPERTY_ALLOWED_KEYS}
    # "required" only makes real JSON Schema sense as a list, alongside this fragment's own
    # "properties" (i.e. it describes an object type's required sub-fields). A bare boolean here
    # is add_field's own required=True/False bookkeeping leaking into a leaf property, not a
    # legitimate use of the keyword.
    if "required" in cleaned and ("properties" not in cleaned or not isinstance(cleaned["required"], list)):
        del cleaned["required"]
    return cleaned


def _normalize_named_properties(properties: dict, required: list | None) -> tuple[dict, list]:
    """Runs a dict of {name: property-fragment} through camel_field_name() and
    _sanitize_raw_property(), renaming `required` to match -- the same normalization
    add_field/add_nested_field give every field, applied here to a raw dict of sub-properties
    handed over whole (a oneOf alternative, a dependentSchemas addition) so naming and content
    stay consistent regardless of entry point."""
    renamed: dict = {}
    rename_map = {}
    for prop_name, prop_def in (properties or {}).items():
        new_name = _dedupe_name_in(renamed, camel_field_name(prop_name))
        rename_map[prop_name] = new_name
        renamed[new_name] = _sanitize_raw_property(prop_def) if isinstance(prop_def, dict) else prop_def
    new_required = [rename_map.get(r, r) for r in (required or [])]
    return renamed, new_required


class SchemaBuilder:
    def __init__(self, schema_code: str):
        self.schema_code = schema_code
        self.properties: dict[str, PropertyDef | dict] = {}
        self.required: list[str] = []
        self.unique_constraints: list[list[str]] = []
        self.indexes: list[IndexDef] = []
        self.conditionals: list[dict] = []
        self.dependent_required: dict[str, list[str]] = {}
        self.dependent_schemas: dict[str, dict] = {}
        self.pattern_properties: dict[str, dict] = {}
        self.defs: dict[str, dict] = {}

    @staticmethod
    def _dedupe_name_in(properties: dict, candidate: str) -> str:
        return _dedupe_name_in(properties, candidate)

    def _dedupe_name(self, candidate: str) -> str:
        return _dedupe_name_in(self.properties, candidate)

    def add_field(self, label: str, type: PropertyType, required: bool = False,
                  format: str | None = None, enum: list[str] | None = None,
                  description: str | None = None, pattern: str | None = None,
                  minimum: float | None = None, maximum: float | None = None,
                  minLength: int | None = None, maxLength: int | None = None) -> str:
        """'What's the next field called, and what kind of value does it hold?' Returns the
        generated field name."""
        name = self._dedupe_name(camel_field_name(label))
        self.properties[name] = PropertyDef(type=type, format=format, enum=enum, description=description,
                                             pattern=pattern, minimum=minimum, maximum=maximum,
                                             minLength=minLength, maxLength=maxLength)
        if required:
            self.required.append(name)
        return name

    def add_nested_field(self, parent_name: str, label: str, type: PropertyType, required: bool = False,
                          format: str | None = None, enum: list[str] | None = None,
                          description: str | None = None, pattern: str | None = None,
                          minimum: float | None = None, maximum: float | None = None,
                          minLength: int | None = None, maxLength: int | None = None) -> str:
        """'What fields does this group contain?' -- one level of nesting under an object-type
        field (e.g. an 'address' field's own 'city'/'pincode' sub-fields). Confirmed as a real,
        common pattern (not a hypothetical) by three independent real schemas found across the
        digitnxt org -- see models.py's docstring. Returns the generated sub-field name."""
        parent = self.properties[parent_name]
        if parent.type != "object":
            raise ValueError(f"'{parent_name}' isn't a group-of-fields (object) type -- can't add sub-fields to it")
        if parent.properties is None:
            parent.properties = {}
        name = self._dedupe_name_in(parent.properties, camel_field_name(label))
        parent.properties[name] = PropertyDef(type=type, format=format, enum=enum, description=description,
                                               pattern=pattern, minimum=minimum, maximum=maximum,
                                               minLength=minLength, maxLength=maxLength)
        if required:
            # Left as None (not an eager []) until something's actually required, so a group
            # with no required sub-fields serializes with no "required" key at all, matching
            # every real example -- an empty list would otherwise round-trip as "required": [],
            # which no real schema actually writes.
            if parent.required is None:
                parent.required = []
            parent.required.append(name)
        return name

    def add_one_of_field(self, label: str, alternatives: list[dict], description: str | None = None) -> str:
        """'This field must be one of several alternative shapes' (e.g. provide either an email
        or a phone number, not something free-form) -- the single most common real-world
        oneOf/anyOf shape. Stored as a raw dict, not a PropertyDef: oneOf has no single top-level
        "type", which PropertyDef requires, so this bypasses it entirely rather than force-fitting
        an unrepresentable shape into it -- the same "any possible JSON Schema" escape hatch
        models.py's SchemaDefinition.properties now allows. Returns the generated field name.

        Each alternative's own property names are run through camel_field_name(), same as
        add_field/add_nested_field -- a caller (an LLM, in practice) handing over human-readable
        labels like "Email Address" as raw dict keys would otherwise land in the schema verbatim,
        the one place naming wouldn't match the rest of the schema's convention."""
        name = self._dedupe_name(camel_field_name(label))
        normalized_alternatives = []
        for alt in alternatives:
            renamed, required = _normalize_named_properties(alt.get("properties", {}), alt.get("required"))
            normalized_alt = dict(alt)
            normalized_alt["properties"] = renamed
            if required:
                normalized_alt["required"] = required
            else:
                normalized_alt.pop("required", None)
            normalized_alternatives.append(normalized_alt)
        prop: dict = {"oneOf": normalized_alternatives}
        if description:
            prop["description"] = description
        self.properties[name] = prop
        return name

    def add_raw_property(self, label: str, raw_schema: dict, required: bool = False) -> str:
        """Escape hatch for JSON Schema constructs none of the other tools cover -- prefixItems,
        contains, propertyNames, unevaluatedProperties, $dynamicRef, or any combination of these
        this project hasn't built a dedicated tool for. Accepts an arbitrary schema fragment
        verbatim, the same "any possible JSON Schema" passthrough models.py's
        SchemaDefinition.properties already permits structurally -- this just gives the drafting
        LLM a way to *reach* it instead of being limited to the specific tools that exist today.
        render.py already renders whatever real constructs happen to be inside the fragment
        interactively (it inspects the dict's own keys, not which tool produced it) and falls back
        to a labeled raw-JSON block only for what it genuinely can't visualize yet -- adding this
        tool requires no render-layer changes.

        If the fragment has its own "properties" (an object shape), those get the same
        normalization as a oneOf alternative's (camelCase names, JSON-Schema-only keys) -- same
        leak risk as any other raw dict handed over whole by a caller (an LLM, in practice)."""
        name = self._dedupe_name(camel_field_name(label))
        schema = dict(raw_schema)
        if isinstance(schema.get("properties"), dict):
            renamed, req = _normalize_named_properties(schema["properties"], schema.get("required"))
            schema["properties"] = renamed
            if req:
                schema["required"] = req
            else:
                schema.pop("required", None)
        self.properties[name] = schema
        if required:
            self.required.append(name)
        return name

    def add_conditional(self, if_field: str, if_value, then_required: list[str] | None = None) -> None:
        """'If <if_field> equals <if_value>, then these other fields become required' -- the
        exact shape the architect flagged as unsupported ("field B is only required when field A
        equals X"). Stored as a raw if/then dict at the schema's top level (JSON Schema's own way
        of expressing this), validated against the fields that exist so far, the same guarding
        every other builder method already does (e.g. add_nested_field rejecting a non-object
        parent)."""
        if if_field not in self.properties:
            raise ValueError(f"'{if_field}' isn't a known field -- add it first")
        for name in (then_required or []):
            if name not in self.properties:
                raise ValueError(f"'{name}' isn't a known field -- add it first")
        self.conditionals.append({
            "if": {"properties": {if_field: {"const": if_value}}, "required": [if_field]},
            "then": {"required": list(then_required or [])},
        })

    def add_dependent_required(self, field: str, requires: list[str]) -> None:
        """'If <field> is filled in at all, these other fields become required too' -- simpler
        than add_conditional since there's no specific triggering value, just presence."""
        if field not in self.properties:
            raise ValueError(f"'{field}' isn't a known field -- add it first")
        for name in requires:
            if name not in self.properties:
                raise ValueError(f"'{name}' isn't a known field -- add it first")
        self.dependent_required[field] = list(requires)

    def add_dependent_schema(self, trigger_field: str, properties: dict, required: list[str] | None = None) -> None:
        """dependentRequired's more general cousin: 'if <trigger_field> is filled in at all,
        these *new* fields (not just already-declared ones) become part of the record and some
        of them required' -- e.g. filling in a credit card number pulls in a whole extra
        CVV/expiry sub-shape, not fields that already existed as optional. `properties` is a raw
        dict of {name: property-fragment}, normalized the same way a oneOf alternative's
        properties are (camelCase names, JSON-Schema-only keys) since a caller (an LLM) hands
        these over the same way."""
        if trigger_field not in self.properties:
            raise ValueError(f"'{trigger_field}' isn't a known field -- add it first")
        renamed, renamed_required = _normalize_named_properties(properties, required)
        self.dependent_schemas[trigger_field] = {"properties": renamed, "required": renamed_required}

    def add_pattern_properties(self, pattern: str, value_schema: dict) -> None:
        """'Any field whose *name* matches this pattern must hold this kind of value' -- for
        open-ended/dynamic fields a fixed properties list can't express (e.g. 'any field starting
        with x- is a free-form string the user can add as many of as they want'). Schema-level,
        not a named field -- there's no single generated field name to return, since the whole
        point is the property names aren't known in advance."""
        if not pattern:
            raise ValueError("pattern can't be empty")
        self.pattern_properties[pattern] = value_schema

    def define_reusable_schema(self, name: str, schema: dict) -> None:
        """Defines a reusable sub-shape once (e.g. "Address"), referenced elsewhere via
        add_ref_field -- so a shape used in multiple places doesn't have to be duplicated field by
        field each time. Stored under $defs, JSON Schema's own mechanism for this.

        If `schema` has its own "properties" (the common case -- an object shape), those are
        normalized the same way a oneOf alternative's or dependentSchemas' properties are
        (camelCase names, JSON-Schema-only keys) -- a caller (an LLM) handing this over the same
        way it hands over any other named-sub-properties dict, same leak risk otherwise."""
        if not name:
            raise ValueError("a reusable schema needs a name")
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            renamed, required = _normalize_named_properties(schema.get("properties"), schema.get("required"))
            schema = dict(schema)
            schema["properties"] = renamed
            if required:
                schema["required"] = required
            else:
                schema.pop("required", None)
        self.defs[name] = schema

    def add_ref_field(self, label: str, defs_name: str, required: bool = False) -> str:
        """Adds a field whose shape is 'whatever define_reusable_schema(defs_name, ...) defined' --
        an internal $ref, resolved and rendered inline by render.py. Validated against $defs
        entries that exist so far, the same guarding every other builder method already does."""
        if defs_name not in self.defs:
            raise ValueError(f"'{defs_name}' isn't a defined reusable schema -- "
                              "call define_reusable_schema first")
        name = self._dedupe_name(camel_field_name(label))
        self.properties[name] = {"$ref": f"#/$defs/{defs_name}"}
        if required:
            self.required.append(name)
        return name

    def add_not_constraint(self, field_name: str, not_schema: dict) -> None:
        """'The value must NOT match this' -- e.g. a string field that must not start with a
        dash, or must not be one of a banned list of values. Mutates an existing field (added via
        add_field/add_nested_field first) rather than being its own field-adding method, since
        `not` is an additional constraint on a field that already has its own type, not a shape
        of its own."""
        if field_name not in self.properties:
            raise ValueError(f"'{field_name}' isn't a known field -- add it first")
        prop = self.properties[field_name]
        if isinstance(prop, dict):
            prop["not"] = not_schema
        else:
            prop.not_ = not_schema

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
            properties=dict(self.properties), required=list(self.required),
            allOf=list(self.conditionals) or None,
            dependentRequired=dict(self.dependent_required) or None,
            dependentSchemas=dict(self.dependent_schemas) or None,
            patternProperties=dict(self.pattern_properties) or None,
            defs_=dict(self.defs) or None,
        )
        return SchemaRequest(
            schemaCode=self.schema_code,
            definition=definition,
            **{
                "x-unique": self.unique_constraints or None,
                "x-indexes": self.indexes or None,
            },
        )
