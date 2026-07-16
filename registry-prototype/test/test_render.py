"""Stress tests for render.py -- offline-safety and structural correctness as a permanent
regression check, mirroring ../workflow-prototype/test_render.py's reasoning (a CDN dependency
there caused a silently blank preview; there's none here to begin with, but it's worth locking in
rather than assuming).
"""

from __future__ import annotations


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from builder import SchemaBuilder
from render import render_data_preview, render_schema_form_preview
from test_schema_builder import build_license_registry, build_pgr2, build_trade_license

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


EXTERNAL_REF_PATTERNS = ["http://", "https://", "@import", "fetch(", "XMLHttpRequest"]


def _render_form(schema, out_path) -> str:
    data = schema.model_dump(by_alias=True, exclude_none=True)
    return render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                       data.get("x-indexes"), str(out_path))


def test_render_01_schema_form_preview_no_external_references():
    schema = build_license_registry().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        _render_form(schema, out)
        html = out.read_text()
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render01-no-{pattern.strip('(:/@')}", pattern not in html, pattern)
    check("render01-no-schema-uri-leaked", "json-schema.org" not in html, html)


def test_render_02_schema_form_preview_one_control_per_flat_field():
    """license-registry has four flat (non-nested) fields, one of them an enum -- confirms every
    field gets exactly one labeled form control (not a table row) and the enum renders as a real
    <select>, not a comma-separated list."""
    schema = build_license_registry().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        _render_form(schema, out)
        html = out.read_text()
    check("render02-field-count", html.count('class="form-field') == len(schema.definition.properties),
          (html.count('class="form-field'), len(schema.definition.properties)))
    for name in schema.definition.properties:
        check(f"render02-has-{name}", f'id="{name}"' in html, name)
    check("render02-status-is-a-select", '<select id="status"' in html, html)
    for value in schema.definition.properties["status"].enum:
        check(f"render02-status-option-{value}", f'>{value}<' in html, value)
    check("render02-required-markers",
          html.count('class="required-marker"') == len(schema.definition.required),
          (html.count('class="required-marker"'), len(schema.definition.required)))


def test_render_03_data_preview_no_external_references():
    schema = build_license_registry().build()
    records = [{"licenseNumber": "DL-001", "holderName": "Jane Citizen",
                "issueDate": "2024-01-10", "status": "ACTIVE"}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render03-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_04_data_preview_one_row_per_record():
    schema = build_license_registry().build()
    records = [
        {"licenseNumber": "DL-001", "holderName": "Jane Citizen", "issueDate": "2024-01-10", "status": "ACTIVE"},
        {"licenseNumber": "DL-002", "holderName": "Bob Builder", "issueDate": "2024-02-01", "status": "SUSPENDED"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render04-row-count", html.count("<tr><td>") == len(records), html.count("<tr><td>"))
    check("render04-has-dl-001", "DL-001" in html)
    check("render04-has-dl-002", "DL-002" in html)


def test_render_05_data_preview_missing_optional_field_renders_blank_not_crashing():
    schema = build_license_registry().build()
    records = [{"licenseNumber": "DL-003", "issueDate": "2024-03-01", "status": "ACTIVE"}]  # no holderName
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render05-does-not-crash-and-renders", "DL-003" in html)


def test_render_06_nested_group_rendered_as_fieldset_and_no_external_refs():
    """trade-license's 'address' field is a one-level-nested group with a required 'city'
    sub-field -- confirms it renders as a real <fieldset> wrapping its own labeled controls
    (city/pincode), not a summary row, and that city's control carries the required marker."""
    schema = build_trade_license().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        _render_form(schema, out)
        html = out.read_text()
    check("render06-group-is-fieldset", '<fieldset class="form-group"><legend>address' in html, html)
    check("render06-nested-city-control", 'id="address.city"' in html, html)
    check("render06-nested-pincode-control", 'id="address.pincode"' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render06-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_07_pattern_and_minmax_shown_as_real_control_attributes():
    """pgr2 is the real tutorial schema with a top-level pattern (mobile), nested min/max
    (address.latitude/longitude), and two levels of nesting (address.auditDetails) -- confirms
    all of these render as live HTML attributes on the actual control, at any depth, not just in
    a click-to-expand JSON blob scoped to top-level fields only."""
    schema = build_pgr2().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        _render_form(schema, out)
        html = out.read_text()
    check("render07-top-level-pattern-on-control", 'pattern="^[0-9]{10}$"' in html, "mobile's pattern")
    check("render07-nested-min-on-control", 'min="-90"' in html, "latitude's minimum")
    check("render07-nested-max-on-control", 'max="90"' in html, "latitude's maximum")
    check("render07-two-level-nesting-does-not-crash", 'id="address.auditDetails.createdBy"' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render07-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_10_assumed_field_gets_visual_badge_stated_field_does_not():
    """field_confidence is how an LLM draft's guesses get flagged for review -- a field marked
    details_stated=False should render with the 'assumed' class/badge; one marked True (or with no
    entry at all, e.g. the deterministic wizard path) should not."""
    schema = build_license_registry().build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    confidence = {
        "holderName": {"required_stated": True, "details_stated": False},
        "status": {"required_stated": True, "details_stated": True},
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out), field_confidence=confidence)
        html = out.read_text()
    check("render10-assumed-badge-present", 'class="assumed-badge"' in html, html)
    check("render10-assumed-field-marked", '<div class="form-field assumed">' in html, html)
    check("render10-stated-field-not-marked",
          html.count('class="assumed-badge"') == 1, html.count('class="assumed-badge"'))
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render10-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_09_exotic_construct_falls_back_to_labeled_raw_json():
    """render_schema_form_preview takes a plain dict, not a typed SchemaRequest, specifically so
    it can accept constructs the bounded PropertyDef model can't represent -- confirms a
    genuinely-still-unsupported construct (patternProperties -- oneOf/anyOf get a real
    interactive picker instead, see test_render_11) still renders as a labeled raw-JSON block
    instead of crashing or silently vanishing from the form."""
    definition = {
        "type": "object",
        "properties": {
            "extraFields": {
                "patternProperties": {"^x-": {"type": "string"}},
                "description": "Any field starting with x- is allowed through, of any value",
            },
            "licenseNumber": {"type": "string"},
        },
        "required": ["licenseNumber"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview("exotic-test", definition, None, None, str(out))
        html = out.read_text()
    check("render09-unsupported-block-present", 'class="form-field unsupported"' in html, html)
    check("render09-raw-json-shown", "patternProperties" in html, html)  # HTML-escaped, no literal quotes
    check("render09-known-field-still-a-real-control", 'id="licenseNumber"' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render09-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_11_oneof_rendered_as_interactive_alternative_picker():
    """oneOf/anyOf get a real radio-button switcher between alternative shapes, not a raw-JSON
    fallback -- so a business user can click between 'email' and 'phone' and see the form
    actually change, rather than reading a JSON blob to guess what the rule means."""
    definition = {
        "type": "object",
        "properties": {
            "contactMethod": {
                "oneOf": [
                    {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
                    {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]},
                ],
                "description": "Either an email or a phone contact",
            },
            "licenseNumber": {"type": "string"},
        },
        "required": ["licenseNumber"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview("oneof-test", definition, None, None, str(out))
        html = out.read_text()
    check("render11-no-raw-json-fallback", 'class="form-field unsupported"' not in html, html)
    check("render11-radio-options-present", html.count('type="radio"') == 2, html)
    check("render11-first-alt-checked", 'value="0" checked' in html, html)
    check("render11-email-control-present", 'id="contactMethod.alt0.email"' in html, html)
    check("render11-phone-control-present", 'id="contactMethod.alt1.phone"' in html, html)
    check("render11-toggle-wired", "toggleOneOf(" in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render11-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_12_dependent_required_shows_live_toggleable_note_and_marker():
    """add_dependent_required's 'if field A is filled in, field B becomes required' should render
    with a plain-language note and a toggleable required-marker span the embedded JS can flip
    live -- the same mechanism as an if/then conditional, just triggered by presence rather than
    a specific value."""
    b = SchemaBuilder("dependent-required-test")
    b.add_field("Credit Card Number", "string")
    b.add_field("Cvv", "string", pattern="^[0-9]{3}$")
    b.add_dependent_required("creditCardNumber", ["cvv"])
    schema = b.build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out))
        html = out.read_text()
    check("render12-toggleable-marker-present", 'id="cvv__reqmarker"' in html, html)
    check("render12-initially-hidden", 'id="cvv__reqmarker" class="required-marker" style="display:none"' in html, html)
    check("render12-note-present", "is filled in" in html, html)
    check("render12-note-names-trigger", "creditCardNumber" in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render12-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_13_internal_ref_resolves_and_renders_inline():
    """add_ref_field/define_reusable_schema's internal $ref should render as if the referenced
    $defs entry were inlined at that point -- real labeled controls, not a raw-JSON fallback."""
    b = SchemaBuilder("ref-test")
    b.define_reusable_schema("Address", {
        "type": "object",
        "properties": {"city": {"type": "string"}, "pincode": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "required": ["city"],
    })
    b.add_ref_field("Billing Address", "Address", required=True)
    schema = b.build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out))
        html = out.read_text()
    check("render13-no-raw-json-fallback", 'class="form-field unsupported"' not in html, html)
    check("render13-city-control-present", 'id="billingAddress.city"' in html, html)
    check("render13-pincode-control-present", 'id="billingAddress.pincode"' in html, html)
    check("render13-pincode-pattern-carried", 'pattern="^[0-9]{6}$"' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render13-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_14_external_ref_falls_back_honestly():
    """An external/cross-document $ref -- unresolvable offline -- should still render (as a
    labeled raw-JSON block with a clear explanation), not crash or silently vanish."""
    definition = {
        "type": "object",
        "properties": {
            "linked": {"$ref": "https://example.com/schemas/address.json"},
            "known": {"type": "string"},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview("external-ref-test", definition, None, None, str(out))
        html = out.read_text()
    check("render14-unsupported-block-present", 'class="form-field unsupported"' in html, html)
    check("render14-honest-note", "defined in another document" in html, html)
    check("render14-gap-panel-present", 'class="preview-gap"' in html, html)
    check("render14-known-field-still-works", 'id="known"' in html, html)
    # http(s):// is deliberately excluded from this check: the fixture's own $ref value is a URL,
    # legitimately displayed as inert escaped text inside the raw-JSON fallback -- not a live
    # reference the browser would fetch. @import/fetch(/XMLHttpRequest would mean an actual
    # network call and are still checked.
    for pattern in ["@import", "fetch(", "XMLHttpRequest"]:
        check(f"render14-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_15_not_constraint_shows_business_readable_note():
    b = SchemaBuilder("not-test")
    b.add_field("Username", "string", required=True)
    b.add_not_constraint("username", {"pattern": "^admin$"})
    schema = b.build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out))
        html = out.read_text()
    check("render15-note-present", 'class="not-note"' in html, html)
    check("render15-note-text", "Must NOT" in html or "Must not" in html, html)
    check("render15-control-still-real", 'id="username"' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render15-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_16_dependent_schema_renders_toggle_container_with_new_fields():
    """add_dependent_schema's fields don't exist in the base form at all until the trigger is
    filled in -- confirms they render inside a hidden-by-default container the JS can reveal,
    not silently missing or always visible."""
    b = SchemaBuilder("dependent-schema-test")
    b.add_field("Credit Card Number", "string")
    b.add_dependent_schema("creditCardNumber", {
        "Cvv": {"type": "string", "pattern": "^[0-9]{3}$"},
        "Expiry Date": {"type": "string", "format": "date"},
    }, required=["Cvv"])
    schema = b.build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out))
        html = out.read_text()
    check("render16-container-hidden-by-default",
          'id="creditCardNumber__dependentSchema" class="dependent-schema-group" style="display:none"' in html, html)
    check("render16-new-fields-present", 'id="creditCardNumber__dep.cvv"' in html, html)
    check("render16-note-present", "is filled in" in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render16-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_17_pattern_properties_renders_dynamic_add_field_section():
    b = SchemaBuilder("pattern-properties-test")
    b.add_field("Known Field", "string")
    b.add_pattern_properties("^x-", {"type": "string", "pattern": "^[a-z]+$"})
    schema = b.build()
    data = schema.model_dump(by_alias=True, exclude_none=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out))
        html = out.read_text()
    check("render17-group-present", 'class="dynamic-fields-group"' in html, html)
    check("render17-pattern-shown", "^x-" in html, html)
    check("render17-add-button-wired", "addDynamicField(0)" in html, html)
    check("render17-name-input-present", 'id="patternProps0__nameInput"' in html, html)
    check("render17-error-div-present", 'id="patternProps0__error"' in html, html)
    check("render17-value-schema-embedded", '"pattern": "^[a-z]+$"' in html, html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render17-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_08_nested_data_value_formatted_readably():
    schema = build_trade_license().build()
    records = [{"applicantId": "A1", "businessName": "Acme", "tradeType": "RETAIL",
                "address": {"city": "Springfield", "pincode": "62704"}}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render08-nested-value-readable", "city: Springfield" in html and "pincode: 62704" in html, html)
    check("render08-not-python-repr", "{'city'" not in html, html)


def test_render_18_data_preview_escapes_html_in_record_values():
    """Security-review finding, confirmed by direct reproduction: a data value containing HTML
    metacharacters (an ordinary business name with '&', not a contrived attack) landed raw in a
    <td> cell before this fix -- e.g. a business name of 'A&B Traders <img src=x
    onerror=alert(document.cookie)>' rendered as live, executing HTML. Field-name headers and
    schemaCode carry the same risk and get the same fix."""
    schema = build_trade_license().build()
    payload = 'A&B Traders <img src=x onerror=alert(document.cookie)>'
    records = [{"applicantId": "A1", "businessName": payload, "tradeType": "RETAIL",
                "address": {"city": "Springfield", "pincode": "62704"}}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render18-no-raw-img-tag", "<img src=x onerror=" not in html, html)
    check("render18-escaped-value-present", "&lt;img src=x onerror=" in html, html)
    check("render18-ampersand-escaped", "A&amp;B Traders" in html, html)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll render.py checks passed -- offline-safe and structurally correct.")
