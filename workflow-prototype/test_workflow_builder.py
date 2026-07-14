"""Tests: (1) build the real trade-license-approval example from workflow.yaml via the builder
and confirm it validates cleanly and matches the expected shape, (2) confirm every completeness
check in validate.py actually catches its corresponding broken case, not just in theory.
"""

from builder import WorkflowBuilder
from models import ProcessDefinitionInput, StateInput, ActionInput
from validate import validate_process_definition

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def build_trade_license_approval() -> WorkflowBuilder:
    """The real example from digit-specs v3.0.0/workflow.yaml's own createProcessDefinition
    example -- built entirely through builder calls, mirroring exactly what the wizard transcript
    in ../DEMO-2026-07-15.md walks through."""
    b = WorkflowBuilder(name="Trade License Approval", code="trade-license-approval",
                         description="End-to-end approval workflow for trade license applications.",
                         sla_ms=432_000_000)

    pending = b.add_initial_state("Pending Review", sla_ms=172_800_000)
    approved = b.add_action_to_new_state(pending, "Approve", "Approved", new_state_type="TERMINAL_SUCCESS")
    returned = b.add_action_to_new_state(pending, "Return for Correction", "Returned", new_state_sla_ms=86_400_000)
    rejected = b.add_action_to_new_state(pending, "Reject", "Rejected", new_state_type="TERMINAL_FAILURE")

    b.add_action_to_existing_state(returned, "Resubmit", pending)
    withdrawn = b.add_action_to_new_state(returned, "Withdraw", "Withdrawn", new_state_type="TERMINAL_FAILURE")

    return b


def test_01_trade_license_approval_builds_and_validates():
    b = build_trade_license_approval()
    process = b.build()
    errors = validate_process_definition(process)
    check("01-builds-clean", not errors, errors)

    by_code = {s.code for s in process.states}
    check("01-five-states", by_code == {"PENDING_REVIEW", "APPROVED", "RETURNED", "REJECTED", "WITHDRAWN"}, by_code)

    initial = [s for s in process.states if s.type == "INITIAL"]
    check("01-one-initial", len(initial) == 1 and initial[0].code == "PENDING_REVIEW")

    returned_state = next(s for s in process.states if s.code == "RETURNED")
    check("01-returned-has-two-actions", len(returned_state.actions) == 2,
          [a.code for a in returned_state.actions])
    resubmit = next(a for a in returned_state.actions if a.code == "RESUBMIT")
    check("01-resubmit-loops-back", resubmit.nextState == "PENDING_REVIEW")


def test_02_missing_initial_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INTERMEDIATE", actions=[
            ActionInput(code="GO", nextState="B")]),
        StateInput(code="B", name="B", type="TERMINAL_SUCCESS", actions=[]),
    ])
    errors = validate_process_definition(process)
    check("02-missing-initial-caught", any("no state has type INITIAL" in e for e in errors), errors)


def test_03_two_initial_states_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL", actions=[ActionInput(code="GO", nextState="C")]),
        StateInput(code="B", name="B", type="INITIAL", actions=[ActionInput(code="GO", nextState="C")]),
        StateInput(code="C", name="C", type="TERMINAL_SUCCESS", actions=[]),
    ])
    errors = validate_process_definition(process)
    check("03-two-initial-caught", any("more than one INITIAL" in e for e in errors), errors)


def test_04_dead_end_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL", actions=[ActionInput(code="GO", nextState="B")]),
        StateInput(code="B", name="B", type="INTERMEDIATE", actions=[]),  # dead end, not terminal
    ])
    errors = validate_process_definition(process)
    check("04-dead-end-caught", any("dead end" in e for e in errors), errors)


def test_05_unresolvable_nextstate_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL",
                   actions=[ActionInput(code="GO", nextState="NOWHERE")]),
    ])
    errors = validate_process_definition(process)
    check("05-unresolvable-nextstate-caught",
          any("does not match any state code" in e for e in errors), errors)


def test_06_duplicate_state_code_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL", actions=[ActionInput(code="GO", nextState="A2")]),
        StateInput(code="A", name="A duplicate", type="TERMINAL_SUCCESS", actions=[]),
    ])
    errors = validate_process_definition(process)
    check("06-duplicate-state-code-caught", any("duplicate state code" in e for e in errors), errors)


def test_07_duplicate_action_code_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL", actions=[
            ActionInput(code="GO", nextState="B"),
            ActionInput(code="GO", nextState="C"),  # same action code twice
        ]),
        StateInput(code="B", name="B", type="TERMINAL_SUCCESS", actions=[]),
        StateInput(code="C", name="C", type="TERMINAL_FAILURE", actions=[]),
    ])
    errors = validate_process_definition(process)
    check("07-duplicate-action-code-caught", any("duplicate action code" in e for e in errors), errors)


def test_08_unreachable_state_caught():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL", actions=[ActionInput(code="GO", nextState="B")]),
        StateInput(code="B", name="B", type="TERMINAL_SUCCESS", actions=[]),
        StateInput(code="ORPHAN", name="Orphan", type="TERMINAL_SUCCESS", actions=[]),  # nobody points here
    ])
    errors = validate_process_definition(process)
    check("08-unreachable-caught", any("unreachable from INITIAL" in e and "ORPHAN" in e for e in errors), errors)


def test_09_terminal_state_with_actions_flagged():
    process = ProcessDefinitionInput(code="x", name="X", states=[
        StateInput(code="A", name="A", type="INITIAL", actions=[ActionInput(code="GO", nextState="B")]),
        StateInput(code="B", name="B", type="TERMINAL_SUCCESS",
                   actions=[ActionInput(code="REOPEN", nextState="A")]),  # terminal but has an action
    ])
    errors = validate_process_definition(process)
    check("09-terminal-with-actions-flagged",
          any("TERMINAL_SUCCESS but has" in e for e in errors), errors)


def test_10_correctly_built_workflow_has_no_false_positives():
    """Confirm none of the checks above are trigger-happy against a genuinely valid workflow."""
    process = build_trade_license_approval().build()
    errors = validate_process_definition(process)
    check("10-no-false-positives", not errors, errors)


def test_11_initial_state_cannot_be_marked_terminal():
    b = WorkflowBuilder(name="X", code="x")
    initial = b.add_initial_state("Start")
    raised = False
    try:
        b.mark_terminal(initial, success=True)
    except ValueError:
        raised = True
    check("11-initial-terminal-blocked", raised)
    check("11-initial-type-unchanged", b.states[initial].type == "INITIAL")


if __name__ == "__main__":
    import sys
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll builder + completeness checks verified against the real workflow.yaml schema.")
