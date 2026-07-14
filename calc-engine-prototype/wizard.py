"""Interactive CLI wizard for authoring CalculationRule[] fresh -- mirrors
../workflow-prototype/wizard.py and ../registry-prototype/wizard.py's shape exactly: plain-
language questions drive the same CalculationRuleBuilder the automated tests use, quit/exit/q
cancels at any prompt, a table preview and explicit confirmation gate before any write, and a
targeted fix-one-thing menu instead of discarding the whole session on "no".

Every jsonPath a rule needs (a condition, appliesOn, sourceAttribute, a formulaVariables entry)
is picked from a real registry schema's fields (fetched once, at the start of the session) rather
than typed by hand -- see registry_lookup.py for why: a hand-typed path is exactly the kind of
"looks right, isn't" mistake that shipped twice already in ../registry-prototype/.

Run: python wizard.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from builder import CalculationRuleBuilder
from formula_parser import FormulaParseError, parse_formula
from models import CalculationRuleSet
from registry_lookup import (fetch_registry_schema, field_to_json_path, field_to_relative_path,
                              list_schema_fields, registry_headers)
from example_generator import generate_scenarios, run_scenarios
from render import render_ruleset_preview
from validate import validate_rule_set_models


class Cancelled(Exception):
    """Raised when the user types quit/exit at any prompt -- caught once, at the top level."""


def ask(prompt: str) -> str:
    answer = input(prompt + " ").strip()
    if answer.lower() in ("quit", "exit", "q"):
        raise Cancelled
    return answer


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = ask(prompt + " (yes/no)").lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  please answer yes or no")


def ask_optional_number(prompt: str):
    raw = ask(prompt)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        print("  couldn't parse that as a number -- skipping")
        return None
    return int(value) if value == int(value) else value


def ask_required_number(prompt: str) -> float:
    while True:
        raw = ask(prompt)
        try:
            value = float(raw)
        except ValueError:
            print("  please enter a number")
            continue
        return int(value) if value == int(value) else value


def _coerce_equals(raw: str):
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        value = float(raw)
        return int(value) if value == int(value) else value
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# Registry field lookup -- the "$." mechanism
# ---------------------------------------------------------------------------

def setup_registry_fields() -> list[str] | None:
    """Asked once, up front. Returns None if skipped or the fetch fails -- callers fall back to
    manual path entry for the rest of the session, they don't crash."""
    if not ask_yes_no("Do you want to pick fields from an existing registry schema (recommended, "
                       "avoids typos in field paths)?"):
        return None
    schema_code = ask("  What's the schema code? (e.g. 'trade-license-application')")
    try:
        schema_data = fetch_registry_schema(schema_code)
    except SystemExit as e:
        print(f"  Couldn't fetch that schema -- {e}")
        print("  You'll type field paths manually for the rest of this session.")
        return None
    fields = list_schema_fields(schema_data)
    print(f"  Found {len(fields)} field(s) in '{schema_code}'.")
    return fields


def ask_field_reference(purpose: str, registry_fields: list[str] | None, relative: bool = False) -> str:
    """Returns a JSONPath -- picked from the fetched registry schema's fields if available, typed
    manually otherwise (or if 'type it myself' is chosen even when fields are available, e.g. for
    a field the fetched schema doesn't have).

    relative=True returns a bare, root-unprefixed path (e.g. 'quantity', not '$.quantity') --
    required for anything *inside* a SUBENTITY-scoped rule (a per-item rate's appliesOn, an
    AGGREGATION's sourceAttribute, or a condition on either): the vocabulary reference states
    these become "relative to one array element", and simulate.py's resolve_relative_path()
    doesn't strip a '$.' prefix itself -- a real bug this project found and fixed once already,
    see configure_aggregation()'s comment."""
    convert = field_to_relative_path if relative else field_to_json_path
    if registry_fields:
        print(f"  Which field {purpose}?")
        for i, f in enumerate(registry_fields, 1):
            print(f"    {i}. {f}")
        manual_choice = len(registry_fields) + 1
        print(f"    {manual_choice}. (type a path myself)")
        while True:
            choice = ask(f"  Pick 1-{manual_choice}:")
            if choice.isdigit() and 1 <= int(choice) <= len(registry_fields):
                return convert(registry_fields[int(choice) - 1])
            if choice == str(manual_choice):
                break
            print("  please pick a valid option")
    raw = ask(f"  Type the field path {purpose} (e.g. 'tradeLicenseDetail.premisesArea'):")
    return convert(raw)


# ---------------------------------------------------------------------------
# Conditions (shared across every mechanism)
# ---------------------------------------------------------------------------

def ask_condition_kind() -> str:
    print("    1. Must equal a specific value")
    print("    2. Must fall within a range")
    print("    3. Just needs to be present (any value counts)")
    while True:
        choice = ask("    Pick 1-3:")
        if choice in ("1", "2", "3"):
            return choice
        print("    please pick 1, 2, or 3")


def ask_conditions(registry_fields: list[str] | None, aggregation_components: list[str],
                    relative: bool = False) -> dict:
    """Returns {attrName: {...}} ready for CalculationRuleBuilder's `conditions` param.
    relative=True for a condition inside a SUBENTITY-scoped rule -- see ask_field_reference()."""
    conditions = {}
    if not ask_yes_no("Does this rule only apply under certain conditions?"):
        return conditions
    while True:
        attr_name = ask("  Give this condition a short name (e.g. 'premisesArea'):")
        if aggregation_components and ask_yes_no(
                f"  Does '{attr_name}' band on a previously-totaled number "
                f"({', '.join(aggregation_components)}), rather than a raw field?"):
            target = ask("  Which totaled component?")
            conditions[attr_name] = {"derivedFrom": target}
        else:
            json_path = ask_field_reference(f"does '{attr_name}' come from", registry_fields, relative=relative)
            kind = ask_condition_kind()
            if kind == "1":
                raw = ask(f"  What value must '{attr_name}' equal?")
                conditions[attr_name] = {"jsonPath": json_path, "equals": _coerce_equals(raw)}
            elif kind == "2":
                lo = ask_optional_number(f"  Lowest allowed value for '{attr_name}'? (blank for no minimum)")
                hi = ask_optional_number(f"  Highest allowed value for '{attr_name}'? (blank for no maximum)")
                spec = {"jsonPath": json_path}
                if lo is not None:
                    spec["from"] = lo
                if hi is not None:
                    spec["to"] = hi
                conditions[attr_name] = spec
            else:
                conditions[attr_name] = {"jsonPath": json_path}
        if not ask_yes_no("  Another condition?"):
            break
    return conditions


# ---------------------------------------------------------------------------
# Shared extras: existing-component picker, dependsOn, priority, roundOff, dates
# ---------------------------------------------------------------------------

def ask_existing_component(builder: CalculationRuleBuilder, purpose: str) -> str:
    components = sorted({r.component for r in builder.rules})
    if not components:
        return ask(f"Which component {purpose}? (name it -- nothing defined yet this session)")
    print(f"  Which component {purpose}?")
    for i, c in enumerate(components, 1):
        print(f"    {i}. {c}")
    manual = len(components) + 1
    print(f"    {manual}. (a component not listed)")
    while True:
        choice = ask(f"  Pick 1-{manual}:")
        if choice.isdigit() and 1 <= int(choice) <= len(components):
            return components[int(choice) - 1]
        if choice == str(manual):
            return ask("  Component name:")
        print("  please pick a valid option")


def ask_extra_depends_on(builder: CalculationRuleBuilder, already: list[str]) -> list[str]:
    others = sorted({r.component for r in builder.rules} - set(already))
    if not others:
        return list(already)
    if ask_yes_no("Does this need to be calculated after any other component, purely for "
                  f"ordering (not reading its value)? ({', '.join(others)})"):
        raw = ask("  Which component(s)? (comma-separated)")
        extra = [c.strip() for c in raw.split(",") if c.strip()]
        return list(already) + [c for c in extra if c not in already]
    return list(already)


def ask_priority(default: int = 100) -> int:
    raw = ask(f"Any specific ordering priority? (lower runs first; blank for default {default})")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print("  couldn't parse that -- using default")
        return default


def ask_round_off() -> str:
    print("  Round the result to the nearest:")
    print("    1. Whole currency unit (default)")
    print("    2. 10")
    print("    3. 100")
    print("    4. Don't round")
    choice = ask("  Pick 1-4 (blank for default):")
    return {"1": "NEAREST_1", "2": "NEAREST_10", "3": "NEAREST_100", "4": "NONE"}.get(choice, "NEAREST_1")


def ask_effective_dates() -> tuple[str, str | None]:
    eff_from = ask("What date does this take effect? (YYYY-MM-DD)")
    eff_to = ask("Does it stop applying on some later date? (YYYY-MM-DD, or blank if it doesn't expire)")
    return eff_from, eff_to or None


# ---------------------------------------------------------------------------
# The mechanism menu, and one configure_* function per mechanism
# ---------------------------------------------------------------------------

_MECHANISM_LABELS = {
    "1": "A flat amount every time (optionally depending on conditions)",
    "2": "A rate multiplied by some field (e.g. per square foot)",
    "3": "A rate charged once per item in a repeating list (e.g. per accessory)",
    "4": "Tiered/marginal bands of the same field (e.g. income tax brackets)",
    "5": "A percentage of another fee (a tax or cess on top of it)",
    "6": "A rebate or deduction from another fee",
    "7": "Totaling up a repeating list into one number (e.g. total floor area)",
    "8": "Real math combining more than one input",
}


def ask_mechanism() -> str:
    print("\nWhat kind of charge is this?")
    for key, label in _MECHANISM_LABELS.items():
        print(f"  {key}. {label}")
    while True:
        choice = ask("  Pick 1-8:")
        if choice in _MECHANISM_LABELS:
            return choice
        print("  please pick one of 1-8")


def configure_flat(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this fee/component? (e.g. 'BASE_LICENSE_FEE')")
    value = ask_required_number("How much is it?")
    conditions = ask_conditions(registry_fields, aggregation_components)
    depends_on = ask_extra_depends_on(builder, [])
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_flat_rule(component, value, conditions=conditions, dependsOn=depends_on,
                           priority=priority, roundOff=round_off, effectiveFrom=eff_from,
                           effectiveTo=eff_to)


def configure_per_unit(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this fee/component?")
    rate = ask_required_number("What's the rate per unit?")
    applies_on = ask_field_reference("does the rate multiply", registry_fields)
    conditions = ask_conditions(registry_fields, aggregation_components)
    depends_on = ask_extra_depends_on(builder, [])
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_per_unit_rule(component, rate, applies_on, conditions=conditions,
                               dependsOn=depends_on, priority=priority, roundOff=round_off,
                               effectiveFrom=eff_from, effectiveTo=eff_to)


def configure_per_item(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this fee/component?")
    rate = ask_required_number("What's the rate per item?")
    sub_entity_path = ask_field_reference("is the repeating list (e.g. 'accessories')", registry_fields)
    if not sub_entity_path.endswith("[*]"):
        sub_entity_path += "[*]"
    # Relative to one item in the list, not root-absolute -- see ask_field_reference()'s docstring.
    applies_on = ask_field_reference("inside each item does the rate multiply "
                                     "(e.g. 'quantity')", registry_fields, relative=True)
    conditions = ask_conditions(registry_fields, aggregation_components, relative=True)
    depends_on = ask_extra_depends_on(builder, [])
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_per_item_rule(component, rate, sub_entity_path, applies_on, conditions=conditions,
                               dependsOn=depends_on, priority=priority, roundOff=round_off,
                               effectiveFrom=eff_from, effectiveTo=eff_to)


def ask_slabs(registry_fields) -> tuple[str, list[dict]]:
    applies_on = ask_field_reference("do the tiers apply to (e.g. 'income')", registry_fields)
    print("  Now define each tier, lowest to highest.")
    slabs = []
    lo = 0
    while True:
        print(f"  Tier starting at {lo}:")
        hi = ask_optional_number("    Ends at? (blank if this is the last, open-ended tier)")
        # The engine divides this by 100 before applying it (same convention as PERCENTAGE's
        # `value`) -- confirmed by calculation-rule-examples.pdf's example #14, whose own prose
        # ("0.5% on the first 500,000, 1% on the remaining 200,000") only reproduces the stated
        # result (2500 + 2000 = 4500) with rate/100; an earlier version of this question told
        # users to pre-divide by 100 themselves, which was compensating for a /100 missing from
        # simulate.py's _compute_slab rather than reflecting real engine behavior.
        rate = ask_required_number("    Rate for this tier? (divided by 100 before being applied "
                                    "-- for a 5% bracket enter 5, not 0.05; for a plain "
                                    "currency-per-unit rate like $5/unit, enter 500)")
        slabs.append({"from": lo, "to": hi, "rate": rate})
        if hi is None:
            break
        lo = hi
        if not ask_yes_no("  Add another tier?"):
            break
    return applies_on, slabs


def configure_slab(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this fee/component?")
    applies_on, slabs = ask_slabs(registry_fields)
    conditions = ask_conditions(registry_fields, aggregation_components)
    depends_on = ask_extra_depends_on(builder, [])
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_slab_rule(component, applies_on, slabs, conditions=conditions,
                           dependsOn=depends_on, priority=priority, roundOff=round_off,
                           effectiveFrom=eff_from, effectiveTo=eff_to)


def configure_percentage(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this tax/cess/component?")
    percentage = ask_required_number("What percentage?")
    applies_on_component = ask_existing_component(builder, "is this a percentage of")
    is_tax = ask_yes_no("Is this a statutory tax (rather than a general fee)?")
    conditions = ask_conditions(registry_fields, aggregation_components)
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_percentage_rule(component, percentage, applies_on_component,
                                 ruleType="TAX" if is_tax else "RATE_MATRIX",
                                 conditions=conditions, priority=priority, roundOff=round_off,
                                 effectiveFrom=eff_from, effectiveTo=eff_to)


def configure_adjustment(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this rebate/deduction?")
    is_percentage = ask_yes_no("Is it a percentage (yes), or a flat amount (no)?")
    value = ask_required_number("How much is the deduction? (use a negative number to reduce the fee)")
    applies_on_component = ask_existing_component(builder, "does this reduce")
    conditions = ask_conditions(registry_fields, aggregation_components)
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_adjustment_rule(component, value, applies_on_component, is_percentage=is_percentage,
                                 conditions=conditions, priority=priority, roundOff=round_off,
                                 effectiveFrom=eff_from, effectiveTo=eff_to)


def configure_aggregation(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this total? (e.g. 'TOTAL_FLOOR_AREA')")
    print("  What kind of total?")
    print("    1. Sum the values")
    print("    2. Count how many there are")
    print("    3. Largest value")
    print("    4. Smallest value")
    print("    5. Average")
    func = {"1": "SUM", "2": "COUNT", "3": "MAX", "4": "MIN", "5": "AVG"}.get(
        ask("  Pick 1-5:"), "SUM")
    sub_entity_path = ask_field_reference("is the repeating list (e.g. 'floors')", registry_fields)
    if not sub_entity_path.endswith("[*]"):
        sub_entity_path += "[*]"
    # Relative to one item in the list (e.g. 'area'), not root-absolute -- simulate.py resolves
    # this against each sub-entity dict directly, per the vocabulary reference's "relative to one
    # array element." A '$.'-prefixed or list-prefixed path here would silently fail to resolve
    # (found and fixed while wiring up worked-example simulation -- see registry_lookup.py's
    # field_to_relative_path() docstring).
    source = ask_field_reference("inside each item should be totaled (e.g. 'area')", registry_fields,
                                  relative=True)
    target_attribute = ask("  What should the result be called? (e.g. 'totalFloorArea')")
    eff_from, eff_to = ask_effective_dates()
    builder.add_aggregation_rule(component, func, sub_entity_path, source, target_attribute,
                                  effectiveFrom=eff_from, effectiveTo=eff_to)
    aggregation_components.append(component)


def ask_formula_variables(registry_fields, builder) -> dict:
    variables = {}
    print("  Name each input your formula needs.")
    while True:
        var_name = ask("  Variable name (e.g. 'size') -- or leave blank if done:")
        if not var_name:
            break
        if ask_yes_no(f"  Does '{var_name}' come from another component's already-computed amount?"):
            component = ask_existing_component(builder, f"does '{var_name}' read")
            variables[var_name] = {"componentRef": component}
        else:
            json_path = ask_field_reference(f"does '{var_name}' come from", registry_fields)
            variables[var_name] = {"jsonPath": json_path}
    return variables


def configure_formula(builder, registry_fields, aggregation_components):
    component = ask("What do you want to call this fee/component?")
    variables = ask_formula_variables(registry_fields, builder)
    while True:
        expr = ask("  Write the formula using those variable names (e.g. 'base + rate * size'):")
        try:
            formula_logic = parse_formula(expr, set(variables.keys()))
            break
        except FormulaParseError as e:
            print(f"  {e}")
    conditions = ask_conditions(registry_fields, aggregation_components)
    depends_on = ask_extra_depends_on(builder, [
        b["componentRef"] for b in variables.values() if "componentRef" in b])
    priority = ask_priority()
    round_off = ask_round_off()
    eff_from, eff_to = ask_effective_dates()
    builder.add_formula_rule(component, formula_logic, variables, conditions=conditions,
                              dependsOn=depends_on, priority=priority, roundOff=round_off,
                              effectiveFrom=eff_from, effectiveTo=eff_to)


_CONFIGURE_BY_MECHANISM = {
    "1": configure_flat,
    "2": configure_per_unit,
    "3": configure_per_item,
    "4": configure_slab,
    "5": configure_percentage,
    "6": configure_adjustment,
    "7": configure_aggregation,
    "8": configure_formula,
}


def configure_one_rule(builder: CalculationRuleBuilder, registry_fields, aggregation_components) -> None:
    mechanism = ask_mechanism()
    _CONFIGURE_BY_MECHANISM[mechanism](builder, registry_fields, aggregation_components)


# ---------------------------------------------------------------------------
# Fix-one-thing menu (instead of discarding the whole session on "no")
# ---------------------------------------------------------------------------

def offer_fix(builder: CalculationRuleBuilder, registry_fields, aggregation_components) -> None:
    components = [r.component for r in builder.rules]
    choice = ask(
        "What do you want to fix? Type a rule number (1-{}) to redo it, 'add' for a new rule, "
        "'delete N' to remove one, or 'module' for the module name.\nRules: {}".format(
            len(builder.rules), ", ".join(f"{i + 1}={c}" for i, c in enumerate(components))))
    lowered = choice.lower()
    if lowered == "module":
        new_module = ask(f"Module? (currently '{builder.module}', blank to keep)")
        if new_module:
            builder.module = new_module
        return
    if lowered == "add":
        configure_one_rule(builder, registry_fields, aggregation_components)
        return
    if lowered.startswith("delete "):
        raw_idx = choice.split(None, 1)[1].strip()
    else:
        raw_idx = choice.strip()
    try:
        idx = int(raw_idx) - 1
    except ValueError:
        print(f"  '{choice}' isn't a valid choice -- nothing changed")
        return
    if not (0 <= idx < len(builder.rules)):
        print("  not a valid rule number -- nothing changed")
        return
    if lowered.startswith("delete "):
        builder.remove_rule(idx)
        print(f"  -> removed rule {idx + 1}")
    else:
        print(f"  Redoing rule {idx + 1} ({builder.rules[idx].component}) from scratch.")
        builder.remove_rule(idx)
        configure_one_rule(builder, registry_fields, aggregation_components)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def run_session() -> CalculationRuleSet:
    """Runs the full question sequence and returns the final, validated CalculationRuleSet.
    Split from main() so tests can drive the exact interactive code path."""
    print("=== Calculation rule wizard ===")
    print("(type 'quit' at any question to stop -- nothing is saved until the very end)\n")
    module = ask("What module are these rules for? (e.g. 'trade-license')")
    builder = CalculationRuleBuilder(module)
    registry_fields = setup_registry_fields()
    aggregation_components: list[str] = []

    configure_one_rule(builder, registry_fields, aggregation_components)
    while ask_yes_no("\nAdd another rule?"):
        configure_one_rule(builder, registry_fields, aggregation_components)

    while True:
        rule_set = builder.build()
        errors = validate_rule_set_models(rule_set)

        if errors:
            print("\nVALIDATION FAILED -- fix these before a preview would mean anything:")
            for e in errors:
                print(f"  - {e}")
            offer_fix(builder, registry_fields, aggregation_components)
            continue

        raw_rules = [r.model_dump(by_alias=True, exclude_none=True) for r in rule_set.rules]
        scenarios = generate_scenarios(raw_rules)
        scenario_results = run_scenarios(raw_rules, scenarios)

        preview_path = os.path.abspath(f"{rule_set.module}_rules_preview.html")
        render_ruleset_preview(rule_set, preview_path, scenario_results=scenario_results)
        print(f"\nAll checks passed. Open this in a browser to review it visually:\n  {preview_path}")
        print(f"(click any row for its exact rule definition -- {len(scenario_results)} worked "
              "example(s) included, showing what this actually computes)")

        if ask_yes_no("\nDoes this look right? Confirm to create these rules"):
            break

        print("Not confirmed -- let's fix just the part that's wrong (type 'quit' to stop entirely).")
        offer_fix(builder, registry_fields, aggregation_components)

    return rule_set


def _calc_engine_headers() -> dict[str, str] | None:
    """Same env vars/header convention already verified for ../workflow-prototype/ and
    ../registry-prototype/ -- but unverified here, since no real Calculation Engine service
    exists anywhere in the digitnxt org to check this against (see models.py's docstring).
    Best-effort, not confirmed."""
    return registry_headers()


def write_rules(rule_set: CalculationRuleSet) -> None:
    headers = _calc_engine_headers()
    body = json.dumps([r.model_dump(by_alias=True, exclude_none=True) for r in rule_set.rules]).encode()

    if headers is None:
        print("\n=== DRY RUN (DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID not all set -- "
              "nothing sent) ===")
        print(f"Would POST to: {{server}}/{rule_set.module}/rules")
        print("Body:")
        print(json.dumps([r.model_dump(by_alias=True, exclude_none=True) for r in rule_set.rules], indent=2))
        return

    server_url = os.environ["DIGIT_SERVER_URL"]
    url = server_url.rstrip("/") + f"/{rule_set.module}/rules"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"\nCreated -- {resp.status} {resp.reason}")
            print(f"{len(rule_set.rules)} rule(s) for '{rule_set.module}' are now live.")
    except urllib.error.HTTPError as e:
        print(f"\nWrite failed -- {e.code} {e.reason}")
        print(e.read().decode(errors="replace"))


def main():
    write_rules(run_session())


if __name__ == "__main__":
    try:
        main()
    except Cancelled:
        print("\nCancelled -- nothing was saved.")
    except (KeyboardInterrupt, EOFError):
        print("\n\nCancelled -- nothing was saved.")
