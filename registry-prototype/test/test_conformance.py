"""Tests conformance-test mode: for the recognized "specific" advanced-construct shapes (contains
+ minContains/maxContains, prefixItems, propertyNames, patternProperties, unevaluatedProperties),
a concrete JSON value satisfying the rule and one violating it are built, then both are validated
with the real jsonschema.Draft202012Validator -- turning the gap panel's plain-English claim into
executed, evidence-backed proof. Covers the probe builders themselves, the "fails closed rather
than guesses" behavior when a full instance can't be confidently synthesized, the offline-safety
guard around $ref, a genuine "surprising" result (the validator disagreeing with what the rule was
built to demonstrate -- the single most valuable thing this feature can catch), and the markup/CLI
wiring that surfaces all of this.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contextlib
import io
import tempfile
from pathlib import Path

import wizard
from conformance import (
    _baseline_leaf_value,
    _baseline_object_value,
    _condition_values,
    _different_value,
    _pattern_sample,
    _wrong_type_value,
    probe_gap,
    summarize_conformance,
)
from render import get_preview_completeness, render_schema_form_preview

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def test_01_pattern_sample_recognized_shapes():
    exact = _pattern_sample("^[0-9]{6}$")
    check("01-exact-matching-length", len(exact[0]) == 6 and exact[0].isdigit(), exact)
    check("01-exact-violating-not-digits", not exact[1].isdigit(), exact)
    ranged = _pattern_sample("^[0-9]{3,6}$")
    check("01-ranged-matching-in-range", 3 <= len(ranged[0]) <= 6 and ranged[0].isdigit(), ranged)
    check("01-unrecognized-returns-none", _pattern_sample("^[A-Z]+$") is None)


def test_02_different_value_negates_by_type():
    check("02-string", _different_value("APPROVED") not in (None, "APPROVED"))
    check("02-bool", _different_value(True) is False)
    check("02-int", _different_value(5) not in (None, 5))
    check("02-avoid-list-respected", _different_value("A", avoid=["A", "A__NOT_MATCHING"]) == "A__ALT_VALUE")
    check("02-unsupported-type-none", _different_value(None) is None)
    check("02-unsupported-list-none", _different_value([1, 2]) is None)


def test_03_condition_values_matches_describe_simple_condition_shape():
    values = _condition_values({"properties": {"status": {"const": "APPROVED"}}})
    check("03-matching", values[0] == {"status": "APPROVED"}, values)
    check("03-violating-present-and-different", values[1] is not None and values[1]["status"] != "APPROVED", values)

    enum_values = _condition_values({"properties": {"status": {"enum": ["A", "B"]}}})
    check("03-enum-matching-first", enum_values[0] == {"status": "A"}, enum_values)
    check("03-enum-violating-not-in-enum", enum_values[1]["status"] not in ("A", "B"), enum_values)

    check("03-unrecognized-shape-none", _condition_values({"type": "string"}) is None)
    check("03-not-a-dict-none", _condition_values("nonsense") is None)


def test_04_wrong_type_value_covers_every_json_type():
    for t in ("string", "integer", "number", "boolean", "array", "object"):
        check(f"04-{t}-produces-a-value", _wrong_type_value(t) is not None)
    check("04-unknown-type-none", _wrong_type_value(None) is None)
    check("04-unknown-type-string-none", _wrong_type_value("null") is None)


def test_05_baseline_leaf_value_handles_common_shapes():
    check("05-const", _baseline_leaf_value({"const": "X"}) == "X")
    check("05-enum", _baseline_leaf_value({"enum": ["A", "B"]}) == "A")
    check("05-string-pattern-recognized", _baseline_leaf_value({"type": "string", "pattern": "^[0-9]{4}$"}) == "1111")
    check("05-string-pattern-unrecognized-none",
          _baseline_leaf_value({"type": "string", "pattern": "^[A-Z]+$"}) is None)
    check("05-string-plain", _baseline_leaf_value({"type": "string"}) == "sample-value")
    check("05-integer-with-minimum", _baseline_leaf_value({"type": "integer", "minimum": 5}) == 5)
    check("05-integer-clamped-to-maximum", _baseline_leaf_value({"type": "integer", "minimum": 5, "maximum": 2}) == 2)
    check("05-boolean", _baseline_leaf_value({"type": "boolean"}) is True)
    check("05-unrecognized-type-none", _baseline_leaf_value({"type": "array"}) is None)
    check("05-not-a-dict-none", _baseline_leaf_value("nonsense") is None)


def test_06_baseline_object_value_only_fills_required_and_fails_closed():
    check("06-empty-schema", _baseline_object_value({}) == {})
    check("06-bare-true-schema", _baseline_object_value(True) == {})
    only_required = _baseline_object_value({
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
    })
    check("06-only-required-field-filled", only_required == {"a": "sample-value"}, only_required)
    unsynthesizable = _baseline_object_value({
        "type": "object", "properties": {"weird": {"type": "array"}}, "required": ["weird"],
    })
    check("06-fails-closed-on-unsynthesizable-required-field", unsynthesizable is None)
    check("06-non-object-type-none", _baseline_object_value({"type": "string"}) is None)


# ---------------------------------------------------------------------------
# probe_gap -- the real, executed conformance checks
# ---------------------------------------------------------------------------

def test_07_probe_contains_min_contains_matches_documents_example():
    prop = {
        "type": "array",
        "items": {"type": "object", "properties": {"status": {"type": "string"}}},
        "contains": {"properties": {"status": {"const": "APPROVED"}}},
        "minContains": 1,
    }
    result = probe_gap(prop)
    check("07-attempted", result["attempted"] is True, result)
    check("07-two-checks", len(result["checks"]) == 2, result)
    positive, negative = result["checks"]
    check("07-positive-passes", positive["actual_valid"] is True and not positive["surprising"], positive)
    check("07-negative-fails-as-expected", negative["actual_valid"] is False and not negative["surprising"], negative)
    check("07-negative-has-real-error", negative["errors"], negative)


def test_08_probe_contains_with_max_contains_respects_cap():
    prop = {
        "type": "array",
        "contains": {"properties": {"status": {"const": "APPROVED"}}},
        "minContains": 2, "maxContains": 2,
    }
    result = probe_gap(prop)
    check("08-attempted", result["attempted"] is True, result)
    positive = result["checks"][0]
    check("08-positive-has-exactly-minContains-items", len(positive["instance"]) == 2, positive)
    check("08-positive-passes", positive["actual_valid"] is True, positive)


def test_08b_probe_contains_huge_min_contains_skips_instantly_instead_of_hanging():
    """Regression test for a real hang found while building this feature: a schema-authored
    minContains this large used to make the probe try to build (and jsonschema-validate) a
    multi-million-item array -- confirmed by hand to still be running after 8+ seconds. Must
    return instantly with an honest skip instead of ever attempting to build the instance."""
    import time
    prop = {"contains": {"properties": {"status": {"const": "APPROVED"}}}, "minContains": 5_000_000}
    start = time.time()
    result = probe_gap(prop)
    elapsed = time.time() - start
    check("08b-returns-instantly", elapsed < 1.0, elapsed)
    check("08b-not-attempted", result["attempted"] is False, result)
    check("08b-honest-reason", "too large" in result["skipped_reason"], result)


def test_08c_pattern_sample_huge_digit_count_skips_instantly_instead_of_hanging():
    """Regression test for a real DoS confirmed by adversarial review: an admin/LLM-authored
    "^[0-9]{N}$" pattern with an absurdly large N used to make _pattern_sample build a
    multi-hundred-MB string (~1GB RSS, several seconds) -- reproduced through the public
    render_schema_form_preview entrypoint, not just the internal helper directly. Must return
    instantly via a cap, mirroring the minContains fix above."""
    import time
    prop = {"type": "array", "prefixItems": [{"type": "string", "pattern": "^[0-9]{100000000}$"}]}
    start = time.time()
    result = probe_gap(prop)
    elapsed = time.time() - start
    check("08c-returns-instantly", elapsed < 1.0, elapsed)
    check("08c-not-attempted", result["attempted"] is False, result)

    start = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        render_schema_form_preview("t", {"type": "object", "properties": {"code": prop}},
                                    None, None, str(Path(tmp) / "s.html"))
    elapsed = time.time() - start
    check("08c-render-returns-instantly", elapsed < 2.0, elapsed)


def test_09_probe_contains_unrecognized_condition_skips_honestly():
    prop = {"contains": {"type": "string", "pattern": "^[A-Z]+$"}}
    result = probe_gap(prop)
    check("09-not-attempted", result["attempted"] is False, result)
    check("09-has-honest-reason", "recognized simple pattern" in result["skipped_reason"], result)


def test_10_probe_prefix_items():
    prop = {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]}
    result = probe_gap(prop)
    check("10-attempted", result["attempted"] is True, result)
    positive, negative = result["checks"]
    check("10-positive-two-items", len(positive["instance"]) == 2, positive)
    check("10-positive-passes", positive["actual_valid"] is True, positive)
    check("10-negative-fails", negative["actual_valid"] is False, negative)
    check("10-negative-preserves-length", len(negative["instance"]) == 2, negative)


def test_11_probe_property_names():
    prop = {"type": "object", "propertyNames": {"pattern": "^[0-9]{3}$"}}
    result = probe_gap(prop)
    check("11-attempted", result["attempted"] is True, result)
    positive, negative = result["checks"]
    check("11-positive-name-is-digits", list(positive["instance"].keys())[0].isdigit(), positive)
    check("11-positive-passes", positive["actual_valid"] is True, positive)
    check("11-negative-fails", negative["actual_valid"] is False, negative)


def test_12_probe_property_names_unrecognized_pattern_skips():
    prop = {"type": "object", "propertyNames": {"pattern": "^x-[a-z]+$"}}
    result = probe_gap(prop)
    check("12-not-attempted", result["attempted"] is False, result)


def test_13_probe_pattern_properties_tests_the_value_schema_not_the_name():
    prop = {"type": "object", "patternProperties": {"^[0-9]{3}$": {"type": "string"}}}
    result = probe_gap(prop)
    check("13-attempted", result["attempted"] is True, result)
    positive, negative = result["checks"]
    positive_name = list(positive["instance"].keys())[0]
    negative_name = list(negative["instance"].keys())[0]
    check("13-same-name-used-both-sides", positive_name == negative_name, (positive, negative))
    check("13-positive-passes", positive["actual_valid"] is True, positive)
    check("13-negative-fails-on-value-type", negative["actual_valid"] is False, negative)


def test_14_probe_unevaluated_properties_false():
    prop = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
            "unevaluatedProperties": False}
    result = probe_gap(prop)
    check("14-attempted", result["attempted"] is True, result)
    positive, negative = result["checks"]
    check("14-positive-passes", positive["actual_valid"] is True, positive)
    check("14-negative-fails", negative["actual_valid"] is False, negative)


def test_15_probe_unevaluated_properties_true_skips_as_no_effect():
    result = probe_gap({"unevaluatedProperties": True})
    check("15-not-attempted", result["attempted"] is False, result)
    check("15-reason-mentions-no-effect", "no restrictive effect" in result["skipped_reason"], result)


def test_16_probe_unevaluated_properties_as_schema_skips():
    result = probe_gap({"unevaluatedProperties": {"type": "string"}})
    check("16-not-attempted", result["attempted"] is False, result)


def test_17_probe_skips_honestly_when_baseline_cannot_be_synthesized():
    """Regression test for a real bug found while building this feature: propertyNames/
    patternProperties used to silently fall back to an empty base object when a REQUIRED sibling
    field couldn't be synthesized, producing a false 'surprising' result for an unrelated reason
    (a missing required field, not the rule actually being probed). Must fail closed instead."""
    prop = {
        "type": "object",
        "properties": {"weird": {"type": "array"}},
        "required": ["weird"],
        "propertyNames": {"pattern": "^[0-9]{3}$"},
    }
    result = probe_gap(prop)
    check("17-not-attempted", result["attempted"] is False, result)
    check("17-honest-reason", "could not synthesize" in result["skipped_reason"], result)


def test_18_probe_finds_a_genuine_surprising_result():
    """A real interaction, not a fabricated one: additionalProperties: true alongside
    unevaluatedProperties: false means every extra property is already 'evaluated' by
    additionalProperties, so unevaluatedProperties: false has NO actual effect -- the plain-
    English explanation ('no extra fields allowed') would be describing a rule that doesn't
    actually do anything here. This is exactly the kind of gap this feature exists to catch."""
    prop = {
        "type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
        "additionalProperties": True, "unevaluatedProperties": False,
    }
    result = probe_gap(prop)
    check("18-attempted", result["attempted"] is True, result)
    negative = result["checks"][1]
    check("18-surprising-flagged", negative["surprising"] is True, negative)
    check("18-actually-passed-unexpectedly", negative["actual_valid"] is True, negative)


def test_18b_probe_validator_exception_is_never_shown_as_a_confident_result():
    """Regression test for a real bug found by adversarial review: when the real validator itself
    raises (e.g. a malformed regex elsewhere in the same schema fragment) instead of returning a
    real pass/fail, actual_valid=None used to be folded into a confident 'FAIL, as expected' line
    (None is falsy). Must be reported as its own explicit 'errored' outcome instead."""
    prop = {"type": "object", "properties": {}, "propertyNames": {"pattern": "[unbalanced("},
            "patternProperties": {"^[0-9]{3}$": {"type": "string"}}}
    result = probe_gap(prop)
    check("18b-attempted", result["attempted"] is True, result)
    for check_ in result["checks"]:
        check("18b-errored-flagged", check_["errored"] is True, check_)
        check("18b-not-surprising", check_["surprising"] is False, check_)
        check("18b-actual-valid-none", check_["actual_valid"] is None, check_)
    summary = summarize_conformance([result])
    check("18b-summary-errored-count", summary["errored"] == len(result["checks"]), summary)
    check("18b-not-counted-as-passed", summary["passed_as_expected"] == 0, summary)


# ---------------------------------------------------------------------------
# Sibling-constraint awareness -- regression tests for a second adversarial review round that
# found several probes produced a false "surprising" (or falsely "passed as expected") verdict
# because the generated instance didn't account for OTHER constraints on the same field
# (uniqueItems, minItems, minLength/maxLength, additionalProperties, a hidden allOf/oneOf
# requirement, a co-occurring construct this module couldn't also synthesize for). Fixed two ways:
# targeted generator improvements where cheap (uniqueItems/minItems/minLength awareness), and a
# general safety net using jsonschema.ValidationError.schema_path to classify any remaining
# mismatch as "inconclusive" (not "surprising") whenever it's attributable to a keyword outside
# the construct actually being probed.
# ---------------------------------------------------------------------------

def test_18c_probe_contains_respects_unique_items():
    prop = {
        "type": "array", "uniqueItems": True,
        "items": {"type": "object", "properties": {"status": {"type": "string"}}},
        "contains": {"properties": {"status": {"const": "active"}}}, "minContains": 2,
    }
    result = probe_gap(prop)
    positive = result["checks"][0]
    check("18c-positive-passes", positive["actual_valid"] is True, positive)
    check("18c-not-surprising", positive["surprising"] is False, positive)
    check("18c-items-are-distinct", len({str(v) for v in positive["instance"]}) == len(positive["instance"]),
          positive)


def test_18d_probe_contains_respects_min_items():
    prop = {
        "type": "array", "minItems": 3,
        "items": {"type": "object", "properties": {"status": {"type": "string"}}},
        "contains": {"properties": {"status": {"const": "active"}}}, "minContains": 1,
    }
    result = probe_gap(prop)
    positive = result["checks"][0]
    check("18d-positive-passes", positive["actual_valid"] is True, positive)
    check("18d-instance-meets-min-items", len(positive["instance"]) >= 3, positive)


def test_18e_probe_prefix_items_respects_min_items():
    prop = {"type": "array", "minItems": 5, "prefixItems": [{"type": "string"}, {"type": "integer"}]}
    result = probe_gap(prop)
    positive = result["checks"][0]
    check("18e-positive-passes", positive["actual_valid"] is True, positive)
    check("18e-instance-meets-min-items", len(positive["instance"]) == 5, positive)


def test_18f_baseline_leaf_value_respects_min_length():
    prop = {"type": "object", "properties": {"code": {"type": "string", "minLength": 20}},
            "required": ["code"], "unevaluatedProperties": False}
    result = probe_gap(prop)
    positive = result["checks"][0]
    check("18f-positive-passes", positive["actual_valid"] is True, positive)
    check("18f-value-meets-min-length", len(positive["instance"]["code"]) >= 20, positive)


def test_18g_baseline_leaf_value_returns_none_for_contradictory_length_bounds():
    check("18g-contradictory-bounds-none",
          _baseline_leaf_value({"type": "string", "minLength": 20, "maxLength": 5}) is None)


def test_18h_probe_property_names_skips_when_existing_required_name_violates_own_pattern():
    prop = {"type": "object", "properties": {"abc": {"type": "string"}}, "required": ["abc"],
            "propertyNames": {"pattern": "^[0-9]{3}$"}}
    result = probe_gap(prop)
    check("18h-not-attempted", result["attempted"] is False, result)
    check("18h-reason-names-the-field", "abc" in result["skipped_reason"], result)


def test_18i_probe_property_names_skips_on_synthesized_name_collision():
    prop = {"type": "object", "properties": {"111": {"type": "string", "enum": ["ABC"]}},
            "propertyNames": {"pattern": "^[0-9]{3}$"}}
    result = probe_gap(prop)
    check("18i-not-attempted", result["attempted"] is False, result)
    check("18i-reason-mentions-collision", "collides" in result["skipped_reason"], result)


def test_18j_probe_inconclusive_when_additional_properties_blocks_an_otherwise_valid_name():
    """The general safety net: additionalProperties:false is a sibling keyword the propertyNames
    probe never accounted for, so a positive check colliding with it must be reported as
    inconclusive, not a false alarm against propertyNames itself (which is working correctly)."""
    prop = {"type": "object", "properties": {"123": {"type": "string"}}, "required": ["123"],
            "additionalProperties": False, "propertyNames": {"pattern": "^[0-9]{3}$"}}
    result = probe_gap(prop)
    check("18j-attempted", result["attempted"] is True, result)
    positive = result["checks"][0]
    check("18j-inconclusive-not-surprising", positive["inconclusive"] is True, positive)
    check("18j-not-surprising", positive["surprising"] is False, positive)
    check("18j-unrelated-keyword-named", positive["unrelated_keywords"] == ["additionalProperties"], positive)


def test_18k_probe_inconclusive_when_co_occurring_construct_cannot_be_synthesized():
    """The general safety net's other key case: prefixItems co-occurs with contains on the same
    array, prefixItems can't synthesize a full example (a hidden required field), and validating
    contains' own otherwise-correct positive instance against the FULL schema (which also
    includes prefixItems) must not blame contains for prefixItems' own unrelated requirement."""
    prop = {
        "type": "array",
        "items": {"type": "object", "properties": {"status": {"type": "string"}}},
        "contains": {"properties": {"status": {"const": "APPROVED"}}}, "minContains": 1,
        "prefixItems": [{"type": "object", "required": ["tags"], "properties": {"tags": {"type": "array"}}}],
    }
    result = probe_gap(prop)
    positive = result["checks"][0]
    check("18k-inconclusive-not-surprising", positive["inconclusive"] is True, positive)
    check("18k-not-surprising", positive["surprising"] is False, positive)
    check("18k-unrelated-keyword-is-prefixitems", positive["unrelated_keywords"] == ["prefixItems"], positive)


def test_18l_probe_inconclusive_when_hidden_allof_requirement_blocks_baseline():
    prop = {"type": "object", "allOf": [{"properties": {"a": {"const": "x"}}, "required": ["a"]}],
            "unevaluatedProperties": False}
    result = probe_gap(prop)
    check("18l-attempted", result["attempted"] is True, result)
    positive = result["checks"][0]
    check("18l-inconclusive-not-surprising", positive["inconclusive"] is True, positive)
    check("18l-unrelated-keyword-is-allof", positive["unrelated_keywords"] == ["allOf"], positive)


def test_18m_summarize_conformance_tracks_inconclusive_separately_from_passed():
    result = probe_gap({"type": "array", "minItems": 3,
                         "items": {"type": "object", "properties": {"status": {"type": "string"}}},
                         "contains": {"properties": {"status": {"const": "X"}}}, "minContains": 1})
    # (this specific case is now fixed and passes -- construct a genuinely inconclusive one instead)
    inconclusive_result = probe_gap({"type": "object",
                                      "allOf": [{"properties": {"a": {"const": "x"}}, "required": ["a"]}],
                                      "unevaluatedProperties": False})
    summary = summarize_conformance([inconclusive_result])
    check("18m-inconclusive-counted", summary["inconclusive"] >= 1, summary)
    check("18m-not-counted-as-passed", summary["passed_as_expected"] < summary["executed"], summary)


def test_18n_pattern_properties_tries_other_entries_when_first_is_unrecognized():
    """Regression test for a real test-coverage gap: the original next(iter(pp.items())) gave up
    entirely if the FIRST patternProperties entry's pattern wasn't a recognized shape, even when a
    LATER entry was perfectly testable. Dict key order is guaranteed by Python 3.7+, so this is a
    deterministic, real behavior difference, not a coincidence of iteration order."""
    prop = {"type": "object", "patternProperties": {
        "^[A-Z]{2}$": {"type": "string"},          # unrecognized shape -- not a digit-count pattern
        "^[0-9]{3}$": {"type": "string"},          # recognized -- this one should be used instead
    }}
    result = probe_gap(prop)
    check("18n-attempted", result["attempted"] is True, result)
    check("18n-positive-passes", result["checks"][0]["actual_valid"] is True, result)


def test_19_probe_no_applicable_keywords_not_attempted():
    result = probe_gap({"type": "string", "pattern": "^[0-9]{6}$"})
    check("19-not-attempted", result["attempted"] is False, result)
    check("19-no-reason-needed", result["skipped_reason"] is None, result)


def test_20_probe_non_dict_prop_not_attempted():
    for weird in (True, False, None, "x", 42):
        result = probe_gap(weird)
        check(f"20-not-attempted-{weird}", result["attempted"] is False, result)


# ---------------------------------------------------------------------------
# Offline-safety guard: never risk a network fetch during validation
# ---------------------------------------------------------------------------

def test_21_probe_skips_when_ref_present_anywhere_in_the_fragment():
    prop = {
        "type": "array",
        "contains": {"properties": {"status": {"const": "APPROVED"}}},
        "minContains": 1,
        "items": {"$ref": "https://example.com/item.json"},
    }
    result = probe_gap(prop)
    check("21-not-attempted", result["attempted"] is False, result)
    check("21-reason-mentions-ref", "$ref" in result["skipped_reason"], result)
    check("21-reason-mentions-network", "network" in result["skipped_reason"], result)


def test_22_probe_skips_for_internal_ref_too_not_just_external():
    """Deliberately conservative: even an internal (in-document) $ref is skipped, since resolving
    it is out of scope here -- safety over coverage (see conformance.py's _contains_ref
    docstring)."""
    prop = {"type": "array", "contains": {"$ref": "#/$defs/Approved"}, "minContains": 1}
    result = probe_gap(prop)
    check("22-not-attempted", result["attempted"] is False, result)


def test_22b_probe_skips_for_dynamic_ref_and_recursive_ref_too():
    """Regression test for a real, confirmed network-safety bug: an adversarial review found
    jsonschema.Draft202012Validator genuinely attempts a DNS resolution for $dynamicRef even
    though the original guard only checked for the literal "$ref" key -- reproduced with
    socket.getaddrinfo intercepted, 4 real outbound resolution attempts observed. $recursiveRef is
    the equivalent Draft 2019-09 keyword; both must be caught the same way $ref is."""
    for ref_key in ("$dynamicRef", "$recursiveRef"):
        prop = {"type": "object", "properties": {"x": {"type": "string"}},
                "patternProperties": {"^[0-9]{3}$": {"type": "string"}}, ref_key: "https://example.com/x#dyn"}
        result = probe_gap(prop)
        check(f"22b-not-attempted-{ref_key}", result["attempted"] is False, result)


def test_22c_probe_never_attempts_a_real_network_call():
    """End-to-end confirmation of the offline-safety guarantee, not just that probe_gap reports
    'not attempted' -- intercepts socket.getaddrinfo so a regression that reintroduces a resolvable
    $ref-family keyword would fail loudly here rather than only being caught by code inspection."""
    import socket
    calls = []
    original = socket.getaddrinfo

    def spy(host, *args, **kwargs):
        calls.append(host)
        raise OSError("blocked for test")

    socket.getaddrinfo = spy
    try:
        definition = {"type": "object", "properties": {"approvals": {
            "type": "object", "properties": {"x": {"type": "string"}},
            "patternProperties": {"^[0-9]{3}$": {"type": "string"}},
            "$dynamicRef": "https://attacker.example.invalid/leak#dyn",
        }}}
        with tempfile.TemporaryDirectory() as tmp:
            render_schema_form_preview("t", definition, None, None, str(Path(tmp) / "s.html"))
    finally:
        socket.getaddrinfo = original
    check("22c-no-network-calls-attempted", calls == [], calls)


# ---------------------------------------------------------------------------
# summarize_conformance
# ---------------------------------------------------------------------------

def test_23_summarize_conformance_aggregates_correctly():
    results = [probe_gap({
        "type": "array", "items": {"type": "object", "properties": {"status": {"type": "string"}}},
        "contains": {"properties": {"status": {"const": "APPROVED"}}}, "minContains": 1,
    }), probe_gap({"contains": {"type": "string", "pattern": "^[A-Z]+$"}})]  # not attempted
    summary = summarize_conformance(results)
    check("23-executed-count", summary["executed"] == 2, summary)
    check("23-passed-as-expected", summary["passed_as_expected"] == 2, summary)
    check("23-no-surprises", summary["surprising"] == 0, summary)
    check("23-gaps-with-tests", summary["gaps_with_tests"] == 1, summary)
    check("23-gaps-total", summary["gaps_total"] == 2, summary)


def test_24_summarize_conformance_empty_list():
    summary = summarize_conformance([])
    check("24-all-zero", summary == {"executed": 0, "passed_as_expected": 0, "surprising": 0,
                                      "errored": 0, "inconclusive": 0,
                                      "gaps_with_tests": 0, "gaps_total": 0}, summary)


def test_25_summarize_conformance_tolerates_none_entries():
    """gap_conformance entries come from gap dicts built across several different code paths;
    tolerating a stray None keeps this robust rather than assuming every caller is perfectly
    consistent."""
    summary = summarize_conformance([None, probe_gap({"unevaluatedProperties": True})])
    check("25-does-not-crash", summary["executed"] == 0, summary)


# ---------------------------------------------------------------------------
# Wired into get_preview_completeness / render_schema_form_preview / CLI output
# ---------------------------------------------------------------------------

_DOCUMENTS_DEFINITION = {
    "type": "object",
    "properties": {
        "documents": {
            "type": "array",
            "items": {"type": "object", "properties": {"status": {"type": "string"}}},
            "contains": {"properties": {"status": {"const": "APPROVED"}}},
            "minContains": 1,
        },
    },
}


def test_26_completeness_includes_conformance_summary():
    completeness = get_preview_completeness(_DOCUMENTS_DEFINITION)
    cs = completeness["conformance_summary"]
    check("26-executed", cs["executed"] == 2, cs)
    check("26-gaps-with-tests", cs["gaps_with_tests"] == 1, cs)
    check("26-no-surprises", cs["surprising"] == 0, cs)


def test_27_completeness_conformance_summary_present_even_with_no_gaps():
    completeness = get_preview_completeness({"type": "object", "properties": {"a": {"type": "string"}}})
    check("27-summary-present", completeness["conformance_summary"] == {
        "executed": 0, "passed_as_expected": 0, "surprising": 0, "errored": 0, "inconclusive": 0,
        "gaps_with_tests": 0, "gaps_total": 0,
    }, completeness)


def test_28_completeness_non_dict_definition_still_has_conformance_summary():
    completeness = get_preview_completeness(True)
    check("28-summary-present", "conformance_summary" in completeness, completeness)
    check("28-zero-executed", completeness["conformance_summary"]["executed"] == 0, completeness)


def _render(definition, out_path):
    render_schema_form_preview("conformance-render-test", definition, None, None, str(out_path))
    return out_path.read_text()


def test_29_gap_panel_renders_conformance_section_with_real_results():
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(_DOCUMENTS_DEFINITION, Path(tmp) / "s.html")
    check("29-conformance-label", "Conformance check" in html, html)
    check("29-pass-line-present", 'class="conformance-pass"' in html, html)
    check("29-fail-line-present", 'class="conformance-fail"' in html, html)
    check("29-validated-note", "same JSON Schema Draft 2020-12 validator" in html, html)
    check("29-as-expected-wording", html.count("as expected") >= 2, html)


def test_30_gap_panel_shows_skip_reason_when_not_attempted():
    definition = {"type": "object", "properties": {
        "weird": {"contains": {"type": "string", "pattern": "^[A-Z]+$"}},
    }}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("30-skip-class-present", 'class="conformance-skip"' in html, html)
    check("30-honest-message", "Could not auto-generate a conformance test" in html, html)


def test_31_gap_panel_shows_surprising_result_distinctly():
    definition = {"type": "object", "properties": {
        "extra": {
            "type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
            "additionalProperties": True, "unevaluatedProperties": False,
        },
    }}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("31-surprising-class-present", 'class="conformance-surprising"' in html, html)
    check("31-warns-may-not-behave", "may not behave exactly as described" in html, html)


def test_31b_gap_panel_shows_inconclusive_result_distinctly_not_as_surprising():
    definition = {"type": "object", "properties": {
        "extra": {"type": "object", "allOf": [{"properties": {"a": {"const": "x"}}, "required": ["a"]}],
                  "unevaluatedProperties": False},
    }}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("31b-no-surprising-class", 'class="conformance-surprising"' not in html, html)
    check("31b-mentions-other-rules", "could not be confirmed here" in html, html)
    check("31b-names-the-unrelated-keyword", "allOf" in html, html)


def test_32_completeness_summary_shows_conformance_line_when_executed():
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(_DOCUMENTS_DEFINITION, Path(tmp) / "s.html")
    check("32-conformance-summary-line", "Conformance checks: 2 executed across 1 rule(s)" in html, html)


def test_33_completeness_summary_omits_conformance_line_when_none_executed():
    definition = {"type": "object", "properties": {"a": {"type": "string"}}}
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(definition, Path(tmp) / "s.html")
    check("33-no-conformance-line", "Conformance checks:" not in html, html)


def test_34_conformance_probe_values_never_leak_offline_safety_violations():
    """The probe-generated JSON values (and their error messages) get embedded in HTML via
    _render_conformance_section -- confirms none of that introduces a network reference, matching
    every other render.py offline-safety test in this suite."""
    with tempfile.TemporaryDirectory() as tmp:
        html = _render(_DOCUMENTS_DEFINITION, Path(tmp) / "s.html")
    for pattern in ("http://", "https://", "@import", "fetch(", "XMLHttpRequest"):
        check(f"34-no-{pattern.strip('(:/@')}", pattern not in html, html)


def test_35_cli_print_preview_completeness_includes_conformance_line():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wizard.print_preview_completeness(_DOCUMENTS_DEFINITION)
    output = buf.getvalue()
    check("35-conformance-line-present",
          "Conformance checks: 2 executed across 1 rule(s), 2 behaved as expected." in output, output)


def test_36_cli_print_preview_completeness_omits_line_when_nothing_executed():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        wizard.print_preview_completeness({"type": "object", "properties": {"a": {"type": "string"}}})
    output = buf.getvalue()
    check("36-no-conformance-line", "Conformance checks:" not in output, output)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll conformance-test-mode checks passed -- gap-panel claims are backed by real, "
          "executed jsonschema.Draft202012Validator evidence, never fabricated.")
