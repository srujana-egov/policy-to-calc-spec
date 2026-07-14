"""WorkflowBuilder: the testable data-collection layer behind the wizard. Each method here
corresponds to exactly one wizard question -- wizard.py (the interactive CLI) and
test_workflow_builder.py (automated tests) both drive the same builder, so the logic is verified
once and used both ways, not duplicated.

Auto-generates `code` from `name`/`label` (collision-checked) -- the wizard never asks a
non-technical person to invent a machine-safe identifier themselves.
"""

from __future__ import annotations

import re

from models import ActionInput, ProcessDefinitionInput, StateInput


def slugify(text: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_").upper()
    return code or "STATE"


class WorkflowBuilder:
    def __init__(self, name: str, code: str, description: str = "", version: str = "1.0",
                 sla_ms: int | None = None):
        self.name = name
        self.code = code
        self.description = description
        self.version = version
        self.sla = sla_ms
        self.states: dict[str, StateInput] = {}
        self.queue: list[str] = []  # codes of states created but not yet fully configured

    def _dedupe_code(self, candidate: str) -> str:
        if candidate not in self.states:
            return candidate
        i = 2
        while f"{candidate}_{i}" in self.states:
            i += 1
        return f"{candidate}_{i}"

    def add_initial_state(self, name: str, sla_ms: int | None = None, description: str = "") -> str:
        if any(s.type == "INITIAL" for s in self.states.values()):
            raise ValueError("an INITIAL state already exists -- a process has exactly one")
        code = self._dedupe_code(slugify(name))
        self.states[code] = StateInput(code=code, name=name, type="INITIAL", sla=sla_ms,
                                        description=description, actions=[])
        self.queue.append(code)
        return code

    def add_action_to_new_state(self, from_state_code: str, label: str, new_state_name: str,
                                 new_state_sla_ms: int | None = None, new_state_type: str = "INTERMEDIATE",
                                 roles: list[str] | None = None, assignee_check: bool = False) -> str:
        """'What can happen from here?' -> a branch to a state that doesn't exist yet. Creates and
        queues the new state, returns its code."""
        next_code = self._dedupe_code(slugify(new_state_name))
        self.states[next_code] = StateInput(code=next_code, name=new_state_name, type=new_state_type,
                                             sla=new_state_sla_ms, actions=[])
        self.queue.append(next_code)
        self._append_action(from_state_code, label, next_code, roles or [], assignee_check)
        return next_code

    def add_action_to_existing_state(self, from_state_code: str, label: str, next_state_code: str,
                                      roles: list[str] | None = None, assignee_check: bool = False) -> None:
        """'Where does this lead? Back to one that already exists.' -- the loop-back case, no new
        state created."""
        if next_state_code not in self.states:
            raise ValueError(f"'{next_state_code}' is not an existing state -- create it first")
        self._append_action(from_state_code, label, next_state_code, roles or [], assignee_check)

    def _append_action(self, from_state_code: str, label: str, next_state_code: str,
                        roles: list[str], assignee_check: bool) -> None:
        state = self.states[from_state_code]
        action_code = self._dedupe_action_code(state, slugify(label))
        state.actions.append(ActionInput(code=action_code, label=label, nextState=next_state_code,
                                          roles=roles, assigneeCheck=assignee_check))

    def _dedupe_action_code(self, state: StateInput, candidate: str) -> str:
        existing = {a.code for a in state.actions}
        if candidate not in existing:
            return candidate
        i = 2
        while f"{candidate}_{i}" in existing:
            i += 1
        return f"{candidate}_{i}"

    def mark_terminal(self, state_code: str, success: bool) -> None:
        """'Nothing else happens here -- is this a good outcome or a bad outcome?'"""
        state = self.states[state_code]
        state.type = "TERMINAL_SUCCESS" if success else "TERMINAL_FAILURE"

    def next_unconfigured_state(self) -> str | None:
        """Drives the wizard's loop: which state still needs 'what can happen from here?'
        answered. Returns None once every queued state has been configured."""
        for code in self.queue:
            if not self.states[code].actions and self.states[code].type not in (
                "TERMINAL_SUCCESS", "TERMINAL_FAILURE",
            ):
                return code
        return None

    def build(self) -> ProcessDefinitionInput:
        return ProcessDefinitionInput(
            code=self.code, name=self.name, description=self.description,
            version=self.version, sla=self.sla, states=list(self.states.values()),
        )
