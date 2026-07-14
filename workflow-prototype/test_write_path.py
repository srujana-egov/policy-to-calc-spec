"""Regression test for the real write path -- the same class of gap that let two real bugs ship
in ../registry-prototype/ (a wrong URL segment, and a field silently landing in the wrong place
in the JSON) before a live write ever exercised them. Every other test here only drives the
dry-run branch of write_process_definition() (no env vars set -> nothing sent), which can't catch
a URL/header/body-shape mistake since it never sends a real request.

Before writing this, the workflow service's route/headers/body-shape/response-shape were
re-verified directly against real Go source (api/routes.go, api/handlers/process_handler.go,
internal/models/common.go/models.go in digitnxt/digit3) rather than trusted from an earlier
session's notes -- unlike registry, nothing was found wrong here. This test locks that in as a
checked fact, not an assumption, and catches a future regression the same way
../registry-prototype/test_write_path.py now would.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from unittest import mock

import wizard
from builder import WorkflowBuilder

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

    def log_message(self, format, *args):
        pass  # keep test output quiet


def _run_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _simple_process():
    b = WorkflowBuilder(name="Test Flow", code="TEST_FLOW", sla_ms=86_400_000)
    start = b.add_initial_state("Start", sla_ms=3_600_000)
    b.add_action_to_new_state(start, "Approve", "Done", roles=["APPROVER"])
    return b


def test_write_01_uses_real_workflow_v3_process_definition_path():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"code": "TEST_FLOW", "name": "Test Flow", "states": []}
    process = _simple_process().build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_JWT_TOKEN": "tok",
            "DIGIT_TENANT_ID": "t",
            "DIGIT_USER_ID": "u",
        }):
            wizard.write_process_definition(process)
    finally:
        server.shutdown()

    check("write01-one-request", len(_CapturingHandler.received) == 1, _CapturingHandler.received)
    req = _CapturingHandler.received[0]
    check("write01-path", req["path"] == "/workflow/v3/process/definition", req["path"])
    check("write01-tenant-header", req["headers"].get("X-Tenant-Id") == "t", req["headers"])
    check("write01-user-header", req["headers"].get("X-User-Id") == "u", req["headers"])
    check("write01-auth-header", req["headers"].get("Authorization") == "Bearer tok", req["headers"])
    check("write01-content-type", req["headers"].get("Content-Type") == "application/json")


def test_write_02_body_matches_built_process_flat_shape():
    """The real request struct (processDefinitionRequest) is flat: code/name/description/version/
    sla/states at the top level, each state's actions carrying code/label/nextState/roles/
    assigneeCheck -- not wrapped in any envelope. Confirms the exact JSON sent matches this,
    not just that the Python objects compare equal."""
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"code": "TEST_FLOW", "name": "Test Flow", "states": []}
    process = _simple_process().build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_JWT_TOKEN": "tok", "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            wizard.write_process_definition(process)
    finally:
        server.shutdown()

    body = _CapturingHandler.received[0]["body"]
    check("write02-top-level-code", body.get("code") == "TEST_FLOW", body)
    check("write02-top-level-states", isinstance(body.get("states"), list) and len(body["states"]) == 2, body)
    start_state = next(s for s in body["states"] if s["code"] == "START")
    check("write02-action-shape",
          start_state["actions"][0].get("nextState") == "DONE" and
          start_state["actions"][0].get("roles") == ["APPROVER"],
          start_state)
    check("write02-no-envelope-wrapper", "data" not in body and "definition" not in body, body)


def test_write_03_success_response_reads_top_level_code_not_wrapped():
    """The real response is the created ProcessDefinitionDetail object directly at the top
    level (c.JSON(status, definitions[0]) in the real handler) -- not wrapped in {"data": ...}
    the way the registry service wraps its responses. write_process_definition() reads
    resp["code"] directly; confirms that assumption against a realistic response body."""
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"code": "TEST_FLOW", "name": "Test Flow", "states": []}
    process = _simple_process().build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_JWT_TOKEN": "tok", "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            # write_process_definition() only prints on success/failure -- if resp["code"]
            # raised (e.g. KeyError from a wrongly-nested response), this would propagate.
            wizard.write_process_definition(process)
    finally:
        server.shutdown()
    check("write03-no-crash-reading-top-level-code", True)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nReal-write HTTP path/headers/body/response-shape verified against a throwaway "
          "local server, matching real Go source in digit3 -- no bug found, but now checked, "
          "not assumed.")
