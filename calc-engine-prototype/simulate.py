"""Offline evaluator reimplementing the Calculation Engine's documented evaluation order,
adapted from ../prototype/simulate.py (kept as an independent copy rather than a cross-directory
import, matching how the three sibling prototypes don't import from each other).

From calculation-engine-3.0.0.yaml's /{module}/estimate description (as reconstructed -- see
models.py's docstring on why this can't be independently re-verified):
  1. AGGREGATION rules first (derive attributes from sub-entity arrays).
  2. RATE_MATRIX rules (SUBENTITY: once per array element; ENTITY: once against root + derived).
  3. ADJUSTMENT rules in ascending priority order (cumulative).
  4. Remaining PENALTY/INTEREST/TAX in dependsOn topological order.
  5. Round each line item per its roundOff, then sum.

Not full engine parity (no cross-tenant registry, no persistence) -- it's the "does this
configuration compute what the wizard's answers imply it should" check for the worked-examples
preview step (see example_generator.py).
"""

from __future__ import annotations

ROUND_OFF_STEPS = {"NONE": None, "NEAREST_1": 1, "NEAREST_10": 10, "NEAREST_100": 100}


def resolve_json_path(path: str, root: dict):
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:]
    return _walk(path, root)


def resolve_relative_path(path: str, node: dict):
    return _walk(path, node)


def _walk(dotted_path: str, obj):
    if not dotted_path:
        return obj
    current = obj
    for segment in dotted_path.split("."):
        if current is None:
            return None
        current = current.get(segment) if isinstance(current, dict) else None
    return current


def get_subentities(sub_entity_path: str, root: dict) -> list[dict]:
    path = sub_entity_path
    if path.startswith("$."):
        path = path[2:]
    path = path.replace("[*]", "")
    entities = _walk(path, root)
    return entities if isinstance(entities, list) else []


def _condition_matches(attr_name: str, cond: dict, context: dict, derived: dict, relative: bool) -> bool:
    if "derivedFrom" in cond:
        value = derived.get(cond["derivedFrom"])
    elif relative:
        value = resolve_relative_path(cond["jsonPath"], context)
    else:
        value = resolve_json_path(cond["jsonPath"], context)

    if value is None:
        return False
    if "equals" in cond:
        return value == cond["equals"]
    if "from" in cond or "to" in cond:
        lo, hi = cond.get("from"), cond.get("to")
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        return True
    return True  # presence-only condition


def _rule_matches(rule: dict, context: dict, derived: dict, relative: bool) -> bool:
    return all(
        _condition_matches(name, cond, context, derived, relative)
        for name, cond in (rule.get("conditions") or {}).items()
    )


def _pick_most_specific(candidates: list[dict]):
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: (-len(r.get("conditions") or {}), r.get("priority", 100)))[0]


def _apply_round_off(amount: float, round_off: str) -> float:
    step = ROUND_OFF_STEPS.get(round_off or "NEAREST_1", 1)
    if step is None:
        return amount
    return round(amount / step) * step


def _eval_formula(node, variables: dict):
    if isinstance(node, dict):
        if "var" in node:
            return variables.get(node["var"])
        (op, args), = node.items()
        if op == "if":
            # JSON Logic "if": [cond, then, else, cond2, then2, ..., default]. Branches must
            # stay lazy (not pre-evaluated like the arithmetic ops below) -- an untaken branch
            # may not be well-defined for this scenario's inputs.
            i = 0
            while i + 1 < len(args):
                if _eval_formula(args[i], variables):
                    return _eval_formula(args[i + 1], variables)
                i += 2
            return _eval_formula(args[i], variables) if i < len(args) else None

        vals = [_eval_formula(a, variables) for a in args]
        if op == "+":
            return sum(vals)
        if op == "-":
            return vals[0] - sum(vals[1:])
        if op == "*":
            result = 1
            for v in vals:
                result *= v
            return result
        if op == "/":
            result = vals[0]
            for v in vals[1:]:
                result /= v
            return result
        if op == "==":
            return vals[0] == vals[1]
        if op == "!=":
            return vals[0] != vals[1]
        if op in (">", ">=", "<", "<="):
            import operator
            return {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}[op](*vals)
        raise ValueError(f"unsupported JSON Logic operator: {op}")
    return node


def _amount_of(line_items: list[dict], derived: dict, component: str) -> float:
    """A componentRef normally reads another RATE_MATRIX/ADJUSTMENT/etc rule's already-computed
    line-item amount. But an AGGREGATION rule never produces one -- it has no calculationType/value
    at all (see models.py); its real result only ever lands in `derived`, keyed by its own
    component name. So a componentRef naming an AGGREGATION component falls back to `derived`
    here, rather than raising -- found while wiring up worked-example simulation: a FORMULA rule
    reading an aggregation's total via componentRef crashed with "not yet computed" even though
    the aggregation ran first and had a real result."""
    for li in line_items:
        if li["component"] == component:
            return li["amount"]
    if component in derived:
        return derived[component]
    raise KeyError(f"component '{component}' not yet computed — missing dependsOn?")


def _compute_amount(rule: dict, context, derived: dict, line_items: list[dict], relative: bool) -> float:
    calc_type = rule["calculationType"]
    if calc_type == "FLAT":
        return rule["value"]
    if calc_type == "PERCENTAGE":
        base = _amount_of(line_items, derived, rule["appliesOn"]["componentRef"])
        return base * rule["value"] / 100
    if calc_type == "PER_UNIT":
        path = rule["appliesOn"]["jsonPath"]
        field_value = resolve_relative_path(path, context) if relative else resolve_json_path(path, context)
        return rule["value"] * (field_value or 0)
    if calc_type == "SLAB":
        path = rule["appliesOn"]["jsonPath"]
        field_value = resolve_relative_path(path, context) if relative else resolve_json_path(path, context)
        return _compute_slab(rule["slabs"], field_value or 0)
    if calc_type == "FORMULA":
        variables = {}
        for name, binding in (rule.get("formulaVariables") or {}).items():
            if binding.get("componentRef"):
                variables[name] = _amount_of(line_items, derived, binding["componentRef"])
            else:
                path = binding["jsonPath"]
                variables[name] = resolve_relative_path(path, context) if relative else resolve_json_path(path, context)
        return _eval_formula(rule["formulaLogic"], variables)
    raise ValueError(f"unsupported calculationType: {calc_type}")


def _compute_slab(slabs: list[dict], value: float) -> float:
    """Per calculation-rule-examples.pdf's example #14 (property tax): '0.5% on the first
    500,000, 1% on the remaining 200,000' stores rate as 0.5/1 and only reproduces that stated
    result (2500 + 2000 = 4500) if rate is divided by 100 here -- matching PERCENTAGE's own
    convention. Without the /100, rate 0.5 on a 500,000 band computes 250,000 (50%), 100x too
    large. Confirmed by the doc's own worked arithmetic, not assumed."""
    total = 0.0
    for slab in sorted(slabs, key=lambda s: s["from"]):
        lo, hi, rate = slab["from"], slab.get("to"), slab["rate"]
        if value <= lo:
            continue
        band_amount = (min(value, hi) if hi is not None else value) - lo
        total += band_amount * rate / 100
    return total


def _topo_order(rules: list[dict]) -> list[dict]:
    by_component = {r["component"]: r for r in rules}
    ordered, visited, visiting = [], set(), set()

    def visit(rule):
        comp = rule["component"]
        if comp in visited:
            return
        visiting.add(comp)
        for dep in rule.get("dependsOn") or []:
            if dep in by_component:
                visit(by_component[dep])
        visiting.discard(comp)
        visited.add(comp)
        ordered.append(rule)

    for r in rules:
        visit(r)
    return ordered


def simulate_estimate(rules: list[dict], entity_detail: dict) -> dict:
    derived: dict = {}
    line_items: list[dict] = []

    for rule in sorted((r for r in rules if r["ruleType"] == "AGGREGATION"), key=lambda r: r.get("priority", 100)):
        subentities = get_subentities(rule["subEntityPath"], entity_detail)
        src_path = rule["sourceAttribute"]["jsonPath"]
        values = [v for v in (resolve_relative_path(src_path, se) for se in subentities) if v is not None]
        func = rule["aggregateFunction"]
        if func == "SUM":
            result = sum(values)
        elif func == "COUNT":
            result = len(values)
        elif func == "MAX":
            result = max(values) if values else None
        elif func == "MIN":
            result = min(values) if values else None
        elif func == "AVG":
            result = sum(values) / len(values) if values else None
        else:
            raise ValueError(f"unsupported aggregateFunction: {func}")
        # Keyed by the AGGREGATION rule's own *component* name, not targetAttribute -- a
        # derivedFrom condition names the aggregation component (per
        # ../reference/calculation-rule-vocabulary.md: "derivedFrom: <aggregationComponent>"),
        # not the arbitrary attribute name the total gets stored under. The original
        # ../prototype/simulate.py keyed this by targetAttribute and looked it up by the
        # condition's own dict key (attr_name) -- correct only by coincidence, if someone
        # happened to name their condition identically to the aggregation's targetAttribute.
        # Fixed here to match what derivedFrom actually names.
        derived[rule["component"]] = result

    rate_matrix_rules = [r for r in rules if r["ruleType"] == "RATE_MATRIX"]
    by_component: dict[str, list[dict]] = {}
    for r in rate_matrix_rules:
        by_component.setdefault(r["component"], []).append(r)

    for component, candidates in by_component.items():
        scope = candidates[0]["scope"]
        if scope == "SUBENTITY":
            subentities = get_subentities(candidates[0]["subEntityPath"], entity_detail)
            for idx, se in enumerate(subentities):
                matching = [r for r in candidates if _rule_matches(r, se, derived, relative=True)]
                best = _pick_most_specific(matching)
                if best:
                    amount = _apply_round_off(_compute_amount(best, se, derived, line_items, relative=True), best.get("roundOff"))
                    line_items.append(_line_item(best, amount, sub_entity_index=idx))
        else:
            matching = [r for r in candidates if _rule_matches(r, entity_detail, derived, relative=False)]
            best = _pick_most_specific(matching)
            if best:
                amount = _apply_round_off(_compute_amount(best, entity_detail, derived, line_items, relative=False), best.get("roundOff"))
                line_items.append(_line_item(best, amount))

    for rule in sorted((r for r in rules if r["ruleType"] == "ADJUSTMENT"), key=lambda r: r.get("priority", 100)):
        if _rule_matches(rule, entity_detail, derived, relative=False):
            amount = _apply_round_off(_compute_amount(rule, entity_detail, derived, line_items, relative=False), rule.get("roundOff"))
            line_items.append(_line_item(rule, amount))

    remaining = [r for r in rules if r["ruleType"] in ("PENALTY", "INTEREST", "TAX")]
    for rule in _topo_order(remaining):
        if _rule_matches(rule, entity_detail, derived, relative=False):
            amount = _apply_round_off(_compute_amount(rule, entity_detail, derived, line_items, relative=False), rule.get("roundOff"))
            line_items.append(_line_item(rule, amount))

    taxable_amount = sum(li["amount"] for li in line_items if li["ruleType"] in ("RATE_MATRIX", "ADJUSTMENT"))
    total_amount = sum(li["amount"] for li in line_items)

    return {"lineItems": line_items, "taxableAmount": taxable_amount, "totalAmount": total_amount, "derived": derived}


def _line_item(rule: dict, amount: float, sub_entity_index: int | None = None) -> dict:
    return {
        "component": rule["component"],
        "ruleType": rule["ruleType"],
        "subEntityIndex": sub_entity_index,
        "amount": amount,
    }
