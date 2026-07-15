"""Conformance-test mode: turns a "preview gap" panel's plain-English claim about what an advanced
JSON Schema construct means into actual, executed proof -- a concrete JSON value built to satisfy
the rule and one built to violate it, both run through the same real jsonschema.Draft202012Validator
this project already uses for schema-syntax checks (see validate.py), with the real pass/fail
result shown back.

Scope is deliberately narrow, not "generate an instance for any possible JSON Schema" (a hard,
open-ended problem full-blown property-based-testing libraries exist to solve). A probe is only
attempted for the exact recognized shapes _explain_advanced_construct in render.py already marks
"specific" (a real field name, a real pattern, a real count -- not a generic placeholder): contains
+ minContains/maxContains with a const/enum condition, prefixItems with a known length,
propertyNames/patternProperties with a recognized digit-pattern, and unevaluatedProperties true/
false. Anything else honestly reports that it couldn't auto-generate a test, rather than fabricating
one that might not actually probe the real rule -- the same "never claim more than can be backed up"
principle the gap panel itself follows.

Deliberately self-contained (no import of render.py, despite overlapping pattern-recognition logic
with _pattern_hint/_describe_simple_condition there) to avoid a circular import, since render.py
needs to call into this module to render conformance results inside the gap panel. The two
recognized-pattern-shape lists can drift over time; the failure mode of that drift is only "fewer
tests get generated," never an incorrect one, since every value produced here is independently
re-validated by the real validator before being trusted.
"""

from __future__ import annotations

import re

import jsonschema

# Mirrors render.py's _pattern_hint recognized shapes (the only patterns this project's own
# wizard ever actually generates -- see ask_text_pattern in wizard.py). Kept separate from
# render.py deliberately -- see module docstring.

# Same reasoning as _MAX_CONTAINS_PROBE_ITEMS: the digit count N comes straight from
# admin/LLM-authored schema content ("^[0-9]{N}$"). An adversarial review confirmed an absurdly
# large N (hundreds of millions) makes this module build a matching multi-hundred-MB string and
# hang for several seconds, reachable through every probe builder that calls _baseline_leaf_value
# on a pattern-constrained string. No real wizard-generated pattern ever needs more than a
# handful of digits (6-digit pincode, 10-digit mobile, 12-digit Aadhaar).
_MAX_PATTERN_PROBE_DIGITS = 100


def _pattern_sample(pattern: str) -> tuple[str, str] | None:
    """Returns (a string that matches `pattern`, a string that does not) for the handful of
    digit-count shapes this project recognizes. None for anything else, INCLUDING a recognized
    shape whose digit count is too large to safely build a sample for (see
    _MAX_PATTERN_PROBE_DIGITS) -- treated the same as "unrecognized," an honest skip rather than
    a fabricated sample."""
    m = re.fullmatch(r"\^\[0-9\]\{(\d+)\}\$", pattern) or re.fullmatch(r"\^\[0-9\]\{(\d+),(\d+)\}\$", pattern)
    if not m:
        return None
    n = int(m.group(1))
    if n > _MAX_PATTERN_PROBE_DIGITS:
        return None
    matching = "1" * n
    violating = "x" * n if n else "x"
    return matching, violating


def _different_value(value, avoid=None):
    """A value of the same JSON type as `value` but guaranteed not equal to it (or to anything in
    `avoid`) -- used to build a concrete violation of a const/enum condition. None if no safe,
    confident negation exists (null/list/dict values aren't attempted)."""
    avoid = set(avoid or [])
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        for suffix in ("__NOT_MATCHING", "__ALT_VALUE", "__DIFFERENT"):
            candidate = value + suffix
            if candidate != value and candidate not in avoid:
                return candidate
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for delta in (1, -1, 1000, -1000):
            candidate = value + delta
            if candidate != value and candidate not in avoid:
                return candidate
        return None
    return None


def _condition_values(sub_schema) -> tuple[dict, dict | None] | None:
    """Mirrors render.py's _describe_simple_condition's recognized shape (const/enum checks on
    named properties): returns (matching_props, violating_props) -- concrete values that would
    satisfy vs. violate the condition. `violating_props` is None if no safe negation could be
    built for any recognized property (the condition is real but can't be safely violated).
    None overall if the sub-schema isn't this recognizable shape at all."""
    if not isinstance(sub_schema, dict):
        return None
    props = sub_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    matching: dict = {}
    violating: dict = {}
    for prop_name, value_schema in props.items():
        if not isinstance(value_schema, dict):
            continue
        if value_schema.get("const") is not None:
            const_val = value_schema["const"]
            matching[prop_name] = const_val
            alt = _different_value(const_val)
            if alt is not None:
                violating[prop_name] = alt
        elif value_schema.get("enum"):
            values = value_schema["enum"]
            if not values:
                continue
            matching[prop_name] = values[0]
            alt = _different_value(values[0], avoid=values)
            if alt is not None:
                violating[prop_name] = alt
    if not matching:
        return None
    return matching, (violating or None)


def _wrong_type_value(type_name):
    """A concrete value guaranteed NOT to be of `type_name` (JSON Schema's type vocabulary) --
    used to build a value that fails a specific sub-schema's own "type" check. None for an
    unspecified/unrecognized type, since there's no single "wrong" value to pick without knowing
    what's actually expected."""
    return {
        "string": 12345, "integer": "not-a-number", "number": "not-a-number",
        "boolean": "not-a-boolean", "array": "not-an-array", "object": "not-an-object",
    }.get(type_name)


def _baseline_leaf_value(schema):
    """Best-effort minimal valid value for a simple (non-object, non-array) property schema.
    Returns None (meaning "couldn't confidently synthesize one") rather than guessing at
    something that might not actually satisfy the schema."""
    if not isinstance(schema, dict):
        return None
    if schema.get("const") is not None:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0] if schema["enum"] else None
    t = schema.get("type")
    if t == "string":
        if isinstance(schema.get("pattern"), str):
            sample = _pattern_sample(schema["pattern"])
            return sample[0] if sample else None
        # A fixed 11-char "sample-value" used to be returned regardless of minLength/maxLength --
        # adversarial review confirmed a schema-declared minLength of 20 makes the resulting
        # instance fail for a reason unrelated to whatever construct was actually being probed.
        value = "sample-value"
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if (isinstance(min_length, int) and isinstance(max_length, int) and min_length > max_length):
            return None  # self-contradictory (unsatisfiable) -- don't fabricate a value either way
        if isinstance(min_length, int) and min_length > len(value):
            if min_length > 1000:
                return None  # not confidently safe to pad this far -- see _MAX_CONTAINS_PROBE_ITEMS
            value = value + "x" * (min_length - len(value))
        if isinstance(max_length, int) and max_length < len(value):
            if max_length < 0:
                return None
            value = value[:max_length]
        return value
    if t == "integer" or t == "number":
        lo, hi = schema.get("minimum"), schema.get("maximum")
        val = lo if isinstance(lo, (int, float)) else 0
        if isinstance(hi, (int, float)) and val > hi:
            val = hi
        return int(val) if t == "integer" else val
    if t == "boolean":
        return True
    return None


def _baseline_object_value(schema) -> dict | None:
    """A minimal object satisfying just the REQUIRED properties of `schema` (extra/optional
    properties are never added, since the point is to build the smallest instance that isolates
    whichever specific construct a probe is testing). None if any required property can't be
    confidently synthesized -- fails closed rather than producing an instance that might fail
    validation for an unrelated, unintended reason."""
    if schema is None or schema is True:
        return {}
    if not isinstance(schema, dict) or schema.get("type") not in (None, "object"):
        return None
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    result = {}
    for name in required:
        sub = properties.get(name)
        val = _baseline_leaf_value(sub) if sub is not None else None
        if val is None:
            return None
        result[name] = val
    return result


# A cap on how many copies of the matching item a positive probe instance ever contains,
# regardless of the schema's own stated minContains. minContains comes straight from
# admin/LLM-authored schema content -- an absurdly large value (a typo, or an adversarial input)
# must never make this module try to build and then jsonschema-validate a multi-million-item
# array during a render; that's a real hang, confirmed by hand (5,000,000 didn't finish in 8+
# seconds). The rule's own enforcement by the real Registry Service is completely unaffected by
# this cap -- it only limits how large *our own demonstration example* is willing to get.
_MAX_CONTAINS_PROBE_ITEMS = 20


_CONTAINS_OWNED_KEYS = frozenset({"contains", "minContains", "maxContains"})


def _probe_contains(prop):
    condition = prop.get("contains")
    values = _condition_values(condition)
    if values is None:
        return None, "the 'contains' condition isn't a recognized simple pattern (const/enum on a named property)"
    matching, violating = values
    items_schema = prop.get("items")
    base = _baseline_object_value(items_schema)
    if base is None:
        return None, "could not synthesize a full example item satisfying this field's 'items' schema"
    n = prop.get("minContains", 1)
    if not isinstance(n, int) or n < 1:
        n = 1
    # A sibling minItems can demand a longer array than minContains alone would; unique_items
    # additionally means the array can't just be n copies of the identical item. Both were found
    # by adversarial review to produce a false "surprising" FAIL otherwise -- the probe's own
    # array didn't respect constraints this same field already declares on itself.
    min_items = prop.get("minItems")
    if isinstance(min_items, int) and min_items > n:
        n = min_items
    if n > _MAX_CONTAINS_PROBE_ITEMS:
        return None, f"minContains/minItems ({n}) is too large to demonstrate with a generated example"
    positive_item = {**base, **matching}
    if prop.get("uniqueItems"):
        positive_instance = [{**positive_item, "__probeIndex": i} for i in range(n)]
    else:
        positive_instance = [dict(positive_item) for _ in range(n)]
    checks = [{"expected_valid": True, "instance": positive_instance, "owned_keys": _CONTAINS_OWNED_KEYS}]
    if violating is not None:
        negative_item = {**base, **violating}
        checks.append({"expected_valid": False, "instance": [negative_item], "owned_keys": _CONTAINS_OWNED_KEYS})
    return checks, None


_PREFIX_ITEMS_OWNED_KEYS = frozenset({"prefixItems"})


def _probe_prefix_items(prop):
    items = prop.get("prefixItems")
    if not isinstance(items, list) or not items:
        return None, "prefixItems isn't a non-empty list"
    positive = []
    for sub in items:
        if not isinstance(sub, dict):
            return None, "one of the prefixItems entries isn't a schema object"
        val = _baseline_object_value(sub) if sub.get("type") == "object" else _baseline_leaf_value(sub)
        if val is None:
            return None, "could not synthesize a valid value for one of the prefixItems positions"
        positive.append(val)
    # A sibling minItems can demand more positions than prefixItems itself defines -- found by
    # adversarial review to produce a false "surprising" FAIL (too short) otherwise. Extra
    # positions are governed by this field's own "items" schema (or unrestricted if absent).
    min_items = prop.get("minItems")
    if isinstance(min_items, int) and min_items > len(positive):
        extra_schema = prop.get("items")
        for _ in range(min_items - len(positive)):
            extra = (_baseline_object_value(extra_schema) if isinstance(extra_schema, dict)
                      and extra_schema.get("type") == "object" else _baseline_leaf_value(extra_schema))
            if extra is None and extra_schema is not None:
                return None, "minItems requires more positions than prefixItems defines, and the " \
                              "extra 'items' schema couldn't be confidently synthesized"
            positive.append(extra if extra is not None else "sample-value")
    checks = [{"expected_valid": True, "instance": positive, "owned_keys": _PREFIX_ITEMS_OWNED_KEYS}]
    first_type = items[0].get("type") if isinstance(items[0], dict) else None
    wrong = _wrong_type_value(first_type)
    if wrong is not None:
        checks.append({"expected_valid": False, "instance": [wrong] + positive[1:],
                       "owned_keys": _PREFIX_ITEMS_OWNED_KEYS})
    return checks, None


_PROPERTY_NAMES_OWNED_KEYS = frozenset({"propertyNames"})


def _probe_property_names(prop):
    pn = prop.get("propertyNames")
    pattern = pn.get("pattern") if isinstance(pn, dict) else None
    if not isinstance(pattern, str):
        return None, "propertyNames has no recognizable pattern"
    sample = _pattern_sample(pattern)
    if sample is None:
        return None, "the propertyNames pattern isn't a recognized simple shape"
    matching_name, violating_name = sample
    base = _baseline_object_value(prop)
    if base is None:
        return None, "could not synthesize a baseline instance for this field's own required properties"
    # Two sibling-interaction cases an adversarial review found produce a false/confusing result
    # rather than an honest one: (a) an already-declared property (required OR optional -- checked
    # against the full "properties" dict, not just the required subset in `base`) whose OWN name
    # doesn't comply with this same propertyNames pattern -- a genuine schema self-inconsistency,
    # but not what THIS probe is testing, so it's fairer to skip than to blame the new synthesized
    # name; (b) the synthesized name colliding with an already-declared property (again, whether
    # required or not), which would silently validate against that property's own value schema
    # instead of testing propertyNames at all.
    declared = prop.get("properties") if isinstance(prop.get("properties"), dict) else {}
    for existing_name in declared:
        if re.fullmatch(pattern, existing_name) is None:
            return None, (f"the schema's own existing property '{existing_name}' doesn't match its "
                           "propertyNames pattern -- skipped rather than produce a misleading result")
    if matching_name in declared or violating_name in declared:
        return None, "the synthesized property name collides with one already declared on this field"
    return [
        {"expected_valid": True, "instance": {**base, matching_name: "sample-value"},
         "owned_keys": _PROPERTY_NAMES_OWNED_KEYS},
        {"expected_valid": False, "instance": {**base, violating_name: "sample-value"},
         "owned_keys": _PROPERTY_NAMES_OWNED_KEYS},
    ], None


_PATTERN_PROPERTIES_OWNED_KEYS = frozenset({"patternProperties"})


def _probe_pattern_properties(prop):
    pp = prop.get("patternProperties")
    if not isinstance(pp, dict) or not pp:
        return None, "patternProperties has no entries"
    # Try every entry for a recognized pattern shape, not just the first -- an adversarial review's
    # mutation testing found the original next(iter(...)) gave up entirely if the FIRST entry
    # happened to be unrecognized, even when a later entry was perfectly testable.
    sample = matching_name = value_schema = None
    for candidate_pattern, candidate_value_schema in pp.items():
        candidate_sample = _pattern_sample(candidate_pattern)
        if candidate_sample is not None:
            sample, value_schema = candidate_sample, candidate_value_schema
            matching_name = candidate_sample[0]
            break
    if sample is None:
        return None, "none of the patternProperties key patterns are a recognized simple shape"
    declared = prop.get("properties") if isinstance(prop.get("properties"), dict) else {}
    if matching_name in declared:
        return None, "the synthesized property name collides with one already declared on this field"
    value = _baseline_leaf_value(value_schema) if isinstance(value_schema, dict) else None
    if value is None:
        value = "sample-value"
    base = _baseline_object_value(prop)
    if base is None:
        return None, "could not synthesize a baseline instance for this field's own required properties"
    checks = [{"expected_valid": True, "instance": {**base, matching_name: value},
               "owned_keys": _PATTERN_PROPERTIES_OWNED_KEYS}]
    wrong_value = _wrong_type_value(value_schema.get("type")) if isinstance(value_schema, dict) else None
    if wrong_value is not None:
        checks.append({"expected_valid": False, "instance": {**base, matching_name: wrong_value},
                       "owned_keys": _PATTERN_PROPERTIES_OWNED_KEYS})
    return checks, None


_UNEVALUATED_PROPERTIES_OWNED_KEYS = frozenset({"unevaluatedProperties"})


def _probe_unevaluated_properties(prop):
    uep = prop.get("unevaluatedProperties")
    if uep is False:
        base = _baseline_object_value(prop)
        if base is None:
            return None, "could not synthesize a baseline instance for this field's own required properties"
        return [
            {"expected_valid": True, "instance": dict(base), "owned_keys": _UNEVALUATED_PROPERTIES_OWNED_KEYS},
            {"expected_valid": False, "instance": {**base, "__unexpectedExtraField": "value"},
             "owned_keys": _UNEVALUATED_PROPERTIES_OWNED_KEYS},
        ], None
    if uep is True:
        return None, "unevaluatedProperties: true has no restrictive effect -- nothing to test"
    return None, "unevaluatedProperties as a nested schema isn't a recognized simple pattern to probe"


_PROBE_BUILDERS = (
    (("contains", "minContains"), _probe_contains),
    (("prefixItems",), _probe_prefix_items),
    (("propertyNames",), _probe_property_names),
    (("patternProperties",), _probe_pattern_properties),
    (("unevaluatedProperties",), _probe_unevaluated_properties),
)


# Every JSON Schema 2020-12 keyword whose whole purpose is "go resolve a schema from elsewhere."
# An adversarial review confirmed jsonschema.Draft202012Validator genuinely attempts a real DNS
# resolution (caught with socket.getaddrinfo intercepted) for $dynamicRef, not just the plain
# $ref this guard originally checked -- the fix is to name every resolution keyword, not just the
# one that happened to be tested first.
_REF_KEYS = ("$ref", "$dynamicRef", "$recursiveRef")


def _contains_ref(value) -> bool:
    """True if `value` (recursively) contains any of _REF_KEYS anywhere. A probe is never
    attempted on a fragment that references another schema -- resolving one, even an internal
    one, is out of scope here, and an EXTERNAL reference could make the real validator attempt a
    network fetch during .is_valid()/.iter_errors(), which would break this project's
    offline-safety guarantee. Safety over coverage: skipping is always the honest fallback (see
    probe_gap's docstring)."""
    if isinstance(value, dict):
        if any(key in value for key in _REF_KEYS):
            return True
        return any(_contains_ref(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_ref(v) for v in value)
    return False


def _build_validator(prop: dict):
    schema = dict(prop)
    schema.pop("$schema", None)
    return jsonschema.Draft202012Validator(schema)


def probe_gap(prop) -> dict:
    """Given the same `prop` fragment a preview-gap explanation was built from, attempts to
    construct one concrete JSON value that SATISFIES the flagged rule and one that VIOLATES it,
    then validates both against `prop` (treated as its own standalone JSON Schema) using the real
    jsonschema.Draft202012Validator -- so the gap panel can show actual, executed pass/fail
    evidence instead of only a plain-language description.

    Returns {"attempted": bool, "checks": [...], "skipped_reason": str | None}. Each check dict:
    {"kind": "positive"|"negative", "instance": <value>, "expected_valid": bool,
     "actual_valid": bool | None, "errored": bool, "surprising": bool, "inconclusive": bool,
     "unrelated_keywords": [str, ...], "errors": [str, ...]}.

    Four distinct outcomes, each with its own honest meaning -- never collapsed into one another:
    - as expected (all four flags false): the flagged construct behaved exactly as described.
    - "errored": the validator itself raised instead of returning pass/fail -- no claim is made.
    - "surprising" -- THE single most valuable signal this feature produces: actual_valid
      disagreeing with what the construct was built to demonstrate, for a reason attributable to
      the construct's OWN keyword(s) (see below), means the rule doesn't behave as described.
    - "inconclusive": actual_valid disagreed with expectation, or matched it for the wrong reason,
      but jsonschema.ValidationError.schema_path attributes every relevant error to a DIFFERENT
      keyword on the same field ("unrelated_keywords") -- e.g. minItems/uniqueItems/
      additionalProperties/a hidden allOf requirement/another co-occurring construct this module
      couldn't also account for. This means the generated example wasn't a full, realistic
      instance and the check doesn't actually confirm or refute the flagged rule either way --
      NOT the same as "surprising," which would falsely implicate a rule that's working fine.

    Never raises -- a fragment this module can't confidently handle is reported as skipped, not
    guessed at."""
    if not isinstance(prop, dict):
        return {"attempted": False, "checks": [], "skipped_reason": None}

    applicable = [builder for keys, builder in _PROBE_BUILDERS if any(k in prop for k in keys)]
    if not applicable:
        return {"attempted": False, "checks": [], "skipped_reason": None}

    if _contains_ref(prop):
        return {"attempted": False, "checks": [],
                "skipped_reason": "this field references another schema ($ref) -- skipped to avoid "
                                   "any risk of a network lookup during validation"}

    all_checks = []
    reasons = []
    for builder in applicable:
        checks, reason = builder(prop)
        if checks:
            all_checks.extend(checks)
        if reason:
            reasons.append(reason)

    if not all_checks:
        return {"attempted": False, "checks": [],
                "skipped_reason": "; ".join(reasons) if reasons else
                                   "could not auto-generate a conformance test for this rule"}

    try:
        validator = _build_validator(prop)
    except Exception as e:
        return {"attempted": False, "checks": [], "skipped_reason": f"could not build a validator: {e}"}

    results = []
    for check in all_checks:
        instance = check["instance"]
        expected_valid = check["expected_valid"]
        owned_keys = check.get("owned_keys") or frozenset()
        errored = False
        surprising = False
        inconclusive = False
        unrelated_keywords = []
        try:
            actual_valid = validator.is_valid(instance)
            raised = [] if actual_valid else list(validator.iter_errors(instance))
            errors = [e.message for e in raised]
        except Exception as e:
            # The validator itself raised (e.g. a malformed regex elsewhere in the schema) rather
            # than returning a real pass/fail -- this must NEVER be presented as a confident
            # result. An adversarial review found the render layer previously collapsed this into
            # a confident "FAIL, as expected" (actual_valid=None is falsy), the exact kind of
            # unbacked claim this whole feature exists to prevent. "errored" is its own explicit
            # outcome, distinct from both a normal pass/fail and a "surprising" disagreement.
            actual_valid = None
            errored = True
            errors = [f"validator error: {e}"]
            raised = []

        if not errored:
            # jsonschema.ValidationError.schema_path[0] names the TOP-LEVEL schema keyword that
            # actually rejected the instance (verified empirically: e.g. a prefixItems item-type
            # mismatch reports .validator == "type" but .schema_path == ['prefixItems', 0,
            # 'type'] -- the leaf keyword alone would misattribute this away from prefixItems).
            # Comparing that against each check's own `owned_keys` is how this module tells "the
            # flagged construct genuinely doesn't behave as described" (surprising) apart from
            # "this example also had to satisfy some OTHER constraint on the same field that
            # wasn't accounted for" (inconclusive) -- an adversarial review found several real
            # cases of the latter (uniqueItems, minItems, additionalProperties, a hidden allOf/
            # oneOf requirement, a co-occurring construct this module couldn't also synthesize
            # for) being misreported as the former, which falsely implicates a rule that's
            # actually working correctly.
            responsible = {e.schema_path[0] for e in raised if e.schema_path}
            if owned_keys and raised and not (responsible & owned_keys):
                inconclusive = True
                unrelated_keywords = sorted(responsible - owned_keys)
            elif actual_valid != expected_valid:
                surprising = True

        results.append({
            "kind": "positive" if expected_valid else "negative",
            "instance": instance,
            "expected_valid": expected_valid,
            "actual_valid": actual_valid,
            "errored": errored,
            "surprising": surprising,
            "inconclusive": inconclusive,
            "unrelated_keywords": unrelated_keywords,
            "errors": errors,
        })
    return {"attempted": True, "checks": results, "skipped_reason": None}


def summarize_conformance(gap_conformance) -> dict:
    """Aggregates a list of probe_gap() results (one per flagged gap) into overall counts for a
    CLI/HTML summary: how many gaps got an actual executed test, how many individual checks ran,
    how many behaved as expected, how many the validator itself couldn't complete (errored), how
    many couldn't confirm or refute the flagged rule because the example also had to satisfy some
    OTHER constraint on the same field (inconclusive) -- each kept separate from "passed as
    expected" so this summary never quietly counts an incomplete or inconclusive check as a
    confirmation -- and, worth the most scrutiny, how many were surprising (the validator
    disagreed with what the rule was built to demonstrate, for a reason attributable to the rule
    itself)."""
    executed = 0
    passed_as_expected = 0
    surprising = 0
    errored = 0
    inconclusive = 0
    gaps_with_tests = 0
    for result in gap_conformance:
        if not result or not result.get("attempted"):
            continue
        gaps_with_tests += 1
        for check in result.get("checks", []):
            executed += 1
            if check.get("errored"):
                errored += 1
            elif check.get("inconclusive"):
                inconclusive += 1
            elif check.get("surprising"):
                surprising += 1
            else:
                passed_as_expected += 1
    return {
        "executed": executed, "passed_as_expected": passed_as_expected, "surprising": surprising,
        "errored": errored, "inconclusive": inconclusive,
        "gaps_with_tests": gaps_with_tests, "gaps_total": len(gap_conformance),
    }
