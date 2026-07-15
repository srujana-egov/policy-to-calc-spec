"""Tests the "preview gap" feature: any JSON Schema construct the interactive form can't fully
visualize/enforce must be translated into a business-language explanation and flagged, never
silently hidden or shown as a bare, meaningless keyword name -- and the "Form Preview XX%
complete" score must accurately reflect how much of the schema that's true for.

Covers the exact real-world example this feature was built for: a documents list where "at least
one document must be approved" (contains + minContains on a const-valued sub-property), which a
form preview cannot visually enforce, but must still explain in plain language and pass through to
the Registry Service.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import subprocess
import tempfile
from pathlib import Path

from builder import SchemaBuilder
from render import (
    _compute_preview_completeness,
    _describe_simple_condition,
    _explain_advanced_construct,
    _scan_preview_gaps,
    get_preview_completeness,
    render_schema_form_preview,
)

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


EXTERNAL_REF_PATTERNS = ["http://", "https://", "@import", "fetch(", "XMLHttpRequest"]


# ---------------------------------------------------------------------------
# _describe_simple_condition / _explain_advanced_construct -- the business-language templates
# ---------------------------------------------------------------------------

def test_01_describe_simple_condition_const():
    result = _describe_simple_condition({"properties": {"status": {"const": "APPROVED"}}})
    check("01-const-condition", result == "status = 'APPROVED'", result)


def test_02_describe_simple_condition_enum():
    result = _describe_simple_condition({"properties": {"status": {"enum": ["APPROVED", "DONE"]}}})
    check("02-enum-condition", result == "status is 'APPROVED' or 'DONE'", result)


def test_03_describe_simple_condition_unrecognized_returns_none():
    check("03-no-properties", _describe_simple_condition({"type": "string"}) is None)
    check("03-not-a-dict", _describe_simple_condition("nonsense") is None)


def test_04_explain_contains_matches_the_documents_example():
    """The exact real-world example this feature was built for: 'at least one document must be
    approved,' expressed as contains + minContains on a const-valued status property."""
    prop = {
        "type": "array",
        "items": {"type": "object", "properties": {"status": {"type": "string"}}},
        "contains": {"properties": {"status": {"const": "APPROVED"}}},
        "minContains": 1,
    }
    gap = _explain_advanced_construct(prop)
    check("04-keywords", gap["keywords"] == ["contains", "minContains"], gap)
    check("04-meaning-mentions-approved", "APPROVED" in gap["meaning"], gap)
    check("04-meaning-is-business-language",
          "at least 1 item(s) in this list must have status = 'APPROVED'.".lower() in gap["meaning"].lower(), gap)
    check("04-limitation-present", "cannot check" in gap["limitation"], gap)
    check("04-marked-specific", gap["specific"] is True, gap)


def test_05_explain_contains_with_max_contains():
    prop = {"contains": {"properties": {"status": {"const": "APPROVED"}}}, "minContains": 2, "maxContains": 5}
    gap = _explain_advanced_construct(prop)
    check("05-keywords", gap["keywords"] == ["contains", "minContains", "maxContains"], gap)
    check("05-mentions-min", "2" in gap["meaning"], gap)
    check("05-mentions-max", "No more than 5" in gap["meaning"], gap)


def test_06_explain_contains_without_recognizable_condition_is_honest_not_specific():
    prop = {"contains": {"type": "string", "pattern": "^[A-Z]+$"}}
    gap = _explain_advanced_construct(prop)
    check("06-generic-but-present", "additional rule" in gap["meaning"], gap)
    check("06-not-marked-specific", gap["specific"] is False, gap)


def test_07_explain_prefix_items():
    prop = {"type": "array", "prefixItems": [{"type": "string"}, {"type": "string"}, {"type": "string"}]}
    gap = _explain_advanced_construct(prop)
    check("07-keywords", gap["keywords"] == ["prefixItems"], gap)
    check("07-mentions-count", "exactly 3 items" in gap["meaning"], gap)
    check("07-specific", gap["specific"] is True, gap)


def test_08_explain_property_names():
    prop = {"type": "object", "propertyNames": {"pattern": "^x-"}}
    gap = _explain_advanced_construct(prop)
    check("08-keywords", gap["keywords"] == ["propertyNames"], gap)
    check("08-mentions-pattern", "^x-" in gap["meaning"], gap)
    check("08-specific", gap["specific"] is True, gap)


def test_09_explain_unevaluated_properties():
    gap_false = _explain_advanced_construct({"unevaluatedProperties": False})
    gap_true = _explain_advanced_construct({"unevaluatedProperties": True})
    gap_schema = _explain_advanced_construct({"unevaluatedProperties": {"type": "string"}})
    check("09-false-means-no-extra", "No extra fields are allowed" in gap_false["meaning"], gap_false)
    check("09-false-is-specific", gap_false["specific"] is True, gap_false)
    check("09-true-means-unrestricted", "no effect as written" in gap_true["meaning"], gap_true)
    check("09-true-is-specific", gap_true["specific"] is True, gap_true)
    check("09-schema-means-conditional", "match a specific additional shape" in gap_schema["meaning"], gap_schema)
    check("09-schema-is-not-specific", gap_schema["specific"] is False, gap_schema)


def test_10_explain_complex_not_falls_back_honestly():
    """A `not` that isn't one of _not_note's recognized simple shapes (pattern/const/enum) --
    must still get SOME explanation, not silence."""
    gap = _explain_advanced_construct({"not": {"type": "object", "properties": {"a": {"type": "string"}}}})
    check("10-keywords", gap["keywords"] == ["not"], gap)
    check("10-not-crash-and-has-meaning", bool(gap["meaning"]), gap)


def test_11_explain_external_ref():
    gap = _explain_advanced_construct({"$ref": "https://example.com/a.json"})
    check("11-keywords", gap["keywords"] == ["$ref"], gap)
    check("11-mentions-offline", "offline" in gap["limitation"], gap)


def test_12_explain_never_returns_nothing_for_unrecognized_input():
    for weird in (True, False, None, "a string", 42, {"totally": "unknown", "shape": 1}):
        gap = _explain_advanced_construct(weird)
        check(f"12-always-has-meaning-{weird}", bool(gap.get("meaning")), gap)
        check(f"12-always-has-keywords-{weird}", bool(gap.get("keywords")), gap)


# ---------------------------------------------------------------------------
# _scan_preview_gaps -- recursive discovery, matching the actual render tree
# ---------------------------------------------------------------------------

def test_13_scan_finds_top_level_gap():
    gaps = _scan_preview_gaps("documents", {
        "type": "array",
        "contains": {"properties": {"status": {"const": "APPROVED"}}},
        "minContains": 1,
    })
    check("13-one-gap-found", len(gaps) == 1, gaps)
    check("13-field-id-correct", gaps[0]["field_id"] == "documents", gaps[0])


def test_14_scan_finds_gap_nested_inside_object():
    gaps = _scan_preview_gaps("group", {
        "type": "object",
        "properties": {
            "known": {"type": "string"},
            "documents": {"type": "array", "contains": {"properties": {"status": {"const": "APPROVED"}}}},
        },
    })
    check("14-one-gap-found", len(gaps) == 1, gaps)
    check("14-nested-field-id", gaps[0]["field_id"] == "group.documents", gaps[0])


def test_15_scan_finds_gap_nested_inside_oneof_alternative():
    gaps = _scan_preview_gaps("contact", {
        "oneOf": [
            {"properties": {"email": {"type": "string"}}, "required": ["email"]},
            {"properties": {"documents": {"type": "array", "contains": {"properties": {"status": {"const": "X"}}}}}},
        ],
    })
    check("15-one-gap-found", len(gaps) == 1, gaps)
    check("15-alt-field-id", gaps[0]["field_id"] == "contact.alt1.documents", gaps[0])


def test_15b_scan_treats_anyof_identically_to_oneof():
    """No fixture anywhere in this suite previously used a literal `anyOf` key -- render.py's own
    comments claim anyOf is handled identically to oneOf (a single-choice picker, not true
    "at least one" semantics), but that claim was never actually exercised by a test."""
    gaps = _scan_preview_gaps("contact", {
        "anyOf": [
            {"properties": {"email": {"type": "string"}}, "required": ["email"]},
            {"properties": {"documents": {"type": "array", "contains": {"properties": {"status": {"const": "X"}}}}}},
        ],
    })
    check("15b-one-gap-found", len(gaps) == 1, gaps)
    check("15b-alt-field-id", gaps[0]["field_id"] == "contact.alt1.documents", gaps[0])


def test_16_scan_does_not_false_positive_on_fully_supported_constructs():
    b = SchemaBuilder("full-support-test")
    b.add_field("Applicant Type", "string", required=True, enum=["Individual", "Company"])
    b.add_field("Aadhaar Number", "string", pattern="^[0-9]{12}$")
    b.add_conditional("applicantType", "Individual", then_required=["aadhaarNumber"])
    b.add_field("Username", "string", required=True)
    b.add_not_constraint("username", {"pattern": "^admin$"})  # recognized simple `not` shape
    b.add_one_of_field("Contact", [
        {"properties": {"email": {"type": "string"}}, "required": ["email"]},
        {"properties": {"phone": {"type": "string"}}, "required": ["phone"]},
    ])
    schema = b.build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    completeness = get_preview_completeness(data["definition"])
    check("16-no-gaps", completeness["gaps"] == [], completeness)
    check("16-fully-complete", completeness["percent"] == 100, completeness)


def test_17_scan_finds_unrecognized_allof_block_previously_silently_skipped():
    """Before this feature, an allOf block with no recognizable if.properties shape was silently
    skipped by _iter_conditionals -- confirms it's now surfaced as a gap instead."""
    definition = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "allOf": [{"if": {"not": {"required": ["a"]}}, "then": {"required": ["a"]}}],  # no if.properties
    }
    completeness = get_preview_completeness(definition)
    check("17-gap-found", len(completeness["gaps"]) == 1, completeness)
    check("17-keyword-is-allof", completeness["gaps"][0]["keywords"] == ["allOf"], completeness["gaps"])


def test_18_scan_finds_nested_pattern_properties_and_dependent_required():
    """patternProperties/dependentRequired ARE fully supported, but only at the schema's top
    level -- nested one level deep inside a property, they're not, and must be flagged rather
    than silently rendered as generic raw JSON with no explanation."""
    definition = {
        "type": "object",
        "properties": {
            "group": {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "patternProperties": {"^x-": {"type": "string"}},
            },
        },
    }
    gaps = _scan_preview_gaps("group", definition["properties"]["group"])
    check("18-gap-found", len(gaps) == 1, gaps)
    check("18-keyword", gaps[0]["keywords"] == ["patternProperties"], gaps)


# ---------------------------------------------------------------------------
# _compute_preview_completeness -- the percentage math
# ---------------------------------------------------------------------------

def test_19_completeness_percent_math():
    definition = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "string"},
            "documents": {"type": "array", "contains": {"properties": {"status": {"const": "APPROVED"}}},
                          "minContains": 1},
        },
    }
    completeness = get_preview_completeness(definition)
    check("19-total-is-3", completeness["total"] == 3, completeness)
    check("19-full-is-2", completeness["full"] == 2, completeness)
    check("19-partial-is-1", completeness["partial"] == 1, completeness)
    check("19-none-is-0", completeness["none"] == 0, completeness)
    check("19-percent-is-67", completeness["percent"] == 67, completeness)


def test_19b_completeness_drops_when_gap_lives_inside_oneof_alternative():
    """Regression test for a gap the adversarial review's test-coverage lens found via mutation
    testing: an unrecognized `not` buried inside one of two oneOf alternatives must still count
    against the completeness score, not get silently absorbed as an ordinary fully-rendered leaf
    just because it sits one level down inside an alternative."""
    def make_definition(with_not: bool):
        pager = {"type": "string"}
        if with_not:
            pager["not"] = {"type": "object", "properties": {"x": {"type": "string"}}}  # unrecognized shape
        return {
            "type": "object",
            "properties": {
                "contact": {
                    "oneOf": [
                        {"properties": {"email": {"type": "string"}, "phone": {"type": "string"}},
                         "required": ["email"]},
                        {"properties": {"fax": {"type": "string"}, "pager": pager}, "required": ["fax"]},
                    ],
                },
            },
        }

    baseline = get_preview_completeness(make_definition(with_not=False))
    check("19b-baseline-total-is-5", baseline["total"] == 5, baseline)
    check("19b-baseline-is-100-percent", baseline["percent"] == 100, baseline)

    with_gap = get_preview_completeness(make_definition(with_not=True))
    check("19b-gap-total-still-5", with_gap["total"] == 5, with_gap)
    check("19b-one-gap-found", len(with_gap["gaps"]) == 1, with_gap)
    check("19b-percent-drops-to-80", with_gap["percent"] == 80, with_gap)


def test_20_completeness_100_percent_when_no_gaps():
    definition = {"type": "object", "properties": {"a": {"type": "string"}}}
    completeness = get_preview_completeness(definition)
    check("20-full-percent", completeness["percent"] == 100, completeness)


def test_21_completeness_handles_non_dict_definition_gracefully():
    for weird in (True, False, None, ["a", "list"], 42, "a string"):
        completeness = get_preview_completeness(weird)
        check(f"21-does-not-crash-{weird}", completeness["percent"] == 0, completeness)
        check(f"21-one-gap-{weird}", len(completeness["gaps"]) == 1, completeness)


def test_21b_render_handles_non_dict_top_level_definition_types():
    """Fix 11's $schema-stripping + <details> collapsing must hold for every malformed top-level
    shape, not just the `True` case originally tested -- a list or a bare scalar must still render
    without crashing, still hide $schema, and still collapse the raw JSON."""
    for weird in (["a", "list"], 42, "a string"):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(weird, Path(tmp) / "s.html")
        check(f"21b-renders-without-crash-{weird}", bool(html))
        check(f"21b-collapsed-{weird}", "<details" in html, html)
        for pattern in EXTERNAL_REF_PATTERNS:
            check(f"21b-no-{pattern.strip('(:/@')}-{weird}", pattern not in html, html)


# ---------------------------------------------------------------------------
# Rendered markup -- the gap panel, completeness summary, acknowledgment gate, raw-schema toggle
# ---------------------------------------------------------------------------

def _render(definition, out_path):
    render_schema_form_preview("preview-gap-test", definition, None, None, str(out_path))
    return out_path.read_text()


def test_22_gap_panel_rendered_with_full_ui_structure():
    definition = {
        "type": "object",
        "properties": {
            "documents": {
                "type": "array",
                "items": {"type": "object", "properties": {"status": {"type": "string"}}},
                "contains": {"properties": {"status": {"const": "APPROVED"}}},
                "minContains": 1,
            },
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("22-needs-review-badge", "Needs review" in html, html)
    check("22-header", "Advanced rule not fully visualized" in html, html)
    check("22-keyword-shown", "<code>contains + minContains</code>" in html, html)
    check("22-cannot-enforce-line", "cannot enforce this rule visually" in html, html)
    check("22-what-it-means-label", "What this rule means" in html, html)
    check("22-business-explanation", "APPROVED" in html, html)
    check("22-what-cannot-show-label", "What the preview cannot show" in html, html)
    check("22-enforced-elsewhere", "This rule will still be enforced by the Registry Service" in html, html)
    check("22-raw-json-collapsed", "<details" in html and "View raw JSON Schema" in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"22-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_23_completeness_summary_rendered():
    definition = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "documents": {"type": "array", "contains": {"properties": {"status": {"const": "APPROVED"}}},
                          "minContains": 1},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("23-completeness-block-present", 'class="completeness-summary"' in html, html)
    check("23-percent-shown", "50% complete" in html, html)


def test_24_acknowledgment_checkbox_present_when_incomplete():
    definition = {
        "type": "object",
        "properties": {"documents": {"type": "array", "contains": {"properties": {"status": {"const": "X"}}}}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("24-ack-checkbox-present", 'id="completenessAck"' in html, html)
    check("24-ack-flag-true", "NEEDS_COMPLETENESS_ACK = true" in html, html)


def test_25_acknowledgment_checkbox_absent_when_fully_complete():
    definition = {"type": "object", "properties": {"a": {"type": "string"}}}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("25-no-ack-checkbox", 'id="completenessAck"' not in html, html)
    check("25-ack-flag-false", "NEEDS_COMPLETENESS_ACK = false" in html, html)


def test_26_raw_schema_toggle_present_and_excludes_schema_uri():
    definition = {"type": "object", "properties": {"a": {"type": "string"}}, "$schema": "https://json-schema.org/draft/2020-12/schema"}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("26-toggle-present", "View the full raw JSON Schema (for technical users)" in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"26-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


# ---------------------------------------------------------------------------
# Wiring-consistency regression tests -- a second adversarial review found the completeness score
# at the top of the page and the gap panels actually rendered below it could disagree: a gap
# would be COUNTED (dropping the percentage) with NOTHING visible anywhere explaining why. Three
# distinct code paths had this bug; each gets its own reproduction here.
# ---------------------------------------------------------------------------

def test_26b_bare_boolean_oneof_alternative_renders_its_own_gap_panel():
    definition = {"type": "object", "properties": {"extra": {"oneOf": [{"type": "string"}, True]}}}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    completeness = get_preview_completeness(definition)
    check("26b-completeness-counts-one-gap", len(completeness["gaps"]) == 1, completeness)
    check("26b-panel-actually-rendered", html.count('class="preview-gap"') == 1, html)


def test_26c_oneof_fields_own_unrecognized_not_renders_its_own_gap_panel():
    definition = {"type": "object", "properties": {"contact": {
        "oneOf": [
            {"properties": {"email": {"type": "string"}}, "required": ["email"]},
            {"properties": {"phone": {"type": "string"}}, "required": ["phone"]},
        ],
        "not": {"oneOf": [{"const": "x"}, {"const": "y"}]},  # unrecognized shape for _not_note
    }}}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    completeness = get_preview_completeness(definition)
    check("26c-completeness-counts-one-gap", len(completeness["gaps"]) == 1, completeness)
    check("26c-panel-actually-rendered", html.count('class="preview-gap"') == 1, html)


def test_26d_exotic_prop_with_unrecognized_not_does_not_double_render():
    """Guard against the fix for 26c double-counting: when a property is ALSO exotic (e.g. it
    carries patternProperties), _explain_advanced_construct(prop) already folds `not` into its
    ONE combined panel via the rules-accumulator -- a second, separate `not`-only panel must not
    also appear."""
    definition = {"type": "object", "properties": {"weird": {
        "type": "object",
        "patternProperties": {"^x-": {"type": "string"}},
        "not": {"oneOf": [{"const": "x"}, {"const": "y"}]},
    }}}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("26d-only-one-panel", html.count('class="preview-gap"') == 1, html)
    check("26d-keywords-combined", "<code>not + patternProperties</code>" in html, html)


def test_26e_schema_level_allof_gap_renders_its_own_panel():
    definition = {"type": "object", "properties": {"a": {"type": "string"}}, "allOf": [{"required": ["a"]}]}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    completeness = get_preview_completeness(definition)
    check("26e-completeness-counts-one-gap", len(completeness["gaps"]) == 1, completeness)
    check("26e-panel-actually-rendered", html.count('class="preview-gap"') == 1, html)
    check("26e-labeled-schema-level", "Schema-level rule" in html, html)


# ---------------------------------------------------------------------------
# Acknowledgment gate -- runs the *real* submit handler (not just validateNode directly), since
# the gate logic lives inside the submit event handler itself, the same code path a real click
# on Submit would run.
# ---------------------------------------------------------------------------

def _run_submit(definition, ack_checked, field_values=None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview("ack-gate-test", definition, None, None, str(out))
        html_text = out.read_text()
    match = re.search(r"<script>([\s\S]*?)</script>", html_text)
    assert match, "no <script> block found"
    script = match.group(1)

    harness = f"""
const fieldValues = {json.dumps(field_values or {})};
let submitHandler = null;
const resultDiv = {{ innerHTML: '' }};
const ackPresent = {json.dumps(ack_checked is not None)};
const ackCheckbox = {{ checked: {json.dumps(bool(ack_checked))} }};
const document = {{
  getElementById: function(id) {{
    if (id === 'schemaPreviewForm') {{
      return {{ addEventListener: function(type, handler) {{ if (type === 'submit') submitHandler = handler; }} }};
    }}
    if (id === 'validationResult') return resultDiv;
    if (id === 'completenessAck') return ackPresent ? ackCheckbox : null;
    return {{ value: Object.prototype.hasOwnProperty.call(fieldValues, id) ? fieldValues[id] : '', style: {{}} }};
  }},
  getElementsByName: function(name) {{ return []; }}
}};
{script}
submitHandler({{ preventDefault: function() {{}} }});
console.log(resultDiv.innerHTML);
"""
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"node harness failed: {result.stderr}"
    return result.stdout


_INCOMPLETE_DEFINITION = {
    "type": "object",
    "properties": {
        "known": {"type": "string"},
        "documents": {"type": "array", "contains": {"properties": {"status": {"const": "APPROVED"}}},
                      "minContains": 1},
    },
}


def test_27_submit_blocked_when_ack_unchecked():
    output = _run_submit(_INCOMPLETE_DEFINITION, ack_checked=False, field_values={"known": "x"})
    check("27-blocked", "acknowledge the preview limitations" in output, output)


def test_28_submit_proceeds_when_ack_checked():
    output = _run_submit(_INCOMPLETE_DEFINITION, ack_checked=True, field_values={"known": "x"})
    check("28-not-blocked", "acknowledge the preview limitations" not in output, output)
    check("28-proceeds-to-validation", "would be accepted" in output, output)


def test_29_submit_needs_no_ack_when_fully_complete():
    complete_definition = {"type": "object", "properties": {"a": {"type": "string"}}}
    output = _run_submit(complete_definition, ack_checked=None, field_values={"a": "x"})
    check("29-no-ack-needed", "acknowledge the preview limitations" not in output, output)
    check("29-proceeds-to-validation", "would be accepted" in output, output)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll preview-gap checks passed -- advanced constructs get business-language "
          "explanations, never silently hidden, and the completeness score matches reality.")
