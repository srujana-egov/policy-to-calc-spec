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
from render import get_preview_completeness, render_data_preview, render_schema_form_preview
from validate import validate_json_schema_syntax, validate_schema_request


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
    "7": ("object", None, "a group of related fields (e.g. an address with street/city/zip)"),
}


def ask_text_pattern(label: str) -> str | None:
    if not ask_yes_no(f"  Does '{label}' need to be an exact number of digits (like a phone "
                       "number or pincode)?"):
        return None
    raw = ask("  How many digits exactly?")
    if raw.isdigit():
        return f"^[0-9]{{{raw}}}$"
    print("  couldn't parse that as a number of digits -- skipping the pattern for now")
    return None


def ask_optional_number(prompt: str) -> float | None:
    raw = ask(prompt)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        print("  couldn't parse that as a number -- skipping")
        return None
    return int(value) if value == int(value) else value


def ask_optional_int(prompt: str) -> int | None:
    raw = ask(prompt)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print("  couldn't parse that as a whole number -- skipping")
        return None


def ask_number_bounds(label: str) -> tuple[float | None, float | None]:
    if not ask_yes_no(f"  Does '{label}' need a minimum or maximum allowed value?"):
        return None, None
    minimum = ask_optional_number("  Smallest allowed value? (blank for no minimum)")
    maximum = ask_optional_number("  Largest allowed value? (blank for no maximum)")
    return minimum, maximum


def ask_text_length_bounds(label: str) -> tuple[int | None, int | None]:
    if not ask_yes_no(f"  Does '{label}' need a minimum or maximum length?"):
        return None, None
    min_length = ask_optional_int("  Shortest allowed length? (blank for no minimum)")
    max_length = ask_optional_int("  Longest allowed length? (blank for no maximum)")
    return min_length, max_length


def ask_field_type(label: str, allow_group: bool = True) -> dict:
    """Returns a dict with type/format/enum/pattern/minimum/maximum/minLength/maxLength --
    allow_group=False excludes the 'group of fields' option, since nesting only goes one level
    deep (see models.py)."""
    options = dict(_FIELD_TYPE_OPTIONS)
    if not allow_group:
        del options["7"]
    print(f"  What kind of value does '{label}' hold?")
    for key, (_, _, description) in options.items():
        print(f"    {key}. {description}")
    while True:
        choice = ask(f"  Pick 1-{len(options)}:")
        if choice in options:
            break
        print(f"  please pick one of 1-{len(options)}")
    type_, format_, description = options[choice]
    result = {"type": type_, "format": format_, "enum": None, "pattern": None, "minimum": None,
              "maximum": None, "minLength": None, "maxLength": None}

    if description == "one of a fixed list of choices":
        raw = ask("  What are the allowed values? (comma-separated)")
        result["enum"] = [v.strip() for v in raw.split(",") if v.strip()]
    elif description == "text":
        result["pattern"] = ask_text_pattern(label)
        result["minLength"], result["maxLength"] = ask_text_length_bounds(label)
    elif description in ("whole number", "decimal number"):
        result["minimum"], result["maximum"] = ask_number_bounds(label)

    return result


def configure_nested_fields(builder: SchemaBuilder, parent_name: str, parent_label: str) -> None:
    """'What fields does this group contain?' -- one level of nesting under an object-type
    field. Confirmed as a real pattern (address/auditDetails-style groups) across three
    independent real schemas found in the digitnxt org, not a hypothetical."""
    print(f"  What fields does '{parent_label}' contain?")
    while True:
        sub_label = ask(f"  Name a field inside '{parent_label}' -- or leave blank if done:")
        if not sub_label:
            break
        field_type = ask_field_type(sub_label, allow_group=False)
        required = ask_yes_no(f"    Is '{sub_label}' required?")
        description = ask(f"    One-line description for '{sub_label}' (optional):")
        sub_name = builder.add_nested_field(
            parent_name, sub_label, field_type["type"], required=required, format=field_type["format"],
            enum=field_type["enum"], description=description or None, pattern=field_type["pattern"],
            minimum=field_type["minimum"], maximum=field_type["maximum"],
            minLength=field_type["minLength"], maxLength=field_type["maxLength"])
        print(f"    -> added '{sub_name}' inside '{parent_label}'")


def ask_field(builder: SchemaBuilder) -> str | None:
    """One full field Q&A round. Returns the generated field name, or None if the user left the
    label blank -- meaning they're done adding fields."""
    label = ask("Name this field (e.g. 'License Number') -- or leave blank if you're done adding fields:")
    if not label:
        return None
    field_type = ask_field_type(label)
    required = ask_yes_no(f"  Is '{label}' required on every record?")
    description = ask(f"  One-line description for '{label}' (optional):")
    name = builder.add_field(label, field_type["type"], required=required, format=field_type["format"],
                              enum=field_type["enum"], description=description or None,
                              pattern=field_type["pattern"], minimum=field_type["minimum"],
                              maximum=field_type["maximum"], minLength=field_type["minLength"],
                              maxLength=field_type["maxLength"])
    print(f"  -> added field '{name}'")
    if field_type["type"] == "object":
        configure_nested_fields(builder, name, label)
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
                gin = ask_yes_no(f"  Should people be able to search within the '{field}' field's "
                                  "text (like a search box), rather than only matching/sorting on "
                                  "the exact value?")
                index_name = ask("  Give this index a name? (optional, blank for auto):")
                builder.add_index(field, method="gin" if gin else "btree", name=index_name or None)
            else:
                print(f"  '{field}' isn't a known field -- skipping")
            if not ask_yes_no("  Index another field?"):
                break


def redo_field(builder: SchemaBuilder, name: str) -> None:
    """Wipes one field's type/required/description (and, if it's a group, every sub-field) and
    re-asks -- the targeted fix, so a wrong type doesn't force restarting the whole schema."""
    label = name
    field_type = ask_field_type(label)
    required = ask_yes_no(f"  Is '{label}' required on every record?")
    description = ask(f"  One-line description for '{label}' (optional):")
    was_required = name in builder.required
    builder.properties[name] = builder.properties[name].__class__(
        type=field_type["type"], format=field_type["format"], enum=field_type["enum"],
        description=description or None, pattern=field_type["pattern"],
        minimum=field_type["minimum"], maximum=field_type["maximum"],
        minLength=field_type["minLength"], maxLength=field_type["maxLength"])
    if required and not was_required:
        builder.required.append(name)
    elif not required and was_required:
        builder.required.remove(name)
    if field_type["type"] == "object":
        configure_nested_fields(builder, name, label)


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


def print_preview_completeness(definition: dict) -> None:
    """Prints the same "how much of this schema can the preview actually show you" summary the
    rendered HTML's completeness banner gives, in the terminal too -- so a CLI user sees it before
    even opening the browser, and knows to look for "Needs review" panels if it's below 100%."""
    completeness = get_preview_completeness(definition)
    print(f"\nPreview completeness: {completeness['percent']}% "
          f"({completeness['full']} fully visualized, {completeness['partial']} explained here "
          f"(server still enforces it), {completeness['none']} not explained (server still enforces it))")
    if completeness["gaps"]:
        print("Some rules need your review in the preview (look for 'Needs review' panels):")
        for gap in completeness["gaps"]:
            print(f"  - {' + '.join(gap['keywords'])}: {gap['meaning']}")
    cs = completeness.get("conformance_summary")
    if cs and cs["executed"]:
        print(f"Conformance checks: {cs['executed']} executed across {cs['gaps_with_tests']} rule(s), "
              f"{cs['passed_as_expected']} behaved as expected"
              + (f", {cs['errored']} could not be validated" if cs.get("errored") else "")
              + (f", {cs['inconclusive']} inconclusive" if cs.get("inconclusive") else "")
              + (f", {cs['surprising']} SURPRISING -- review these rules closely" if cs["surprising"] else "") + ".")


def resolve_required_gaps(builder: SchemaBuilder, confidence: dict) -> None:
    """Targeted follow-up for the single highest-value gap an LLM draft can leave: fields whose
    required/optional status was inferred rather than actually stated by the user. Question count
    scales with how much was left unsaid, not with schema size -- a precisely-described schema
    gets no questions here at all."""
    for field_id, info in confidence.items():
        if info.get("required_stated"):
            continue
        if "." in field_id:
            parent_name, sub_name = field_id.split(".", 1)
            parent = builder.properties[parent_name]
            currently_required = sub_name in (parent.required or [])
            wants_required = ask_yes_no(f"Is '{sub_name}' (inside '{parent_name}') required on every record?")
            if wants_required and not currently_required:
                if parent.required is None:
                    parent.required = []
                parent.required.append(sub_name)
            elif not wants_required and currently_required:
                parent.required.remove(sub_name)
        else:
            currently_required = field_id in builder.required
            wants_required = ask_yes_no(f"Is '{field_id}' required on every record?")
            if wants_required and not currently_required:
                builder.required.append(field_id)
            elif not wants_required and currently_required:
                builder.required.remove(field_id)


def run_llm_schema_session() -> SchemaRequest:
    """Free-text entry point: the user describes the form in plain language instead of answering
    fixed questions. An LLM drafts the schema by calling the same SchemaBuilder methods the guided
    wizard uses (see llm_schema_draft.py), a short targeted follow-up closes the highest-value gap
    left unstated (required-ness), and the rest of the loop -- render, confirm, targeted
    fix-one-field, write -- is the existing wizard machinery, unchanged."""
    from llm_schema_draft import draft_schema_from_description, judge_schema_against_description, log_judge_result

    print("=== Registry schema drafting -- describe it, don't answer a form ===")
    print("(type 'quit' at any question to stop -- nothing is saved until the very end)\n")
    schema_code = ask("What do you want to call this schema? (e.g. 'license-registry')")
    description = ask("Describe the form you need, in your own words -- the fields, whether "
                       "each one's required, and any groups (like an address with city/pincode):")

    print("\nDrafting from your description...")
    builder, confidence = draft_schema_from_description(schema_code, description)
    if not builder.properties:
        print("Couldn't draft any fields from that description -- let's build it field by field instead.")
        configure_fields(builder)
    else:
        resolve_required_gaps(builder, confidence)

        print("\nDouble-checking the draft against what you described...")
        judged_definition = builder.build().model_dump(by_alias=True, exclude_none=True)["definition"]
        judgment = judge_schema_against_description(description, judged_definition)
        # Reduced snapshot (everything but the verbose per-gap "gaps" list -- recoverable later by
        # re-running get_preview_completeness against the logged `definition` itself) so low
        # preview coverage can eventually be correlated against human corrections and judge
        # confidence, not just logged as an isolated number nobody can act on yet.
        completeness = get_preview_completeness(judged_definition)
        preview_coverage = {k: v for k, v in completeness.items() if k != "gaps"}
        log_judge_result(schema_code, description, judged_definition, confidence, judgment,
                          preview_coverage=preview_coverage)
        if not judgment["ok"] and judgment["issues"]:
            print("An automated check found possible mismatches -- worth checking in the preview:")
            for issue in judgment["issues"]:
                print(f"  - {issue}")
        else:
            print("No obvious mismatches found -- still worth reviewing the preview yourself.")

    while True:
        schema = builder.build()
        data = schema.model_dump(by_alias=True, exclude_none=True)
        errors = validate_schema_request(schema)
        # Meta-schema check is separate from (and doesn't replace) validate_schema_request's
        # referential-integrity checks above -- this one catches malformed regex/wrong keyword
        # types (technical JSON Schema validity), which a dangling-reference check wouldn't; the
        # reverse is also true, so both run every pass.
        errors += validate_json_schema_syntax(data["definition"])

        if errors:
            print("\nVALIDATION FAILED -- fix these before a preview would mean anything:")
            for e in errors:
                print(f"  - {e}")
            offer_fix_schema(builder)
            continue

        preview_path = os.path.abspath(f"{schema.schemaCode}_schema_preview.html")
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), preview_path, field_confidence=confidence)
        print(f"\nOpen this in a browser to review it visually:\n  {preview_path}")
        print("(fields marked 'assumed' were guessed from your description, not stated -- check those first)")
        print_preview_completeness(data["definition"])

        if ask_yes_no("\nDoes this look right? Confirm to create the schema"):
            break

        print("Not confirmed -- let's fix just the part that's wrong (type 'quit' to stop entirely).")
        offer_fix_schema(builder)

    return schema


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
        data = schema.model_dump(by_alias=True, exclude_none=True)
        render_schema_form_preview(data["schemaCode"], data["definition"], data.get("x-unique"),
                                    data.get("x-indexes"), preview_path)
        print(f"\nAll checks passed. Open this in a browser to review it visually:\n  {preview_path}")
        print("(this shows what the data-entry form will actually look like)")
        print_preview_completeness(data["definition"])

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
    mode = ask("How do you want to build this schema? Type 'describe' to describe it in plain "
               "language (drafted by AI), or 'wizard' for guided step-by-step questions:")
    schema = run_llm_schema_session() if mode.strip().lower().startswith("d") else run_schema_session()
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
