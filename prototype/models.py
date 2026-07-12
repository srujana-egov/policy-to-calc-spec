"""Pydantic models for both pipeline stages, passed as output_format to Claude's structured
outputs (client.messages.parse) so the LLM's response is guaranteed to conform — no hand-rolled
JSON parsing/retry logic. Field names/aliases mirror calculation-engine-3.0.0.yaml exactly."""

from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, ConfigDict


# --- Stage 2 (extract.py) intermediate representation ---

class Band(BaseModel):
    from_: Optional[float] = Field(default=None, alias="from")
    to: Optional[float] = None
    amount: float
    model_config = ConfigDict(populate_by_name=True)


class PolicyRule(BaseModel):
    scheduleId: str
    tradeNames: list[str]
    conditionAttribute: str
    suggestedJsonPath: str
    bands: list[Band]
    sourceText: str
    confidence: float = Field(ge=0, le=1)


class PolicyExtraction(BaseModel):
    rules: list[PolicyRule]
    documentNotes: list[str] = []


# --- Stage 4 (synthesize.py) target CalculationRule schema ---

class AttributeCondition(BaseModel):
    jsonPath: str
    derivedFrom: Optional[str] = None
    equals: Optional[Any] = None
    from_: Optional[float] = Field(default=None, alias="from")
    to: Optional[float] = None
    model_config = ConfigDict(populate_by_name=True)


class AttributeBinding(BaseModel):
    jsonPath: Optional[str] = None
    componentRef: Optional[str] = None


class Slab(BaseModel):
    from_: float = Field(alias="from")
    to: Optional[float] = None
    rate: float
    model_config = ConfigDict(populate_by_name=True)


class CalculationRule(BaseModel):
    module: Optional[str] = None
    ruleType: Literal["RATE_MATRIX", "ADJUSTMENT", "PENALTY", "INTEREST", "TAX", "AGGREGATION"]
    component: str
    scope: Literal["ENTITY", "SUBENTITY"]
    subEntityPath: Optional[str] = None
    conditions: dict[str, AttributeCondition] = {}
    calculationType: Literal["FLAT", "PERCENTAGE", "PER_UNIT", "SLAB", "FORMULA"]
    value: Optional[float] = None
    slabs: Optional[list[Slab]] = None
    formulaLogic: Optional[dict] = None
    formulaVariables: Optional[dict[str, AttributeBinding]] = None
    appliesOn: Optional[AttributeBinding] = None
    aggregateFunction: Optional[Literal["SUM", "COUNT", "MAX", "MIN", "AVG"]] = None
    sourceAttribute: Optional[AttributeBinding] = None
    targetAttribute: Optional[str] = None
    dependsOn: list[str] = []
    priority: int = 100
    roundOff: Literal["NONE", "NEAREST_1", "NEAREST_10", "NEAREST_100"] = "NEAREST_1"
    effectiveFrom: str
    effectiveTo: Optional[str] = None


class CalculationRuleSet(BaseModel):
    rules: list[CalculationRule]
    assumptions: list[str]
