"""Regression test for the real write path (POST /calculation/v3/{module}/rules, one rule per
call) and the registry-schema fetch (GET /registry/v3/schema/:schemaCode) against a throwaway
local HTTP server -- mirrors ../workflow-prototype/test_write_path.py and
../registry-prototype/test_write_path.py's reasoning: every other test here only drives the
dry-run branch, which can't catch a URL/header/body-shape mistake since it never sends a real
request.

Verified against the real spec (see fixtures/real_world/calculation-engine-3.0.0.yaml, confirmed
from the platform team -- README.md's "Spec found and verified" section has the full account):
the path prefix is `/calculation/v3` (from the spec's own `servers:` block, the same convention as
registry's `/registry/v3` and workflow's `/workflow/v3`), and POST's requestBody schema is a
*single* CalculationRule object, not an array -- an earlier version of this prototype sent one
request with the whole rule set as a bulk array, never checked against a real spec until now. The
registry-schema GET, by contrast, was already the same real, verified route proven in
../registry-prototype/test_write_path.py.
"""

from __future__ import annotations


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import http.server
import json
import os
import threading
from unittest import mock

import wizard
from builder import CalculationRuleBuilder
from registry_lookup import fetch_registry_schema

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


class _CapturingHandler(http.server.BaseHTTPRequestHandler):
    received = []
    response_body = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingHandler.received.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body) if body else None,
        })
        payload = json.dumps(_CapturingHandler.response_body).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        _CapturingHandler.received.append({"path": self.path, "headers": dict(self.headers)})
        payload = json.dumps(_CapturingHandler.response_body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


def _run_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


_ENV = {"DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u", "DIGIT_JWT_TOKEN": "jwt-abc"}


def test_write_01_rules_write_uses_calculation_v3_prefixed_module_scoped_path():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"id": "some-uuid", "version": 1}
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")
    rule_set = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {"DIGIT_SERVER_URL": f"http://127.0.0.1:{port}", **_ENV}):
            wizard.write_rules(rule_set)
    finally:
        server.shutdown()

    check("write01-one-request", len(_CapturingHandler.received) == 1, _CapturingHandler.received)
    req = _CapturingHandler.received[0]
    check("write01-path", req["path"] == "/calculation/v3/trade-license/rules", req["path"])
    check("write01-tenant-header", req["headers"].get("X-Tenant-Id") == "t", req["headers"])
    check("write01-user-header", req["headers"].get("X-User-Id") == "u", req["headers"])
    check("write01-bearer-header", req["headers"].get("Authorization") == "Bearer jwt-abc", req["headers"])
    check("write01-no-client-id-header", "X-Client-Id" not in req["headers"])


def test_write_02_one_post_per_rule_body_is_a_single_object_no_array():
    """Confirmed against the real spec: POST /{module}/rules' requestBody schema is a single
    CalculationRule object, not an array -- so a 2-rule set fires 2 separate requests, each body
    a plain object matching one rule, not a bulk array."""
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"id": "some-uuid", "version": 1}
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")
    b.add_percentage_rule("CESS", 5, "FEE", effectiveFrom="2024-01-01")
    rule_set = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {"DIGIT_SERVER_URL": f"http://127.0.0.1:{port}", **_ENV}):
            wizard.write_rules(rule_set)
    finally:
        server.shutdown()

    check("write02-two-separate-requests", len(_CapturingHandler.received) == 2,
          _CapturingHandler.received)
    first_body, second_body = (r["body"] for r in _CapturingHandler.received)
    check("write02-first-body-is-a-plain-object-not-a-list", isinstance(first_body, dict), first_body)
    check("write02-first-rule-component", first_body["component"] == "FEE", first_body)
    check("write02-second-rule-has-appliesOn", second_body["appliesOn"]["componentRef"] == "FEE",
          second_body)
    check("write02-no-module-field-on-rules",
          "module" not in first_body and "module" not in second_body, (first_body, second_body))
    check("write02-both-requests-same-path",
          all(r["path"] == "/calculation/v3/trade-license/rules" for r in _CapturingHandler.received),
          _CapturingHandler.received)


def test_write_03_registry_schema_fetch_uses_real_verified_route():
    """Same route already verified directly against real Go source in
    ../registry-prototype/README.md -- GET /registry/v3/schema/:schemaCode."""
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {
        "success": True,
        "data": {"schemaCode": "trade-license-application",
                  "definition": {"properties": {"premisesArea": {"type": "number"}}}},
    }

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            schema_data = fetch_registry_schema("trade-license-application")
    finally:
        server.shutdown()

    req = _CapturingHandler.received[0]
    check("write03-path", req["path"] == "/registry/v3/schema/trade-license-application", req["path"])
    check("write03-tenant-header", req["headers"].get("X-Tenant-Id") == "t")
    check("write03-user-header", req["headers"].get("X-User-Id") == "u")
    check("write03-schema-parsed", schema_data["schemaCode"] == "trade-license-application", schema_data)


def test_write_04_missing_jwt_forces_dry_run_even_with_other_env_vars_set():
    """The real spec requires security: BearerAuth on every operation with no alternative scheme
    -- unlike registry/workflow, where a JWT is optional best-effort, here its absence must force
    a dry run rather than sending an unauthenticated request the real service would reject."""
    _CapturingHandler.received = []
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")
    rule_set = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }, clear=False):
            os.environ.pop("DIGIT_JWT_TOKEN", None)
            wizard.write_rules(rule_set)
    finally:
        server.shutdown()

    check("write04-no-request-sent-without-jwt", len(_CapturingHandler.received) == 0,
          _CapturingHandler.received)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nReal-write and registry-fetch HTTP paths verified against a throwaway local server.")
