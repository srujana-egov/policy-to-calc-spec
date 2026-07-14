"""Pydantic models mirroring the real DIGIT Workflow Service schema exactly (digit-specs
v3.0.0/workflow.yaml -- ActionInput, StateInput, ProcessDefinitionInput), fetched and verified
directly, not guessed. This is the target shape POST /process/definition expects."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class ActionInput(BaseModel):
    code: str
    label: Optional[str] = None
    nextState: str
    roles: list[str] = []  # which roles may execute this action -- real, currently-active column
                            # (actions.roles JSONB), confirmed against the actual digit3 service
    assigneeCheck: bool = False


class StateInput(BaseModel):
    code: str
    name: str
    type: Literal["INITIAL", "INTERMEDIATE", "DECISION", "TERMINAL_SUCCESS", "TERMINAL_FAILURE"]
    description: Optional[str] = None
    sla: Optional[int] = None  # milliseconds
    actions: list[ActionInput] = []


class ProcessDefinitionInput(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    version: Optional[str] = None
    sla: Optional[int] = None  # milliseconds, overall process SLA
    states: list[StateInput] = Field(min_length=1)
