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


def _resolve_ref_for_entry(prop: dict, defs: dict) -> dict | None:
    """Same reasoning as render.py's _resolve_internal_ref -- only an internal '#/$defs/...' or
    '#/definitions/...' ref is resolved (no network calls, matching this project's offline-safety
    guarantee); an external/unresolvable ref falls through to the raw-JSON fallback below."""
    ref = prop.get("$ref")
    if not isinstance(ref, str):
        return None
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            target = defs.get(ref[len(prefix):])
            return target if isinstance(target, dict) else None
    return None


def _oneof_alt_summary(alt, index: int) -> str:
    if not isinstance(alt, dict):
        return f"Option {index + 1}"
    if alt.get("title"):
        return str(alt["title"])
    props = alt.get("properties")
    if props:
        return ", ".join(props.keys())
    return f"Option {index + 1}"


def ask_one_of_value(field_name: str, alternatives: list, required: bool, defs: dict):
    """oneOf/anyOf as a CLI Q&A: 'choose ONE of the following ways to provide this,' the same
    construct render.py's HTML preview renders as a radio-button switcher (_render_one_of) --
    here, a numbered menu, then that alternative's own sub-fields asked as a nested group."""
    print(f"  '{field_name}' is one of several possible shapes:")
    for i, alt in enumerate(alternatives):
        print(f"    {i + 1}) {_oneof_alt_summary(alt, i)}")
    while True:
        raw = ask(f"  Which one applies?{'' if required else ' (blank to skip)'}")
        if not raw and not required:
            return None
        try:
            choice = int(raw) - 1
        except ValueError:
            choice = -1
        if 0 <= choice < len(alternatives):
            break
        print(f"  please enter a number from 1-{len(alternatives)}")
    alt = alternatives[choice]
    if not isinstance(alt, dict):
        return None
    alt_required = set(alt.get("required") or [])
    nested = {}
    for sub_name, sub_prop in alt.get("properties", {}).items():
        value = ask_record_value(sub_name, sub_prop, sub_name in alt_required, defs=defs)
        if value is not None:
            nested[sub_name] = value
    return nested or None


def ask_nested_record_value(field_name: str, prop: dict, required: bool, defs: dict | None = None):
    """One level of nesting -- a group-of-fields value (e.g. 'address') asked as its own set of
    sub-questions, matching how ../wizard.py authors it. An optional group can be skipped
    entirely; a required one always asks its own sub-fields (which may themselves be optional).
    Takes a plain dict (not a PropertyDef) -- ask_record_value normalizes either shape to a dict
    before calling this, since a nested group can originate from a modeled PropertyDef.properties
    entry or from a raw dict's own "properties"/"required" keys (e.g. a dependentSchemas group,
    or an add_raw_property fragment) -- one shape, uniformly handled here either way."""
    if not required and not ask_yes_no(f"  Include '{field_name}'?"):
        return None
    print(f"  --- {field_name} ---")
    nested_required = set(prop.get("required") or [])
    nested = {}
    for sub_name, sub_prop in (prop.get("properties") or {}).items():
        value = ask_record_value(sub_name, sub_prop, sub_name in nested_required, defs=defs)
        if value is not None:
            nested[sub_name] = value
    return nested or None


def ask_record_value(field_name: str, prop, required: bool, defs: dict | None = None):
    """Asks for one field's value via the CLI. `prop` may be a PropertyDef (the common, guided-
    wizard case) or a plain dict -- models.py's "any JSON Schema" escape hatch for constructs
    PropertyDef can't represent (oneOf/anyOf, $ref, or anything add_raw_property/
    define_reusable_schema introduced, most often via the free-text LLM-drafting path). A real
    crash found live: this used to assume every property was a PropertyDef and do unconditional
    attribute access (prop.type), which broke the moment a drafted schema had any field shaped
    this way -- the guided wizard and the HTML preview already both handle this (see render.py),
    but this CLI record-entry flow never did. Normalizing to a dict up front here means the same
    question logic below handles both origins identically, rather than duplicating it per shape."""
    defs = defs or {}
    if isinstance(prop, PropertyDef):
        prop = prop.model_dump(exclude_none=True, by_alias=True)
    elif not isinstance(prop, dict):
        # A bare true/false sub-schema (JSON Schema legally allows this) -- "anything goes" or
        # "nothing is allowed." Neither has a sensible guided question; falls through to the
        # raw-JSON prompt at the bottom (blank/optional skip still works for "nothing allowed").
        prop = {}

    if "$ref" in prop:
        resolved = _resolve_ref_for_entry(prop, defs)
        if resolved is not None:
            return ask_record_value(field_name, resolved, required, defs=defs)

    alternatives = prop.get("oneOf") or prop.get("anyOf")
    if isinstance(alternatives, list) and alternatives:
        return ask_one_of_value(field_name, alternatives, required, defs)

    if prop.get("type") == "object" and prop.get("properties"):
        return ask_nested_record_value(field_name, prop, required, defs=defs)

    suffix = " (required)" if required else " (optional, blank to skip)"

    if prop.get("enum"):
        options = "/".join(str(v) for v in prop["enum"])
        while True:
            raw = ask(f"  {field_name}{suffix} -- one of [{options}]:")
            if not raw:
                if required:
                    print("  this field is required")
                    continue
                return None
            match = next((v for v in prop["enum"] if str(v).lower() == raw.lower()), None)
            if match is None:
                print(f"  must be one of: {options}")
                continue
            return match

    if prop.get("type") == "boolean":
        while True:
            raw = ask(f"  {field_name}{suffix} -- yes/no:").lower()
            if not raw and not required:
                return None
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            print("  please answer yes or no")

    if prop.get("type") == "integer":
        while True:
            raw = ask(f"  {field_name}{suffix} -- whole number:")
            if not raw and not required:
                return None
            try:
                return int(raw)
            except ValueError:
                print("  please enter a whole number")

    if prop.get("type") == "number":
        while True:
            raw = ask(f"  {field_name}{suffix} -- number:")
            if not raw and not required:
                return None
            try:
                return float(raw)
            except ValueError:
                print("  please enter a number")

    if prop.get("format") == "date":
        while True:
            raw = ask(f"  {field_name}{suffix} -- date (YYYY-MM-DD):")
            if not raw and not required:
                return None
            if _DATE_RE.match(raw):
                return raw
            print("  please use YYYY-MM-DD")

    if prop.get("type") == "string" or not prop:
        # A plain string field, or nothing at all was recognized on this dict (a bare-boolean
        # sub-schema normalized to {} above) -- both get a plain free-text question, matching this
        # project's existing behavior for an ordinary string field.
        while True:
            raw = ask(f"  {field_name}{suffix}:")
            if raw or not required:
                return raw or None
            print("  this field is required")

    # Genuinely exotic (type == "array", patternProperties, contains, propertyNames,
    # unevaluatedProperties, or any combination this CLI has no dedicated question for) -- honest
    # raw-JSON fallback rather than silently dropping the field, guessing wrong, or crashing.
    print(f"  '{field_name}'{suffix} has an advanced rule guided entry doesn't have a question "
          "for -- enter its value as raw JSON.")
    while True:
        raw = ask(f"  {field_name} (raw JSON):")
        if not raw:
            if required:
                print("  this field is required")
                continue
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  not valid JSON ({e}) -- try again")


def ask_record(schema: SchemaRequest) -> dict:
    print("\n--- New record ---")
    defs = schema.definition.defs_ or {}
    record = {}
    for name, prop in schema.definition.properties.items():
        value = ask_record_value(name, prop, name in schema.definition.required, defs=defs)
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
