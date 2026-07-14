"""Regression test for the real write path (POST /{module}/rules) and the registry-schema fetch
(GET /registry/v3/schema/:schemaCode) against a throwaway local HTTP server -- mirrors
../workflow-prototype/test_write_path.py and ../registry-prototype/test_write_path.py's reasoning:
every other test here only drives the dry-run branch, which can't catch a URL/header/body-shape
mistake since it never sends a real request.

Important, honest caveat (see models.py's docstring too): unlike the sibling prototypes' write
paths, there's no real Calculation Engine service anywhere in the digitnxt org to verify
POST /{module}/rules against -- this locks in the *documented* shape from
../CONFIG-PIPELINE.md and the header convention already verified for the other two services, not
an independently confirmed one. The registry-schema GET, by contrast, *is* the same real, verified
route already proven in ../registry-prototype/test_write_path.py.
"""

from __future__ import annotations

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


def test_write_01_rules_write_uses_module_scoped_path():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"success": True}
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")
    rule_set = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            wizard.write_rules(rule_set)
    finally:
        server.shutdown()

    check("write01-one-request", len(_CapturingHandler.received) == 1, _CapturingHandler.received)
    req = _CapturingHandler.received[0]
    check("write01-path", req["path"] == "/trade-license/rules", req["path"])
    check("write01-tenant-header", req["headers"].get("X-Tenant-Id") == "t", req["headers"])
    check("write01-user-header", req["headers"].get("X-User-Id") == "u", req["headers"])
    check("write01-no-client-id-header", "X-Client-Id" not in req["headers"])


def test_write_02_body_is_a_flat_array_of_rules_no_envelope():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"success": True}
    b = CalculationRuleBuilder("trade-license")
    b.add_flat_rule("FEE", 100, effectiveFrom="2024-01-01")
    b.add_percentage_rule("CESS", 5, "FEE", effectiveFrom="2024-01-01")
    rule_set = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            wizard.write_rules(rule_set)
    finally:
        server.shutdown()

    body = _CapturingHandler.received[0]["body"]
    check("write02-is-a-list", isinstance(body, list) and len(body) == 2, body)
    check("write02-first-rule-component", body[0]["component"] == "FEE", body)
    check("write02-second-rule-has-appliesOn", body[1]["appliesOn"]["componentRef"] == "FEE", body)
    check("write02-no-module-field-on-rules", all("module" not in r for r in body), body)


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


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nReal-write and registry-fetch HTTP paths verified against a throwaway local server.")
