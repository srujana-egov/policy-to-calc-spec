"""Pydantic models for DIGIT's Calculation Engine -- CalculationRule/Slab/AttributeCondition/
AttributeBinding/CalculationRuleSet, field names/aliases matching calculation-engine-3.0.0.yaml.

Important, honest caveat this project's other two prototypes don't have to make: unlike
../workflow-prototype/ and ../registry-prototype/ (both verified against real Go source in
digitnxt/digit3), no Calculation Engine service exists anywhere in the digitnxt org -- confirmed
by checking `src/services/` (no such directory) and searching the whole org for
"calculation-engine" (no hits). The actual `calculation-engine-3.0.0.yaml` spec file was never
found saved anywhere in this repo either. This model is inherited from ../prototype/models.py
(this project's own earlier reconstruction of that spec, built before this repo's rule of
"verify against real source" was established), treated here as the best available ground truth,
not an independently re-verified one. Flagged clearly rather than glossed over.

Nested object properties, `pattern`/`minimum`/`maximum` support, and the schema-code regex fix
made in ../registry-prototype/ don't apply here directly -- this prototype's schemas are
CalculationRule (a different contract), not registry schemas. Where a CalculationRule needs to
reference a *registry* field (any `jsonPath`), see registry_lookup.py for the mechanism that
turns a picked registry field into the right `$.` path, rather than asking a user to type one.
"""

from typing import Literal, Optional, Any, Union

from pydantic import BaseModel, Field, ConfigDict

# Union[int, float], not float, on every numeric field below: pydantic's "smart" union mode keeps
# an int input as an int on output, rather than coercing e.g. the real chennai fixture's
# "to": 1000 into "to": 1000.0. Same fix already applied to minimum/maximum in
# ../registry-prototype/models.py after finding the identical issue there.
Number = Union[int, float]


class AttributeCondition(BaseModel):
    jsonPath: Optional[str] = None
    derivedFrom: Optional[str] = None   # reads an AGGREGATION result instead of a raw payload field
    equals: Optional[Any] = None
    from_: Optional[Number] = Field(default=None, alias="from")
    to: Optional[Number] = None
    model_config = ConfigDict(populate_by_name=True)


class AttributeBinding(BaseModel):
    """Exactly one of jsonPath/componentRef -- jsonPath reads the raw payload (via a registry
    field), componentRef reads another component's already-computed amount."""
    jsonPath: Optional[str] = None
    componentRef: Optional[str] = None


class Slab(BaseModel):
    from_: Number = Field(alias="from")
    to: Optional[Number] = None
    rate: Number
    model_config = ConfigDict(populate_by_name=True)


class CalculationRule(BaseModel):
    # No `module` field -- marked readOnly in the real schema, resolved from the {module} path
    # segment on write (POST /{module}/rules). Carried once, for the whole batch, on
    # CalculationRuleSet instead.
    ruleType: Literal["RATE_MATRIX", "ADJUSTMENT", "PENALTY", "INTEREST", "TAX", "AGGREGATION"]
    component: str
    scope: Literal["ENTITY", "SUBENTITY"]
    subEntityPath: Optional[str] = None
    conditions: dict[str, AttributeCondition] = Field(default_factory=dict)
    # Optional, not required -- AGGREGATION rules never set this (confirmed by
    # calculation-rule-examples.pdf's examples #22-24: no calculationType, no value at all; they
    # derive an attribute, they don't compute a billable amount). An earlier version of this
    # model made this required and builder.py compensated with an invented "FLAT"/0 placeholder
    # on AGGREGATION rules -- never verified against a real example until this PDF surfaced.
    calculationType: Optional[Literal["FLAT", "PERCENTAGE", "PER_UNIT", "SLAB", "FORMULA"]] = None
    value: Optional[Number] = None
    slabs: Optional[list[Slab]] = None
    formulaLogic: Optional[dict] = None
    formulaVariables: Optional[dict[str, AttributeBinding]] = None
    appliesOn: Optional[AttributeBinding] = None
    aggregateFunction: Optional[Literal["SUM", "COUNT", "MAX", "MIN", "AVG"]] = None
    sourceAttribute: Optional[AttributeBinding] = None
    targetAttribute: Optional[str] = None
    dependsOn: list[str] = Field(default_factory=list)
    priority: int = 100
    roundOff: Literal["NONE", "NEAREST_1", "NEAREST_10", "NEAREST_100"] = "NEAREST_1"
    isActive: bool = True
    effectiveFrom: str
    effectiveTo: Optional[str] = None


class CalculationRuleSet(BaseModel):
    module: str   # the {module} path segment every rule in this batch is POSTed under -- one
                  # value per batch, not per rule
    rules: list[CalculationRule]
    assumptions: list[str] = Field(default_factory=list)
