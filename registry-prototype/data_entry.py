"""Phase 2: entering data records against an already-authored schema -- as many data records as
they need for this schema. Same shape as wizard.py's schema phase: one question per field per
record, a table preview, an explicit confirmation gate, and a targeted fix-one-record menu instead
of discarding every record on "no".
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from models import PropertyDef, SchemaRequest
from render import render_data_preview
from wizard import _registry_headers, ask, ask_yes_no

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ask_nested_record_value(field_name: str, prop: PropertyDef, required: bool):
    """One level of nesting -- a group-of-fields value (e.g. 'address') asked as its own set of
    sub-questions, matching how ../wizard.py authors it. An optional group can be skipped
    entirely; a required one always asks its own sub-fields (which may themselves be optional)."""
    if not required and not ask_yes_no(f"  Include '{field_name}'?"):
        return None
    print(f"  --- {field_name} ---")
    nested = {}
    for sub_name, sub_prop in (prop.properties or {}).items():
        value = ask_record_value(sub_name, sub_prop, sub_name in (prop.required or []))
        if value is not None:
            nested[sub_name] = value
    return nested or None


def ask_record_value(field_name: str, prop: PropertyDef, required: bool):
    if prop.type == "object" and prop.properties:
        return ask_nested_record_value(field_name, prop, required)

    suffix = " (required)" if required else " (optional, blank to skip)"

    if prop.enum:
        options = "/".join(prop.enum)
        while True:
            raw = ask(f"  {field_name}{suffix} -- one of [{options}]:")
            if not raw:
                if required:
                    print("  this field is required")
                    continue
                return None
            match = next((v for v in prop.enum if v.lower() == raw.lower()), None)
            if match is None:
                print(f"  must be one of: {options}")
                continue
            return match

    if prop.type == "boolean":
        while True:
            raw = ask(f"  {field_name}{suffix} -- yes/no:").lower()
            if not raw and not required:
                return None
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            print("  please answer yes or no")

    if prop.type == "integer":
        while True:
            raw = ask(f"  {field_name}{suffix} -- whole number:")
            if not raw and not required:
                return None
            try:
                return int(raw)
            except ValueError:
                print("  please enter a whole number")

    if prop.type == "number":
        while True:
            raw = ask(f"  {field_name}{suffix} -- number:")
            if not raw and not required:
                return None
            try:
                return float(raw)
            except ValueError:
                print("  please enter a number")

    if prop.format == "date":
        while True:
            raw = ask(f"  {field_name}{suffix} -- date (YYYY-MM-DD):")
            if not raw and not required:
                return None
            if _DATE_RE.match(raw):
                return raw
            print("  please use YYYY-MM-DD")

    while True:
        raw = ask(f"  {field_name}{suffix}:")
        if raw or not required:
            return raw or None
        print("  this field is required")


def ask_record(schema: SchemaRequest) -> dict:
    print("\n--- New record ---")
    record = {}
    for name, prop in schema.definition.properties.items():
        value = ask_record_value(name, prop, name in schema.definition.required)
        if value is not None:
            record[name] = value
    return record


def offer_data_fix(schema: SchemaRequest, records: list[dict]) -> None:
    choice = ask(
        f"What do you want to fix? Type a record number (1-{len(records)}) to redo it, "
        "'add' for a new record, or 'delete N' to remove one."
    )
    lowered = choice.lower()
    if lowered == "add":
        records.append(ask_record(schema))
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
    if not (0 <= idx < len(records)):
        print("  not a valid record number -- nothing changed")
        return
    if lowered.startswith("delete "):
        records.pop(idx)
        print(f"  -> removed record {idx + 1}")
    else:
        records[idx] = ask_record(schema)


def run_data_session(schema: SchemaRequest) -> list[dict]:
    """Runs the record-entry question sequence and returns the final, confirmed list of
    records. Split from write_records() so tests can drive the exact interactive code path."""
    records = [ask_record(schema)]
    while ask_yes_no("Add another record?"):
        records.append(ask_record(schema))

    while True:
        preview_path = os.path.abspath(f"{schema.schemaCode}_data_preview.html")
        render_data_preview(schema, records, preview_path)
        print(f"\nOpen this in a browser to review it visually:\n  {preview_path}")

        if ask_yes_no("Does this look right? Confirm to create these records"):
            break

        print("Not confirmed -- let's fix just the part that's wrong (type 'quit' to stop entirely).")
        offer_data_fix(schema, records)

    return records


def write_records(schema: SchemaRequest, records: list[dict]) -> None:
    headers = _registry_headers()

    if headers is None:
        print("\n=== DRY RUN (DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID not all set -- "
              "nothing sent) ===")
        for i, record in enumerate(records, start=1):
            print(f"Would POST to: {{server}}/registry/v3/{schema.schemaCode}/data")
            print(f"Record {i}:", json.dumps({"data": record}, indent=2))
        return

    server_url = os.environ["DIGIT_SERVER_URL"]
    url = server_url.rstrip("/") + f"/registry/v3/{schema.schemaCode}/data"
    created = 0
    for i, record in enumerate(records, start=1):
        body = json.dumps({"data": record}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                registry_id = json.loads(resp.read())["data"]["registryId"]
                print(f"Record {i}: created -- {registry_id}")
                created += 1
        except urllib.error.HTTPError as e:
            print(f"Record {i}: FAILED -- {e.code} {e.reason}")
            print(e.read().decode(errors="replace"))
    print(f"\n{created}/{len(records)} record(s) created.")
