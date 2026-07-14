"""Regression test for a real bug: write_records() constructed a data-write URL with an extra
'/schema/' segment that the actual registry service's router doesn't have -- main.go mounts
`dataRoutes := v1.Group("/:schemaCode/data")` as a *sibling* of `schemaRoutes := v1.Group("/schema")`,
not nested under it, so the real path is `/registry/v3/{schemaCode}/data`, not
`/registry/v3/schema/{schemaCode}/data`. A live write returned a bare 404 because of this, even
though the code read correctly at a glance and every dry-run test passed (dry runs never send a
real HTTP request, so a URL-construction bug like this can't be caught by them).

This starts a throwaway local HTTP server and asserts the exact path/headers/body actually sent
over the wire, so a URL typo like this can't silently regress again.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from unittest import mock

import data_entry
import wizard
from builder import SchemaBuilder

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


class _CapturingHandler(http.server.BaseHTTPRequestHandler):
    received = []
    response_body = {"success": True, "data": {}}

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


def test_write_01_schema_write_uses_registry_v3_schema():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"success": True, "data": {"schemaCode": "x"}}
    b = SchemaBuilder("x")
    b.add_field("Name", "string", required=True)
    b.add_unique_constraint(["name"])
    b.add_index("name")
    schema = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            wizard.write_schema(schema)
    finally:
        server.shutdown()

    check("write01-one-request", len(_CapturingHandler.received) == 1, _CapturingHandler.received)
    req = _CapturingHandler.received[0]
    check("write01-path", req["path"] == "/registry/v3/schema", req["path"])
    check("write01-tenant-header", req["headers"].get("X-Tenant-Id") == "t")
    check("write01-user-header", req["headers"].get("X-User-Id") == "u")
    check("write01-no-client-id-header", "X-Client-Id" not in req["headers"])
    # x-unique/x-indexes must be top-level keys of the sent body, siblings of "definition" --
    # not nested inside it. The real Go CreateSchema handler does c.ShouldBindJSON(&request)
    # straight into a struct where XUnique/XIndexes are fields on the request itself; anything
    # nested inside the "definition" JSON blob (which is just json.RawMessage server-side) is
    # inert and silently ignored. A prior version of this code nested them wrong and every
    # schema created with a unique constraint or index silently lost that constraint/index on
    # the real server despite the create call succeeding with 201.
    body = req["body"]
    check("write01-x-unique-is-top-level", body.get("x-unique") == [["name"]], body)
    check("write01-x-unique-not-nested-in-definition", "x-unique" not in body.get("definition", {}), body)
    check("write01-x-indexes-is-top-level", body.get("x-indexes", [{}])[0].get("fieldPath") == "name", body)
    check("write01-x-indexes-not-nested-in-definition", "x-indexes" not in body.get("definition", {}), body)


def test_write_02_data_write_uses_registry_v3_schemacode_data_no_schema_segment():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"success": True, "data": {"registryId": "REG-1"}}
    b = SchemaBuilder("license-registry")
    b.add_field("Name", "string", required=True)
    schema = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            data_entry.write_records(schema, [{"name": "Bob"}])
    finally:
        server.shutdown()

    check("write02-one-request", len(_CapturingHandler.received) == 1, _CapturingHandler.received)
    req = _CapturingHandler.received[0]
    check("write02-path-has-no-schema-segment", req["path"] == "/registry/v3/license-registry/data", req["path"])
    check("write02-body-wraps-data", req["body"] == {"data": {"name": "Bob"}}, req["body"])


def test_write_03_multiple_records_post_once_each():
    _CapturingHandler.received = []
    _CapturingHandler.response_body = {"success": True, "data": {"registryId": "REG-1"}}
    b = SchemaBuilder("license-registry")
    b.add_field("Name", "string", required=True)
    schema = b.build()

    server, port = _run_server()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            data_entry.write_records(schema, [{"name": "Bob"}, {"name": "Alice"}])
    finally:
        server.shutdown()

    check("write03-two-requests", len(_CapturingHandler.received) == 2, _CapturingHandler.received)
    check("write03-both-same-path",
          all(r["path"] == "/registry/v3/license-registry/data" for r in _CapturingHandler.received))
    check("write03-bodies-distinct",
          [r["body"] for r in _CapturingHandler.received] == [{"data": {"name": "Bob"}}, {"data": {"name": "Alice"}}])


def test_write_04_fetch_schema_parses_real_response_shape():
    """add_data.py's fetch_schema() against a mock server returning the real Go Schema struct's
    JSON shape -- schemaCode/definition/x-unique/x-indexes as top-level sibling keys, plus extra
    fields (id, version, isLatest, isActive, auditDetails) it must ignore."""
    import add_data

    class _GetHandler(http.server.BaseHTTPRequestHandler):
        requested_path = None

        def do_GET(self):
            _GetHandler.requested_path = self.path
            body = json.dumps({
                "success": True,
                "data": {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "schemaCode": "license-registry",
                    "version": 1,
                    "definition": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"licenseNumber": {"type": "string"}},
                        "required": ["licenseNumber"],
                    },
                    "x-unique": [["licenseNumber"]],
                    "x-indexes": [{"fieldPath": "licenseNumber", "method": "btree"}],
                    "isLatest": True,
                    "isActive": True,
                    "auditDetails": {"createdBy": "u", "createdTime": 0},
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _GetHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with mock.patch.dict(os.environ, {
            "DIGIT_SERVER_URL": f"http://127.0.0.1:{port}",
            "DIGIT_TENANT_ID": "t", "DIGIT_USER_ID": "u",
        }):
            schema = add_data.fetch_schema("license-registry")
    finally:
        server.shutdown()

    check("write04-correct-path", _GetHandler.requested_path == "/registry/v3/schema/license-registry",
          _GetHandler.requested_path)
    check("write04-schema-code", schema.schemaCode == "license-registry")
    check("write04-field-parsed", "licenseNumber" in schema.definition.properties)
    check("write04-unique-parsed", schema.x_unique == [["licenseNumber"]])
    check("write04-index-parsed", schema.x_indexes[0].fieldPath == "licenseNumber")


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nReal-write HTTP paths verified against a throwaway local server -- the exact bug "
          "class that shipped once already can't silently regress.")
