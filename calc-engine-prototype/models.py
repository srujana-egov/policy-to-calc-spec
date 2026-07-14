"""Pydantic models for DIGIT's Calculation Engine -- CalculationRule/Slab/AttributeCondition/
AttributeBinding/CalculationRuleSet, matching fixtures/real_world/calculation-engine-3.0.0.yaml.

Spec found and verified (see README.md's "Spec found and verified" section for the full account).
This model was originally inherited from ../prototype/models.py -- this project's own earlier
reconstruction, built before the real spec was located -- and has since been re-verified field by
field against the real OpenAPI schema (confirmed from the platform team). Two real, confirmed
discrepancies were found this way and fixed: the real write path is `/calculation/v3/{module}/rules`
(this prototype previously omitted the `/calculation/v3` prefix entirely), and `POST` creates
**one** rule per call (`requestBody` is a single `CalculationRule` object, not an array) -- this
prototype previously sent the whole rule set as one bulk array in a single request. See
wizard.py's write_rules() for both fixes.

One genuinely interesting finding while re-verifying: the spec's own formal JSON-Schema `required`
lists are looser than what its own worked examples show -- `CalculationRule.required` includes
`calculationType` unconditionally, yet the spec's own "aggregation" example (`paths./{module}/rules
.post.requestBody.examples.aggregation`) omits it entirely; `AttributeCondition.required` includes
`jsonPath` unconditionally, yet the spec's own "bulkSurchargeOnDerivedTotal" example sets only
`derivedFrom`, no `jsonPath` at all. In both cases the concrete example matches this project's own
already-independently-confirmed behavior (via calculation-rule-examples.pdf's real examples #22-24
and #25) -- the prose `x-businessRules` and worked examples are the reliable source of truth here,
not the bare `required` arrays, which read like boilerplate not customized per ruleType/calculationType.

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
    component: str = Field(min_length=1, max_length=64)
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
    priority: int = Field(default=100, ge=1, le=1000)
    roundOff: Literal["NONE", "NEAREST_1", "NEAREST_10", "NEAREST_100"] = "NEAREST_1"
    isActive: bool = True
    effectiveFrom: str
    effectiveTo: Optional[str] = None


class CalculationRuleSet(BaseModel):
    # the {module} path segment every rule in this batch is POSTed under -- one value per batch,
    # not per rule. minLength/maxLength match the real spec's ModulePath parameter.
    module: str = Field(min_length=1, max_length=64)
    rules: list[CalculationRule]
    assumptions: list[str] = Field(default_factory=list)
