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


def test_wiz_04b_invalid_schema_code_reasks_immediately():
    """A real gap found live at a demo: an obviously-wrong schema name (a typo, or someone
    unfamiliar with the naming rule typing a sentence) used to be silently accepted and only
    surfaced as a validation error much later, deep into drafting. ask_schema_code() now catches
    it the moment it's typed, with a specific reason, and re-asks -- consistent with every other
    invalid-input-reasks-immediately convention in this wizard (see test_wiz_04 above)."""
    answers = [
        "i dont understand", "trade license with spaces", "trade-license",  # two invalid, then valid
        "Name", "1", "no", "no", "yes", "",
        "",
        "no", "no",
        "yes",
    ]
    schema, leftover = run_schema_session_with(answers)
    check("wiz04b-all-input-consumed", not leftover, leftover)
    check("wiz04b-valid-code-accepted", schema.schemaCode == "trade-license", schema.schemaCode)


def test_wiz_04c_yes_no_reprompts_helpfully_after_repeated_confusion():
    """A real gap found live at a demo: someone typing something like 'i dont understand' at a
    yes/no prompt got only a bare 'please answer yes or no' every time, with no way to recover
    the actual question if it had scrolled off screen. After a second wrong answer, the question
    itself gets re-shown, plus a reminder that 'quit' is always available."""
    with mock.patch("builtins.input", side_effect=["i dont understand", "still confused", "yes"]):
        with io.StringIO() as buf:
            with contextlib.redirect_stdout(buf):
                result = wizard.ask_yes_no("Is 'applicantType' required on every record?")
            output = buf.getvalue()
    check("wiz04c-eventually-returns-true", result is True, result)
    check("wiz04c-question-repeated", "the question again: Is 'applicantType'" in output, output)
    check("wiz04c-quit-mentioned", "quit" in output, output)


def test_wiz_04d_yes_no_help_shows_specific_explanation_without_deciding_the_answer():
    """The 'help' escape hatch is deliberately NOT 'let an AI interpret what you meant' -- it only
    ever explains the fixed question in plain language, then still requires an actual yes/no
    answer afterward. Typing 'help' must never itself count as an answer."""
    with mock.patch("builtins.input", side_effect=["help", "yes"]):
        with io.StringIO() as buf:
            with contextlib.redirect_stdout(buf):
                result = wizard.ask_yes_no(
                    "Is 'aadhaarNumber' required on every record?",
                    help_text="This means: will every single record need a value for 'aadhaarNumber'?")
            output = buf.getvalue()
    check("wiz04d-still-returns-true-after-help", result is True, result)
    check("wiz04d-specific-help-shown", "will every single record need a value for 'aadhaarNumber'" in output, output)


def test_wiz_04e_yes_no_help_falls_back_to_a_generic_message_when_none_given():
    with mock.patch("builtins.input", side_effect=["?", "no"]):
        with io.StringIO() as buf:
            with contextlib.redirect_stdout(buf):
                result = wizard.ask_yes_no("Some question with no help_text")
            output = buf.getvalue()
    check("wiz04e-still-returns-false-after-help", result is False, result)
    check("wiz04e-generic-fallback-shown", "no extra explanation available" in output, output)


def test_wiz_04f_every_ask_yes_no_call_site_in_wizard_has_help_text():
    """Guards against the fallback ('no extra explanation available for this one') silently
    becoming the norm as new prompts get added -- every current call site was deliberately given
    a specific explanation; a future one that forgets should be caught here, not slip through."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(wizard))
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ask_yes_no":
            if not any(kw.arg == "help_text" for kw in node.keywords):
                missing.append(getattr(node, "lineno", "?"))
    check("wiz04f-no-call-sites-missing-help-text", not missing, missing)


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


def test_wiz_15b_resolve_required_gaps_skips_a_field_removed_mid_draft():
    """A real bug found live-testing the free-text drafting flow: the model added a plain field,
    then replaced it with a differently-shaped one under the same label (colliding into
    'fieldName2') and removed the original -- but confidence (tracked separately from the
    builder, never cleaned up by remove_field) still had a stale entry for the removed name.
    Before this fix, answering 'yes' here would add a required-list entry for a field that no
    longer exists -- and nothing could ever remove it afterward, since set_required/remove_field
    both refuse to touch a field name that isn't in `properties`, an unrecoverable loop."""
    builder = SchemaBuilder("x")
    builder.add_field("Real Field", "string", required=True)
    confidence = {
        "realField": {"required_stated": True, "details_stated": True},
        "uploadedDocuments": {"required_stated": False, "details_stated": False},  # stale -- no such field
    }
    with canned_input([]) as queue:  # no question should be asked for the stale entry
        wizard.resolve_required_gaps(builder, confidence)
    check("wiz15b-no-question-asked", not queue, queue)
    check("wiz15b-not-added-to-required", "uploadedDocuments" not in builder.required, builder.required)
    check("wiz15b-real-field-untouched", "realField" in builder.required, builder.required)


def test_wiz_15c_resolve_required_gaps_skips_a_nested_field_whose_parent_is_gone():
    builder = SchemaBuilder("x")
    builder.add_field("Real Field", "string", required=True)
    confidence = {
        "realField": {"required_stated": True, "details_stated": True},
        "ghostParent.city": {"required_stated": False, "details_stated": True},  # parent never existed
    }
    with canned_input([]) as queue:
        wizard.resolve_required_gaps(builder, confidence)
    check("wiz15c-no-question-asked", not queue, queue)
    check("wiz15c-no-crash", "realField" in builder.required, builder.required)


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
