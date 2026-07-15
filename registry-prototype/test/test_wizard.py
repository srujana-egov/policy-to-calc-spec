"""Stress tests for wizard.py + data_entry.py -- the interactive layer itself, which
test_schema_builder.py doesn't touch (it drives SchemaBuilder directly, never through input()).
Mirrors ../workflow-prototype/test_wizard.py's approach: real-fixture replay plus targeted edge
cases, driven via a mocked input() against the exact code a person's keystrokes would hit.
"""

from __future__ import annotations


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import data_entry
import wizard
from builder import SchemaBuilder
from test_schema_builder import canonicalize_built, canonicalize_real_world, load_real_world
from validate import validate_schema_request

FIXTURES = Path(__file__).parent.parent / "fixtures"
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


@contextlib.contextmanager
def canned_input(answers):
    queue = list(answers)

    def fake_input(prompt=""):
        if not queue:
            raise AssertionError(f"ran out of canned input (last prompt: {prompt!r})")
        return queue.pop(0)

    with mock.patch("builtins.input", fake_input):
        yield queue


def in_scratch_cwd(fn):
    """Runs fn() inside a temp cwd (the wizard writes a preview HTML as a side effect) with
    stdout suppressed."""
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return fn()
        finally:
            os.chdir(cwd)


def run_schema_session_with(answers):
    with canned_input(answers) as queue:
        schema = in_scratch_cwd(wizard.run_schema_session)
        return schema, queue


def run_data_session_with(schema, answers):
    with canned_input(answers) as queue:
        records = in_scratch_cwd(lambda: data_entry.run_data_session(schema))
        return records, queue


def load_lines(fixture_name: str) -> list[str]:
    text = (FIXTURES / fixture_name).read_text()
    return text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")


# ---------------------------------------------------------------------------
# Real-fixture replay
# ---------------------------------------------------------------------------

def test_wiz_01_license_registry_schema_matches_golden():
    answers = load_lines("license_registry_schema_session.txt")
    schema, leftover = run_schema_session_with(answers)
    check("wiz01-all-input-consumed", not leftover, leftover)
    check("wiz01-validates-clean", not validate_schema_request(schema))
    golden = json.loads((FIXTURES / "license_registry_golden.json").read_text())
    check("wiz01-matches-golden",
          schema.model_dump(by_alias=True, exclude_none=True) == golden)
    return schema


def test_wiz_02_license_registry_data_matches_golden():
    schema, _ = run_schema_session_with(load_lines("license_registry_schema_session.txt"))
    answers = load_lines("license_registry_data_session.txt")
    records, leftover = run_data_session_with(schema, answers)
    check("wiz02-all-input-consumed", not leftover, leftover)
    golden = json.loads((FIXTURES / "license_registry_data_golden.json").read_text())
    check("wiz02-matches-golden", records == golden)


def test_wiz_02b_trade_license_schema_with_nested_field_matches_real_schema():
    """Drives the actual interactive nested-object flow (choosing 'a group of related fields',
    then configure_nested_fields()'s own sub-question loop) against digit-specs' own canonical
    trade-license example -- not just SchemaBuilder called directly, the real Q&A a person
    would answer, including one required and one optional sub-field."""
    answers = load_lines("trade_license_session.txt")
    schema, leftover = run_schema_session_with(answers)
    check("wiz02b-all-input-consumed", not leftover, leftover)
    check("wiz02b-validates-clean", not validate_schema_request(schema))
    real = canonicalize_real_world(load_real_world("trade_license.json"))
    check("wiz02b-matches-real-schema", canonicalize_built(schema) == real, canonicalize_built(schema))
    address = schema.definition.properties["address"]
    check("wiz02b-city-required", address.required == ["city"], address.required)
    check("wiz02b-pincode-optional", "pincode" not in (address.required or []))


def test_wiz_02c_nested_record_entry_via_interactive_flow():
    """The data-entry side of the same nested field -- ask_nested_record_value()'s own
    sub-question loop, driven through run_data_session() like a person would answer it."""
    schema, _ = run_schema_session_with(load_lines("trade_license_session.txt"))
    answers = [
        "ACME-001", "Acme Trading Co", "RETAIL",  # applicantId, businessName, tradeType
        "yes",                                      # include 'address'? (optional group)
        "Springfield", "",                             # city (required), pincode (optional, blank)
        "no",                                            # add another record?
        "yes",                                              # confirm
    ]
    records, leftover = run_data_session_with(schema, answers)
    check("wiz02c-all-input-consumed", not leftover, leftover)
    check("wiz02c-nested-value-correct", records[0]["address"] == {"city": "Springfield"}, records[0])


def test_wiz_02d_optional_nested_group_can_be_skipped_entirely():
    schema, _ = run_schema_session_with(load_lines("trade_license_session.txt"))
    answers = [
        "ACME-002", "Acme Trading Co 2", "WHOLESALE",
        "no",                                          # skip 'address' entirely
        "no",
        "yes",
    ]
    records, leftover = run_data_session_with(schema, answers)
    check("wiz02d-all-input-consumed", not leftover, leftover)
    check("wiz02d-no-address-key", "address" not in records[0], records[0])


# ---------------------------------------------------------------------------
# Schema-phase edge cases
# ---------------------------------------------------------------------------

def test_wiz_03_quit_cancels_mid_session():
    answers = ["cancel-test", "quit"]
    raised = False
    try:
        run_schema_session_with(answers)
    except wizard.Cancelled:
        raised = True
    check("wiz03-cancelled-raised", raised)


def test_wiz_04_invalid_type_choice_reasks():
    answers = [
        "x",
        "Name", "9", "1", "no", "no", "yes", "",   # invalid '9' first, then '1' (text, no pattern/length)
        "",                             # done adding fields
        "no", "no",                     # no unique constraint, no index
        "yes",                          # confirm
    ]
    schema, leftover = run_schema_session_with(answers)
    check("wiz04-all-input-consumed", not leftover, leftover)
    check("wiz04-field-added", "name" in schema.definition.properties)


def test_wiz_05_redo_field_after_no():
    answers = [
        "x",
        "Age", "2", "no", "no", "",      # Age: integer, no min/max, not required, no description
        "",                              # done adding fields
        "no", "no",                      # no unique, no index
        "no",                              # confirm: not right
        "age",                              # fix target: redo 'age'
        "2", "no", "yes", "the person's age",  # redo: integer, no min/max, now required, with description
        "yes",                                 # confirm: yes
    ]
    schema, leftover = run_schema_session_with(answers)
    check("wiz05-all-input-consumed", not leftover, leftover)
    age = schema.definition.properties["age"]
    check("wiz05-still-integer", age.type == "integer")
    check("wiz05-now-required", "age" in schema.definition.required)
    check("wiz05-description-applied", age.description == "the person's age")


def test_wiz_06_add_field_via_offer_fix():
    answers = [
        "x",
        "Name", "1", "no", "no", "yes", "",
        "",
        "no", "no",
        "no",                            # confirm: not right
        "add",                             # fix target: add a new field
        "Age", "2", "no", "no", "",           # the new field's own questions (integer, no min/max)
        "yes",                                 # confirm: yes
    ]
    schema, leftover = run_schema_session_with(answers)
    check("wiz06-all-input-consumed", not leftover, leftover)
    check("wiz06-two-fields", set(schema.definition.properties) == {"name", "age"})


def test_wiz_07_delete_field_then_fix_dangling_unique_constraint():
    """Deletes a field that a unique constraint referenced, confirms validate.py catches the
    resulting dangling reference, then fixes it via the 'constraints' option -- the full
    composed loop, matching the analogous test in ../workflow-prototype/test_wizard.py."""
    answers = [
        "x",
        "Name", "1", "no", "no", "yes", "",
        "Mistake", "1", "no", "no", "no", "",   # a field that will be deleted
        "",                                # done adding fields
        "yes", "name, mistake", "no",      # unique constraint referencing both fields
        "no",                                # no index
        "no",                                  # confirm: not right
        "delete mistake",                        # remove the mistaken field -> dangling unique ref
        "constraints",                             # fix target: redo unique/index constraints
        "yes", "name", "no",                          # unique: yes, just 'name', no more
        "no",                                            # no index
        "yes",                                             # confirm: yes
    ]
    schema, leftover = run_schema_session_with(answers)
    check("wiz07-all-input-consumed", not leftover, leftover)
    check("wiz07-mistake-removed", "mistake" not in schema.definition.properties)
    check("wiz07-unique-constraint-fixed", schema.x_unique == [["name"]], schema.x_unique)


def test_wiz_08_rename_schema_code_via_offer_fix():
    answers = [
        "old-code",
        "Name", "1", "no", "no", "yes", "",
        "",
        "no", "no",
        "no",                    # confirm: not right
        "rename",                  # fix target: schema's own code
        "new-code",                   # new code
        "yes",                          # confirm: yes
    ]
    schema, leftover = run_schema_session_with(answers)
    check("wiz08-all-input-consumed", not leftover, leftover)
    check("wiz08-code-renamed", schema.schemaCode == "new-code")


def test_wiz_09_unknown_fix_target_triggers_ai_fix_path():
    """Unrecognized input used to be a silent no-op ('nothing changed'); it's now treated as a
    free-text fix request and handed to apply_fix_from_description -- the tedious guided-Q&A
    complaint's actual fix. Mocked here (not a live call) so this test stays deterministic and
    network-free, matching every other test in this suite; the live path itself is exercised
    manually against the real API, same convention as draft_schema_from_description."""
    b = SchemaBuilder("x")
    b.add_field("Name", "string", required=True)
    before = set(b.properties.keys())
    with mock.patch("llm_schema_draft.apply_fix_from_description") as mock_fix:
        with canned_input(["the name field shouldn't be required"]) as queue:
            wizard.offer_fix_schema(b)
    check("wiz09-ai-fix-invoked", mock_fix.called, mock_fix.called)
    check("wiz09-called-with-builder-and-text",
          mock_fix.call_args[0] == (b, "the name field shouldn't be required"), mock_fix.call_args)
    check("wiz09-fields-unchanged-since-mocked", set(b.properties.keys()) == before)
    check("wiz09-answer-consumed", not queue)


def test_wiz_09b_known_commands_still_take_the_deterministic_path_not_ai():
    """Guard against the AI-fix fallback swallowing the still-supported deterministic commands --
    'add', 'delete FIELD_NAME', 'rename', 'constraints', and an exact existing field name must
    never reach apply_fix_from_description."""
    b = SchemaBuilder("x")
    b.add_field("Name", "string", required=True)
    with mock.patch("llm_schema_draft.apply_fix_from_description") as mock_fix:
        with canned_input(["delete name"]):
            wizard.offer_fix_schema(b)
    check("wiz09b-ai-fix-not-invoked-for-delete", not mock_fix.called)
    check("wiz09b-field-actually-removed", "name" not in b.properties, b.properties)


# ---------------------------------------------------------------------------
# Data-entry edge cases
# ---------------------------------------------------------------------------

def _simple_schema():
    b = SchemaBuilder("x")
    b.add_field("Name", "string", required=True)
    b.add_field("Status", "string", required=True, enum=["A", "B"])
    return b.build()


def test_wiz_10_required_field_blank_reasks():
    schema = _simple_schema()
    answers = ["", "Bob", "A", "no", "yes"]  # blank name reasked, then valid
    records, leftover = run_data_session_with(schema, answers)
    check("wiz10-all-input-consumed", not leftover, leftover)
    check("wiz10-name-eventually-set", records[0]["name"] == "Bob")


def test_wiz_11_invalid_enum_value_reasks():
    schema = _simple_schema()
    answers = ["Bob", "Z", "A", "no", "yes"]  # invalid enum 'Z' reasked, then valid 'A'
    records, leftover = run_data_session_with(schema, answers)
    check("wiz11-all-input-consumed", not leftover, leftover)
    check("wiz11-status-eventually-valid", records[0]["status"] == "A")


def test_wiz_12_add_delete_redo_record_via_offer_data_fix():
    schema = _simple_schema()
    answers = [
        "Bob", "A",         # record 1
        "no",                 # no more records
        "no",                   # confirm: not right
        "add",                    # add a new record
        "Alice", "B",                # record 2's answers
        "no",                          # confirm: not right again
        "delete 1",                      # remove record 1 (Bob)
        "no",                              # confirm: not right again
        "1",                                  # redo record 1 (now Alice) in place
        "Carol", "A",                            # redo answers
        "yes",                                     # confirm: yes
    ]
    records, leftover = run_data_session_with(schema, answers)
    check("wiz12-all-input-consumed", not leftover, leftover)
    check("wiz12-one-record-left", len(records) == 1, records)
    check("wiz12-final-record-is-carol", records[0] == {"name": "Carol", "status": "A"}, records)


# ---------------------------------------------------------------------------
# resolve_required_gaps -- the targeted follow-up for an LLM draft's highest-value gap
# ---------------------------------------------------------------------------

def test_wiz_13_resolve_required_gaps_only_asks_about_unstated_fields():
    """A field whose required-ness the user actually stated (required_stated=True) should get no
    question at all -- only fields the model had to guess at get asked about. Question count
    scales with what was left unsaid, not with how many fields exist."""
    builder = SchemaBuilder("x")
    builder.add_field("License Number", "string", required=True)   # stated: leave alone
    builder.add_field("Notes", "string", required=False)           # not stated: will be asked
    confidence = {
        "licenseNumber": {"required_stated": True, "details_stated": True},
        "notes": {"required_stated": False, "details_stated": True},
    }
    with canned_input(["yes"]) as queue:  # only one question should be asked
        wizard.resolve_required_gaps(builder, confidence)
    check("wiz13-only-one-question-asked", not queue, queue)
    check("wiz13-unstated-field-updated", "notes" in builder.required, builder.required)
    check("wiz13-stated-field-untouched", "licenseNumber" in builder.required, builder.required)


def test_wiz_14_resolve_required_gaps_handles_nested_fields():
    builder = SchemaBuilder("x")
    address = builder.add_field("Address", "object", required=True)
    builder.add_nested_field(address, "City", "string", required=False)
    confidence = {
        "address": {"required_stated": True, "details_stated": True},
        "address.city": {"required_stated": False, "details_stated": True},
    }
    with canned_input(["yes"]) as queue:
        wizard.resolve_required_gaps(builder, confidence)
    check("wiz14-input-consumed", not queue, queue)
    check("wiz14-nested-field-now-required", "city" in builder.properties["address"].required)


def test_wiz_15_resolve_required_gaps_can_answer_no_and_leave_optional():
    builder = SchemaBuilder("x")
    builder.add_field("Notes", "string", required=False)
    confidence = {"notes": {"required_stated": False, "details_stated": True}}
    with canned_input(["no"]) as queue:
        wizard.resolve_required_gaps(builder, confidence)
    check("wiz15-input-consumed", not queue, queue)
    check("wiz15-stays-optional", "notes" not in builder.required, builder.required)


# ---------------------------------------------------------------------------
# print_preview_completeness -- the CLI-side echo of the HTML completeness banner. Every other
# test that exercises this code path (via in_scratch_cwd) redirects stdout to a StringIO that's
# never read back, so the actual printed content was never asserted on.
# ---------------------------------------------------------------------------

def test_wiz_16_print_preview_completeness_reports_percent_and_gap_lines():
    definition = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "documents": {"type": "array", "contains": {"properties": {"status": {"const": "APPROVED"}}},
                          "minContains": 1},
        },
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wizard.print_preview_completeness(definition)
    output = buf.getvalue()
    check("wiz16-percent-line", "Preview completeness: 50%" in output, output)
    check("wiz16-full-count", "1 fully visualized" in output, output)
    check("wiz16-partial-count", "1 explained here" in output, output)
    check("wiz16-needs-review-hint", "Needs review" in output, output)
    check("wiz16-gap-keyword-listed", "contains + minContains" in output, output)
    check("wiz16-gap-meaning-listed", "APPROVED" in output, output)


def test_wiz_17_print_preview_completeness_omits_gap_section_when_fully_complete():
    definition = {"type": "object", "properties": {"a": {"type": "string"}}}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wizard.print_preview_completeness(definition)
    output = buf.getvalue()
    check("wiz17-percent-line", "Preview completeness: 100%" in output, output)
    check("wiz17-no-gap-section", "Needs review" not in output, output)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll wizard.py/data_entry.py interactive-layer checks passed against real fixtures and edge cases.")
