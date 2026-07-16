"""Drafts a registry schema from a business user's free-text description, via an OpenAI
tool-calling loop that calls the *same* deterministic SchemaBuilder methods the guided wizard
uses -- add_field, add_nested_field, add_unique_constraint, add_index -- rather than having the
model freely generate a raw JSON Schema document. This is a deliberate architecture choice: the
riskiest constructs (a group's sub-structure, required-ness, constraints) are exactly where a
free-generation approach is most likely to produce subtly wrong nested JSON, and each of these
tools already validates its own inputs (e.g. add_nested_field rejects a parent that isn't an
object-type field), so a mistake surfaces immediately as a tool error the model can react to,
instead of silently reaching the rendered preview.

The one new idea beyond wrapping existing methods: every field-adding call also reports two
confidence flags -- required_stated and details_stated -- so the caller knows which parts of the
draft were genuinely stated by the user versus guessed by the model. That confidence map is what
lets wizard.py ask a short, *targeted* follow-up only about the highest-value gap (required-ness
left unstated) and visually flag the rest in the rendered preview, rather than either trusting a
vague description blindly or re-asking a full fixed question set for every field regardless of
how much the user already said.
"""

from __future__ import annotations

import datetime
import json
import os

from builder import SchemaBuilder

_SYSTEM_PROMPT = """You are helping a business user turn a plain-language description of a data \
entry form into a structured schema, by calling the tools provided. You do not write JSON \
directly -- you call add_field / add_nested_field / add_unique_constraint / add_index to build \
the schema up step by step, the same way a human would answer a guided wizard's questions, one \
field at a time.

For every field you add, set required_stated=true ONLY if the user explicitly said whether that \
field is required or optional. Otherwise pick a sensible default (usually required for something \
clearly essential to the record, optional otherwise) and set required_stated=false -- do not \
guess at required_stated=true just because you're confident in your own guess.

Set details_stated=true only if the type, format, and any constraints (pattern, min/max, enum \
choices) are clearly implied by what the user described. Set details_stated=false if you are \
filling in a reasonable default without clear evidence -- for example, if the user mentions "an \
address" with no further detail, you may add it as a single string field, but mark \
details_stated=false, since a real address might need city/pincode sub-fields the user simply \
didn't spell out.

Use add_nested_field only for sub-fields of a field you already added with type "object" via \
add_field. Use add_unique_constraint / add_index only when the user's description implies a real \
need for them (e.g. "each license number must be unique", "search by status") -- do not add them \
speculatively.

Several more tools handle rules a simple flat field list can't express -- use them whenever the \
user's description implies one of these shapes, don't force everything into plain required/optional \
fields when it doesn't fit:
- add_conditional: "field B is only required when field A equals some specific value" (e.g. an \
  Aadhaar number that's only required when applicant type is 'Individual', not 'Company').
- add_dependent_required: "if field A is filled in at all, field B becomes required too" -- no \
  specific triggering value, just presence (e.g. if a credit card number is given, its CVV becomes \
  required).
- add_dependent_schema: like add_dependent_required, but for pulling in *entirely new* fields (not \
  just making an existing field required) once a trigger field is filled in -- e.g. filling in a \
  credit card number should reveal its own CVV and expiry-date fields, which don't otherwise exist \
  on the form at all.
- add_one_of_field: "provide exactly one of these alternative ways" (e.g. either an email or a \
  phone number as the contact method, not a free mix of both, and not two separate optional fields \
  that don't clearly express the choice).
- add_pattern_properties: "the user can add any number of custom fields whose *name* follows a \
  pattern" (e.g. "let them add any field starting with x- holding free text") -- use only when the \
  user genuinely describes open-ended/dynamic fields, not for a fixed, known set of fields (those \
  are just add_field calls).
- define_reusable_schema + add_ref_field: whenever the SAME sub-shape (e.g. "an address with city, \
  pincode, and GPS coordinates") is described for TWO OR MORE fields, or whenever the user says \
  two fields "have the same fields," "look the same," or "use the same structure" -- you MUST \
  define it ONCE with define_reusable_schema and reference it with add_ref_field for EACH field \
  that needs it. Do not call add_nested_field or add_raw_property more than once to duplicate the \
  same sub-fields under two different field names -- that produces two independent copies that can \
  drift apart, exactly what this pair of tools exists to avoid.
- Nesting more than one level deep (e.g. an address that itself contains a GPS coordinates group \
  with its own latitude/longitude sub-fields): add_nested_field only supports ONE level under a \
  top-level object field. For anything deeper, write the WHOLE multi-level shape out at once, \
  either as the `schema` argument to define_reusable_schema (if it's also reused elsewhere) or as \
  the `raw_schema` argument to add_raw_property (if used only once) -- e.g. {"type": "object", \
  "properties": {"city": {"type": "string"}, "geoLocation": {"type": "object", "properties": \
  {"latitude": {"type": "number"}, "longitude": {"type": "number"}}, "required": ["latitude", \
  "longitude"]}}, "required": ["city"]}. Nest as many levels as the description actually implies --
  never flatten a described sub-group into loose top-level fields just because add_nested_field \
  itself only goes one level.
- "At least one item in a list must satisfy some condition" (e.g. "at least one uploaded document \
  must be marked approved before the application is accepted," "at least one attached photo must \
  be tagged as the primary photo"): use add_raw_property for the WHOLE list field directly (never \
  add_field first, then add_raw_property second for the same field -- that creates two separate, \
  orphaned fields instead of one), with a schema like {"type": "array", "items": {"type": "object", \
  "properties": {...each item's own fields...}}, "contains": {"properties": {"status": {"const": \
  "APPROVED"}}}, "minContains": 1} -- "contains" describes the condition at least one array entry \
  must satisfy, "minContains" how many entries must satisfy it (usually 1). Do NOT invent a \
  separate boolean field (like "hasApprovedDocument") as a substitute -- the condition belongs on \
  the array itself, so it travels with the actual document data instead of a disconnected flag.
- add_not_constraint: "the value must NOT be/match X" -- a banned exact value, or a pattern the \
  value must avoid (e.g. a username that must not be exactly 'admin'). Call this on a field \
  already added via add_field.
- add_raw_property: also the general escape hatch for any other genuinely exotic construct none \
  of the tools above cover (a fixed-order list of specific types via "prefixItems," "only field \
  names matching this pattern" via "propertyNames," etc). Prefer the specific tools above whenever \
  they apply -- they're validated more precisely -- but don't hesitate to reach for add_raw_property \
  whenever the user's description implies a real JSON Schema shape none of the named tools cover;
  it accepts any valid fragment, including nested "properties," "oneOf"/"anyOf," or any combination.

When you've captured everything the user described, stop calling tools and reply with a short \
plain-text summary of what you built (no more tool calls)."""

_FIELD_PROPS = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Human-readable field name, e.g. 'License Number'"},
        "type": {"type": "string", "enum": ["string", "integer", "number", "boolean", "object"]},
        "required": {"type": "boolean"},
        "required_stated": {"type": "boolean"},
        "details_stated": {"type": "boolean"},
        "format": {"type": "string", "description": "e.g. 'date' for a date-valued string field. Omit if not applicable."},
        "enum": {"type": "array", "items": {"type": "string"}, "description": "Fixed list of allowed values. Omit if not applicable."},
        "description": {"type": "string"},
        "pattern": {"type": "string", "description": "A regex the value must match, e.g. '^[0-9]{6}$' for a 6-digit pincode."},
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "minLength": {"type": "integer"},
        "maxLength": {"type": "integer"},
    },
    "required": ["label", "type", "required", "required_stated", "details_stated"],
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_field",
            "description": "Add a new top-level field to the schema being drafted.",
            "parameters": _FIELD_PROPS,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_nested_field",
            "description": "Add a sub-field inside an existing top-level field of type 'object'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_name": {"type": "string", "description": "The generated field name of the parent object field, returned by an earlier add_field call."},
                    **_FIELD_PROPS["properties"],
                },
                "required": ["parent_name", *_FIELD_PROPS["required"]],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_unique_constraint",
            "description": "Mark one field, or a combination of fields, as required to be unique across every record.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_names": {"type": "array", "items": {"type": "string"},
                                    "description": "Generated field name(s) returned by earlier add_field/add_nested_field calls."},
                },
                "required": ["field_names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_index",
            "description": "Mark a field as needing an index for fast search/filtering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "method": {"type": "string", "enum": ["btree", "gin"],
                               "description": "'gin' for searching within text (like a search box); 'btree' for exact match/sort."},
                    "name": {"type": "string", "description": "Optional index name."},
                },
                "required": ["field_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_conditional",
            "description": "'If <if_field> equals <if_value>, then these other fields become required.' "
                            "Both if_field and every entry in then_required must already exist "
                            "(added via add_field/add_nested_field first).",
            "parameters": {
                "type": "object",
                "properties": {
                    "if_field": {"type": "string", "description": "Generated name of the triggering top-level field."},
                    "if_value": {"description": "The value that triggers the condition (string, number, or boolean)."},
                    "then_required": {"type": "array", "items": {"type": "string"},
                                       "description": "Generated field names that become required when the condition holds."},
                },
                "required": ["if_field", "if_value", "then_required"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_dependent_required",
            "description": "'If <field> is filled in at all, these other fields become required too' -- "
                            "no specific triggering value, just presence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "requires": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["field", "requires"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_one_of_field",
            "description": "Add a field that must match exactly one of several alternative shapes -- "
                            "e.g. 'provide either an email or a phone number,' not a free mix of both.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "alternatives": {
                        "type": "array",
                        "description": "Each alternative is a small object schema fragment: "
                                       "{\"properties\": {sub-field name -> {\"type\": ..., \"format\": ..., "
                                       "\"pattern\": ..., \"enum\": [...]}}, \"required\": [sub-field names]}. "
                                       "Each sub-field's own definition must contain ONLY real JSON Schema "
                                       "keywords (type/format/pattern/enum/minimum/maximum/etc) -- do NOT "
                                       "include required/required_stated/details_stated inside a sub-field's "
                                       "definition; those only belong on add_field/add_nested_field's own "
                                       "arguments, never inside a nested property object.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "properties": {"type": "object"},
                                "required": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["properties"],
                        },
                    },
                },
                "required": ["label", "alternatives"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_dependent_schema",
            "description": "'If <trigger_field> is filled in at all, these *new* fields become part of "
                            "the form (not just already-declared ones becoming required).' "
                            "trigger_field must already exist (added via add_field first).",
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_field": {"type": "string"},
                    "properties": {
                        "type": "object",
                        "description": "{new sub-field name -> {\"type\": ..., \"pattern\": ..., ...}}. "
                                       "Only real JSON Schema keywords inside each sub-field's definition, "
                                       "same rule as add_one_of_field's alternatives.",
                    },
                    "required": {"type": "array", "items": {"type": "string"},
                                 "description": "Which of the new sub-field names become required once triggered."},
                },
                "required": ["trigger_field", "properties"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_pattern_properties",
            "description": "'Let the user add any number of custom fields whose *name* matches this "
                            "pattern, each holding this kind of value.' Use only for genuinely "
                            "open-ended/dynamic fields the user describes, not a fixed known set.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex the custom field's name must match, e.g. '^x-'."},
                    "value_schema": {"type": "object",
                                     "description": "{\"type\": ..., \"pattern\": ..., ...} -- the shape "
                                                     "every matching custom field's value must have."},
                },
                "required": ["pattern", "value_schema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "define_reusable_schema",
            "description": "Defines a reusable sub-shape once (e.g. 'Address'), to be referenced from "
                            "multiple fields via add_ref_field instead of repeating it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "e.g. 'Address'"},
                    "schema": {"type": "object",
                               "description": "A full schema fragment, e.g. {\"type\": \"object\", "
                                              "\"properties\": {...}, \"required\": [...]}. Each "
                                              "sub-field inside \"properties\" must contain ONLY real "
                                              "JSON Schema keywords (type/format/pattern/enum/etc) -- "
                                              "do NOT include required/required_stated/details_stated "
                                              "inside a sub-field's own definition."},
                },
                "required": ["name", "schema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_ref_field",
            "description": "Adds a field whose shape is a reusable schema defined earlier via "
                            "define_reusable_schema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "defs_name": {"type": "string", "description": "The name passed to define_reusable_schema."},
                    "required": {"type": "boolean"},
                },
                "required": ["label", "defs_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_not_constraint",
            "description": "'The value must NOT be/match this' -- a banned exact value or a pattern to "
                            "avoid. field_name must already exist (added via add_field first).",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "not_pattern": {"type": "string", "description": "A regex the value must NOT match. Omit if not applicable."},
                    "not_const": {"description": "An exact value the field must NOT be. Omit if not applicable."},
                    "not_enum": {"type": "array", "items": {}, "description": "A list of values the field must NOT be any of. Omit if not applicable."},
                },
                "required": ["field_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_raw_property",
            "description": "Escape hatch for a JSON Schema construct none of the other tools cover "
                            "(prefixItems, contains, propertyNames, unevaluatedProperties, "
                            "$dynamicRef, or a combination of these). Use this ONLY when the user's "
                            "description genuinely needs one of these and no other tool fits -- "
                            "prefer the specific tools (add_conditional, add_one_of_field, etc.) "
                            "whenever they apply, since those are validated more precisely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "raw_schema": {"type": "object",
                                   "description": "A complete JSON Schema fragment for this field, "
                                                  "e.g. {\"type\": \"array\", \"prefixItems\": [...]}. "
                                                  "Only real JSON Schema keywords -- do NOT include "
                                                  "required/required_stated/details_stated inside it."},
                    "required": {"type": "boolean"},
                },
                "required": ["label", "raw_schema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_field",
            "description": "Remove an existing top-level field that shouldn't be part of the schema "
                            "after all -- e.g. the user said a field isn't needed anymore.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string",
                                   "description": "The generated field name to remove, e.g. 'billingAddress'."},
                },
                "required": ["field_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_required",
            "description": "Change whether an existing top-level field is required on every record, "
                            "WITHOUT recreating it -- use this instead of removing and re-adding a "
                            "field just to flip its required/optional status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["field_name", "required"],
            },
        },
    },
]


def _record_confidence(confidence: dict, field_id: str, args: dict) -> None:
    confidence[field_id] = {
        "required_stated": bool(args.get("required_stated", False)),
        "details_stated": bool(args.get("details_stated", False)),
    }


def _execute_tool_call(builder: SchemaBuilder, confidence: dict, name: str, args: dict) -> dict:
    try:
        if name == "add_field":
            field_name = builder.add_field(
                args["label"], args["type"], required=args.get("required", False),
                format=args.get("format"), enum=args.get("enum"), description=args.get("description"),
                pattern=args.get("pattern"), minimum=args.get("minimum"), maximum=args.get("maximum"),
                minLength=args.get("minLength"), maxLength=args.get("maxLength"))
            _record_confidence(confidence, field_name, args)
            return {"field_name": field_name}

        if name == "add_nested_field":
            parent_name = args["parent_name"]
            if parent_name not in builder.properties:
                return {"error": f"'{parent_name}' isn't a known field on this schema -- "
                                  "add it with add_field first"}
            sub_name = builder.add_nested_field(
                parent_name, args["label"], args["type"], required=args.get("required", False),
                format=args.get("format"), enum=args.get("enum"), description=args.get("description"),
                pattern=args.get("pattern"), minimum=args.get("minimum"), maximum=args.get("maximum"),
                minLength=args.get("minLength"), maxLength=args.get("maxLength"))
            field_id = f"{parent_name}.{sub_name}"
            _record_confidence(confidence, field_id, args)
            return {"field_name": sub_name, "field_id": field_id}

        if name == "add_unique_constraint":
            fields = args["field_names"]
            unknown = [f for f in fields if f not in builder.properties]
            if unknown:
                return {"error": f"unknown field(s), not added by add_field yet: {unknown}"}
            builder.add_unique_constraint(fields)
            return {"ok": True}

        if name == "add_index":
            field = args["field_name"]
            if field not in builder.properties:
                return {"error": f"'{field}' isn't a known field on this schema"}
            builder.add_index(field, method=args.get("method", "btree"), name=args.get("name"))
            return {"ok": True}

        if name == "add_conditional":
            builder.add_conditional(args["if_field"], args["if_value"],
                                     then_required=args.get("then_required"))
            return {"ok": True}

        if name == "add_dependent_required":
            builder.add_dependent_required(args["field"], args["requires"])
            return {"ok": True}

        if name == "add_one_of_field":
            field_name = builder.add_one_of_field(args["label"], args["alternatives"],
                                                   description=args.get("description"))
            return {"field_name": field_name}

        if name == "add_dependent_schema":
            builder.add_dependent_schema(args["trigger_field"], args.get("properties", {}),
                                          required=args.get("required"))
            return {"ok": True}

        if name == "add_pattern_properties":
            builder.add_pattern_properties(args["pattern"], args.get("value_schema", {}))
            return {"ok": True}

        if name == "define_reusable_schema":
            builder.define_reusable_schema(args["name"], args.get("schema", {}))
            return {"ok": True}

        if name == "add_ref_field":
            field_name = builder.add_ref_field(args["label"], args["defs_name"],
                                                required=args.get("required", False))
            return {"field_name": field_name}

        if name == "add_not_constraint":
            not_schema = {}
            if args.get("not_pattern"):
                not_schema["pattern"] = args["not_pattern"]
            if args.get("not_const") is not None:
                not_schema["const"] = args["not_const"]
            if args.get("not_enum"):
                not_schema["enum"] = args["not_enum"]
            builder.add_not_constraint(args["field_name"], not_schema)
            return {"ok": True}

        if name == "add_raw_property":
            field_name = builder.add_raw_property(args["label"], args.get("raw_schema", {}),
                                                   required=args.get("required", False))
            return {"field_name": field_name}

        if name == "remove_field":
            field_name = args["field_name"]
            if field_name not in builder.properties:
                return {"error": f"'{field_name}' isn't a known field -- nothing to remove"}
            builder.remove_field(field_name)
            return {"ok": True}

        if name == "set_required":
            field_name = args["field_name"]
            wants_required = bool(args["required"])
            if field_name not in builder.properties:
                # A real bug found live-testing: a field can end up listed in `required` with no
                # matching entry in `properties` (a stale confidence-tracking entry in wizard.py
                # resurrecting a field the model had already removed mid-draft). Before this
                # fix, asking to un-require a field in that state always errored here -- since
                # there's no way to satisfy "make this required" for a field that doesn't exist,
                # but "make sure this ISN'T required" is trivially satisfiable by just clearing
                # the dangling entry, which is exactly what's needed to escape that state.
                if not wants_required and field_name in builder.required:
                    builder.required.remove(field_name)
                    return {"ok": True, "note": f"'{field_name}' wasn't an actual field -- "
                                                 "removed the dangling required-list entry"}
                return {"error": f"'{field_name}' isn't a known field"}
            currently_required = field_name in builder.required
            if wants_required and not currently_required:
                builder.required.append(field_name)
            elif not wants_required and currently_required:
                builder.required.remove(field_name)
            return {"ok": True}

        return {"error": f"unknown tool '{name}'"}
    except ValueError as e:
        return {"error": str(e)}
    except KeyError as e:
        # A real gap found by an adversarial review: the model omitting a required argument (e.g.
        # calling add_field with no "type") raises KeyError on the args["..."] lookups above, which
        # this handler didn't cover before -- crashing the whole drafting/fixing session instead of
        # surfacing a tool error the model could react to, exactly like a validation ValueError
        # already does. Confirmed by direct reproduction: _execute_tool_call(b, {}, 'add_field',
        # {'label': 'Name'}) (missing 'type') raised uncaught.
        return {"error": f"missing required argument {e} for tool '{name}'"}
    except TypeError as e:
        # Same reasoning as KeyError just above, for the other predictable shape of a malformed
        # tool call: an argument present but the wrong type (e.g. a string where add_unique_constraint
        # expects a list of field names), which can raise TypeError inside the builder method it's
        # passed to rather than at the args["..."] lookup itself.
        return {"error": f"invalid arguments for tool '{name}': {e}"}
    except Exception as e:
        # A second adversarial review found this boundary was still incomplete after the two
        # handlers above: ValueError/KeyError/TypeError only cover the specific shapes anticipated
        # at the time, and enumerating exception types one at a time as new ones turn up is
        # provably a losing game -- a systematic audit across all 15 tools found the same
        # underlying mistake (a non-string "label" reaching camel_field_name()'s unconditional
        # .strip() call, and a parent field stored as a raw dict rather than a PropertyDef reaching
        # a plain ".type" attribute access) surfacing as AttributeError in add_field,
        # add_nested_field, add_one_of_field, add_ref_field, and add_raw_property alike. This
        # function's entire contract is "never let a malformed tool call escape as a crash" --
        # every builder method it calls is already covered by this project's own direct tests
        # (test_schema_builder.py) against well-formed input, so a bug surfacing HERE always means
        # the arguments were shaped wrong, not that the builder itself is broken. A bare
        # except Exception is the right scope for that contract, not scope creep: it's the only
        # way to make the "no other error type could still be waiting to be found" property
        # actually true, rather than an ever-growing, never-provably-complete list.
        return {"error": f"tool '{name}' failed unexpectedly with malformed arguments: {e}"}


def _run_tool_call(builder: SchemaBuilder, confidence: dict, call) -> dict:
    """Parses one model tool call's arguments and executes it, translating a malformed arguments
    string into the same kind of {"error": ...} dict _execute_tool_call already returns for a
    malformed-but-parseable call -- so the model sees a reactable tool error either way, instead of
    an uncaught json.JSONDecodeError crashing the whole session. Shared by
    draft_schema_from_description and apply_fix_from_description, which otherwise ran this same two
    -step parse-then-execute logic independently."""
    try:
        args = json.loads(call.function.arguments)
    except json.JSONDecodeError as e:
        return {"error": f"your last tool call's arguments weren't valid JSON: {e}"}
    return _execute_tool_call(builder, confidence, call.function.name, args)


def draft_schema_from_description(schema_code: str, description: str, model: str = "gpt-4o",
                                   max_rounds: int = 40) -> tuple[SchemaBuilder, dict]:
    """Runs the tool-calling loop end to end and returns the populated builder plus a confidence
    map (keyed by field id -- a bare name for top-level fields, "parent.child" for nested ones)
    recording which pieces the model inferred rather than the user actually stating. Caller (the
    wizard's free-text entry point) uses that map both to ask a targeted follow-up about
    required-ness gaps and to flag the remaining guesses in the rendered preview.

    Defaults to gpt-4o, not gpt-4o-mini: live-tested side by side on the same deliberately vague,
    multi-construct description (conditionals + $ref reuse + nested geoLocation inside the ref'd
    shape + oneOf + contains/minContains + patternProperties + not + dependentRequired, all in one
    paragraph, none named by JSON Schema keyword). gpt-4o-mini reliably got the simple fields and
    conditionals right but consistently mishandled the compound/deep constructs across repeated
    runs -- inventing a substitute boolean field instead of contains/minContains, duplicating the
    address into two flat objects instead of $ref-ing a shared one, flattening or orphaning nested
    sub-groups instead of nesting them, and (once) nesting "required" as a bogus property key
    inside a oneOf alternative's "properties" instead of as a sibling (a shape now defensively
    rescued by _normalize_named_properties() in builder.py regardless of which model is used, but
    still evidence of shakier reasoning under compound instructions). gpt-4o got every one of those
    same constructs right in one pass. Matches judge_schema_against_description's own reasoning for
    the identical model choice, for the identical reason -- see its docstring."""
    from openai import OpenAI

    client = OpenAI()
    builder = SchemaBuilder(schema_code)
    confidence: dict[str, dict] = {}

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": description},
    ]

    for _ in range(max_rounds):
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            break

        for call in message.tool_calls:
            result = _run_tool_call(builder, confidence, call)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
    else:
        print(f"  (stopped after {max_rounds} rounds -- draft may be incomplete)")

    return builder, confidence


_FIX_SYSTEM_PROMPT_TEMPLATE = """You are helping a business user fix a registry schema you already \
drafted, based on their feedback after reviewing the rendered preview. You do not write JSON \
directly -- you call the same tools used to build the schema originally (add_field, \
add_conditional, add_one_of_field, etc.) plus two tools specifically for fixes: remove_field (drop \
a field that shouldn't be part of the schema after all) and set_required (flip an existing field's \
required/optional status WITHOUT recreating it -- always prefer this over remove_field + add_field \
for a pure required-ness change, since re-adding a field can lose constraints like pattern/enum \
that weren't mentioned in the feedback).

Make ONLY the changes the user's feedback actually implies -- do not rebuild, rename, or \
re-describe fields that are already correct and weren't mentioned. If the feedback describes a \
genuinely new field or rule, add it the same way you would when drafting from scratch.

Here is the CURRENT schema definition, exactly as it stands before this fix:
{current_definition}

When you've applied the fix, stop calling tools and reply with a short summary of what you \
changed."""


def apply_fix_from_description(builder: SchemaBuilder, feedback: str, model: str = "gpt-4o",
                                max_rounds: int = 20) -> None:
    """The free-text counterpart to wizard.py's offer_fix_schema guided menu (redo one field's
    fixed Q&A, 'add', 'delete FIELD_NAME', 'rename', 'constraints'): instead of picking a specific
    target and re-answering fixed questions, the user just describes what's wrong in their own
    words (e.g. "the promo code shouldn't be required" or "we don't need a separate billing
    address, remove it"), and this applies exactly that change via the same validated builder
    tools draft_schema_from_description uses -- narrowing the model's job to picking the right
    tool call for an already-scoped fix, not freely rewriting the schema. Mutates `builder` in
    place; the caller's existing re-validate/re-render loop (wizard.py's run_llm_schema_session)
    catches anything the fix breaks (e.g. a dangling reference left by a removed field) the same
    way it already catches mistakes from the guided fix path."""
    from openai import OpenAI

    client = OpenAI()
    current_definition = builder.build().model_dump(by_alias=True, exclude_none=True)["definition"]
    system_prompt = _FIX_SYSTEM_PROMPT_TEMPLATE.format(
        current_definition=json.dumps(current_definition, indent=2))
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": feedback},
    ]
    confidence: dict[str, dict] = {}

    for _ in range(max_rounds):
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            if message.content:
                print(f"  {message.content.strip()}")
            break

        for call in message.tool_calls:
            result = _run_tool_call(builder, confidence, call)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
    else:
        print(f"  (stopped after {max_rounds} rounds -- fix may be incomplete)")


def judge_schema_against_description(description: str, definition: dict, model: str = "gpt-4o") -> dict:
    """A second, independent LLM call comparing the drafted schema against the *original*
    free-text description -- catches misunderstandings neither meta-schema validation (checks
    syntax, not intent) nor the tool-calling loop's own referential checks (checks structure, not
    meaning) can catch, e.g. a rule that should have been an if/then conditional but ended up as
    a plain always-required field.

    Defaults to gpt-4o: a real false positive was found live-testing this with gpt-4o-mini -- it
    misread a correct allOf/if/then conditional as "always required," flagging a schema that
    actually matched the description. gpt-4o got the same case right. Independently, gpt-4o-mini
    also turned out unreliable for draft_schema_from_description's own job (see that function's
    docstring for the side-by-side comparison) -- both ended up needing the same upgrade, for
    the same underlying reason: accurately reasoning about compound/nested JSON Schema structure
    (drafting it correctly, or interpreting it correctly here) is a harder task than it first
    looks, and worth the extra cost/latency for reliability. A wrong verdict here is exactly the
    kind of subtle, plausible-sounding mistake this whole project has tried to avoid taking at
    face value.

    This is informational, not a gate: it never blocks or auto-fixes anything, and it does not
    replace the interactive rendered form + human confirm, which stays the one thing that catches
    a mistake a business user can't name in JSON Schema terms but would recognize immediately by
    playing with the form. Fails open (reports "ok") if the judge call itself errors or returns
    something unparseable -- an additional informational
    check shouldn't be able to block the user from reaching the render/confirm step."""
    from openai import OpenAI

    client = OpenAI()
    prompt = (
        "A business user described a data-entry form like this:\n\n"
        f'"""{description}"""\n\n'
        "The following JSON Schema was drafted from that description:\n\n"
        f"{json.dumps(definition, indent=2)}\n\n"
        "Compare them carefully. Does the schema genuinely capture what was described -- the "
        "right fields, the right required-ness, any conditional rules, any alternative-shape "
        "rules, any banned values? Respond with a JSON object of the exact shape "
        '{"ok": true or false, "issues": ["specific mismatch or omission", ...]}. '
        'If everything looks correct, respond {"ok": true, "issues": []}. Be specific in each '
        "issue -- name the field and what's wrong, not a vague generality."
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return {"ok": bool(result.get("ok", True)), "issues": [str(i) for i in (result.get("issues") or [])]}
    except Exception:
        return {"ok": True, "issues": []}


def log_judge_result(schema_code: str, description: str, definition: dict, confidence: dict,
                      judgment: dict, preview_coverage: dict | None = None,
                      log_path: str | None = None) -> None:
    """Appends one line to a local judge-result log -- every judge call, not just the ones that
    found issues. Logging only disagreements would look like a shortcut to the same goal, but it
    silently throws away the ability to ever measure the judge's real precision/recall: without
    the "ok" cases too, there's no way to later compute a false-negative rate (the judge said
    fine, but a human reviewing the same schema later found a real problem) -- only a partial
    picture of false positives. Over time, with a human spot-labeling a sample of these entries,
    this becomes exactly the dataset needed to improve the drafting/judge prompts and spot
    JSON Schema patterns the tools don't cover well yet, per the request that prompted this.

    `preview_coverage` is the reduced snapshot of render.get_preview_completeness()'s return value
    (full/partial/none/total/percent/conformance_summary -- everything except the verbose
    per-gap "gaps" list, which just duplicates prose derivable from `definition` at analysis time,
    not worth bloating every log line with). Once enough entries have a human_verdict, this is
    what lets low coverage be correlated against correction rate, judge confidence, and submission
    acceptance -- turning this log from a debugging trail into a closed-loop quality dataset.
    Optional and defaults to None so existing callers/tests don't need updating.

    Never raises: a logging failure (disk full, permissions, read-only filesystem) shouldn't
    block the wizard's main flow -- the same "informational, not a gate" principle
    judge_schema_against_description itself already follows."""
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "schema_code": schema_code,
        "description": description,
        "definition": definition,
        "confidence": confidence,
        "judgment": judgment,
        "preview_coverage": preview_coverage,
        # Left blank for a human reviewer to fill in later: "was the judge actually right here?"
        # -- turning a sample of these entries into a labeled precision/recall dataset over time,
        # not just trusting the judge's own self-reported "ok".
        "human_verdict": None,
    }
    path = log_path or os.path.join(os.getcwd(), "judge_log.jsonl")
    try:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
