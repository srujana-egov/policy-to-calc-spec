"""Pydantic models for both pipeline stages, passed as output_format to Claude's structured
outputs (client.messages.parse) so the LLM's response is guaranteed to conform — no hand-rolled
JSON parsing/retry logic. Field names/aliases mirror calculation-engine-3.0.0.yaml exactly."""

from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, ConfigDict


# --- Stage 2 (extract.py) intermediate representation ---
#
# Expanded to cover all 11 tiers / 30 patterns in calculation-rule-examples.pdf, not just
# flat/banded fees. Deliberately still simpler and flatter than CalculationRule (no ruleType
# enum forcing exact target vocabulary, no raw JSON Logic at this stage) -- Synthesize still
# does the real mapping, informed by reference/calculation-rule-vocabulary.md. This is the fix
# for the gap found by checking PolicyRule field-by-field against all 30 reference examples:
# previously only ~3 of 30 patterns had anywhere to go in this schema.

class PolicyCondition(BaseModel):
    attributeName: str
    suggestedJsonPath: str
    equals: Optional[Any] = None                      # exact match (string/bool/etc), e.g. category="RESTAURANT"
    from_: Optional[float] = Field(default=None, alias="from")   # numeric range, inclusive lower bound
    to: Optional[float] = None                         # numeric range, inclusive upper bound
    derivedFrom: Optional[str] = None                  # names another PolicyRule this bands on, instead of a raw field
    model_config = ConfigDict(populate_by_name=True)


class PolicyValueSource(BaseModel):
    """One named input a FORMULA/TIME_BASED rule's math needs."""
    variableName: str
    suggestedJsonPath: Optional[str] = None            # set when the input is a raw payload field
    referencesComponent: Optional[str] = None          # set when the input is another component's computed amount


class PolicyRuleVariant(BaseModel):
    """One row: a specific set of simultaneous (ANDed) conditions, and what it costs."""
    conditions: list[PolicyCondition] = []
    amount: Optional[float] = None                     # flat amount, or the rate for PER_UNIT/percentage


class PolicyRule(BaseModel):
    scheduleId: str
    tradeNames: list[str]
    mechanism: Literal[
        "FLAT_OR_BANDED",          # a fixed or banded amount; 0, 1, or several simultaneous conditions
        "PER_UNIT",                 # a rate multiplied by one raw numeric field, no repeating array
        "PER_ITEM_IN_LIST",         # charged once per element of a repeating array (accessories, floors, taps)
        "PERCENTAGE_OF_COMPONENT",  # a tax/cess computed as a percentage of another component's amount
        "REBATE_OF_COMPONENT",      # a rebate/deduction/surcharge adjusting another component, usually negative
        "AGGREGATION",              # derives one attribute by summing/counting/etc. over a repeating array
        "FORMULA",                  # real math beyond a single rate -- more than one input combined
        "TIME_BASED",               # interest/penalty, typically reading a principal plus a raw time field
    ]
    variants: list[PolicyRuleVariant]
    referencesComponents: list[str] = []   # components this rule's value or sequencing depends on
    rateAppliesToAttribute: Optional[str] = None    # raw field the rate multiplies -- PER_UNIT
    subEntityHint: Optional[str] = None             # e.g. "accessories" -- PER_ITEM_IN_LIST, AGGREGATION
    aggregateFunctionHint: Optional[str] = None     # SUM / COUNT / MAX / MIN / AVG -- AGGREGATION
    aggregationTargetName: Optional[str] = None     # name the aggregated result is stored under -- AGGREGATION
    valueSources: list[PolicyValueSource] = []      # named math inputs -- FORMULA, TIME_BASED
    formulaHint: Optional[str] = None               # free-text description of the math, e.g. "200 + 15*size" --
                                                     # FORMULA/TIME_BASED; Synthesize formalizes into JSON Logic
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
