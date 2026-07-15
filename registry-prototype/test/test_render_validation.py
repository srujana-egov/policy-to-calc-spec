"""Tests the embedded client-side validator (render_schema_form_preview's VALIDATION_SCHEMA +
validateNode JS) actually behaves correctly -- not just that the right strings appear in the
HTML, but that real field values produce the right accept/reject decisions and business-readable
error messages. Runs the *real* extracted JS via node, against a stubbed `document`, rather than
re-implementing the validation logic in Python and asserting against itself.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builder import SchemaBuilder
from render import render_schema_form_preview
from test_schema_builder import build_password_field_schema, build_pgr2

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def _extract_script(html_text: str) -> str:
    match = re.search(r"<script>([\s\S]*?)</script>", html_text)
    assert match, "no <script> block found in rendered HTML"
    return match.group(1)


def run_validation(schema, field_values: dict, radio_choices: dict | None = None) -> list[str]:
    """Renders the schema, pulls out the real embedded validator JS, and runs it under node
    against a stubbed document whose getElementById returns the given field_values -- the same
    code path a browser would run on Submit, just driven headlessly. radio_choices (for
    oneOf/anyOf fields) maps a radio group name to {"count": N, "chosen": i}."""
    data = schema.model_dump(by_alias=True, exclude_none=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), str(out))
        script = _extract_script(out.read_text())

    harness = f"""
const fieldValues = {json.dumps(field_values)};
const radioChoices = {json.dumps(radio_choices or {})};
const document = {{
  getElementById: function(id) {{
    if (id === 'schemaPreviewForm') return {{ addEventListener: function() {{}} }};
    if (id === 'validationResult') return {{ innerHTML: '' }};
    // Every real rendered field has a live element, empty string if the user left it blank --
    // unlike a plain lookup map, absence here must mean "blank," not "field doesn't exist." style
    // is a plain object so a __reqmarker lookup's `.style.display = ...` doesn't crash the stub.
    return {{ value: Object.prototype.hasOwnProperty.call(fieldValues, id) ? fieldValues[id] : '', style: {{}} }};
  }},
  getElementsByName: function(name) {{
    const choice = radioChoices[name];
    if (!choice) return [];
    const arr = [];
    for (let i = 0; i < choice.count; i++) {{ arr.push({{checked: i === choice.chosen}}); }}
    return arr;
  }}
}};
{script}
const errors = [];
const extraRequired = conditionallyRequiredIds();
VALIDATION_SCHEMA.forEach(function(node) {{ validateNode(node, errors, extraRequired); }});
console.log(JSON.stringify(errors));
"""
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"node harness failed: {result.stderr}"
    return json.loads(result.stdout)


def test_01_valid_pgr2_values_produce_no_errors():
    schema = build_pgr2().build()
    errors = run_validation(schema, {
        "serviceRequestId": "SR-1", "tenantId": "pb.amritsar",
        "mobile": "9876543210", "address.pincode": "141001",
        "address.latitude": "30.9", "address.longitude": "75.8",
    })
    check("val01-no-errors-on-valid-input", errors == [], errors)


def test_02_bad_mobile_pattern_produces_readable_error():
    schema = build_pgr2().build()
    errors = run_validation(schema, {
        "serviceRequestId": "SR-1", "tenantId": "pb.amritsar",
        "mobile": "12345",  # not 10 digits
    })
    check("val02-pattern-error-present", any("mobile" in e and "10 digit" in e for e in errors), errors)


def test_03_bad_pincode_pattern_on_nested_field_produces_readable_error():
    schema = build_pgr2().build()
    errors = run_validation(schema, {
        "serviceRequestId": "SR-1", "tenantId": "pb.amritsar",
        "address.pincode": "2wqd",
    })
    check("val03-nested-pattern-error-present",
          any("pincode" in e and "6 digit" in e for e in errors), errors)


def test_04_latitude_out_of_range_produces_readable_error():
    schema = build_pgr2().build()
    errors = run_validation(schema, {
        "serviceRequestId": "SR-1", "tenantId": "pb.amritsar",
        "address.latitude": "120",  # > 90
    })
    check("val04-max-error-present", any("latitude" in e and "at most 90" in e for e in errors), errors)


def test_05_missing_required_field_produces_readable_error():
    schema = build_pgr2().build()
    errors = run_validation(schema, {})  # nothing filled in at all
    check("val05-required-error-present",
          any("serviceRequestId" in e and "required" in e for e in errors), errors)


def test_06_minlength_maxlength_enforced():
    schema = build_password_field_schema().build()
    too_short = run_validation(schema, {"username": "ab", "password": "short"})
    check("val06-too-short-error", any("password" in e and "at least 8" in e for e in too_short), too_short)
    ok = run_validation(schema, {"username": "abcdef", "password": "longenoughpassword"})
    check("val06-long-enough-no-password-error", not any("password" in e for e in ok), ok)


def _contact_method_schema():
    b = SchemaBuilder("contact-test")
    b.add_field("License Number", "string", required=True)
    b.add_one_of_field("Contact Method", [
        {"type": "object", "properties": {"email": {"type": "string", "pattern": "^[^@]+@[^@]+$"}},
         "required": ["email"]},
        {"type": "object", "properties": {"phone": {"type": "string", "pattern": "^[0-9]{10}$"}},
         "required": ["phone"]},
    ])
    return b.build()


def test_07_oneof_validates_only_the_chosen_alternative():
    schema = _contact_method_schema()
    radio_choices = {"contactMethod__choice": {"count": 2, "chosen": 0}}
    # Email alternative chosen, phone alternative left completely blank -- phone must NOT error,
    # since it's not the alternative the user picked.
    errors = run_validation(schema, {
        "licenseNumber": "L1", "contactMethod.alt0.email": "a@b.com",
    }, radio_choices)
    check("val07-no-errors-when-chosen-alt-filled", errors == [], errors)


def test_08_oneof_requires_the_chosen_alternatives_own_fields():
    schema = _contact_method_schema()
    radio_choices = {"contactMethod__choice": {"count": 2, "chosen": 1}}
    # Phone alternative chosen but left blank -- must error. Email (alt0) left blank too, but
    # since it's not chosen, it must NOT contribute an error.
    errors = run_validation(schema, {"licenseNumber": "L1"}, radio_choices)
    check("val08-chosen-alt-required-error", any("required" in e for e in errors), errors)
    check("val08-only-one-error-not-two", len(errors) == 1, errors)


def test_09_oneof_pattern_enforced_on_chosen_alternative():
    schema = _contact_method_schema()
    radio_choices = {"contactMethod__choice": {"count": 2, "chosen": 1}}
    errors = run_validation(schema, {
        "licenseNumber": "L1", "contactMethod.alt1.phone": "not-a-phone-number",
    }, radio_choices)
    check("val09-pattern-error-on-chosen-alt", any("10 digit" in e for e in errors), errors)


def _applicant_type_schema():
    """The architect's own example: 'field B is only required when field A equals X'."""
    b = SchemaBuilder("applicant-test")
    b.add_field("Applicant Type", "string", required=True, enum=["Individual", "Company"])
    b.add_field("Aadhaar Number", "string", pattern="^[0-9]{12}$")
    b.add_conditional("applicantType", "Individual", then_required=["aadhaarNumber"])
    return b.build()


def test_10_conditional_not_triggered_leaves_field_optional():
    schema = _applicant_type_schema()
    errors = run_validation(schema, {"applicantType": "Company"})  # aadhaarNumber left blank
    check("val10-not-required-when-condition-not-met", errors == [], errors)


def test_11_conditional_triggered_makes_field_required():
    schema = _applicant_type_schema()
    errors = run_validation(schema, {"applicantType": "Individual"})  # aadhaarNumber left blank
    check("val11-required-when-condition-met",
          any("aadhaarNumber" in e and "required" in e for e in errors), errors)


def test_12_conditional_triggered_still_enforces_the_fields_own_pattern():
    schema = _applicant_type_schema()
    errors = run_validation(schema, {"applicantType": "Individual", "aadhaarNumber": "abc"})
    check("val12-pattern-still-enforced", any("aadhaarNumber" in e and "12 digit" in e for e in errors), errors)


def test_13_conditional_satisfied_produces_no_errors():
    schema = _applicant_type_schema()
    errors = run_validation(schema, {"applicantType": "Individual", "aadhaarNumber": "123456789012"})
    check("val13-fully-satisfied-no-errors", errors == [], errors)


def _credit_card_schema():
    b = SchemaBuilder("credit-card-test")
    b.add_field("Credit Card Number", "string")
    b.add_field("Cvv", "string", pattern="^[0-9]{3}$")
    b.add_dependent_required("creditCardNumber", ["cvv"])
    return b.build()


def test_14_dependent_required_not_triggered_when_trigger_field_empty():
    schema = _credit_card_schema()
    errors = run_validation(schema, {})  # neither field filled in
    check("val14-not-required-when-trigger-empty", errors == [], errors)


def test_15_dependent_required_triggered_by_presence_alone():
    schema = _credit_card_schema()
    errors = run_validation(schema, {"creditCardNumber": "4111111111111111"})  # cvv left blank
    check("val15-required-once-trigger-present",
          any("cvv" in e and "required" in e for e in errors), errors)


def test_16_dependent_required_triggered_still_enforces_pattern():
    schema = _credit_card_schema()
    errors = run_validation(schema, {"creditCardNumber": "4111111111111111", "cvv": "12"})
    check("val16-pattern-still-enforced", any("cvv" in e and "3 digit" in e for e in errors), errors)


def test_17_dependent_required_fully_satisfied_produces_no_errors():
    schema = _credit_card_schema()
    errors = run_validation(schema, {"creditCardNumber": "4111111111111111", "cvv": "123"})
    check("val17-fully-satisfied-no-errors", errors == [], errors)


def _ref_schema():
    b = SchemaBuilder("ref-test")
    b.define_reusable_schema("Address", {
        "type": "object",
        "properties": {"city": {"type": "string"}, "pincode": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "required": ["city"],
    })
    b.add_ref_field("Billing Address", "Address", required=True)
    return b.build()


def test_18_internal_ref_resolved_fields_enforce_their_own_rules():
    schema = _ref_schema()
    errors = run_validation(schema, {})  # billingAddress.city left blank -- required inside the ref
    check("val18-nested-required-enforced", any("city" in e and "required" in e for e in errors), errors)


def test_19_internal_ref_resolved_pattern_enforced():
    schema = _ref_schema()
    errors = run_validation(schema, {"billingAddress.city": "Springfield", "billingAddress.pincode": "abc"})
    check("val19-pattern-enforced", any("pincode" in e and "6 digit" in e for e in errors), errors)


def test_20_internal_ref_fully_satisfied_produces_no_errors():
    schema = _ref_schema()
    errors = run_validation(schema, {"billingAddress.city": "Springfield", "billingAddress.pincode": "141001"})
    check("val20-fully-satisfied-no-errors", errors == [], errors)


def _not_schema():
    b = SchemaBuilder("not-test")
    b.add_field("Username", "string", required=True)
    b.add_not_constraint("username", {"pattern": "^admin$"})
    return b.build()


def test_21_not_constraint_rejects_the_banned_value():
    schema = _not_schema()
    errors = run_validation(schema, {"username": "admin"})
    check("val21-banned-value-rejected", any("username" in e and "must not match" in e for e in errors), errors)


def test_22_not_constraint_allows_anything_else():
    schema = _not_schema()
    errors = run_validation(schema, {"username": "alice"})
    check("val22-other-values-allowed", errors == [], errors)


def _dependent_schema_fixture():
    b = SchemaBuilder("dependent-schema-test")
    b.add_field("Credit Card Number", "string")
    b.add_dependent_schema("creditCardNumber", {
        "Cvv": {"type": "string", "pattern": "^[0-9]{3}$"},
    }, required=["Cvv"])
    return b.build()


def test_23_dependent_schema_not_triggered_when_trigger_absent():
    schema = _dependent_schema_fixture()
    errors = run_validation(schema, {})  # neither field filled in
    check("val23-no-errors-when-trigger-absent", errors == [], errors)


def test_24_dependent_schema_triggered_requires_its_new_field():
    schema = _dependent_schema_fixture()
    errors = run_validation(schema, {"creditCardNumber": "4111111111111111"})  # cvv left blank
    check("val24-new-field-required-once-triggered",
          any("cvv" in e and "required" in e for e in errors), errors)


def test_25_dependent_schema_triggered_enforces_new_fields_pattern():
    schema = _dependent_schema_fixture()
    errors = run_validation(schema, {"creditCardNumber": "4111111111111111", "creditCardNumber__dep.cvv": "12"})
    check("val25-new-fields-pattern-enforced", any("3 digit" in e for e in errors), errors)


def test_26_dependent_schema_fully_satisfied_produces_no_errors():
    schema = _dependent_schema_fixture()
    errors = run_validation(schema, {"creditCardNumber": "4111111111111111", "creditCardNumber__dep.cvv": "123"})
    check("val26-fully-satisfied-no-errors", errors == [], errors)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll embedded client-side validator checks passed -- real JS, run headlessly via node.")
