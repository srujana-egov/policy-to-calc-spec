"""Interactive CLI wizard for authoring a DIGIT Registry schema and then entering data records
against it -- mirrors ../workflow-prototype/wizard.py's shape closely: plain-language questions
drive the same SchemaBuilder the automated tests use, quit/exit/q cancels at any prompt, a table
preview and explicit confirmation gate before any write, and a targeted fix-one-thing menu instead
of discarding the whole session on "no".

Run: python wizard.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from builder import SchemaBuilder
from models import SchemaRequest
from render import render_data_preview, render_schema_preview
from validate import validate_schema_request


class Cancelled(Exception):
    """Raised when the user types quit/exit at any prompt -- caught once, at the top level."""


def ask(prompt: str) -> str:
    answer = input(prompt + " ").strip()
    if answer.lower() in ("quit", "exit", "q"):
        raise Cancelled
    return answer


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = ask(prompt + " (yes/no)").lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  please answer yes or no")


_FIELD_TYPE_OPTIONS = {
    "1": ("string", None, "text"),
    "2": ("integer", None, "whole number"),
    "3": ("number", None, "decimal number"),
    "4": ("boolean", None, "yes/no"),
    "5": ("string", "date", "date"),
    "6": ("string", None, "one of a fixed list of choices"),
}


def ask_field_type(label: str) -> tuple[str, str | None, list[str] | None]:
    print(f"  What kind of value does '{label}' hold?")
    for key, (_, _, description) in _FIELD_TYPE_OPTIONS.items():
        print(f"    {key}. {description}")
    while True:
        choice = ask("  Pick 1-6:")
        if choice in _FIELD_TYPE_OPTIONS:
            break
        print("  please pick one of 1-6")
    type_, format_, description = _FIELD_TYPE_OPTIONS[choice]
    if description == "one of a fixed list of choices":
        raw = ask("  What are the allowed values? (comma-separated)")
        enum = [v.strip() for v in raw.split(",") if v.strip()]
        return type_, format_, enum
    return type_, format_, None


def ask_field(builder: SchemaBuilder) -> str | None:
    """One full field Q&A round. Returns the generated field name, or None if the user left the
    label blank -- meaning they're done adding fields."""
    label = ask("Name this field (e.g. 'License Number') -- or leave blank if you're done adding fields:")
    if not label:
        return None
    type_, format_, enum = ask_field_type(label)
    required = ask_yes_no(f"  Is '{label}' required on every record?")
    description = ask(f"  One-line description for '{label}' (optional):")
    name = builder.add_field(label, type_, required=required, format=format_, enum=enum,
                              description=description or None)
    print(f"  -> added field '{name}'")
    return name


def configure_fields(builder: SchemaBuilder) -> None:
    while True:
        if ask_field(builder) is None:
            break


def configure_constraints(builder: SchemaBuilder) -> None:
    if ask_yes_no("Should any field (or combination of fields) be unique across every record?"):
        while True:
            raw = ask(f"  Which field(s)? (comma-separated: {', '.join(builder.properties.keys())})")
            fields = [f.strip() for f in raw.split(",") if f.strip()]
            if fields:
                builder.add_unique_constraint(fields)
            if not ask_yes_no("  Another unique constraint?"):
                break

    if ask_yes_no("Do you want any field indexed for fast search/filtering?"):
        while True:
            field = ask(f"  Which field? ({', '.join(builder.properties.keys())})")
            if field in builder.properties:
                builder.add_index(field)
            else:
                print(f"  '{field}' isn't a known field -- skipping")
            if not ask_yes_no("  Index another field?"):
                break


def redo_field(builder: SchemaBuilder, name: str) -> None:
    """Wipes one field's type/required/description and re-asks -- the targeted fix, so a wrong
    type doesn't force restarting the whole schema."""
    label = name
    type_, format_, enum = ask_field_type(label)
    required = ask_yes_no(f"  Is '{label}' required on every record?")
    description = ask(f"  One-line description for '{label}' (optional):")
    was_required = name in builder.required
    builder.properties[name] = builder.properties[name].__class__(
        type=type_, format=format_, enum=enum, description=description or None)
    if required and not was_required:
        builder.required.append(name)
    elif not required and was_required:
        builder.required.remove(name)


def offer_fix_schema(builder: SchemaBuilder) -> None:
    field_names = ", ".join(builder.properties.keys())
    choice = ask(
        "What do you want to fix? Type a field name to redo it, 'add' for a new field, "
        "'delete FIELD_NAME' to remove one, 'rename' for the schema's own code, or "
        "'constraints' to redo which fields must be unique/indexed (e.g. after deleting a "
        f"field one of them referenced).\nFields: {field_names}"
    )
    lowered = choice.lower()
    if lowered == "rename":
        new_code = ask(f"Schema code? (currently '{builder.schema_code}', blank to keep)")
        if new_code:
            builder.schema_code = new_code
    elif lowered == "add":
        ask_field(builder)
    elif lowered == "constraints":
        builder.unique_constraints = []
        builder.indexes = []
        configure_constraints(builder)
    elif lowered.startswith("delete "):
        target = choice.split(None, 1)[1].strip()
        if target not in builder.properties:
            print(f"  '{target}' isn't a known field -- nothing removed")
        else:
            builder.remove_field(target)
            print(f"  -> removed '{target}'")
    elif choice in builder.properties:
        redo_field(builder, choice)
    else:
        print(f"  '{choice}' isn't a known field -- nothing changed")


def run_schema_session() -> SchemaRequest:
    """Runs the schema-authoring question sequence and returns the final, validated
    SchemaRequest. Split from main() so tests can drive the exact interactive code path."""
    print("=== Registry schema wizard ===")
    print("(type 'quit' at any question to stop -- nothing is saved until the very end)\n")
    schema_code = ask("What do you want to call this schema? (e.g. 'license-registry')")
    builder = SchemaBuilder(schema_code)

    configure_fields(builder)
    configure_constraints(builder)

    while True:
        schema = builder.build()
        errors = validate_schema_request(schema)

        if errors:
            print("\nVALIDATION FAILED -- fix these before a preview would mean anything:")
            for e in errors:
                print(f"  - {e}")
            offer_fix_schema(builder)
            continue

        preview_path = os.path.abspath(f"{schema.schemaCode}_schema_preview.html")
        render_schema_preview(schema, preview_path)
        print(f"\nAll checks passed. Open this in a browser to review it visually:\n  {preview_path}")
        print("(click any field for its exact definition)")

        if ask_yes_no("\nDoes this look right? Confirm to create the schema"):
            break

        print("Not confirmed -- let's fix just the part that's wrong (type 'quit' to stop entirely).")
        offer_fix_schema(builder)

    return schema


def _registry_headers() -> dict[str, str] | None:
    """None if the environment isn't configured for a real write -- callers fall back to a dry
    run. X-User-Id, not X-Client-Id: the real middleware only reads X-User-Id, even though
    swagger.yaml, the README, and the project's own Postman collection all document/send
    X-Client-Id, which is silently ignored."""
    server_url = os.environ.get("DIGIT_SERVER_URL")
    tenant_id = os.environ.get("DIGIT_TENANT_ID")
    user_id = os.environ.get("DIGIT_USER_ID")
    if not (server_url and tenant_id and user_id):
        return None
    headers = {"Content-Type": "application/json", "X-Tenant-Id": tenant_id, "X-User-Id": user_id}
    jwt_token = os.environ.get("DIGIT_JWT_TOKEN")
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    return headers


def write_schema(schema: SchemaRequest) -> None:
    headers = _registry_headers()
    body = schema.model_dump_json(by_alias=True, exclude_none=True).encode()

    if headers is None:
        print("\n=== DRY RUN (DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID not all set -- "
              "nothing sent) ===")
        print("Would POST to: {server}/registry/v3/schema")
        print("Body:")
        print(schema.model_dump_json(by_alias=True, indent=2, exclude_none=True))
        return

    server_url = os.environ["DIGIT_SERVER_URL"]
    url = server_url.rstrip("/") + "/registry/v3/schema"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"\nCreated -- {resp.status} {resp.reason}")
            print(json.loads(resp.read())["data"]["schemaCode"], "is now live.")
    except urllib.error.HTTPError as e:
        print(f"\nWrite failed -- {e.code} {e.reason}")
        print(e.read().decode(errors="replace"))


def main():
    schema = run_schema_session()
    write_schema(schema)

    if ask_yes_no("\nAdd data records to this schema now?"):
        from data_entry import run_data_session, write_records
        records = run_data_session(schema)
        write_records(schema, records)


if __name__ == "__main__":
    try:
        main()
    except Cancelled:
        print("\nCancelled -- nothing was saved.")
    except (KeyboardInterrupt, EOFError):
        print("\n\nCancelled -- nothing was saved.")
