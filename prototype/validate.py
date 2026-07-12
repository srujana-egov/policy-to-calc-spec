"""Deterministic validator for CalculationRule sets.

Mirrors the x-businessRules in calculation-engine-3.0.0.yaml's components/schemas
(CalculationRule, AttributeCondition, AttributeBinding, Slab) directly — no LLM judgment here,
this is the "trust deterministic code, not LLM freelancing" half of the pipeline.

Validates a whole rule *set* at once (not one rule in isolation) because several checks are
cross-rule: attribute-path registration conflicts, dependsOn cycles, subEntityPath consistency
per component.
"""

from __future__ import annotations

REQUIRED_TOP_LEVEL = ["ruleType", "component", "scope", "calculationType", "effectiveFrom"]

RULE_TYPES = {"RATE_MATRIX", "ADJUSTMENT", "PENALTY", "INTEREST", "TAX", "AGGREGATION"}
CALC_TYPES = {"FLAT", "PERCENTAGE", "PER_UNIT", "SLAB", "FORMULA"}
AGGREGATE_FUNCTIONS = {"SUM", "COUNT", "MAX", "MIN", "AVG"}


class ValidationError(Exception):
    def __init__(self, rule_index, message):
        self.rule_index = rule_index
        self.message = message
        super().__init__(f"rule[{rule_index}]: {message}")


def validate_rule_set(rules: list[dict]) -> list[str]:
    """Returns a list of human-readable error strings; empty list means the set is valid."""
    errors: list[str] = []

    for i, rule in enumerate(rules):
        errors.extend(f"rule[{i}]: {msg}" for msg in _validate_single_rule(rule))

    errors.extend(_validate_attribute_path_registry(rules))
    errors.extend(_validate_subentity_path_consistency(rules))
    errors.extend(_validate_dependson_dag(rules))

    return errors


def _validate_single_rule(rule: dict) -> list[str]:
    errs = []

    for field in REQUIRED_TOP_LEVEL:
        if rule.get(field) in (None, ""):
            errs.append(f"missing required field '{field}'")

    rule_type = rule.get("ruleType")
    calc_type = rule.get("calculationType")
    scope = rule.get("scope")

    if rule_type is not None and rule_type not in RULE_TYPES:
        errs.append(f"unknown ruleType '{rule_type}'")
    if calc_type is not None and calc_type not in CALC_TYPES:
        errs.append(f"unknown calculationType '{calc_type}'")

    # Scope / subEntityPath
    sub_entity_path = rule.get("subEntityPath")
    if scope == "SUBENTITY":
        if not sub_entity_path:
            errs.append("scope=SUBENTITY requires subEntityPath")
        elif "[" not in sub_entity_path:
            errs.append(f"subEntityPath '{sub_entity_path}' does not look like it ends in an array/wildcard segment")
    elif scope == "ENTITY" and sub_entity_path:
        errs.append("scope=ENTITY must not set subEntityPath")

    # value required for FLAT/PERCENTAGE/PER_UNIT
    if calc_type in ("FLAT", "PERCENTAGE", "PER_UNIT") and rule.get("value") is None:
        errs.append(f"calculationType={calc_type} requires 'value'")

    # appliesOn requirements
    applies_on = rule.get("appliesOn") or {}
    if calc_type in ("PER_UNIT", "SLAB") and not applies_on.get("jsonPath"):
        errs.append(f"calculationType={calc_type} requires appliesOn.jsonPath")
    if calc_type == "PERCENTAGE" and not applies_on.get("componentRef"):
        errs.append("calculationType=PERCENTAGE requires appliesOn.componentRef")
    if rule_type == "ADJUSTMENT" and not applies_on.get("componentRef"):
        errs.append("ruleType=ADJUSTMENT requires appliesOn.componentRef")

    # SLAB
    if calc_type == "SLAB":
        slabs = rule.get("slabs") or []
        if not slabs:
            errs.append("calculationType=SLAB requires a non-empty 'slabs' array")
        else:
            errs.extend(_validate_slabs(slabs))

    # FORMULA
    if calc_type == "FORMULA":
        formula_logic = rule.get("formulaLogic")
        formula_vars = rule.get("formulaVariables") or {}
        if not formula_logic:
            errs.append("calculationType=FORMULA requires formulaLogic")
        if not formula_vars:
            errs.append("calculationType=FORMULA requires formulaVariables")
        if formula_logic:
            referenced = _extract_var_refs(formula_logic)
            missing = referenced - set(formula_vars.keys())
            if missing:
                errs.append(f"formulaLogic references undeclared variable(s): {sorted(missing)}")

    # AGGREGATION
    if rule_type == "AGGREGATION":
        if scope != "SUBENTITY":
            errs.append("ruleType=AGGREGATION requires scope=SUBENTITY")
        if rule.get("aggregateFunction") not in AGGREGATE_FUNCTIONS:
            errs.append("ruleType=AGGREGATION requires a valid aggregateFunction")
        if not (rule.get("sourceAttribute") or {}).get("jsonPath"):
            errs.append("ruleType=AGGREGATION requires sourceAttribute.jsonPath")
        if not rule.get("targetAttribute"):
            errs.append("ruleType=AGGREGATION requires targetAttribute")

    # conditions: equals vs from/to mutual exclusivity; jsonPath required per condition
    for attr_name, cond in (rule.get("conditions") or {}).items():
        if "jsonPath" not in cond:
            errs.append(f"condition '{attr_name}' missing required 'jsonPath'")
        has_equals = "equals" in cond
        has_range = "from" in cond or "to" in cond
        if has_equals and has_range:
            errs.append(f"condition '{attr_name}' sets both 'equals' and 'from'/'to' — pick one")

    # effectiveFrom < effectiveTo
    effective_from = rule.get("effectiveFrom")
    effective_to = rule.get("effectiveTo")
    if effective_from and effective_to and effective_to <= effective_from:
        errs.append(f"effectiveTo ({effective_to}) must be strictly after effectiveFrom ({effective_from})")

    return errs


def _validate_slabs(slabs: list[dict]) -> list[str]:
    errs = []
    for i, slab in enumerate(slabs):
        if slab.get("from") is None:
            errs.append(f"slabs[{i}] missing required 'from'")
        if slab.get("rate") is None:
            errs.append(f"slabs[{i}] missing required 'rate'")
        is_last = i == len(slabs) - 1
        if not is_last and slab.get("to") is None:
            errs.append(f"slabs[{i}] must set 'to' (only the final tier may omit it)")
    sorted_slabs = sorted(slabs, key=lambda s: s.get("from", 0))
    for i in range(len(sorted_slabs) - 1):
        cur_to = sorted_slabs[i].get("to")
        nxt_from = sorted_slabs[i + 1].get("from")
        if cur_to is not None and nxt_from is not None and cur_to > nxt_from:
            errs.append(f"slabs overlap: tier ending at {cur_to} overlaps tier starting at {nxt_from}")
    return errs


def _extract_var_refs(node) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, dict):
        if "var" in node and isinstance(node["var"], str):
            refs.add(node["var"])
        for v in node.values():
            refs |= _extract_var_refs(v)
    elif isinstance(node, list):
        for item in node:
            refs |= _extract_var_refs(item)
    return refs


def _validate_attribute_path_registry(rules: list[dict]) -> list[str]:
    """First rule to use an attribute name registers its jsonPath; later rules must match."""
    errs = []
    registry: dict[str, str] = {}
    for i, rule in enumerate(rules):
        for attr_name, cond in (rule.get("conditions") or {}).items():
            if "derivedFrom" in cond:
                continue
            json_path = cond.get("jsonPath")
            if attr_name in registry and registry[attr_name] != json_path:
                errs.append(
                    f"rule[{i}]: attribute '{attr_name}' already registered with jsonPath "
                    f"'{registry[attr_name]}', this rule declares '{json_path}' — 409 AttributePath.Conflict"
                )
            else:
                registry[attr_name] = json_path
    return errs


def _validate_subentity_path_consistency(rules: list[dict]) -> list[str]:
    errs = []
    paths_by_component: dict[str, str] = {}
    for i, rule in enumerate(rules):
        component = rule.get("component")
        sub_entity_path = rule.get("subEntityPath")
        if not sub_entity_path:
            continue
        if component in paths_by_component and paths_by_component[component] != sub_entity_path:
            errs.append(
                f"rule[{i}]: component '{component}' already uses subEntityPath "
                f"'{paths_by_component[component]}', this rule declares '{sub_entity_path}'"
            )
        else:
            paths_by_component[component] = sub_entity_path
    return errs


def _validate_dependson_dag(rules: list[dict]) -> list[str]:
    """dependsOn must not introduce a cycle across all active rules for a module."""
    graph: dict[str, set[str]] = {}
    for rule in rules:
        component = rule.get("component")
        if component is None:
            continue
        graph.setdefault(component, set())
        for dep in rule.get("dependsOn") or []:
            graph[component].add(dep)

    visiting: set[str] = set()
    visited: set[str] = set()
    errs = []

    def visit(node, path):
        if node in visited:
            return
        if node in visiting:
            errs.append(f"dependsOn cycle detected: {' -> '.join(path + [node])}")
            return
        visiting.add(node)
        for dep in graph.get(node, ()):
            visit(dep, path + [node])
        visiting.discard(node)
        visited.add(node)

    for component in graph:
        visit(component, [])

    return errs
