"""Deterministic completeness checks for a ProcessDefinitionInput, mirroring the real
x-businessRules in digit-specs v3.0.0/workflow.yaml exactly (verified against the fetched spec,
not guessed) -- plus graph-shape checks (dead ends, reachability) the spec implies but doesn't
spell out as a single rule.
"""

from __future__ import annotations

from models import ProcessDefinitionInput

NON_TERMINAL_TYPES = {"INITIAL", "INTERMEDIATE", "DECISION"}
TERMINAL_TYPES = {"TERMINAL_SUCCESS", "TERMINAL_FAILURE"}


def validate_process_definition(process: ProcessDefinitionInput) -> list[str]:
    errors: list[str] = []
    states_by_code = {s.code: s for s in process.states}

    # x-businessRule: each state code must be unique within the states array.
    seen_codes = set()
    for s in process.states:
        if s.code in seen_codes:
            errors.append(f"duplicate state code '{s.code}'")
        seen_codes.add(s.code)

    # x-businessRule: exactly one state must have type INITIAL.
    initial_states = [s.code for s in process.states if s.type == "INITIAL"]
    if len(initial_states) == 0:
        errors.append("no state has type INITIAL -- exactly one is required")
    elif len(initial_states) > 1:
        errors.append(f"more than one INITIAL state: {initial_states} -- exactly one is required")

    for s in process.states:
        # x-businessRule: each action code must be unique within its state.
        action_codes = [a.code for a in s.actions]
        for code in set(action_codes):
            if action_codes.count(code) > 1:
                errors.append(f"state '{s.code}': duplicate action code '{code}'")

        # x-businessRule: every action nextState must reference a state code present in states[].
        for a in s.actions:
            if a.nextState not in states_by_code:
                errors.append(
                    f"state '{s.code}', action '{a.code}': nextState '{a.nextState}' does not "
                    f"match any state code in this process"
                )

        # Not a literal schema requirement ("typically has no outbound actions"), but a genuine
        # design smell worth flagging: a terminal state with actions is very likely a mistake.
        if s.type in TERMINAL_TYPES and s.actions:
            errors.append(f"state '{s.code}' is {s.type} but has {len(s.actions)} action(s) -- terminal states should have none")

        # Graph-shape check the spec implies but doesn't state as one rule: a non-terminal state
        # with no way out is a dead end nobody would ever notice just from writing the YAML.
        if s.type in NON_TERMINAL_TYPES and not s.actions:
            errors.append(f"state '{s.code}' ({s.type}) has no actions -- dead end, nothing can happen from here")

    # Reachability: every state should be reachable from INITIAL, or it's a state nobody can
    # ever actually reach -- an orphan created by mistake.
    if len(initial_states) == 1:
        reachable = {initial_states[0]}
        frontier = [initial_states[0]]
        while frontier:
            current = frontier.pop()
            for a in states_by_code[current].actions:
                if a.nextState in states_by_code and a.nextState not in reachable:
                    reachable.add(a.nextState)
                    frontier.append(a.nextState)
        unreachable = set(states_by_code) - reachable
        if unreachable:
            errors.append(f"state(s) unreachable from INITIAL: {sorted(unreachable)}")

    return errors
