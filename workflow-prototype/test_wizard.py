"""Stress tests for wizard.py -- the interactive layer itself, which test_workflow_builder.py
doesn't touch (it drives WorkflowBuilder directly, never through input()). Every bug found by
hand this project (self-loop discoverability, seconds-unit SLA, the yes/no wording bug, "no"
discarding the whole session) lived here, so this is where a regression would actually show up.

Two kinds of checks:
1. Real-fixture replays (fixtures/*_session_input.txt) driven through the exact same
   run_session() a person's keystrokes would drive, compared against either the real production
   config (bpa_original.json, in DIGIT's own businessService JSON shape) or a golden snapshot
   captured from a prior verified wizard run (*_golden.json, in ProcessDefinitionInput shape).
2. Targeted edge cases: cancel, invalid input retry, self-loops, the fix-one-thing-not-everything
   flow (redo/edit/delete), and its failure modes (dangling reference after a delete, deleting
   the INITIAL state, an unknown fix target).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import wizard
from builder import WorkflowBuilder
from validate import validate_process_definition

FIXTURES = Path(__file__).parent / "fixtures"
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


@contextlib.contextmanager
def canned_input(answers):
    """Patches builtins.input to pop canned answers in order. Raises a clear error if a test
    under-provides answers, instead of hanging or raising a bare StopIteration."""
    queue = list(answers)

    def fake_input(prompt=""):
        if not queue:
            raise AssertionError(f"ran out of canned input (last prompt: {prompt!r})")
        return queue.pop(0)

    with mock.patch("builtins.input", fake_input):
        yield queue


def run_session_with(answers):
    """Drives the real wizard.run_session() -- the exact code path a person's keystrokes would
    hit -- in a scratch cwd (run_session writes a preview HTML as a side effect) with stdout
    suppressed. Returns (process, leftover_answers) so a test can also assert every provided
    answer was actually consumed."""
    with canned_input(answers) as queue:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    process = wizard.run_session()
            finally:
                os.chdir(cwd)
        return process, queue


def load_lines(fixture_name: str) -> list[str]:
    text = (FIXTURES / fixture_name).read_text()
    return text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")


def canonical(process) -> dict:
    """State -> type/sla/actions, keyed and compared independent of list ordering."""
    return {
        s.code: {
            "type": s.type,
            "sla": s.sla,
            "actions": {
                a.code: (a.nextState, frozenset(a.roles), a.assigneeCheck) for a in s.actions
            },
        }
        for s in process.states
    }


def canonical_golden(golden: dict) -> dict:
    return {
        s["code"]: {
            "type": s["type"],
            "sla": s.get("sla"),
            "actions": {
                a["code"]: (a["nextState"], frozenset(a.get("roles", [])), a.get("assigneeCheck", False))
                for a in s.get("actions", [])
            },
        }
        for s in golden["states"]
    }


def canonical_actions_only(process) -> dict:
    return {s.code: {a.code: (a.nextState, frozenset(a.roles)) for a in s.actions} for s in process.states}


# The real config's state codes were hand-written before this wizard existed and are terser
# than what slugify() derives from the same human-readable names (e.g. "In Progress" ->
# IN_PROGRESS, not INPROGRESS) -- a cosmetic difference already confirmed harmless during manual
# verification earlier, not a structural one, so the comparison aliases them rather than treating
# the wizard's own naming convention as a bug.
REAL_TO_BUILT_CODE_ALIASES = {
    "PENDINGAPPROVAL": "PENDING_APPROVAL",
    "DOC_VERIFICATION_PENDING": "DOCUMENT_VERIFICATION_PENDING",
    "FIELDINSPECTION_PENDING": "FIELD_INSPECTION_PENDING",
    "INPROGRESS": "IN_PROGRESS",
    "PENDING_APPL_FEE_PAYMENT": "PENDING_APPLICATION_FEE_PAYMENT",
    "PENDING_SANC_FEE_PAYMENT": "PENDING_SANCTION_FEE_PAYMENT",
}


def canonical_real_config(real: dict) -> dict:
    """DIGIT's own businessService JSON shape: state=None is the pre-creation pseudo-entry
    (actions valid before any state exists yet) -- functionally identical to the INITIAL state's
    own self-loop action of the same name, already captured there, so it's excluded here rather
    than double counted."""
    def alias(code):
        return REAL_TO_BUILT_CODE_ALIASES.get(code, code)

    result = {}
    for entry in real["states"]:
        code = entry["state"]
        if code is None:
            continue
        result[alias(code)] = {
            a["action"]: (alias(a["nextState"]), frozenset(a["roles"])) for a in (entry["actions"] or [])
        }
    return result


# ---------------------------------------------------------------------------
# Real-fixture replays
# ---------------------------------------------------------------------------

def test_wiz_01_bpa_replay_matches_real_production_config():
    answers = load_lines("bpa_session_input.txt")
    process, leftover = run_session_with(answers)
    check("wiz01-all-input-consumed", not leftover, leftover)
    check("wiz01-validates-clean", not validate_process_definition(process))

    real = json.loads((FIXTURES / "bpa_original.json").read_text())
    check("wiz01-matches-real-bpa-config", canonical_actions_only(process) == canonical_real_config(real))


def test_wiz_02_bpa_replay_matches_golden_snapshot():
    process, _ = run_session_with(load_lines("bpa_session_input.txt"))
    golden = json.loads((FIXTURES / "bpa_golden.json").read_text())
    check("wiz02-matches-golden-snapshot", canonical(process) == canonical_golden(golden))
    check("wiz02-14-states", len(process.states) == 14, len(process.states))


def test_wiz_03_bpa_low_replay_matches_golden_snapshot():
    answers = load_lines("bpa_low_session_input.txt")
    process, leftover = run_session_with(answers)
    check("wiz03-all-input-consumed", not leftover, leftover)
    check("wiz03-validates-clean", not validate_process_definition(process))
    golden = json.loads((FIXTURES / "bpa_low_golden.json").read_text())
    check("wiz03-matches-golden-snapshot", canonical(process) == canonical_golden(golden))
    check("wiz03-10-states", len(process.states) == 10, len(process.states))


def test_wiz_04_pgr67_replay_matches_golden_snapshot():
    answers = load_lines("pgr67_session_input.txt")
    process, leftover = run_session_with(answers)
    check("wiz04-all-input-consumed", not leftover, leftover)
    check("wiz04-validates-clean", not validate_process_definition(process))
    golden = json.loads((FIXTURES / "pgr67_golden.json").read_text())
    check("wiz04-matches-golden-snapshot", canonical(process) == canonical_golden(golden))


# ---------------------------------------------------------------------------
# Low-level helper edge cases
# ---------------------------------------------------------------------------

def test_wiz_05_yes_no_reasks_on_garbage():
    with canned_input(["blah", "yes"]) as queue:
        result = wizard.ask_yes_no("Does this work?")
    check("wiz05-eventually-true", result is True)
    check("wiz05-both-answers-consumed", not queue)


def test_wiz_06_sla_seconds_unit():
    with canned_input(["86400 seconds"]):
        result = wizard.ask_sla_ms()
    check("wiz06-seconds-to-ms", result == 86_400_000, result)


def test_wiz_07_sla_invalid_unit_reasks_instead_of_guessing():
    with canned_input(["5 fortnights", "2 days"]) as queue:
        result = wizard.ask_sla_ms()
    check("wiz07-reasked-correctly", result == 172_800_000, result)
    check("wiz07-both-answers-consumed", not queue)


def test_wiz_08_quit_cancels_mid_session():
    answers = [
        "Cancel Test", "CANCEL_TEST", "", "",  # name/code/description/overall sla
        "Start", "",  # first state name, its sla
        "yes",  # has_next
        "quit",  # typed instead of an action label
    ]
    raised = False
    try:
        run_session_with(answers)
    except wizard.Cancelled:
        raised = True
    check("wiz08-cancelled-raised", raised)


# ---------------------------------------------------------------------------
# Targeted-fix flow (the "don't make me redo everything" behavior)
# ---------------------------------------------------------------------------

def test_wiz_09_redo_one_state_after_no_leaves_rest_untouched():
    answers = [
        "Test Flow", "TEST_FLOW", "", "",       # name/code/description/overall sla
        "Start", "",                             # first state name, sla
        "yes",                                   # has_next
        "Approve", "no", "", "Done",             # action -> new state 'Done', no roles
        "no",                                     # nothing else from Start
        "",                                       # Done's sla
        "no", "yes",                              # Done: no next step, good outcome -> TERMINAL_SUCCESS
        "no",                                      # confirm: not right
        "START",                                   # fix target: redo START
        "",                                         # START's sla again
        "yes",                                      # has_next
        "Approve", "yes", "APPROVER", "DONE",       # this time: existing state, with a role
        "no",                                        # nothing else
        "yes",                                        # confirm: yes
    ]
    process, leftover = run_session_with(answers)
    check("wiz09-all-input-consumed", not leftover, leftover)
    by_code = {s.code: s for s in process.states}
    check("wiz09-still-two-states", len(process.states) == 2, len(process.states))
    approve = by_code["START"].actions[0]
    check("wiz09-role-applied", approve.roles == ["APPROVER"], approve.roles)
    check("wiz09-still-points-to-done", approve.nextState == "DONE", approve.nextState)
    check("wiz09-done-untouched", by_code["DONE"].type == "TERMINAL_SUCCESS")


def test_wiz_10_edit_process_fields_changes_only_named_fields():
    answers = [
        "Test Flow", "TEST_FLOW", "original description", "",
        "Start", "",
        "yes",
        "Approve", "no", "", "Done",               # Start -> new state 'Done'
        "no",
        "", "no", "yes",                            # Done: sla, no next, good outcome -> terminal
        "no",                                       # confirm: not right
        "process",                                  # fix target: the workflow's own fields
        "Renamed Flow", "",                          # new name, keep code
        "",                                           # keep description
        "no",                                          # don't change SLA
        "yes",                                          # confirm: yes
    ]
    process, leftover = run_session_with(answers)
    check("wiz10-all-input-consumed", not leftover, leftover)
    check("wiz10-name-changed", process.name == "Renamed Flow", process.name)
    check("wiz10-code-unchanged", process.code == "TEST_FLOW", process.code)
    check("wiz10-description-unchanged", process.description == "original description", process.description)


def test_wiz_11_delete_state_then_fix_dangling_reference():
    """Deletes a state that another state's action still points to, confirms validate.py
    catches the resulting dangling nextState, then fixes the *other* state to resolve it --
    the full composed loop, not just one isolated fix."""
    answers = [
        "Two Branch", "TWO_BRANCH", "", "",
        "Start", "",
        "yes",
        "Approve", "no", "", "Approved",          # Start -> new 'Approved'
        "yes",
        "Mistake", "no", "", "Oops",               # Start -> new 'Oops' (the one to delete)
        "no",
        "", "no", "yes",                            # Approved: sla, no next, good outcome
        "", "no", "yes",                            # Oops: sla, no next, good outcome
        "no",                                          # confirm: not right
        "delete OOPS",                                 # remove the mistaken state
        "START",                                        # validation now fails (dangling ref) -> fix START
        "",                                              # START's sla again
        "yes",
        "Approve", "yes", "", "APPROVED",                 # redo: only the real action, to the existing state
        "no",
        "yes",                                              # confirm: yes
    ]
    process, leftover = run_session_with(answers)
    check("wiz11-all-input-consumed", not leftover, leftover)
    codes = {s.code for s in process.states}
    check("wiz11-oops-removed", "OOPS" not in codes, codes)
    check("wiz11-two-states-left", codes == {"START", "APPROVED"}, codes)
    start = next(s for s in process.states if s.code == "START")
    check("wiz11-single-action-left", len(start.actions) == 1, [a.code for a in start.actions])


def test_wiz_12_delete_initial_state_is_blocked():
    b = WorkflowBuilder(name="X", code="X")
    initial = b.add_initial_state("Start")
    b.add_action_to_new_state(initial, "Go", "End", new_state_type="TERMINAL_SUCCESS")
    with canned_input([f"delete {initial}"]) as queue:
        wizard.offer_fix(b)
    check("wiz12-initial-not-removed", initial in b.states)
    check("wiz12-answer-consumed", not queue)


def test_wiz_14_initial_state_cannot_be_marked_terminal():
    """Saying 'no next step' for the very first stage must not silently overwrite its INITIAL
    type -- that would leave the process with no INITIAL state at all (not representable in the
    real schema). The wizard should explain and require a real next step instead."""
    answers = [
        "X", "X", "", "",
        "Start", "",
        "no",                      # has_next: no -- should NOT ask "is this a good outcome?"
        "Go", "no", "", "End",     # falls through into the action loop instead
        "no",
        "", "no", "yes",           # End: sla, no next, good outcome -> terminal
        "yes",                     # confirm: yes
    ]
    process, leftover = run_session_with(answers)
    check("wiz14-all-input-consumed", not leftover, leftover)
    by_code = {s.code: s for s in process.states}
    check("wiz14-start-still-initial", by_code["START"].type == "INITIAL")
    check("wiz14-start-has-the-real-action", [a.code for a in by_code["START"].actions] == ["GO"])
    check("wiz14-end-is-terminal", by_code["END"].type == "TERMINAL_SUCCESS")


def test_wiz_13_unknown_fix_target_does_not_crash():
    b = WorkflowBuilder(name="X", code="X")
    initial = b.add_initial_state("Start")
    b.add_action_to_new_state(initial, "Go", "End", new_state_type="TERMINAL_SUCCESS")
    before = set(b.states.keys())
    with canned_input(["NOT_A_REAL_STATE"]) as queue:
        wizard.offer_fix(b)
    check("wiz13-states-unchanged", set(b.states.keys()) == before)
    check("wiz13-answer-consumed", not queue)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll wizard.py interactive-layer checks passed against real fixtures and edge cases.")
