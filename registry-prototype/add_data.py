"""Standalone entry point for adding data records to a schema that already exists on the server
-- skips schema authoring entirely. Fetches the real schema definition via
GET /registry/v3/schema/:schemaCode (same env vars as wizard.py's write step), then runs the same
record-entry flow as wizard.py's phase 2 (data_entry.py).

Run: python3 add_data.py [schema-code]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from data_entry import run_data_session, write_records
from models import SchemaRequest
from wizard import Cancelled, _registry_headers, ask


def fetch_schema(schema_code: str) -> SchemaRequest:
    headers = _registry_headers()
    if headers is None:
        raise SystemExit(
            "DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID must all be set to fetch an existing "
            "schema -- there's no dry-run form of this (nothing to preview without knowing the "
            "real field definitions)."
        )
    server_url = os.environ["DIGIT_SERVER_URL"]
    url = server_url.rstrip("/") + f"/registry/v3/schema/{schema_code}"
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())["data"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Couldn't fetch schema '{schema_code}' -- {e.code} {e.reason}\n"
                          f"{e.read().decode(errors='replace')}")
    return SchemaRequest(
        schemaCode=data["schemaCode"],
        definition=data["definition"],
        **{"x-unique": data.get("x-unique"), "x-indexes": data.get("x-indexes")},
    )


def main():
    schema_code = sys.argv[1] if len(sys.argv) > 1 else ask(
        "Which schema do you want to add data to? (exact schema code)")
    print(f"Fetching '{schema_code}' from the server...")
    schema = fetch_schema(schema_code)
    print(f"Found it -- {len(schema.definition.properties)} field(s): "
          f"{', '.join(schema.definition.properties)}")
    records = run_data_session(schema)
    write_records(schema, records)


if __name__ == "__main__":
    try:
        main()
    except Cancelled:
        print("\nCancelled -- nothing was saved.")
    except (KeyboardInterrupt, EOFError):
        print("\n\nCancelled -- nothing was saved.")
