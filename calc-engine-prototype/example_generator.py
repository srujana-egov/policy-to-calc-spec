"""Generates targeted worked examples for the confirmation preview -- what does this
configuration actually compute for a representative scenario? Each example is chosen to exercise
one specific decision the wizard's questions captured (a condition band, a slab tier, an
aggregation threshold), not a random value. This pipeline's own design calls for "business user
sees all the generated rules ... + a few representative worked examples" -- this is the "a few
worked examples" half, previously not wired in (render.py only showed the rule table). Lets
someone notice "wait, a 1000 sq ft shop pays 2000 but 1000.01 sq ft pays 5000 -- did I mean to
draw the line there?" instead of only reading rule definitions and mentally simulating them.

Reuses simulate.py (adapted from ../prototype/simulate.py) to actually compute each scenario's
result, not just display the input -- a wrong boundary decision shows up as a visibly wrong
number, not just an ambiguous-looking condition.
"""

from __future__ import annotations

import copy
import json

from simulate import simulate_estimate

MAX_SCENARIOS = 15


def _strip_dollar_prefix(json_path: str) -> str:
    if json_path.startswith("$."):
        return json_path[2:]
    return json_path.lstrip(".")


def _set_path(payload: dict, path: str, value) -> None:
    segments = _strip_dollar_prefix(path).split(".")
    node = payload
    for seg in segments[:-1]:
        node = node.setdefault(seg, {})
    node[segments[-1]] = value


def _get_path(payload: dict, path: str):
    node = payload
    for seg in _strip_dollar_prefix(path).split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _midpoint(lo, hi):
    if lo is None:
        lo = 0
    if hi is None:
        return lo + 1
    return (lo + hi) / 2


def _entity_conditions(rules: list[dict]):
    """Yields (rule, name, cond) for every ENTITY-scope condition with a real jsonPath (skipping
    derivedFrom -- that's not a raw payload field, and skipping SUBENTITY-scope conditions, which
    live inside the list built for that rule instead of at the payload root)."""
    for rule in rules:
        if rule.get("scope") != "ENTITY":
            continue
        for name, cond in (rule.get("conditions") or {}).items():
            if cond.get("jsonPath"):
                yield rule, name, cond


def build_baseline_payload(rules: list[dict]) -> dict:
    """A single representative payload with a plausible default for every field these rules
    reference -- the starting point every scenario below is a small variant of."""
    payload: dict = {}

    for rule, name, cond in _entity_conditions(rules):
        path = cond["jsonPath"]
        if _get_path(payload, path) is not None:
            # First rule to set a shared path wins (e.g. two FLAT rules banding on the same
            # field) -- otherwise the "typical case" baseline ends up wherever the *last*
            # rule's condition happens to land, not a deliberately representative value.
            continue
        if "equals" in cond:
            _set_path(payload, path, cond["equals"])
        elif "from" in cond or "to" in cond:
            _set_path(payload, path, _midpoint(cond.get("from"), cond.get("to")))
        else:
            _set_path(payload, path, True)  # presence-only

    for rule in rules:
        if rule.get("scope") != "ENTITY":
            continue
        for binding_key in ("appliesOn", "sourceAttribute"):
            binding = rule.get(binding_key)
            if binding and binding.get("jsonPath") and _get_path(payload, binding["jsonPath"]) is None:
                _set_path(payload, binding["jsonPath"], 10)
        for binding in (rule.get("formulaVariables") or {}).values():
            if binding.get("jsonPath") and _get_path(payload, binding["jsonPath"]) is None:
                _set_path(payload, binding["jsonPath"], 10)

    for rule in rules:
        if rule.get("scope") != "SUBENTITY" or not rule.get("subEntityPath"):
            continue
        list_path = rule["subEntityPath"].replace("[*]", "")
        if _get_path(payload, list_path) is not None:
            continue
        item_field = None
        if rule["ruleType"] == "AGGREGATION":
            item_field = rule["sourceAttribute"]["jsonPath"]
        elif (rule.get("appliesOn") or {}).get("jsonPath"):
            item_field = rule["appliesOn"]["jsonPath"]
        sample_items = [{item_field: 2}, {item_field: 3}] if item_field else [{}, {}]
        _set_path(payload, list_path, sample_items)

    return payload


def _label_value(value) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _condition_scenarios(rules: list[dict], baseline: dict) -> list[dict]:
    scenarios = []
    for rule, name, cond in _entity_conditions(rules):
        path = cond["jsonPath"]
        if "equals" in cond:
            variant = copy.deepcopy(baseline)
            _set_path(variant, path, cond["equals"])
            scenarios.append({"label": f"{name} = {cond['equals']!r} ({rule['component']})", "payload": variant})
            continue
        if "from" not in cond and "to" not in cond:
            continue
        lo, hi = cond.get("from"), cond.get("to")
        mid = _midpoint(lo, hi)
        variant = copy.deepcopy(baseline)
        _set_path(variant, path, mid)
        scenarios.append({"label": f"{name} = {_label_value(mid)} (within {rule['component']}'s band)",
                           "payload": variant})
        if lo is not None:
            v = copy.deepcopy(baseline)
            _set_path(v, path, lo)
            scenarios.append({"label": f"{name} = {_label_value(lo)} (exactly the lower boundary)", "payload": v})
        if hi is not None:
            v = copy.deepcopy(baseline)
            _set_path(v, path, hi)
            scenarios.append({"label": f"{name} = {_label_value(hi)} (exactly the upper boundary)", "payload": v})
            step = 0.01 if isinstance(hi, float) or isinstance(lo, float) else 1
            just_over = hi + step
            v2 = copy.deepcopy(baseline)
            _set_path(v2, path, just_over)
            scenarios.append({"label": f"{name} = {_label_value(just_over)} (just past that boundary)", "payload": v2})
    return scenarios


def _slab_scenarios(rules: list[dict], baseline: dict) -> list[dict]:
    scenarios = []
    for rule in rules:
        if rule.get("calculationType") != "SLAB":
            continue
        path = rule["appliesOn"]["jsonPath"]
        for slab in rule["slabs"]:
            lo, hi = slab["from"], slab.get("to")
            mid = _midpoint(lo, hi if hi is not None else lo + 2 * max(lo, 1))
            variant = copy.deepcopy(baseline)
            _set_path(variant, path, mid)
            tier_label = f"{lo}-{hi}" if hi is not None else f"{lo} and up"
            scenarios.append({
                "label": f"{rule['component']}: value = {_label_value(mid)} (in the {tier_label} tier)",
                "payload": variant})
    return scenarios


def _aggregation_threshold_scenarios(rules: list[dict], baseline: dict) -> list[dict]:
    """For every condition that bands on a previously-aggregated total (derivedFrom), show one
    scenario just below and one just above that threshold -- exactly the kind of "did I mean to
    draw the line here" case a plain rule table can't surface."""
    scenarios = []
    agg_rules_by_component = {r["component"]: r for r in rules if r["ruleType"] == "AGGREGATION"}

    for rule in rules:
        for name, cond in (rule.get("conditions") or {}).items():
            target = cond.get("derivedFrom")
            if not target or target not in agg_rules_by_component:
                continue
            agg_rule = agg_rules_by_component[target]
            list_path = agg_rule["subEntityPath"].replace("[*]", "")
            item_field = agg_rule["sourceAttribute"]["jsonPath"]
            func = agg_rule["aggregateFunction"]
            threshold = cond.get("from") if cond.get("from") is not None else cond.get("to")
            if threshold is None:
                continue

            if func == "COUNT":
                # The condition bands on how many items there are, not their values.
                below_items = [{item_field: 1}] * max(int(threshold) - 1, 0)
                above_items = [{item_field: 1}] * (int(threshold) + 1)
            elif func == "SUM":
                # Two equal items summing clearly below/above the threshold.
                below_items = [{item_field: threshold * 0.2}, {item_field: threshold * 0.2}]
                above_items = [{item_field: threshold * 0.7}, {item_field: threshold * 0.7}]
            else:
                # AVG/MAX/MIN reflect individual item values directly, not their sum -- doubling
                # two below-threshold items (as SUM's case does) would never cross the threshold.
                below_items = [{item_field: threshold * 0.7}, {item_field: threshold * 0.8}]
                above_items = [{item_field: threshold * 1.2}, {item_field: threshold * 1.3}]

            below = copy.deepcopy(baseline)
            _set_path(below, list_path, below_items)
            scenarios.append({"label": f"{name}: total just below the {_label_value(threshold)} threshold",
                               "payload": below})

            above = copy.deepcopy(baseline)
            _set_path(above, list_path, above_items)
            scenarios.append({"label": f"{name}: total at/above the {_label_value(threshold)} threshold",
                               "payload": above})
    return scenarios


def generate_scenarios(rules: list[dict], max_scenarios: int = MAX_SCENARIOS) -> list[dict]:
    """Returns [{"label": str, "payload": dict}], deduplicated and capped -- the baseline first,
    then targeted variants covering every band/tier/threshold the wizard's answers created."""
    baseline = build_baseline_payload(rules)
    all_scenarios = (
        [{"label": "Typical case (default values)", "payload": baseline}]
        + _condition_scenarios(rules, baseline)
        + _slab_scenarios(rules, baseline)
        + _aggregation_threshold_scenarios(rules, baseline)
    )

    seen = set()
    deduped = []
    for scenario in all_scenarios:
        key = json.dumps(scenario["payload"], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(scenario)

    return deduped[:max_scenarios]


def run_scenarios(rules: list[dict], scenarios: list[dict]) -> list[dict]:
    """Runs each scenario's payload through simulate_estimate(), returning
    [{"label", "payload", "result"}] ready for render.py."""
    return [
        {"label": s["label"], "payload": s["payload"], "result": simulate_estimate(rules, s["payload"])}
        for s in scenarios
    ]
