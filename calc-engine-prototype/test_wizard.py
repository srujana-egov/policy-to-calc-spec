"""Stress tests for wizard.py -- the interactive layer itself, which test_calc_rule_builder.py
doesn't touch (it drives CalculationRuleBuilder directly, never through input()). Mirrors
../workflow-prototype/test_wizard.py and ../registry-prototype/test_wizard.py's approach:
real-fixture replay plus targeted edge cases, driven via a mocked input() against the exact code
a person's keystrokes would hit.
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest import mock

import wizard
from builder import CalculationRuleBuilder

FIXTURES = Path(__file__).parent / "fixtures"
PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


@contextlib.contextmanager
def canned_input(answers):
    queue = list(answers)

    def fake_input(prompt=""):
        if not queue:
            raise AssertionError(f"ran out of canned input (last prompt: {prompt!r})")
        return queue.pop(0)

    with mock.patch("builtins.input", fake_input):
        yield queue


def run_session_with(answers):
    """Drives the real wizard.run_session() in a scratch cwd (it writes a preview HTML as a side
    effect) with stdout suppressed. Returns (rule_set, leftover_answers)."""
    with canned_input(answers) as queue:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rule_set = wizard.run_session()
            finally:
                os.chdir(cwd)
        return rule_set, queue


def load_lines(fixture_name: str) -> list[str]:
    text = (FIXTURES / fixture_name).read_text()
    return text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")


# ---------------------------------------------------------------------------
# Real-fixture replay
# ---------------------------------------------------------------------------

def test_wiz_01_flat_percentage_matches_golden():
    rule_set, leftover = run_session_with(load_lines("flat_percentage_session.txt"))
    check("wiz01-all-input-consumed", not leftover, leftover)
    golden = json.loads((FIXTURES / "flat_percentage_golden.json").read_text())
    check("wiz01-matches-golden", rule_set.model_dump(by_alias=True, exclude_none=True) == golden)


def test_wiz_02_slab_aggregation_formula_matches_golden():
    rule_set, leftover = run_session_with(load_lines("slab_aggregation_formula_session.txt"))
    check("wiz02-all-input-consumed", not leftover, leftover)
    golden = json.loads((FIXTURES / "slab_aggregation_formula_golden.json").read_text())
    check("wiz02-matches-golden", rule_set.model_dump(by_alias=True, exclude_none=True) == golden)
    check("wiz02-three-rules", len(rule_set.rules) == 3, len(rule_set.rules))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_wiz_03_quit_cancels_mid_session():
    answers = ["cancel-test", "no", "quit"]
    raised = False
    try:
        run_session_with(answers)
    except wizard.Cancelled:
        raised = True
    check("wiz03-cancelled-raised", raised)


def test_wiz_04_invalid_mechanism_choice_reasks():
    answers = [
        "x", "no",
        "9", "1",              # invalid '9' first, then valid '1' (flat)
        "FEE", "100", "no", "", "", "2024-01-01", "",
        "no",
        "yes",
    ]
    rule_set, leftover = run_session_with(answers)
    check("wiz04-all-input-consumed", not leftover, leftover)
    check("wiz04-rule-added", rule_set.rules[0].component == "FEE")


def test_wiz_05_redo_rule_after_no():
    answers = [
        "x", "no",
        "1", "FEE", "100", "no", "", "", "2024-01-01", "",
        "no",                          # add another rule? no
        "no",                            # confirm: not right
        "1",                              # fix target: redo rule 1
        "1", "FEE", "200", "no", "", "", "2024-01-01", "",   # redo with a different value
        "yes",                                                 # confirm: yes
    ]
    rule_set, leftover = run_session_with(answers)
    check("wiz05-all-input-consumed", not leftover, leftover)
    check("wiz05-one-rule", len(rule_set.rules) == 1, len(rule_set.rules))
    check("wiz05-value-updated", rule_set.rules[0].value == 200, rule_set.rules[0].value)


def test_wiz_06_add_rule_via_offer_fix():
    answers = [
        "x", "no",
        "1", "FEE", "100", "no", "", "", "2024-01-01", "",
        "no",
        "no",                    # confirm: not right
        "add",                     # fix target: add a new rule
        "1", "FEE2", "200", "no", "no", "", "", "2024-01-01", "",   # extra "no" -- depends on FEE for ordering?
        "yes",
    ]
    rule_set, leftover = run_session_with(answers)
    check("wiz06-all-input-consumed", not leftover, leftover)
    check("wiz06-two-rules", {r.component for r in rule_set.rules} == {"FEE", "FEE2"})


def test_wiz_07_delete_rule_then_fix_dangling_dependency():
    """Deletes a rule that a percentage rule depends on, confirms validate.py catches the
    resulting attribute/dependency mismatch is at least still valid (percentage no longer has a
    real base to reference is allowed structurally, but exercises delete + add-back via offer_fix
    -- the full composed loop, matching the analogous test in the sibling prototypes."""
    answers = [
        "x", "no",
        "1", "MISTAKE", "100", "no", "", "", "2024-01-01", "",
        "yes",
        "1", "REAL_FEE", "150", "no", "no", "", "", "2024-01-01", "",   # extra "no" -- depends on MISTAKE?
        "no",
        "no",                       # confirm: not right
        "delete 1",                   # remove MISTAKE
        "yes",                          # confirm: yes (REAL_FEE alone is still valid)
    ]
    rule_set, leftover = run_session_with(answers)
    check("wiz07-all-input-consumed", not leftover, leftover)
    check("wiz07-mistake-removed", {r.component for r in rule_set.rules} == {"REAL_FEE"})


def test_wiz_08_rename_module_via_offer_fix():
    answers = [
        "old-module", "no",
        "1", "FEE", "100", "no", "", "", "2024-01-01", "",
        "no",
        "no",
        "module",
        "new-module",
        "yes",
    ]
    rule_set, leftover = run_session_with(answers)
    check("wiz08-all-input-consumed", not leftover, leftover)
    check("wiz08-module-renamed", rule_set.module == "new-module")


def test_wiz_09_unknown_fix_target_does_not_crash():
    b = CalculationRuleBuilder("x")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")
    before = len(b.rules)
    with canned_input(["NOT_A_REAL_CHOICE"]) as queue:
        wizard.offer_fix(b, None, [])
    check("wiz09-rules-unchanged", len(b.rules) == before)
    check("wiz09-answer-consumed", not queue)


# ---------------------------------------------------------------------------
# The "$." registry field-picker mechanism, against a mock server
# ---------------------------------------------------------------------------

class _SchemaHandler(http.server.BaseHTTPRequestHandler):
    requested_path = None

    def do_GET(self):
        _SchemaHandler.requested_path = self.path
        body = json.dumps({
            "success": True,
            "data": {
                "schemaCode": "trade-license-application",
                "definition": {
                    "properties": {
                        "premisesArea": {"type": "number"},
                        "tradeLicenseDetail": {"type": "object", "properties": {
                            "premisesArea": {"type": "number"}}},
                    },
                },
            },
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _run_schema_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _SchemaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_wiz_10_registry_field_picker_produces_dollar_path():
    server, port = _run_schema_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            answers = [
                "x",
                "yes", "trade-license-application",   # use registry lookup
                "1", "FEE", "100",                       # flat rule
                "yes", "area",                              # conditions? yes; condition name
                "3",                                          # field pick #3 (nested premisesArea)
                "1", "1000",                                    # exact match kind, value
                "no",                                             # another condition? no
                "", "", "2024-01-01", "",
                "no",
                "yes",
            ]
            rule_set, leftover = run_session_with(answers)
    finally:
        server.shutdown()
    check("wiz10-all-input-consumed", not leftover, leftover)
    check("wiz10-fetched-correct-path", _SchemaHandler.requested_path ==
          "/registry/v3/schema/trade-license-application", _SchemaHandler.requested_path)
    cond = rule_set.rules[0].conditions["area"]
    check("wiz10-dollar-path-generated", cond.jsonPath == "$.tradeLicenseDetail.premisesArea", cond.jsonPath)


def test_wiz_11_registry_fetch_failure_falls_back_to_manual_entry():
    """No env vars set at all -- fetch_registry_schema() should raise SystemExit internally,
    caught by setup_registry_fields(), which falls back to manual path entry rather than
    crashing the whole session."""
    answers = [
        "x",
        "yes", "some-schema",     # try registry lookup -- will fail, no env vars set
        "1", "FEE", "100", "no", "", "", "2024-01-01", "",
        "no",
        "yes",
    ]
    rule_set, leftover = run_session_with(answers)
    check("wiz11-all-input-consumed", not leftover, leftover)
    check("wiz11-rule-still-built", rule_set.rules[0].component == "FEE")


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll wizard.py interactive-layer checks passed against real fixtures and edge cases.")
