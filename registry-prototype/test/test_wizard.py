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


def test_wiz_09_unknown_fix_target_does_not_crash():
    b = SchemaBuilder("x")
    b.add_field("Name", "string", required=True)
    before = set(b.properties.keys())
    with canned_input(["NOT_A_REAL_FIELD"]) as queue:
        wizard.offer_fix_schema(b)
    check("wiz09-fields-unchanged", set(b.properties.keys()) == before)
    check("wiz09-answer-consumed", not queue)


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


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll wizard.py/data_entry.py interactive-layer checks passed against real fixtures and edge cases.")
