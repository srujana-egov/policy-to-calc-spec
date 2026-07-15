"""Stress tests the render layer's core promise: given *any* JSON Schema shape -- not just what
this prototype's own bounded model or LLM-drafting path can produce -- render_schema_form_preview
never crashes and always writes an offline-safe page, even where all it can honestly do is show
raw JSON. This is deliberately adversarial: bare boolean sub-schemas, non-string enum values,
malformed patterns, non-object top-level types, and other edge cases found by feeding the renderer
directly, bypassing the wizard/LLM entirely -- exactly the inputs a hand-authored or
externally-supplied schema could contain. Every case here was a real crash before being fixed;
this file is what keeps it that way.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from render import render_schema_form_preview

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


EXTERNAL_REF_PATTERNS = ["http://", "https://", "@import", "fetch(", "XMLHttpRequest"]

# name -> (definition, x_unique, x_indexes) -- every one of these once crashed
# render_schema_form_preview before the corresponding fix in render.py.
CASES = {
    "boolean_subschema_true": {
        "type": "object",
        "properties": {"anythingGoes": True, "known": {"type": "string"}},
    },
    "boolean_subschema_false": {
        "type": "object",
        "properties": {"neverAllowed": False},
    },
    "const_without_type": {
        "type": "object",
        "properties": {"fixedField": {"const": "ALWAYS_THIS"}},
    },
    "enum_without_type": {
        "type": "object",
        "properties": {"color": {"enum": ["red", "green", "blue"]}},
    },
    "enum_non_string_values": {
        "type": "object",
        "properties": {"priority": {"type": "integer", "enum": [1, 2, 3]}},
    },
    "enum_mixed_and_bool_and_null_values": {
        "type": "object",
        "properties": {"weird": {"enum": [1, True, None, "x"]}},
    },
    "top_level_array": {
        "type": "array",
        "items": {"type": "string"},
    },
    "top_level_bare_true": True,
    "empty_property_schema": {
        "type": "object",
        "properties": {"anything": {}},
    },
    "ref_property": {
        "type": "object",
        "properties": {"linked": {"$ref": "#/$defs/something"}},
        "$defs": {"something": {"type": "string"}},
    },
    "pattern_properties_top_level": {
        "type": "object",
        "properties": {"known": {"type": "string"}},
        "patternProperties": {"^x-": {"type": "string"}},
    },
    "additional_properties_as_schema": {
        "type": "object",
        "properties": {
            "group": {"type": "object", "properties": {"a": {"type": "string"}},
                      "additionalProperties": {"type": "string"}},
        },
    },
    "nested_oneof_inside_oneof_alt": {
        "type": "object",
        "properties": {
            "outer": {
                "oneOf": [
                    {"type": "object", "properties": {
                        "inner": {"oneOf": [
                            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                            {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]},
                        ]}
                    }},
                    {"type": "object", "properties": {"c": {"type": "string"}}, "required": ["c"]},
                ]
            }
        },
    },
    "oneof_alt_is_boolean": {
        "type": "object",
        "properties": {"weird": {"oneOf": [True, {"type": "object", "properties": {"a": {"type": "string"}}}]}},
    },
    "oneof_with_no_alternatives": {
        "type": "object",
        "properties": {"empty": {"oneOf": []}},
    },
    "numeric_extras_beyond_what_this_project_models": {
        "type": "object",
        "properties": {"count": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 100, "multipleOf": 5}},
    },
    "null_property_value": {
        "type": "object",
        "properties": {"broken": None},
    },
    "pattern_is_not_a_string": {
        "type": "object",
        "properties": {"broken": {"type": "string", "pattern": 12345}},
    },
    "not_construct": {
        "type": "object",
        "properties": {"notNegative": {"not": {"type": "string", "pattern": "^-"}}},
    },
    "deeply_nested_object": {
        "type": "object",
        "properties": {
            "a": {"type": "object", "properties": {
                "b": {"type": "object", "properties": {
                    "c": {"type": "object", "properties": {
                        "d": {"type": "string"}
                    }}
                }}
            }}
        },
    },
    "missing_properties_key_entirely": {
        "type": "object",
    },
    "allOf_missing_if_properties": {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "allOf": [{"if": {}, "then": {"required": ["a"]}}],
    },
    "required_list_but_no_properties_dict": {
        "type": "object",
        "properties": {},
        "required": ["ghost"],
    },
    "ref_pointing_at_missing_defs_entry": {
        "type": "object",
        "properties": {"linked": {"$ref": "#/$defs/ghost"}},
        "$defs": {},
    },
    "circular_ref": {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A"}},
        "$defs": {"A": {"$ref": "#/$defs/A"}},
    },
    "ref_defs_not_a_dict": {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A"}},
        "$defs": {"A": "not-a-schema"},
    },
    "not_schema_is_not_a_dict": {
        "type": "object",
        "properties": {"a": {"type": "string", "not": "not-a-schema"}},
    },
    "not_schema_is_boolean": {
        "type": "object",
        "properties": {"a": {"type": "string", "not": True}},
    },
    "dependent_schema_block_not_a_dict": {
        "type": "object",
        "properties": {"trigger": {"type": "string"}},
        "dependentSchemas": {"trigger": "not-a-schema"},
    },
    "dependent_schema_trigger_unknown_field": {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "dependentSchemas": {"ghostTrigger": {"properties": {"b": {"type": "string"}}}},
    },
    "pattern_properties_value_schema_not_a_dict": {
        "type": "object",
        "properties": {"known": {"type": "string"}},
        "patternProperties": {"^x-": "not-a-schema"},
    },
    "pattern_properties_empty": {
        "type": "object",
        "properties": {"known": {"type": "string"}},
        "patternProperties": {},
    },
    # -- preview-gap feature: adversarial shapes for the new scanner/explanation code --
    "contains_sub_schema_not_a_dict": {
        "type": "object",
        "properties": {"docs": {"type": "array", "contains": "not-a-schema"}},
    },
    "contains_condition_not_a_dict": {
        "type": "object",
        "properties": {"docs": {"type": "array", "contains": {"properties": "not-a-dict"}}},
    },
    "min_max_contains_non_numeric": {
        "type": "object",
        "properties": {"docs": {"type": "array", "contains": {"properties": {"a": {"const": 1}}},
                               "minContains": "not-a-number", "maxContains": None}},
    },
    "prefix_items_not_a_list": {
        "type": "object",
        "properties": {"tuple": {"type": "array", "prefixItems": "not-a-list"}},
    },
    "property_names_not_a_dict": {
        "type": "object",
        "properties": {"group": {"type": "object", "propertyNames": "not-a-schema"}},
    },
    "unevaluated_properties_weird_value": {
        "type": "object",
        "properties": {"group": {"type": "object", "unevaluatedProperties": 12345}},
    },
    "allof_block_not_a_dict": {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "allOf": ["not-a-dict"],
    },
    "not_deeply_nested_unrecognized": {
        "type": "object",
        "properties": {"a": {"type": "string", "not": {"oneOf": [{"type": "string"}, {"type": "number"}]}}},
    },
}


def test_render_robustness_battery_never_crashes_and_stays_offline_safe():
    for case_name, definition in CASES.items():
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.html"
            render_schema_form_preview(f"torture-{case_name}", definition, None, None, str(out))
            html = out.read_text()
        check(f"robustness-{case_name}-renders", bool(html), case_name)
        for pattern in EXTERNAL_REF_PATTERNS:
            check(f"robustness-{case_name}-no-{pattern.strip('(:/@')}", pattern not in html, (case_name, pattern))


def test_preview_gap_scanner_never_crashes_on_the_same_battery():
    """The gap scanner + completeness scorer walk the same tree the renderer does, using new
    code -- confirms every adversarial shape in CASES above is safe to run through
    get_preview_completeness too, not just render_schema_form_preview."""
    from render import get_preview_completeness

    for case_name, definition in CASES.items():
        completeness = get_preview_completeness(definition)
        check(f"gap-scan-{case_name}-returns-dict", isinstance(completeness, dict), case_name)
        check(f"gap-scan-{case_name}-has-percent",
              isinstance(completeness.get("percent"), int), (case_name, completeness))


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print(f"\nAll {len(CASES)} adversarial JSON Schema shapes rendered without crashing, offline-safe.")
