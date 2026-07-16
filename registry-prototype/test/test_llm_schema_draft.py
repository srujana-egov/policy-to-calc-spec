"""Tests for llm_schema_draft.py's tool dispatcher -- the deterministic layer an LLM's tool calls
actually land on. No network calls here (that's covered separately, live, against the real OpenAI
API): this locks in that each tool validates its own inputs the same way the guided wizard's
underlying SchemaBuilder methods already do, and that confidence flags get recorded correctly,
without needing a real model in the loop.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
from pathlib import Path

from builder import SchemaBuilder
from llm_schema_draft import _execute_tool_call, log_judge_result

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def test_01_add_field_records_name_and_confidence():
    builder = SchemaBuilder("x")
    confidence = {}
    result = _execute_tool_call(builder, confidence, "add_field", {
        "label": "License Number", "type": "string", "required": True,
        "required_stated": True, "details_stated": True,
    })
    check("01-field-created", result["field_name"] == "licenseNumber", result)
    check("01-required-applied", "licenseNumber" in builder.required)
    check("01-confidence-recorded", confidence["licenseNumber"] == {
        "required_stated": True, "details_stated": True,
    }, confidence)


def test_02_add_field_defaults_confidence_flags_when_missing():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Status", "type": "string", "required": False,
    })
    check("02-defaults-to-not-stated", confidence["status"] == {
        "required_stated": False, "details_stated": False,
    }, confidence)


def test_03_add_nested_field_on_unknown_parent_returns_error_not_crash():
    builder = SchemaBuilder("x")
    confidence = {}
    result = _execute_tool_call(builder, confidence, "add_nested_field", {
        "parent_name": "ghost", "label": "City", "type": "string", "required": False,
        "required_stated": False, "details_stated": False,
    })
    check("03-unknown-parent-error", "error" in result, result)
    check("03-nothing-added", not confidence)


def test_04_add_nested_field_on_non_object_parent_returns_error_not_crash():
    """SchemaBuilder.add_nested_field raises ValueError for this -- confirms the dispatcher
    converts it into a tool-result error the model can see and react to, rather than an
    unhandled exception that would kill the whole drafting loop."""
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Status", "type": "string", "required": False,
        "required_stated": True, "details_stated": True,
    })
    result = _execute_tool_call(builder, confidence, "add_nested_field", {
        "parent_name": "status", "label": "City", "type": "string", "required": False,
        "required_stated": False, "details_stated": False,
    })
    check("04-non-object-parent-error", "error" in result, result)


def test_05_add_nested_field_records_dotted_confidence_key():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Address", "type": "object", "required": False,
        "required_stated": True, "details_stated": False,
    })
    result = _execute_tool_call(builder, confidence, "add_nested_field", {
        "parent_name": "address", "label": "City", "type": "string", "required": True,
        "required_stated": True, "details_stated": True,
    })
    check("05-field-id", result["field_id"] == "address.city", result)
    check("05-confidence-key-dotted", "address.city" in confidence, confidence)
    check("05-nested-required-applied", "city" in builder.properties["address"].required)


def test_06_add_unique_constraint_rejects_unknown_field():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "License Number", "type": "string", "required": True,
        "required_stated": True, "details_stated": True,
    })
    result = _execute_tool_call(builder, confidence, "add_unique_constraint", {
        "field_names": ["licenseNumber", "ghost"],
    })
    check("06-unknown-field-rejected", "error" in result, result)
    check("06-no-constraint-added", not builder.unique_constraints)


def test_07_add_unique_constraint_and_add_index_apply_cleanly():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "License Number", "type": "string", "required": True,
        "required_stated": True, "details_stated": True,
    })
    ok1 = _execute_tool_call(builder, confidence, "add_unique_constraint", {
        "field_names": ["licenseNumber"],
    })
    ok2 = _execute_tool_call(builder, confidence, "add_index", {
        "field_name": "licenseNumber", "method": "gin",
    })
    check("07-unique-ok", ok1 == {"ok": True}, ok1)
    check("07-index-ok", ok2 == {"ok": True}, ok2)
    check("07-unique-applied", builder.unique_constraints == [["licenseNumber"]])
    check("07-index-applied", builder.indexes[0].method == "gin")


def test_08_unknown_tool_name_returns_error_not_crash():
    builder = SchemaBuilder("x")
    result = _execute_tool_call(builder, {}, "delete_everything", {})
    check("08-unknown-tool-error", "error" in result, result)


def test_09_add_conditional_applies_and_rejects_unknown_fields():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Applicant Type", "type": "string", "required": True,
        "required_stated": True, "details_stated": True,
    })
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Aadhaar Number", "type": "string", "required": False,
        "required_stated": False, "details_stated": True,
    })
    ok = _execute_tool_call(builder, confidence, "add_conditional", {
        "if_field": "applicantType", "if_value": "Individual", "then_required": ["aadhaarNumber"],
    })
    check("09-conditional-ok", ok == {"ok": True}, ok)
    check("09-conditional-applied", len(builder.conditionals) == 1, builder.conditionals)

    bad = _execute_tool_call(builder, confidence, "add_conditional", {
        "if_field": "applicantType", "if_value": "X", "then_required": ["ghostField"],
    })
    check("09-conditional-rejects-unknown-field", "error" in bad, bad)


def test_10_add_dependent_required_applies_and_rejects_unknown_fields():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Credit Card Number", "type": "string", "required": False,
        "required_stated": False, "details_stated": True,
    })
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Cvv", "type": "string", "required": False,
        "required_stated": False, "details_stated": True,
    })
    ok = _execute_tool_call(builder, confidence, "add_dependent_required", {
        "field": "creditCardNumber", "requires": ["cvv"],
    })
    check("10-dependent-required-ok", ok == {"ok": True}, ok)
    check("10-dependent-required-applied", builder.dependent_required == {"creditCardNumber": ["cvv"]},
          builder.dependent_required)

    bad = _execute_tool_call(builder, confidence, "add_dependent_required", {
        "field": "ghost", "requires": ["cvv"],
    })
    check("10-dependent-required-rejects-unknown-field", "error" in bad, bad)


def test_11_add_one_of_field_creates_field_and_stores_alternatives():
    builder = SchemaBuilder("x")
    confidence = {}
    result = _execute_tool_call(builder, confidence, "add_one_of_field", {
        "label": "Contact Method",
        "description": "Either an email or a phone contact",
        "alternatives": [
            {"properties": {"email": {"type": "string"}}, "required": ["email"]},
            {"properties": {"phone": {"type": "string"}}, "required": ["phone"]},
        ],
    })
    check("11-field-created", result["field_name"] == "contactMethod", result)
    check("11-stored-as-oneof-dict", "oneOf" in builder.properties["contactMethod"],
          builder.properties["contactMethod"])
    check("11-two-alternatives", len(builder.properties["contactMethod"]["oneOf"]) == 2)


def test_12_add_dependent_schema_applies_and_rejects_unknown_trigger():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Credit Card Number", "type": "string", "required": False,
        "required_stated": False, "details_stated": True,
    })
    ok = _execute_tool_call(builder, confidence, "add_dependent_schema", {
        "trigger_field": "creditCardNumber",
        "properties": {"Cvv": {"type": "string", "pattern": "^[0-9]{3}$"}},
        "required": ["Cvv"],
    })
    check("12-ok", ok == {"ok": True}, ok)
    check("12-applied", "cvv" in builder.dependent_schemas["creditCardNumber"]["properties"],
          builder.dependent_schemas)

    bad = _execute_tool_call(builder, confidence, "add_dependent_schema", {
        "trigger_field": "ghost", "properties": {"a": {"type": "string"}},
    })
    check("12-rejects-unknown-trigger", "error" in bad, bad)


def test_13_add_pattern_properties_applies_and_rejects_empty_pattern():
    builder = SchemaBuilder("x")
    confidence = {}
    ok = _execute_tool_call(builder, confidence, "add_pattern_properties", {
        "pattern": "^x-", "value_schema": {"type": "string"},
    })
    check("13-ok", ok == {"ok": True}, ok)
    check("13-applied", builder.pattern_properties.get("^x-") == {"type": "string"}, builder.pattern_properties)

    bad = _execute_tool_call(builder, confidence, "add_pattern_properties", {
        "pattern": "", "value_schema": {"type": "string"},
    })
    check("13-rejects-empty-pattern", "error" in bad, bad)


def test_14_define_reusable_schema_and_add_ref_field_apply_and_reject_unknown_defs():
    builder = SchemaBuilder("x")
    confidence = {}
    ok1 = _execute_tool_call(builder, confidence, "define_reusable_schema", {
        "name": "Address", "schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    })
    result = _execute_tool_call(builder, confidence, "add_ref_field", {
        "label": "Billing Address", "defs_name": "Address", "required": True,
    })
    check("14-define-ok", ok1 == {"ok": True}, ok1)
    check("14-field-created", result["field_name"] == "billingAddress", result)
    check("14-ref-shape", builder.properties["billingAddress"] == {"$ref": "#/$defs/Address"},
          builder.properties["billingAddress"])
    check("14-required-applied", "billingAddress" in builder.required, builder.required)

    bad = _execute_tool_call(builder, confidence, "add_ref_field", {
        "label": "Other", "defs_name": "Ghost",
    })
    check("14-rejects-unknown-defs", "error" in bad, bad)


def test_15_add_not_constraint_assembles_schema_from_flattened_args():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field", {
        "label": "Username", "type": "string", "required": True,
        "required_stated": True, "details_stated": True,
    })
    ok = _execute_tool_call(builder, confidence, "add_not_constraint", {
        "field_name": "username", "not_pattern": "^admin$",
    })
    check("15-ok", ok == {"ok": True}, ok)
    check("15-not-applied", builder.properties["username"].not_ == {"pattern": "^admin$"},
          builder.properties["username"])

    bad = _execute_tool_call(builder, confidence, "add_not_constraint", {
        "field_name": "ghost", "not_pattern": "^x$",
    })
    check("15-rejects-unknown-field", "error" in bad, bad)


def test_16_add_raw_property_stores_verbatim():
    builder = SchemaBuilder("x")
    confidence = {}
    result = _execute_tool_call(builder, confidence, "add_raw_property", {
        "label": "Tags", "raw_schema": {"type": "array", "prefixItems": [{"type": "string"}]},
        "required": True,
    })
    check("16-field-created", result["field_name"] == "tags", result)
    check("16-stored-verbatim", builder.properties["tags"]["prefixItems"] == [{"type": "string"}],
          builder.properties["tags"])
    check("16-required-applied", "tags" in builder.required, builder.required)


def test_16b_remove_field_drops_an_existing_field():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field",
                        {"label": "Billing Address", "type": "string", "required": False,
                         "required_stated": True, "details_stated": True})
    check("16b-field-present-before-removal", "billingAddress" in builder.properties)
    result = _execute_tool_call(builder, confidence, "remove_field", {"field_name": "billingAddress"})
    check("16b-ok", result == {"ok": True}, result)
    check("16b-field-removed", "billingAddress" not in builder.properties, builder.properties)


def test_16c_remove_field_rejects_unknown_field():
    builder = SchemaBuilder("x")
    confidence = {}
    result = _execute_tool_call(builder, confidence, "remove_field", {"field_name": "ghost"})
    check("16c-error-returned", "error" in result, result)


def test_16d_set_required_flips_required_without_recreating_the_field():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field",
                        {"label": "Promo Code", "type": "string", "required": True,
                         "required_stated": True, "details_stated": True, "pattern": "^[A-Z0-9]{6}$"})
    check("16d-required-before", "promoCode" in builder.required)
    result = _execute_tool_call(builder, confidence, "set_required",
                                 {"field_name": "promoCode", "required": False})
    check("16d-ok", result == {"ok": True}, result)
    check("16d-no-longer-required", "promoCode" not in builder.required, builder.required)
    check("16d-pattern-preserved-not-recreated", builder.properties["promoCode"].pattern == "^[A-Z0-9]{6}$",
          builder.properties["promoCode"])


def test_16e_set_required_is_idempotent_and_rejects_unknown_field():
    builder = SchemaBuilder("x")
    confidence = {}
    _execute_tool_call(builder, confidence, "add_field",
                        {"label": "Name", "type": "string", "required": False,
                         "required_stated": True, "details_stated": True})
    result = _execute_tool_call(builder, confidence, "set_required", {"field_name": "name", "required": False})
    check("16e-idempotent-ok", result == {"ok": True}, result)
    check("16e-still-not-required", "name" not in builder.required, builder.required)
    unknown_result = _execute_tool_call(builder, confidence, "set_required",
                                         {"field_name": "ghost", "required": True})
    check("16e-unknown-field-error", "error" in unknown_result, unknown_result)


def test_16f_set_required_cleans_up_a_dangling_required_entry_for_a_nonexistent_field():
    """A real bug found live-testing: a field can end up listed in `required` with no matching
    entry in `properties` (see test_wiz_15b in test_wizard.py for how this actually happens).
    Before this fix, asking to un-require a field in that state always returned an error and
    never touched `required` -- an unrecoverable loop, since that was the AI's only way to try to
    fix it. 'make sure this ISN'T required' must be satisfiable even for a phantom field, since
    that's exactly the escape hatch needed."""
    builder = SchemaBuilder("x")
    confidence = {}
    builder.required.append("ghostField")  # simulates the dangling state directly
    result = _execute_tool_call(builder, confidence, "set_required",
                                 {"field_name": "ghostField", "required": False})
    check("16f-ok", result.get("ok") is True, result)
    check("16f-dangling-entry-removed", "ghostField" not in builder.required, builder.required)


def test_16g_set_required_still_rejects_requiring_a_nonexistent_field():
    """The escape hatch only applies to un-requiring a phantom field -- there's no way to
    satisfy "make this required" for a field that was never actually added, so that must still
    error, not silently add a bogus entry to `required`."""
    builder = SchemaBuilder("x")
    confidence = {}
    result = _execute_tool_call(builder, confidence, "set_required",
                                 {"field_name": "ghostField", "required": True})
    check("16g-still-errors", "error" in result, result)
    check("16g-nothing-added", "ghostField" not in builder.required, builder.required)


def test_17_log_judge_result_writes_valid_jsonl_with_expected_fields():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "judge_log.jsonl")
        log_judge_result("license-registry", "a form with a license number, required",
                          {"type": "object", "properties": {"licenseNumber": {"type": "string"}}},
                          {"licenseNumber": {"required_stated": True, "details_stated": True}},
                          {"ok": True, "issues": []}, log_path=log_path)
        lines = Path(log_path).read_text().strip().split("\n")
    check("17-one-line-written", len(lines) == 1, lines)
    entry = json.loads(lines[0])
    check("17-schema-code-present", entry["schema_code"] == "license-registry", entry)
    check("17-description-present", "license number" in entry["description"], entry)
    check("17-definition-present", entry["definition"]["properties"]["licenseNumber"]["type"] == "string", entry)
    check("17-judgment-present", entry["judgment"] == {"ok": True, "issues": []}, entry)
    check("17-human-verdict-defaults-none", entry["human_verdict"] is None, entry)
    check("17-timestamp-present", bool(entry.get("timestamp")), entry)


def test_18_log_judge_result_appends_not_overwrites():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "judge_log.jsonl")
        for i in range(3):
            log_judge_result(f"schema-{i}", "desc", {"type": "object", "properties": {}}, {},
                              {"ok": True, "issues": []}, log_path=log_path)
        lines = Path(log_path).read_text().strip().split("\n")
    check("18-three-lines-appended", len(lines) == 3, lines)
    codes = [json.loads(line)["schema_code"] for line in lines]
    check("18-all-three-present", codes == ["schema-0", "schema-1", "schema-2"], codes)


def test_19b_log_judge_result_records_preview_coverage_snapshot():
    """The 'preview_coverage' addition: a business user's low-coverage schema and a high-coverage
    schema must be distinguishable in the log purely from this field, without needing to
    re-render or re-scan the stored `definition` -- that's what eventually lets low coverage be
    correlated against human corrections and judge confidence."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "judge_log.jsonl")
        coverage = {"full": 3, "partial": 1, "none": 0, "total": 4, "percent": 75,
                    "conformance_summary": {"executed": 2, "passed_as_expected": 2, "surprising": 0,
                                             "errored": 0, "inconclusive": 0, "gaps_with_tests": 1,
                                             "gaps_total": 1}}
        log_judge_result("x", "y", {"type": "object", "properties": {}}, {}, {"ok": True, "issues": []},
                          preview_coverage=coverage, log_path=log_path)
        entry = json.loads(Path(log_path).read_text().strip())
    check("19b-coverage-present", entry["preview_coverage"] == coverage, entry)
    check("19b-percent-directly-queryable", entry["preview_coverage"]["percent"] == 75, entry)
    check("19b-conformance-summary-nested", entry["preview_coverage"]["conformance_summary"]["executed"] == 2, entry)


def test_19c_log_judge_result_defaults_preview_coverage_to_none():
    """Backward compatibility: existing callers that don't pass preview_coverage (or a future
    caller that genuinely has none to report) must not crash and must get an explicit None, not a
    missing key -- so a later analysis script can rely on the key always being present."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = str(Path(tmp) / "judge_log.jsonl")
        log_judge_result("x", "y", {"type": "object", "properties": {}}, {}, {"ok": True, "issues": []},
                          log_path=log_path)
        entry = json.loads(Path(log_path).read_text().strip())
    check("19c-key-present", "preview_coverage" in entry, entry)
    check("19c-defaults-to-none", entry["preview_coverage"] is None, entry)


def test_19_log_judge_result_never_raises_on_write_failure():
    # A path inside a directory that doesn't exist -- open() will raise OSError internally,
    # which log_judge_result must swallow rather than crash the wizard's main flow over.
    bad_path = "/this/directory/does/not/exist/judge_log.jsonl"
    try:
        log_judge_result("x", "y", {"type": "object", "properties": {}}, {}, {"ok": True, "issues": []},
                          log_path=bad_path)
        check("19-does-not-raise", True)
    except OSError:
        check("19-does-not-raise", False, "log_judge_result raised OSError instead of swallowing it")


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll llm_schema_draft.py tool-dispatcher checks passed.")
