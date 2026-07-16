"""Generates self-contained HTML previews for a non-technical business user. The schema preview
is a rendered form mockup -- labeled input controls per field, the way the real data-entry screen
will look -- rather than a table of field metadata, so a business user can look at it and confirm
"yes, based on what I described, my form will look like this." The data preview stays table-shaped,
matching how the registry's own record data is table-shaped (a list of records). No external
dependencies, same reasoning as ../workflow-prototype/render.py: a CDN script tag silently produced
a blank page there when opened offline, so this has none to begin with.
"""

from __future__ import annotations

import html
import json
import re

import conformance
from models import SchemaRequest

_STYLE = """
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 24px; }
  h1 { font-size: 18px; margin: 0 0 4px 0; }
  .subtitle { color: #888; font-size: 13px; margin-bottom: 18px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 8px 12px; font-size: 13px; text-align: left; vertical-align: top; }
  th { background: #f5f5f5; }
  .required { color: #a01f1f; font-weight: bold; }
  .optional { color: #888; }
  .role-tag { background: #e8f0fe; color: #3a6fc4; border-radius: 10px; padding: 2px 8px; font-size: 11px; margin-right: 4px; display: inline-block; }
  .form-field { margin-bottom: 14px; max-width: 420px; }
  .form-field label { display: block; font-weight: 600; margin-bottom: 4px; }
  .form-field input, .form-field select { width: 100%; padding: 6px 8px; font-size: 13px; border: 1px solid #ccc;
    border-radius: 4px; box-sizing: border-box; }
  .form-field input[type="checkbox"] { width: auto; }
  .required-marker { color: #a01f1f; }
  .field-help { color: #888; font-size: 12px; margin-top: 3px; }
  fieldset.form-group { margin-bottom: 16px; border: 1px solid #ddd; border-radius: 6px; padding: 12px 14px; max-width: 460px; }
  fieldset.form-group legend { font-weight: 600; padding: 0 4px; }
  .form-field.unsupported { max-width: 640px; }
  .raw-json-note { color: #a06a1f; font-size: 12px; margin-bottom: 4px; }
  .raw-json { background: #fafafa; border: 1px solid #eee; border-radius: 6px; padding: 8px 10px; font-size: 12px;
    font-family: ui-monospace, monospace; white-space: pre-wrap; }
  .assumed-badge { background: #fff3cd; color: #8a6100; border-radius: 8px; padding: 1px 6px; font-size: 10px;
    margin-left: 6px; font-weight: 600; }
  .form-field.assumed input, .form-field.assumed select { border-color: #e0b34d; }
  fieldset.form-group.assumed { border-color: #e0b34d; }
  button[type="submit"] { margin-top: 8px; padding: 8px 20px; font-size: 13px; background: #3a6fc4; color: white;
    border: none; border-radius: 5px; cursor: pointer; }
  button[type="submit"]:hover { background: #2f5aa0; }
  .validation-errors { background: #fdecea; border: 1px solid #f3b4ab; color: #8a1f11; padding: 10px 14px;
    border-radius: 6px; margin-top: 16px; max-width: 460px; font-size: 13px; }
  .validation-errors ul { margin: 6px 0 0 0; padding-left: 18px; }
  .validation-ok { background: #eaf7ea; border: 1px solid #a8d8a8; color: #1f6b1f; padding: 10px 14px;
    border-radius: 6px; margin-top: 16px; max-width: 460px; font-size: 13px; font-weight: 600; }
  .oneof-group { max-width: 460px; }
  .oneof-note { color: #888; font-size: 12px; margin: 2px 0 6px 0; }
  .oneof-options { display: flex; gap: 14px; margin-bottom: 10px; }
  .oneof-option { font-weight: 500; font-size: 13px; display: flex; align-items: center; gap: 4px; }
  .oneof-alt { border-left: 3px solid #e8f0fe; padding-left: 12px; margin-bottom: 6px; }
  .conditional-note { color: #3a6fc4; font-size: 12px; margin-top: 3px; font-style: italic; }
  .not-note { color: #a01f1f; font-size: 12px; margin-top: 3px; font-style: italic; }
  .dependent-schema-group { border-left: 3px solid #e8f0fe; padding-left: 12px; margin: 8px 0 14px 0; }
  .dynamic-fields-group { margin-bottom: 16px; border: 1px dashed #ccc; border-radius: 6px; padding: 12px 14px; max-width: 460px; }
  .dynamic-fields-group .group-label { font-weight: 600; margin-bottom: 4px; }
  .dynamic-field-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
  .dynamic-field-row input { flex: 1; padding: 6px 8px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; }
  .dynamic-field-row button, .add-dynamic-field button { padding: 4px 10px; font-size: 12px; border: 1px solid #ccc;
    border-radius: 4px; background: #f5f5f5; cursor: pointer; }
  .add-dynamic-field { display: flex; gap: 8px; margin-top: 6px; }
  .add-dynamic-field input { flex: 1; padding: 6px 8px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; }
  .dynamic-field-error { color: #a01f1f; font-size: 12px; margin-top: 3px; }
  .completeness-summary { background: #f5f7fa; border: 1px solid #dde3ea; border-radius: 8px; padding: 14px 16px;
    margin-bottom: 18px; max-width: 460px; }
  .completeness-summary .percent { font-size: 22px; font-weight: 700; }
  .completeness-summary .percent.full { color: #1f6b1f; }
  .completeness-summary .percent.partial { color: #a06a1f; }
  .completeness-breakdown { display: flex; gap: 18px; margin-top: 8px; font-size: 12px; color: #555; }
  .completeness-breakdown .count { font-weight: 700; display: block; font-size: 15px; }
  .completeness-conformance { margin-top: 10px; padding-top: 8px; border-top: 1px solid #dde3ea;
    font-size: 12px; color: #555; }
  .completeness-conformance strong { color: #8a1f11; }
  .completeness-ack { background: #fff3cd; border: 1px solid #e0c060; border-radius: 6px; padding: 10px 14px;
    margin: 12px 0; max-width: 460px; font-size: 13px; }
  .completeness-ack label { display: flex; align-items: flex-start; gap: 8px; cursor: pointer; }
  .completeness-ack input[type="checkbox"] { margin-top: 2px; }
  .preview-gap { background: #fff8ec; border: 1px solid #e6c778; border-radius: 6px; padding: 10px 12px;
    margin-bottom: 8px; font-size: 13px; }
  .gap-badge { background: #a06a1f; color: white; border-radius: 8px; padding: 2px 8px; font-size: 10px;
    font-weight: 700; text-transform: uppercase; }
  .gap-header { font-weight: 600; margin-top: 6px; }
  .gap-header code { background: #f0e6d0; border-radius: 4px; padding: 1px 5px; font-size: 12px; }
  .gap-line { color: #886a2f; font-size: 12px; margin-top: 2px; }
  .gap-section { margin-top: 8px; }
  .gap-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; color: #9a8355; font-weight: 700; }
  .gap-meaning { margin-top: 2px; }
  .gap-limitation { margin-top: 2px; color: #665; }
  .gap-footer { margin-top: 8px; font-size: 12px; color: #555; }
  .gap-enforced-badge { background: #e2efe2; color: #1f6b1f; border-radius: 8px; padding: 1px 7px; font-size: 10px;
    font-weight: 700; margin-right: 4px; }
  .raw-json-details { margin-top: 8px; }
  .raw-json-details summary { cursor: pointer; font-size: 12px; color: #3a6fc4; }
  .conformance-note { color: #886a2f; font-size: 11px; margin-top: 2px; margin-bottom: 4px; }
  .conformance-pass, .conformance-fail { font-size: 12px; margin-top: 3px; color: #1f6b1f; }
  .conformance-skip { font-size: 12px; margin-top: 2px; color: #886a2f; font-style: italic; }
  .conformance-error { font-size: 12px; margin-top: 3px; color: #555; font-style: italic; }
  .conformance-surprising { font-size: 12px; margin-top: 4px; color: #8a1f11; font-weight: 700;
    background: #fdecea; border: 1px solid #f3b4ab; border-radius: 4px; padding: 6px 8px; }
  .conformance-detail { font-weight: 400; font-family: ui-monospace, monospace; }
"""

# Constructs this deterministic renderer doesn't yet turn into a real form control -- these fall
# back to a labeled raw-JSON block instead of being silently dropped, so "any JSON Schema" stays
# honest about what it can and can't visualize today. oneOf/anyOf are handled separately (see
# _render_one_of) as a real interactive alternative-picker, and `not` is handled as an additional
# validation note/rule on top of a field's normal control (see _not_note) -- neither listed here.
# Schema-level if/then conditionals (definition["allOf"]), dependentRequired, dependentSchemas,
# and patternProperties are also handled separately at the top level (see
# render_schema_form_preview and friends), but any of these nested *inside* a single property's
# own sub-schema (legal JSON Schema, just not something this project's own builder ever produces
# that way) would still land here and fall back to raw JSON -- an internal $ref is resolved and
# rendered inline before reaching this check; only an unresolved (external, or circular) one
# still falls back.
# propertyNames/unevaluatedProperties added after an adversarial review found they were never
# routed to the gap/explanation machinery at all -- a property carrying either alongside
# "properties" fell straight through to the plain fieldset render path, silently scored as
# "fully visualized" even though neither is actually enforced or shown.
# contains/minContains/maxContains/prefixItems added after building conformance-test mode exposed
# the same failure mode again: _is_exotic only caught these via the separate `type == "array"`
# check below, so a property using contains/prefixItems WITHOUT an explicit "type": "array" (legal
# JSON Schema -- these keywords are meaningful for array instances regardless of whether "type" is
# stated) fell straight through undetected, same as the propertyNames/unevaluatedProperties bug.
_EXOTIC_KEYS = ("allOf", "if", "then", "else", "$ref", "patternProperties",
                "dependentRequired", "dependentSchemas", "propertyNames", "unevaluatedProperties",
                "contains", "minContains", "maxContains", "prefixItems")


def _is_exotic(prop) -> bool:
    # JSON Schema allows a bare `true`/`false` (or, from a malformed source, None) as an entire
    # sub-schema -- not a dict at all. There's no form control for that either way, so it falls
    # back to the same raw-JSON path rather than crashing on the .get()/`in` calls below.
    if not isinstance(prop, dict):
        return True
    return prop.get("type") == "array" or any(key in prop for key in _EXOTIC_KEYS)


def _oneof_alt_label(alt: dict, index: int) -> str:
    if alt.get("title"):
        return html.escape(str(alt["title"]))
    props = alt.get("properties")
    if props:
        return html.escape(", ".join(props.keys()))
    return f"Option {index + 1}"


def _pattern_hint(pattern: str) -> str | None:
    """Translates the handful of digit-count patterns this project's own wizard actually
    generates (see ask_text_pattern in wizard.py) into a business-readable phrase, so a
    validation error reads 'must be exactly 6 digits' instead of 'must match ^[0-9]{6}$'. Returns
    None for anything else -- the caller falls back to showing the raw pattern rather than
    guessing at an explanation it can't back up."""
    m = re.fullmatch(r"\^\[0-9\]\{(\d+)\}\$", pattern)
    if m:
        return f"must be exactly {m.group(1)} digit(s)"
    m = re.fullmatch(r"\^\[0-9\]\{(\d+),(\d+)\}\$", pattern)
    if m:
        return f"must be {m.group(1)}-{m.group(2)} digits"
    return None


def _resolve_internal_ref(ref, defs: dict) -> dict | None:
    """Resolves '#/$defs/Name' or '#/definitions/Name' against this schema's own $defs -- the
    only kind of $ref this renderer resolves. An external/cross-document $ref (a URL, a different
    file) is intentionally left unresolved: fetching one would require a network call, breaking
    this project's offline-safety guarantee, and it's the same reasoning models.py already
    documents for why cross-schema references (x-ref-schema) are out of scope for the registry
    service this prototype targets. Returns None for anything it can't resolve -- the caller falls
    back to a labeled raw-JSON block rather than guessing."""
    if not isinstance(ref, str):
        return None
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            target = defs.get(ref[len(prefix):])
            return target if isinstance(target, dict) else None
    return None


def _not_note(not_schema) -> str | None:
    """A plain-language explanation for the common, tractable shapes a `not` constraint takes --
    'must not match a pattern,' 'must not be one of a banned list of exact values.' Returns None
    for anything more exotic (a `not` wrapping its own combinator, say), which still gets
    enforced structurally nowhere but at least doesn't claim an explanation it can't back up."""
    if not isinstance(not_schema, dict):
        return None
    if isinstance(not_schema.get("pattern"), str):
        hint = _pattern_hint(not_schema["pattern"])
        return f"Must NOT {hint}." if hint else f"Must not match the pattern: {html.escape(not_schema['pattern'])}."
    if not_schema.get("const") is not None:
        return f"Must not be exactly '{html.escape(str(not_schema['const']))}'."
    if not_schema.get("enum"):
        values = ", ".join(f"'{html.escape(str(v))}'" for v in not_schema["enum"])
        return f"Must not be any of: {values}."
    return None


def _describe_simple_condition(sub_schema) -> str | None:
    """Best-effort plain-language description of a simple property-based condition (a const or
    enum check on one or more named properties) -- e.g. {"properties": {"status": {"const":
    "APPROVED"}}} -> "status = 'APPROVED'". Used to fill in the *specific* half of an advanced
    construct's business explanation (contains' most common real shape is exactly this: "at least
    one item in this list has some field set to some value"), rather than a fully generic
    placeholder. Returns None if the sub-schema doesn't match this recognizable shape, so the
    caller can fall back to an honest, non-specific explanation instead of guessing at one.

    Returns raw (unescaped) text -- html.escape happens exactly once, at the point this ends up
    embedded in HTML (_render_gap_panel), not here. Escaping here too would double-encode entities
    (a literal '<' would show up as the garbled text '&lt;' instead of '<') and would also corrupt
    the plain-text copy this same string is reused for in wizard.py's CLI printout."""
    if not isinstance(sub_schema, dict):
        return None
    props = sub_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    parts = []
    for prop_name, value_schema in props.items():
        if not isinstance(value_schema, dict):
            continue
        if value_schema.get("const") is not None:
            parts.append(f"{prop_name} = '{value_schema['const']}'")
        elif value_schema.get("enum"):
            values = " or ".join(f"'{v}'" for v in value_schema["enum"])
            parts.append(f"{prop_name} is {values}")
    return " and ".join(parts) if parts else None


_READABLE_TYPE_WORDS = {
    "string": "text", "integer": "whole number", "number": "number",
    "boolean": "yes/no", "object": "grouped", "array": "list",
}


def _explain_advanced_construct(prop, field_name: str | None = None) -> dict:
    """Translates an advanced/unsupported JSON Schema construct into the business-language
    explanation a "preview gap" panel needs: which keyword(s) are involved, what the rule means
    in plain language, and what specifically the interactive preview can't show/enforce about it.
    Never returns nothing -- even a construct with no specific template still gets an honest,
    generic explanation, per this feature's whole point: a business user should always see
    *something* in plain language, never a bare, meaningless keyword name and never silence.

    Every applicable rule found on `prop` is combined into one panel (not "first match wins"):
    a property can legally carry more than one of these keywords at once (prefixItems + contains,
    propertyNames + unevaluatedProperties + patternProperties, contains alongside an unrecognized
    `not`), and an adversarial review found the original first-match design silently dropped every
    keyword but the first -- including losing `not` entirely whenever it co-occurred with any
    other exotic keyword, even though the server still enforces it.

    Returns raw (unescaped) text in "meaning"/"limitation" -- see _describe_simple_condition's
    docstring for why escaping doesn't belong here.

    "specific" marks whether at least one combined rule is backed by the schema's own actual
    values (a real field name, a real pattern, a real count) -- the "partial, with a real
    explanation" vs "none, only a generic placeholder" distinction the completeness score uses."""
    if not isinstance(prop, dict):
        return {"keywords": ["(unrecognized)"],
                "meaning": "This field has an advanced rule that can't be described further.",
                "limitation": "The preview cannot show or enforce this at all.", "specific": False}

    subject = f"'{field_name}'" if field_name else "this list"
    rules: list[tuple[list[str], str, str, bool]] = []

    if "contains" in prop or "minContains" in prop or "maxContains" in prop:
        keywords = [k for k in ("contains", "minContains", "maxContains") if k in prop]
        if "contains" in prop:
            condition = _describe_simple_condition(prop["contains"])
            n = prop.get("minContains", 1)
            if condition:
                meaning = f"At least {n} item(s) in {subject} must have {condition}."
                specific = True
            else:
                meaning = f"At least {n} item(s) in {subject} must satisfy an additional rule."
                specific = False
            if prop.get("maxContains") is not None:
                meaning += f" No more than {prop['maxContains']} item(s) may satisfy it."
        else:
            # minContains/maxContains with no sibling "contains": per the JSON Schema spec, these
            # keywords have NO EFFECT at all without "contains" alongside them. Saying anything
            # else here would assert an enforced rule that doesn't actually exist -- the opposite
            # of this feature's whole point.
            meaning = ("minContains/maxContains are set here, but without a 'contains' rule "
                       "alongside them, they don't currently restrict anything.")
            specific = True
        rules.append((keywords, meaning,
                       "The preview cannot check whether the items you enter here actually "
                       "satisfy this rule.", specific))

    if "prefixItems" in prop:
        items = prop.get("prefixItems")
        n = len(items) if isinstance(items, list) else None
        meaning = (f"This must be a list of exactly {n} items, each in a specific order, with its "
                   "own expected shape.") if n is not None else \
            "This must be a list with a fixed number of items in a specific order."
        rules.append((["prefixItems"], meaning,
                       "The preview cannot show or enforce the individual shape of each position "
                       "in the list.", n is not None))

    if "propertyNames" in prop:
        pn = prop["propertyNames"]
        pattern = pn.get("pattern") if isinstance(pn, dict) else None
        if pattern:
            hint = _pattern_hint(pattern)
            meaning = f"Field names you add here {hint}." if hint else \
                f"Field names you add here must match the pattern: {pattern}."
            specific = True
        else:
            meaning = "Field names themselves must follow a specific rule."
            specific = False
        rules.append((["propertyNames"], meaning,
                       "The preview cannot enforce naming rules on custom field names you might "
                       "add.", specific))

    if "unevaluatedProperties" in prop:
        uep = prop["unevaluatedProperties"]
        if uep is False:
            meaning = "No extra fields are allowed beyond what's explicitly defined here."
            specific = True
        elif uep is True:
            # true means this keyword has NO restrictive effect -- the same as omitting it
            # entirely. Saying "restricted" here would be backwards.
            meaning = "Extra fields ARE allowed here without restriction (this rule has no effect as written)."
            specific = True
        else:
            meaning = "Extra fields are allowed here, but only if they match a specific additional shape."
            specific = False
        rules.append((["unevaluatedProperties"], meaning,
                       "The preview cannot detect or block extra fields you might try to add.", specific))

    if "not" in prop and _not_note(prop["not"]) is None:
        # Only reached when _not_note's recognized pattern/const/enum shapes don't apply -- a
        # `not` wrapping its own combinator or a nested condition. If `not` co-occurs with any
        # other rule above, it must still show up here rather than being silently dropped.
        rules.append((["not"], "A specific value or combination of values is not allowed here.",
                       "The preview cannot describe exactly which combination is banned.", False))

    if "$ref" in prop:
        rules.append((["$ref"], "This field's shape is defined in another document.",
                       "The preview cannot fetch or show that document (it works fully offline).", False))

    if "patternProperties" in prop:
        pp = prop["patternProperties"]
        if isinstance(pp, dict) and pp:
            hints = [_pattern_hint(p) or f"match '{p}'" for p in pp.keys()]
            meaning = f"Extra fields whose name {' or '.join(hints)} may be added here."
            specific = True
        else:
            meaning = "Extra fields matching a name pattern may be added here."
            specific = False
        rules.append((["patternProperties"], meaning,
                       "The preview only supports this as a top-level rule, not nested this deep.", specific))

    if "dependentRequired" in prop or "dependentSchemas" in prop:
        rules.append(([k for k in ("dependentRequired", "dependentSchemas") if k in prop],
                       "Filling in one field here makes other fields required, or adds new ones.",
                       "The preview only supports this as a top-level rule, not nested this deep.", False))

    if "allOf" in prop:
        rules.append((["allOf"], "This combines multiple additional rules together.",
                       "The preview cannot break down or enforce what each combined rule requires.", False))

    if not rules and prop.get("type") == "array":
        items = prop.get("items")
        item_type = items.get("type") if isinstance(items, dict) else None
        readable = _READABLE_TYPE_WORDS.get(item_type)
        meaning = f"This must be a list of {readable} items." if readable else "This must be a list of items."
        rules.append((["array"], meaning,
                       "The preview doesn't yet offer an add/remove list editor for this -- shown "
                       "as raw data only.", bool(readable)))

    if not rules:
        rules.append((["(advanced rule)"],
                       "This field has an advanced rule that can't be described further.",
                       "The preview cannot show or enforce this at all.", False))

    keywords: list[str] = []
    for kw_list, _, _, _ in rules:
        for kw in kw_list:
            if kw not in keywords:
                keywords.append(kw)
    limitations: list[str] = []
    for _, _, limitation, _ in rules:
        if limitation not in limitations:
            limitations.append(limitation)
    return {
        "keywords": keywords,
        "meaning": " ".join(m for _, m, _, _ in rules),
        "limitation": " ".join(limitations),
        "specific": any(s for _, _, _, s in rules),
    }


def _build_validation_node(name: str, prop: dict, required_here: bool, id_prefix: str = "",
                            defs: dict | None = None, ref_chain: tuple = ()) -> dict | None:
    """Builds the tree the embedded client-side validator walks on submit -- a parallel,
    simplified structure to the one _render_field builds for display, keyed the same way
    (dotted ids matching the actual <input>/<select> element ids) so the two stay in lockstep.
    Returns None for exotic/unsupported constructs: there's no real form control to validate yet
    for those (rendered instead as a labeled raw-JSON note), so silently skipping them here is
    consistent with what's actually shown, not a silent gap."""
    field_id = f"{id_prefix}{name}"

    if not isinstance(prop, dict):
        return None

    defs = defs or {}
    if "$ref" in prop and prop["$ref"] not in ref_chain:
        resolved = _resolve_internal_ref(prop["$ref"], defs)
        if resolved is not None:
            return _build_validation_node(name, resolved, required_here, id_prefix=id_prefix,
                                           defs=defs, ref_chain=ref_chain + (prop["$ref"],))

    if "oneOf" in prop or "anyOf" in prop:
        alternatives = prop.get("oneOf") or prop.get("anyOf") or []
        alt_nodes = []
        for i, alt in enumerate(alternatives):
            if not isinstance(alt, dict):
                alt_nodes.append({"children": []})
                continue
            alt_required = set(alt.get("required") or [])
            children = [
                node for sub_name, sub_prop in alt.get("properties", {}).items()
                if (node := _build_validation_node(sub_name, sub_prop, sub_name in alt_required,
                                                    id_prefix=f"{field_id}.alt{i}.", defs=defs,
                                                    ref_chain=ref_chain)) is not None
            ]
            alt_nodes.append({"children": children})
        return {"kind": "oneOf", "radioName": f"{field_id}__choice", "alternatives": alt_nodes}

    if _is_exotic(prop):
        return None

    if prop.get("type") == "object" and prop.get("properties"):
        nested_required = set(prop.get("required") or [])
        children = [
            node for sub_name, sub_prop in prop["properties"].items()
            if (node := _build_validation_node(sub_name, sub_prop, sub_name in nested_required,
                                                id_prefix=f"{field_id}.", defs=defs,
                                                ref_chain=ref_chain)) is not None
        ]
        return {"kind": "group", "children": children}

    node = {"kind": "field", "id": field_id, "label": html.escape(name), "type": prop.get("type"),
            "required": required_here}
    if isinstance(prop.get("pattern"), str):
        node["pattern"] = prop["pattern"]
        hint = _pattern_hint(prop["pattern"])
        if hint:
            node["patternHint"] = hint
    if prop.get("minLength") is not None:
        node["minLength"] = prop["minLength"]
    if prop.get("maxLength") is not None:
        node["maxLength"] = prop["maxLength"]
    if prop.get("minimum") is not None:
        node["minimum"] = prop["minimum"]
    if prop.get("maximum") is not None:
        node["maximum"] = prop["maximum"]
    not_schema = prop.get("not")
    if isinstance(not_schema, dict):
        if isinstance(not_schema.get("pattern"), str):
            node["notPattern"] = not_schema["pattern"]
        if not_schema.get("const") is not None:
            node["notConst"] = not_schema["const"]
        if not_schema.get("enum"):
            node["notEnum"] = not_schema["enum"]
    return node


def _iter_conditionals(definition: dict):
    """Yields (trigger_field, trigger_value, then_required) for each schema-level if/then block
    in definition["allOf"] -- the shape SchemaBuilder.add_conditional produces. Trigger field is
    assumed top-level (matching what add_conditional currently supports); a conditional with no
    recognizable if.properties entry is skipped rather than guessed at."""
    for block in definition.get("allOf") or []:
        if not isinstance(block, dict):
            continue
        if_props = (block.get("if") or {}).get("properties") or {}
        if not if_props:
            continue
        trigger_field, trigger_schema = next(iter(if_props.items()))
        trigger_value = trigger_schema.get("const")
        then_required = (block.get("then") or {}).get("required") or []
        yield trigger_field, trigger_value, then_required


def _iter_dependent_required(definition: dict):
    """Yields (trigger_field, then_required) for each entry in definition["dependentRequired"] --
    the shape SchemaBuilder.add_dependent_required produces. Simpler than a conditional: no
    specific triggering value, the dependent fields become required the moment the trigger field
    is filled in at all."""
    for trigger_field, then_required in (definition.get("dependentRequired") or {}).items():
        yield trigger_field, list(then_required or [])


def _build_conditional_notes(definition: dict) -> dict:
    """A plain-language explanation per dependent field -- 'Required only when X = Y' or
    'Required only when X is filled in' -- so a business user reading the rendered form
    understands *why* a field might become required, not just that it sometimes does. Keyed by
    field id, same as field_confidence."""
    notes: dict = {}
    for trigger_field, trigger_value, then_required in _iter_conditionals(definition):
        for dep in then_required:
            notes.setdefault(dep, []).append(
                f"Required only when '{html.escape(str(trigger_field))}' = "
                f"'{html.escape(str(trigger_value))}'.")
    for trigger_field, then_required in _iter_dependent_required(definition):
        for dep in then_required:
            notes.setdefault(dep, []).append(
                f"Required only when '{html.escape(str(trigger_field))}' is filled in.")
    return notes


def _build_conditional_specs(definition: dict) -> list[dict]:
    """The data the embedded JS needs to evaluate conditionals live -- both to toggle the
    required-marker as the user types/selects, and to enforce it for real on Submit. A
    dependentRequired entry carries no triggerValue -- the JS treats "any non-empty value" as
    satisfying it, rather than matching a specific constant."""
    specs = [
        {"triggerId": trigger_field, "triggerValue": trigger_value, "thenRequired": list(then_required)}
        for trigger_field, trigger_value, then_required in _iter_conditionals(definition)
    ]
    specs.extend(
        {"triggerId": trigger_field, "presence": True, "thenRequired": then_required}
        for trigger_field, then_required in _iter_dependent_required(definition)
    )
    return specs


def _iter_dependent_schemas(definition: dict):
    """Yields (trigger_field, properties, required) for each entry in
    definition["dependentSchemas"] -- the shape SchemaBuilder.add_dependent_schema produces.
    dependentRequired's more general cousin: presence of trigger_field pulls in whole *new*
    fields (not already part of the base properties), not just new required-ness on existing
    ones."""
    for trigger_field, block in (definition.get("dependentSchemas") or {}).items():
        if not isinstance(block, dict):
            continue
        yield trigger_field, block.get("properties") or {}, list(block.get("required") or [])


def _render_dependent_schema_group(trigger_field: str, properties: dict, required: list,
                                    field_confidence: dict | None, defs: dict | None) -> str:
    """Renders a dependentSchemas entry's fields wrapped in a container the embedded JS shows
    only once the trigger field has any value -- these are fields that don't exist in the base
    form at all until then, unlike dependentRequired's 'existing optional field becomes
    required.'"""
    required_set = set(required)
    inner = "".join(
        _render_field(sub_name, sub_prop, sub_name in required_set, id_prefix=f"{trigger_field}__dep.",
                      field_confidence=field_confidence, defs=defs)
        for sub_name, sub_prop in properties.items())
    note = f"These additional fields apply because '{html.escape(trigger_field)}' is filled in."
    # trigger_field is a raw property name here (needed unescaped just above, for id_prefix -- it
    # has to match the unescaped ids _build_validation_node/_render_field build elsewhere); only
    # this attribute-embedding use needs its own escaped copy.
    trigger_field_attr = html.escape(trigger_field, quote=True)
    return (f'<div id="{trigger_field_attr}__dependentSchema" class="dependent-schema-group" style="display:none">'
            f'<div class="conditional-note">{note}</div>{inner}</div>')


def _build_dependent_schema_validation_nodes(definition: dict, defs: dict | None) -> list[dict]:
    nodes = []
    for trigger_field, properties, required in _iter_dependent_schemas(definition):
        required_set = set(required)
        children = [
            node for sub_name, sub_prop in properties.items()
            if (node := _build_validation_node(sub_name, sub_prop, sub_name in required_set,
                                                id_prefix=f"{trigger_field}__dep.", defs=defs)) is not None
        ]
        nodes.append({"kind": "dependentSchema", "triggerId": trigger_field, "children": children})
    return nodes


def _build_pattern_properties_specs(definition: dict) -> list[dict]:
    """One entry per patternProperties pattern -- 'any field whose name matches this regex must
    hold this kind of value.' Unlike every other construct, there's no fixed set of field ids to
    pre-render: the property *names* aren't known until a business user adds one while playing
    with the form, so this only carries what the embedded JS needs to build/validate a field on
    demand (type, pattern, length/range bounds), not the fields themselves."""
    specs = []
    for i, (pattern, value_schema) in enumerate((definition.get("patternProperties") or {}).items()):
        if not isinstance(value_schema, dict):
            value_schema = {}
        vs: dict = {"type": value_schema.get("type")}
        if isinstance(value_schema.get("pattern"), str):
            vs["pattern"] = value_schema["pattern"]
            hint = _pattern_hint(value_schema["pattern"])
            if hint:
                vs["patternHint"] = hint
        if value_schema.get("minLength") is not None:
            vs["minLength"] = value_schema["minLength"]
        if value_schema.get("maxLength") is not None:
            vs["maxLength"] = value_schema["maxLength"]
        if value_schema.get("minimum") is not None:
            vs["minimum"] = value_schema["minimum"]
        if value_schema.get("maximum") is not None:
            vs["maximum"] = value_schema["maximum"]
        if value_schema.get("enum"):
            vs["enum"] = [str(v) for v in value_schema["enum"]]
        specs.append({"index": i, "pattern": pattern, "valueSchema": vs})
    return specs


def _render_pattern_properties_group(spec: dict) -> str:
    i = spec["index"]
    pattern_display = html.escape(spec["pattern"])
    return (f'<div class="dynamic-fields-group">'
            f'<div class="group-label">Custom fields (name must match: {pattern_display})</div>'
            f'<div id="patternProps{i}__list"></div>'
            f'<div class="add-dynamic-field">'
            f'<input type="text" id="patternProps{i}__nameInput" placeholder="field name">'
            f'<button type="button" onclick="addDynamicField({i})">Add field</button>'
            f'</div>'
            f'<div id="patternProps{i}__error" class="dynamic-field-error"></div>'
            f'</div>')


def _render_conformance_section(conformance_result: dict) -> str:
    """The 'Conformance check' block inside a gap panel -- real, executed pass/fail evidence from
    running the same jsonschema.Draft202012Validator this project already uses to check schema
    syntax against a concrete example built to satisfy the rule and one built to violate it (see
    conformance.py). Turns "the server still enforces this" from a claim into a demonstration.

    A "surprising" check (the validator disagreed with what the example was built to show) is the
    single most valuable thing this feature can surface, so it gets its own visibly distinct,
    unmissable style rather than blending in with a normal PASS/FAIL line."""
    if not conformance_result.get("attempted"):
        reason = conformance_result.get("skipped_reason")
        note = (f"Could not auto-generate a conformance test for this rule ({html.escape(reason)})."
                if reason else "No automatic conformance test is available for this rule yet.")
        return (
            '<div class="gap-section"><div class="gap-label">Conformance check</div>'
            f'<div class="conformance-skip">{note}</div></div>'
        )
    lines = []
    for check in conformance_result["checks"]:
        kind_label = "An example that satisfies this rule" if check["kind"] == "positive" \
            else "An example that violates this rule"
        if check.get("errored"):
            # The validator itself raised instead of returning pass/fail -- this must never be
            # folded into a confident PASS/FAIL line (a real bug an adversarial review found:
            # actual_valid=None is falsy, so this used to silently print "FAIL, as expected").
            errors = "; ".join(check["errors"])
            lines.append(
                '<div class="conformance-error">? ' + html.escape(f"{kind_label} could not be validated.") +
                (f' <span class="conformance-detail">{html.escape(errors)}</span>' if errors else '') + '</div>'
            )
        elif check.get("inconclusive"):
            # actual_valid disagreed (or matched for the wrong reason), but every relevant
            # jsonschema error is attributed to some OTHER keyword on this same field, not the one
            # this check is actually testing -- an adversarial review found several real cases
            # (uniqueItems, minItems, additionalProperties, a hidden allOf/oneOf requirement, a
            # co-occurring construct this module couldn't also synthesize for) where reporting
            # this as "surprising" would falsely implicate a rule that's actually working fine.
            unrelated = ", ".join(check.get("unrelated_keywords") or [])
            lines.append(
                '<div class="conformance-skip">' +
                html.escape(f"{kind_label} could not be confirmed here -- the example also needs to satisfy "
                            f"other rules on this field ({unrelated}) that this check doesn't account for.")
                + '</div>'
            )
        elif check["surprising"]:
            outcome = ("was expected to PASS but the validator marked it FAIL" if check["expected_valid"]
                       else "was expected to FAIL but the validator marked it PASS")
            errors = "; ".join(check["errors"])
            lines.append(
                '<div class="conformance-surprising">&#9888; ' + html.escape(f"{kind_label} {outcome}.") +
                (f' <span class="conformance-detail">{html.escape(errors)}</span>' if errors else '') +
                ' This rule may not behave exactly as described above.</div>'
            )
        else:
            result_word = "PASS" if check["actual_valid"] else "FAIL"
            css_class = "conformance-pass" if check["actual_valid"] else "conformance-fail"
            lines.append(f'<div class="{css_class}">&#10003; {html.escape(kind_label)} was validated: '
                          f'<strong>{result_word}</strong>, as expected.</div>')
    return (
        '<div class="gap-section"><div class="gap-label">Conformance check</div>'
        '<div class="conformance-note">Validated against the same JSON Schema Draft 2020-12 validator '
        'used to check your schema\'s syntax.</div>' + "".join(lines) + '</div>'
    )


def _render_gap_panel(gap: dict) -> str:
    """The 'preview gap' panel itself -- badge, which keyword(s) are involved, the business
    explanation, what the preview can't show, real conformance-test evidence when available, and
    the reassurance that the Registry Service still enforces it regardless. Never silently hidden:
    this is the PRIMARY content wherever a construct falls back to raw JSON, not an afterthought
    next to it."""
    keywords_display = " + ".join(gap["keywords"])
    return (
        '<div class="preview-gap">'
        '<span class="gap-badge">Needs review</span>'
        f'<div class="gap-header">Advanced rule not fully visualized <code>{html.escape(keywords_display)}</code></div>'
        '<div class="gap-line">The form preview cannot enforce this rule visually.</div>'
        '<div class="gap-section"><div class="gap-label">What this rule means</div>'
        f'<div class="gap-meaning">{html.escape(gap["meaning"])}</div></div>'
        '<div class="gap-section"><div class="gap-label">What the preview cannot show</div>'
        f'<div class="gap-limitation">{html.escape(gap["limitation"])}</div></div>'
        + _render_conformance_section(gap.get("conformance") or {"attempted": False, "skipped_reason": None}) +
        '<div class="gap-footer"><span class="gap-enforced-badge">Stored &amp; validated</span>'
        'This rule will still be enforced by the Registry Service.</div>'
        '</div>'
    )


def _gap_dict(field_id: str, prop, field_name: str | None) -> dict:
    """Builds one gap-panel entry: the business-language explanation plus its "conformance" probe
    result (real pass/fail evidence, or an honest reason none could be generated -- see
    conformance.probe_gap). Every gap dict has this key so downstream rendering never needs a
    fallback for its absence."""
    return {"field_id": field_id, **_explain_advanced_construct(prop, field_name=field_name),
            "conformance": conformance.probe_gap(prop)}


def _scan_preview_gaps(name: str, prop, id_prefix: str = "", defs: dict | None = None,
                        ref_chain: tuple = ()) -> list[dict]:
    """Recursively finds every construct the interactive form can't fully visualize/enforce,
    walking the *same* tree _render_field renders (same $ref resolution, same oneOf traversal,
    same nested-object recursion, same id scheme) so every gap the renderer actually produces
    gets counted -- and, just as importantly, catches a `not` that isn't one of _not_note's
    recognized simple shapes even on an otherwise fully-rendered field, which would otherwise be
    silently omitted rather than flagged."""
    field_id = f"{id_prefix}{name}"

    if not isinstance(prop, dict):
        return [_gap_dict(field_id, prop, name)]

    defs = defs or {}
    if "$ref" in prop and prop["$ref"] not in ref_chain:
        resolved = _resolve_internal_ref(prop["$ref"], defs)
        if resolved is not None:
            merged = dict(resolved)
            return _scan_preview_gaps(name, merged, id_prefix=id_prefix, defs=defs,
                                       ref_chain=ref_chain + (prop["$ref"],))
        return [_gap_dict(field_id, prop, name)]

    if "oneOf" in prop or "anyOf" in prop:
        gaps = []
        for i, alt in enumerate(prop.get("oneOf") or prop.get("anyOf") or []):
            if not isinstance(alt, dict):
                gaps.append(_gap_dict(f"{field_id}__alt{i}", alt, name))
                continue
            for sub_name, sub_prop in alt.get("properties", {}).items():
                gaps.extend(_scan_preview_gaps(sub_name, sub_prop, id_prefix=f"{field_id}.alt{i}.",
                                                defs=defs, ref_chain=ref_chain))
        not_schema = prop.get("not")
        if not_schema is not None and _not_note(not_schema) is None:
            gaps.append(_gap_dict(field_id, {"not": not_schema}, name))
        return gaps

    if _is_exotic(prop):
        return [_gap_dict(field_id, prop, name)]

    if prop.get("type") == "object" and prop.get("properties"):
        gaps = []
        for sub_name, sub_prop in prop["properties"].items():
            gaps.extend(_scan_preview_gaps(sub_name, sub_prop, id_prefix=f"{field_id}.",
                                            defs=defs, ref_chain=ref_chain))
        not_schema = prop.get("not")
        if not_schema is not None and _not_note(not_schema) is None:
            gaps.append(_gap_dict(field_id, {"not": not_schema}, name))
        return gaps

    # A normal, fully-rendered leaf control -- its own `not` (if present) might still not be one
    # of the recognized simple shapes, which would otherwise vanish silently.
    not_schema = prop.get("not")
    if not_schema is not None and _not_note(not_schema) is None:
        return [_gap_dict(field_id, {"not": not_schema}, name)]
    return []


def _scan_schema_level_gaps(definition: dict) -> list[dict]:
    """Schema-level (not per-property) gaps -- currently just allOf blocks that don't match the
    recognizable if/then-with-properties shape _iter_conditionals expects. Those would otherwise
    be silently skipped (see _iter_conditionals's own "skipped rather than guessed at" comment)
    instead of surfaced."""
    gaps = []
    for i, block in enumerate(definition.get("allOf") or []):
        if_props = (block.get("if") or {}).get("properties") if isinstance(block, dict) else None
        if isinstance(if_props, dict) and if_props:
            continue  # a recognized if/then conditional -- fully handled elsewhere, not a gap
        gaps.append(_gap_dict(f"__allOf{i}", {"allOf": [block]}, None))
    return gaps


def _count_total_nodes(name: str, prop, defs: dict | None = None, ref_chain: tuple = ()) -> int:
    """Counts every property node the same way _scan_preview_gaps walks them, so the
    "how much of the schema is this" denominator behind the completeness percentage matches what
    was actually scanned, not a different notion of "field count"."""
    if not isinstance(prop, dict):
        return 1
    defs = defs or {}
    if "$ref" in prop and prop["$ref"] not in ref_chain:
        resolved = _resolve_internal_ref(prop["$ref"], defs)
        if resolved is not None:
            return _count_total_nodes(name, resolved, defs=defs, ref_chain=ref_chain + (prop["$ref"],))
        return 1
    if "oneOf" in prop or "anyOf" in prop:
        total = 1
        for alt in (prop.get("oneOf") or prop.get("anyOf") or []):
            if not isinstance(alt, dict):
                total += 1
                continue
            for sub_name, sub_prop in alt.get("properties", {}).items():
                total += _count_total_nodes(sub_name, sub_prop, defs=defs, ref_chain=ref_chain)
        return total
    # Must check _is_exotic BEFORE recursing into "properties", matching the exact order
    # _scan_preview_gaps and _render_field both use -- otherwise an object that also carries an
    # exotic keyword (e.g. patternProperties nested one level in) gets its sub-fields counted as
    # if they were individually rendered, when the real render collapses the whole node into one
    # raw-JSON block. Found by adversarial review: this mismatch overstated completeness.
    if _is_exotic(prop):
        return 1
    if prop.get("type") == "object" and prop.get("properties"):
        total = 1
        for sub_name, sub_prop in prop["properties"].items():
            total += _count_total_nodes(sub_name, sub_prop, defs=defs, ref_chain=ref_chain)
        return total
    return 1


def _compute_preview_completeness(definition: dict, defs: dict) -> dict:
    """The "Form Preview XX% complete" score: walks every field (recursively, mirroring the
    renderer's own tree exactly) plus every schema-level rule (conditionals, dependentRequired,
    dependentSchemas, patternProperties -- each of these is fully interactive today, so counts
    toward "full"; an allOf block that doesn't match a recognized conditional shape counts as a
    gap instead), classifying each as fully visualized+enforced ("full"), given a specific
    business explanation but not interactively enforced ("partial"), or only a generic
    unexplained fallback ("none" -- should be rare to never, now that every known advanced
    keyword has a real template)."""
    properties = definition.get("properties", {}) if isinstance(definition, dict) else {}
    field_gaps = []
    field_total = 0
    for name, prop in properties.items():
        field_total += _count_total_nodes(name, prop, defs=defs)
        field_gaps.extend(_scan_preview_gaps(name, prop, defs=defs))

    schema_level_gaps = _scan_schema_level_gaps(definition)
    recognized_schema_rules = (
        len(list(_iter_conditionals(definition))) + len(list(_iter_dependent_required(definition))) +
        len(list(_iter_dependent_schemas(definition))) + len(definition.get("patternProperties") or {})
    )
    schema_level_total = recognized_schema_rules + len(schema_level_gaps)

    total = field_total + schema_level_total
    gaps = field_gaps + schema_level_gaps
    partial = sum(1 for g in gaps if g.get("specific"))
    none_count = len(gaps) - partial
    full = max(total - len(gaps), 0)
    percent = round(100 * full / total) if total else 100
    conformance_summary = conformance.summarize_conformance([g["conformance"] for g in gaps])
    return {"full": full, "partial": partial, "none": none_count, "total": total,
            "percent": percent, "gaps": gaps, "conformance_summary": conformance_summary}


def get_preview_completeness(definition: dict) -> dict:
    """Public entry point for callers (wizard.py's CLI output) that want the completeness score
    without rendering a full page -- extracts $defs the same way render_schema_form_preview does
    internally, so the two stay in lockstep."""
    if not isinstance(definition, dict):
        gaps = [_gap_dict("(schema)", definition, None)]
        return {"full": 0, "partial": 0, "none": 1, "total": 1, "percent": 0, "gaps": gaps,
                "conformance_summary": conformance.summarize_conformance([g["conformance"] for g in gaps])}
    defs = definition.get("$defs") or definition.get("definitions") or {}
    return _compute_preview_completeness(definition, defs)


def _render_completeness_summary(completeness: dict) -> str:
    percent = completeness["percent"]
    percent_class = "full" if percent == 100 else "partial"
    conformance_line = ""
    cs = completeness.get("conformance_summary")
    if cs and cs["executed"]:
        surprising_note = (f' -- <strong>{cs["surprising"]} surprising</strong>' if cs["surprising"] else "")
        errored_note = f', {cs["errored"]} could not be validated' if cs.get("errored") else ""
        inconclusive_note = f', {cs["inconclusive"]} inconclusive' if cs.get("inconclusive") else ""
        conformance_line = (
            '<div class="completeness-conformance">'
            f'Conformance checks: {cs["executed"]} executed across {cs["gaps_with_tests"]} rule(s), '
            f'{cs["passed_as_expected"]} behaved as expected{errored_note}{inconclusive_note}{surprising_note}.</div>'
        )
    return (
        '<div class="completeness-summary">'
        f'<div>Form Preview <span class="percent {percent_class}">{percent}% complete</span></div>'
        '<div class="completeness-breakdown">'
        f'<div><span class="count">{completeness["full"]}</span>Fully visualized</div>'
        f'<div><span class="count">{completeness["partial"]}</span>Explained here (server still enforces it)</div>'
        f'<div><span class="count">{completeness["none"]}</span>Not explained (server still enforces it)</div>'
        '</div>' + conformance_line + '</div>'
    )


def _control_html(field_id: str, prop: dict) -> str:
    # field_id lands raw in id=/name= attributes below -- it's built from a property name, which
    # can be anything a free-text LLM drafting session's add_raw_property/define_reusable_schema
    # tools produce, not just the wizard's own controlled vocabulary. Escaped once here (this
    # function only ever uses field_id for HTML-attribute embedding, never for a dict lookup or
    # recursive id_prefix), same principle as `label` in _render_field.
    field_id = html.escape(field_id, quote=True)
    type_ = prop.get("type")
    enum = prop.get("enum")
    if enum:
        # enum values are legally any JSON type (numbers, booleans, null), not just strings --
        # str() first so html.escape (which requires a real string) doesn't crash on one.
        options = "".join(
            f'<option value="{html.escape(str(v), quote=True)}">{html.escape(str(v))}</option>' for v in enum)
        return f'<select id="{field_id}" name="{field_id}"><option value="">-- choose --</option>{options}</select>'
    if type_ == "boolean":
        return f'<input type="checkbox" id="{field_id}" name="{field_id}">'
    if type_ in ("integer", "number"):
        attrs = []
        if prop.get("minimum") is not None:
            attrs.append(f'min="{prop["minimum"]}"')
        if prop.get("maximum") is not None:
            attrs.append(f'max="{prop["maximum"]}"')
        attrs.append('step="1"' if type_ == "integer" else 'step="any"')
        return f'<input type="number" id="{field_id}" name="{field_id}" {" ".join(attrs)}>'
    if type_ == "string" and prop.get("format") == "date":
        return f'<input type="date" id="{field_id}" name="{field_id}">'
    attrs = []
    if isinstance(prop.get("pattern"), str):
        attrs.append(f'pattern="{html.escape(prop["pattern"], quote=True)}"')
    if prop.get("minLength") is not None:
        attrs.append(f'minlength="{prop["minLength"]}"')
    if prop.get("maxLength") is not None:
        attrs.append(f'maxlength="{prop["maxLength"]}"')
    return f'<input type="text" id="{field_id}" name="{field_id}" {" ".join(attrs)}>'


def _render_one_of(field_id: str, label: str, required_marker: str, assumed_class: str,
                    assumed_badge: str, help_html: str, prop: dict, field_confidence: dict | None,
                    conditional_notes: dict | None, defs: dict | None = None, ref_chain: tuple = (),
                    name: str | None = None) -> str:
    """Renders oneOf/anyOf as a real radio-button switcher between alternative shapes -- 'choose
    ONE of the following ways to provide this' -- rather than a raw-JSON fallback, since this is
    exactly the kind of construct a business user needs to *play with* to understand ("if I pick
    email, do I still need to fill in phone?"), not just read about. Treats anyOf the same as
    oneOf (a single-choice picker) -- anyOf's true "at least one, possibly more" semantics are a
    subtlety not worth modeling in this interactive preview; every real-world use of either
    construct at the field level found so far is really "pick one alternative shape."""
    alternatives = prop.get("oneOf") or prop.get("anyOf") or []
    options_html = []
    panels_html = []
    # field_id is a raw property name/path (needed unescaped below, where it's used as the
    # id_prefix for a recursive _render_field call -- that has to match the unescaped ids
    # _build_validation_node builds separately for the JS validator to look up). Every place it's
    # embedded directly into HTML source here instead needs its own escaped copy: once for a plain
    # attribute value, and once JSON-encoded-then-HTML-escaped for the onchange handler, since that
    # string has to survive both HTML-attribute parsing and, after the browser decodes it, JS
    # string-literal parsing.
    field_id_attr = html.escape(field_id, quote=True)
    field_id_js = html.escape(json.dumps(field_id))
    for i, alt in enumerate(alternatives):
        if not isinstance(alt, dict):
            # A bare true/false alternative (legal but bizarre JSON Schema) -- no sub-fields to
            # render. _scan_preview_gaps already counts this as its own gap
            # (field_id="{field_id}__alt{i}"), but nothing previously rendered the actual gap
            # panel here -- only a raw-JSON <details> -- found by an adversarial review's
            # wiring-consistency lens: the completeness summary and the visible page disagreed.
            alt_label = f"Option {i + 1}"
            checked = " checked" if i == 0 else ""
            options_html.append(
                f'<label class="oneof-option"><input type="radio" name="{field_id_attr}__choice" value="{i}"{checked} '
                f'onchange="toggleOneOf({field_id_js}, {i}, {len(alternatives)})"> {alt_label}</label>')
            display = "block" if i == 0 else "none"
            alt_gap = {**_explain_advanced_construct(alt, field_name=name),
                       "conformance": conformance.probe_gap(alt)}
            panels_html.append(
                f'<div id="{field_id_attr}__alt{i}" class="oneof-alt" style="display:{display}">'
                f'{_render_gap_panel(alt_gap)}'
                f'<details class="raw-json-details"><summary>View raw JSON Schema for this alternative</summary>'
                f'<pre class="raw-json">{html.escape(json.dumps(alt))}</pre></details></div>')
            continue
        alt_label = _oneof_alt_label(alt, i)
        checked = " checked" if i == 0 else ""
        options_html.append(
            f'<label class="oneof-option"><input type="radio" name="{field_id_attr}__choice" value="{i}"{checked} '
            f'onchange="toggleOneOf({field_id_js}, {i}, {len(alternatives)})"> {alt_label}</label>')
        display = "block" if i == 0 else "none"
        alt_required = set(alt.get("required") or [])
        inner = "".join(
            _render_field(sub_name, sub_prop, sub_name in alt_required, id_prefix=f"{field_id}.alt{i}.",
                          field_confidence=field_confidence, conditional_notes=conditional_notes,
                          defs=defs, ref_chain=ref_chain)
            for sub_name, sub_prop in alt.get("properties", {}).items())
        panels_html.append(f'<div id="{field_id_attr}__alt{i}" class="oneof-alt" style="display:{display}">{inner}</div>')

    return (f'<div class="form-field oneof-group{assumed_class}">'
            f'<label>{label}{required_marker}{assumed_badge}</label>'
            f'<div class="oneof-note">Choose ONE of the following:</div>'
            f'<div class="oneof-options">{"".join(options_html)}</div>'
            f'{"".join(panels_html)}'
            f'{help_html}</div>')


def _render_field(name: str, prop: dict, required_here: bool, id_prefix: str = "",
                   field_confidence: dict | None = None, conditional_notes: dict | None = None,
                   defs: dict | None = None, ref_chain: tuple = ()) -> str:
    field_id = f"{id_prefix}{name}"
    # field_id stays raw throughout this function -- it's a dict key (conditional_notes/
    # field_confidence lookups below) and the id_prefix for recursive calls, both of which must
    # match the unescaped ids _build_validation_node builds separately for the JS validator. Only
    # the two places it's embedded directly as HTML source (the required-marker span id, and the
    # label's for=) get an escaped copy -- everywhere else, _control_html/_render_one_of/
    # _render_dependent_schema_group each escape it themselves at the point they emit HTML.
    field_id_attr = html.escape(field_id, quote=True)
    label = html.escape(name)

    if not isinstance(prop, dict):
        # JSON Schema allows a bare `true`/`false` as an entire sub-schema (meaning "anything
        # goes" / "nothing is allowed") -- no .get() calls are safe on it, so it's an immediate,
        # honest fallback (a business-language gap panel, not a crash) rather than a raw dump.
        raw = json.dumps(prop)
        gap = {**_explain_advanced_construct(prop, field_name=name), "conformance": conformance.probe_gap(prop)}
        return (f'<div class="form-field unsupported">'
                f'<label>{label}</label>'
                f'{_render_gap_panel(gap)}'
                f'<details class="raw-json-details"><summary>View raw JSON Schema for this field</summary>'
                f'<pre class="raw-json">{html.escape(raw)}</pre></details></div>')

    defs = defs or {}
    if "$ref" in prop and prop["$ref"] not in ref_chain:
        resolved = _resolve_internal_ref(prop["$ref"], defs)
        if resolved is not None:
            merged = dict(resolved)
            if prop.get("description") and "description" not in merged:
                merged["description"] = prop["description"]
            return _render_field(name, merged, required_here, id_prefix=id_prefix,
                                  field_confidence=field_confidence, conditional_notes=conditional_notes,
                                  defs=defs, ref_chain=ref_chain + (prop["$ref"],))
        # Unresolved (external, or would-be-circular) -- falls through to the exotic raw-JSON
        # fallback below, which _is_exotic already catches via "$ref" in _EXOTIC_KEYS.

    description = prop.get("description")
    help_html = f'<div class="field-help">{html.escape(description)}</div>' if description else ""

    not_schema = prop.get("not")
    not_note = _not_note(not_schema)
    if not_note:
        help_html += f'<div class="not-note">{not_note}</div>'
    elif not_schema is not None and not _is_exotic(prop):
        # An unrecognized `not` shape (not one of _not_note's pattern/const/enum cases) --
        # _scan_preview_gaps already counts this as its own gap wherever it appears (oneOf/anyOf,
        # a plain object, or a plain leaf control), but nothing previously rendered it INTO the
        # actual page for any of those cases -- found by an adversarial review's
        # wiring-consistency lens. help_html flows through every non-exotic render path below
        # (oneOf/anyOf, the object fieldset, and the plain leaf control), so adding it here once
        # fixes all of them. Guarded on "not _is_exotic(prop)": when prop IS exotic, the fallback
        # branch below already renders ONE combined panel via _explain_advanced_construct(prop),
        # whose rules-accumulator already folds this same `not` in -- adding a second, separate
        # panel here would double it.
        not_gap = {**_explain_advanced_construct({"not": not_schema}, field_name=name),
                   "conformance": conformance.probe_gap({"not": not_schema})}
        help_html += _render_gap_panel(not_gap)

    notes = (conditional_notes or {}).get(field_id)
    if notes:
        # A toggleable span (not a plain string) so the embedded JS can flip it live as the
        # user changes the triggering field -- e.g. watching "required" appear/disappear as they
        # pick a different applicant type -- rather than only being told about the rule in prose.
        required_marker = (f' <span id="{field_id_attr}__reqmarker" class="required-marker" '
                            f'style="display:{"inline" if required_here else "none"}">*</span>')
        help_html += "".join(f'<div class="conditional-note">{n}</div>' for n in notes)
    else:
        required_marker = ' <span class="required-marker">*</span>' if required_here else ""

    confidence = (field_confidence or {}).get(field_id)
    assumed = bool(confidence) and not confidence.get("details_stated", True)
    assumed_class = " assumed" if assumed else ""
    assumed_badge = (' <span class="assumed-badge" title="Guessed from your description -- '
                      'please check">assumed</span>') if assumed else ""

    if "oneOf" in prop or "anyOf" in prop:
        return _render_one_of(field_id, label, required_marker, assumed_class, assumed_badge,
                               help_html, prop, field_confidence, conditional_notes, defs, ref_chain,
                               name=name)

    if _is_exotic(prop):
        raw = json.dumps(prop, indent=2)
        gap = {**_explain_advanced_construct(prop, field_name=name), "conformance": conformance.probe_gap(prop)}
        return (f'<div class="form-field unsupported">'
                f'<label>{label}{required_marker}</label>'
                f'{_render_gap_panel(gap)}'
                f'<details class="raw-json-details"><summary>View raw JSON Schema for this field</summary>'
                f'<pre class="raw-json">{html.escape(raw)}</pre></details>'
                f'{help_html}</div>')

    if prop.get("type") == "object" and prop.get("properties"):
        nested_required = set(prop.get("required") or [])
        inner = "".join(
            _render_field(sub_name, sub_prop, sub_name in nested_required, id_prefix=f"{field_id}.",
                          field_confidence=field_confidence, conditional_notes=conditional_notes,
                          defs=defs, ref_chain=ref_chain)
            for sub_name, sub_prop in prop["properties"].items())
        return (f'<fieldset class="form-group{assumed_class}"><legend>{label}{required_marker}{assumed_badge}</legend>'
                f'{help_html}{inner}</fieldset>')

    return (f'<div class="form-field{assumed_class}">'
            f'<label for="{field_id_attr}">{label}{required_marker}{assumed_badge}</label>'
            f'{_control_html(field_id, prop)}{help_html}</div>')


def _json_for_script(value, **kwargs) -> str:
    """json.dumps, but safe to embed directly inside a <script> block: a string value anywhere in
    `value` (a field name, a pattern, a description -- any of which can come from a free-text LLM
    drafting session, not just this project's own controlled vocabulary) could contain a literal
    '</script>', which would close the surrounding tag early and let whatever follows execute as a
    new, real <script> element. JSON/JS strings never need a literal, unescaped '/' -- '\\/' decodes
    to the exact same '/' character -- so replacing every '</' with '<\\/' defeats the browser's
    HTML tokenizer without changing any decoded value at all. The standard fix for this class of
    bug (see OWASP's guidance on embedding JSON in HTML)."""
    return json.dumps(value, **kwargs).replace("</", "<\\/")


def _render_raw_json_fallback_page(schema_code: str, definition, out_path: str, reason: str) -> str:
    """Whole-page 'can't show this as an interactive form' fallback -- used both for a top-level
    shape this project's field-by-field form model can't represent (an array or bare-value schema)
    and for a schema nested too deep for the recursive renderer to walk at all (see the
    RecursionError guard in render_schema_form_preview below). Strips $schema and collapses behind
    a <details> toggle -- the same two protections every other raw-JSON view in this file applies
    (the offline-safety leak this exact fallback allowed, before those two protections existed, was
    found by an earlier adversarial review: a non-object top-level schema carrying its own $schema
    URI landed in an always-visible <pre>, the only raw-JSON dump in the file that skipped both)."""
    definition_for_raw = ({k: v for k, v in definition.items() if k != "$schema"}
                           if isinstance(definition, dict) else definition)
    try:
        raw = (json.dumps(definition_for_raw, indent=2) if isinstance(definition_for_raw, (dict, list))
               else json.dumps(definition_for_raw))
        raw_json_html = ('<details class="raw-json-details" open><summary>View raw JSON Schema</summary>'
                          f'<pre class="raw-json">{html.escape(raw)}</pre></details>')
    except RecursionError:
        # json.dumps recurses once per nesting level too -- a schema deep enough to exceed the
        # interactive renderer's own recursion budget (the very case this fallback exists for) can
        # just as easily exceed this dump's. There's no shallower way left to show it, so this is
        # the last, honest degradation: say so in plain text instead of raising a second, unhandled
        # RecursionError while trying to explain the first one.
        raw_json_html = ('<div class="raw-json-note">This schema is nested too deeply to display, '
                          'even as raw JSON.</div>')
    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Form preview -- {html.escape(schema_code)}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{html.escape(schema_code)}</h1>
  <div class="subtitle">{html.escape(reason)}</div>
  {raw_json_html}
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html_doc)
    return out_path


def render_schema_form_preview(schema_code: str, definition: dict, x_unique: list | None,
                                x_indexes: list | None, out_path: str,
                                field_confidence: dict | None = None) -> str:
    """Renders what the actual data-entry form will look like -- labeled input controls per
    field -- so a business user can confirm "yes, based on what I described, my form will look
    like this," the same review this project already asks for everywhere else, just applied to a
    form instead of a table.

    Takes a plain dict, not a typed SchemaRequest: this is what lets the renderer accept any JSON
    Schema shape, not just what models.py's bounded PropertyDef can represent. The caller may be
    the existing deterministic wizard (schema.model_dump(by_alias=True, exclude_none=True)) or,
    eventually, a freely LLM-drafted schema -- one renderer, either producer. Walks the dict with
    .get() throughout rather than assuming Pydantic attribute access, and never renders the raw
    $schema value (it defaults to a real https:// URI in models.py, which would otherwise trip the
    offline-safety checks below for no useful reason).

    If `definition` isn't even a dict (JSON Schema legally allows a bare true/false as an entire
    schema) or its top-level type isn't "object" (an array-of-primitives or bare-string schema,
    which this project's own field-by-field form model has no way to represent), this falls back
    to a whole-page raw-JSON note rather than silently rendering an empty, misleadingly-blank
    form."""
    if not isinstance(definition, dict) or definition.get("type") not in (None, "object"):
        return _render_raw_json_fallback_page(
            schema_code, definition, out_path,
            "This schema's top-level shape isn't a field-by-field object form (e.g. an array or a "
            "bare value) -- not shown as an interactive form yet.")

    properties = definition.get("properties", {})
    required = set(definition.get("required") or [])
    defs = definition.get("$defs") or definition.get("definitions") or {}
    # Every step below walks the schema's own nesting depth recursively (field rendering, gap
    # scanning, completeness counting, validation-tree building all mirror each other's traversal --
    # see their own docstrings). add_raw_property/define_reusable_schema explicitly support
    # "arbitrary depth," per this project's own README, but Python's call stack doesn't: a schema
    # nested deep enough (confirmed at ~1000+ levels) blows past the default recursion limit and
    # raises RecursionError. Falling back to the same raw-JSON page used for a top-level shape this
    # renderer can't represent is consistent with this feature's whole "never crash, always degrade
    # honestly" philosophy -- a hard-to-reach corner case shouldn't kill the whole CLI session.
    try:
        conditional_notes = _build_conditional_notes(definition)
        conditional_specs = _build_conditional_specs(definition)
        fields_html = "".join(
            _render_field(name, prop, name in required, field_confidence=field_confidence,
                          conditional_notes=conditional_notes, defs=defs)
            for name, prop in properties.items())
        dependent_schema_html = "".join(
            _render_dependent_schema_group(trigger_field, dep_properties, dep_required, field_confidence, defs)
            for trigger_field, dep_properties, dep_required in _iter_dependent_schemas(definition))
        dependent_schema_triggers = [trigger_field for trigger_field, _, _ in _iter_dependent_schemas(definition)]
        pattern_properties_specs = _build_pattern_properties_specs(definition)
        pattern_properties_html = "".join(_render_pattern_properties_group(spec) for spec in pattern_properties_specs)
        # _scan_schema_level_gaps (e.g. an allOf block that doesn't match a recognized if/then shape)
        # was already counted in the completeness score, but an adversarial review found nothing ever
        # actually rendered these gaps anywhere on the page -- the score dropped below 100% with no
        # visible "Needs review" panel to explain why, the exact silent-gap failure mode this whole
        # feature exists to prevent.
        schema_level_gaps_html = "".join(
            f'<div class="form-field unsupported"><label>Schema-level rule</label>{_render_gap_panel(gap)}</div>'
            for gap in _scan_schema_level_gaps(definition))
        validation_tree = [
            node for name, prop in properties.items()
            if (node := _build_validation_node(name, prop, name in required, defs=defs)) is not None
        ]
        validation_tree.extend(_build_dependent_schema_validation_nodes(definition, defs))
        completeness = _compute_preview_completeness(definition, defs)
    except RecursionError:
        return _render_raw_json_fallback_page(
            schema_code, definition, out_path,
            "This schema is nested too deeply to render as an interactive form -- not shown as a "
            "form preview, but it will still be validated and enforced by the Registry Service.")
    completeness_html = _render_completeness_summary(completeness)
    needs_ack = completeness["percent"] < 100
    ack_html = (
        '<div class="completeness-ack"><label>'
        '<input type="checkbox" id="completenessAck">'
        "I understand this preview doesn't fully show every rule above (see the "
        '"Needs review" sections) and confirm I\'ve reviewed the explanations given.'
        '</label></div>'
    ) if needs_ack else ""
    # Never dump `definition` verbatim: it may carry the literal https:// $schema URI (see
    # models.py), which would otherwise trip the offline-safety checks for no useful reason --
    # same reasoning as everywhere else in this file that already excludes it.
    definition_for_raw_view = {k: v for k, v in definition.items() if k != "$schema"}
    raw_schema_toggle_html = (
        '<details class="raw-json-details"><summary>View the full raw JSON Schema (for technical users)</summary>'
        f'<pre class="raw-json">{html.escape(json.dumps(definition_for_raw_view, indent=2))}</pre></details>'
    )

    unique_html = ""
    if x_unique:
        items = "".join(f"<li>{' + '.join(c)}</li>" for c in x_unique)
        unique_html = f"<h3>Must be unique across every record</h3><ul>{items}</ul>"

    index_html = ""
    if x_indexes:
        items = "".join(f"<li>{i['fieldPath']} ({i.get('method', 'btree')})</li>" for i in x_indexes)
        index_html = f"<h3>Indexed for fast search/filter</h3><ul>{items}</ul>"

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Form preview -- {html.escape(schema_code)}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{html.escape(schema_code)}</h1>
  <div class="subtitle">This is what the data-entry form will look like, based on what you described. {len(properties)} field(s). Try filling it out -- Submit checks it against the rules you described, the same way the real data-entry form would.</div>
  {completeness_html}
  <form id="schemaPreviewForm">
    {fields_html}
    {dependent_schema_html}
    {pattern_properties_html}
    {schema_level_gaps_html}
    {ack_html}
    <button type="submit">Submit</button>
  </form>
  <div id="validationResult"></div>
  {unique_html}
  {index_html}
  {raw_schema_toggle_html}
  <script>
    const VALIDATION_SCHEMA = {_json_for_script(validation_tree, indent=2)};
    const CONDITIONALS = {_json_for_script(conditional_specs, indent=2)};
    const DEPENDENT_SCHEMA_TRIGGERS = {_json_for_script(dependent_schema_triggers)};
    const PATTERN_PROPERTIES = {_json_for_script(pattern_properties_specs, indent=2)};
    const NEEDS_COMPLETENESS_ACK = {_json_for_script(needs_ack)};
    const dynamicFieldCounters = {{}};

    function toggleOneOf(fieldId, chosenIndex, altCount) {{
      for (let i = 0; i < altCount; i++) {{
        const el = document.getElementById(fieldId + '__alt' + i);
        if (el) el.style.display = (i === chosenIndex) ? 'block' : 'none';
      }}
    }}

    function buildDynamicControl(fieldId, schema) {{
      if (schema.enum) {{
        let opts = '<option value="">-- choose --</option>';
        schema.enum.forEach(function(v) {{ opts += '<option value="' + v + '">' + v + '</option>'; }});
        return '<select id="' + fieldId + '">' + opts + '</select>';
      }}
      if (schema.type === 'boolean') return '<input type="checkbox" id="' + fieldId + '">';
      if (schema.type === 'integer' || schema.type === 'number') return '<input type="number" id="' + fieldId + '">';
      return '<input type="text" id="' + fieldId + '">';
    }}

    function addDynamicField(groupIndex) {{
      const group = PATTERN_PROPERTIES[groupIndex];
      const nameInput = document.getElementById('patternProps' + groupIndex + '__nameInput');
      const errorDiv = document.getElementById('patternProps' + groupIndex + '__error');
      const name = nameInput.value.trim();
      errorDiv.textContent = '';
      if (!name) {{ errorDiv.textContent = 'Enter a field name.'; return; }}
      if (!new RegExp(group.pattern).test(name)) {{
        errorDiv.textContent = "'" + name + "' doesn't match the required pattern: " + group.pattern;
        return;
      }}
      const list = document.getElementById('patternProps' + groupIndex + '__list');
      const already = Array.prototype.slice.call(list.querySelectorAll('.dynamic-field-row'))
        .some(function(row) {{ return row.dataset.fieldName === name; }});
      if (already) {{ errorDiv.textContent = "'" + name + "' has already been added."; return; }}
      const count = (dynamicFieldCounters[groupIndex] = (dynamicFieldCounters[groupIndex] || 0) + 1);
      const fieldId = 'patternProps' + groupIndex + '__field' + count;
      const row = document.createElement('div');
      row.className = 'dynamic-field-row';
      row.dataset.fieldName = name;
      row.innerHTML = '<label>' + name + '</label>' + buildDynamicControl(fieldId, group.valueSchema) +
        '<button type="button" onclick="this.parentElement.remove()">Remove</button>';
      list.appendChild(row);
      nameInput.value = '';
    }}

    function validateDynamicFields(errors) {{
      PATTERN_PROPERTIES.forEach(function(group) {{
        const list = document.getElementById('patternProps' + group.index + '__list');
        if (!list) return;
        const rows = list.querySelectorAll('.dynamic-field-row');
        rows.forEach(function(row) {{
          const label = row.dataset.fieldName;
          const input = row.querySelector('input, select');
          if (!input) return;
          if (input.type === 'checkbox') return;
          const raw = input.value;
          if (raw === '') return;
          const schema = group.valueSchema;
          if (schema.type === 'integer' || schema.type === 'number') {{
            const num = Number(raw);
            if (Number.isNaN(num)) {{ errors.push(label + ' must be a number.'); return; }}
            if (schema.minimum !== undefined && num < schema.minimum) {{
              errors.push(label + ' must be at least ' + schema.minimum + '.');
            }}
            if (schema.maximum !== undefined && num > schema.maximum) {{
              errors.push(label + ' must be at most ' + schema.maximum + '.');
            }}
            return;
          }}
          if (schema.minLength !== undefined && raw.length < schema.minLength) {{
            errors.push(label + ' must be at least ' + schema.minLength + ' character(s).');
          }}
          if (schema.maxLength !== undefined && raw.length > schema.maxLength) {{
            errors.push(label + ' must be at most ' + schema.maxLength + ' character(s).');
          }}
          if (schema.pattern) {{
            const re = new RegExp(schema.pattern);
            if (!re.test(raw)) {{
              errors.push(label + ' ' + (schema.patternHint || ('must match the required format: ' + schema.pattern)) + '.');
            }}
          }}
        }});
      }});
    }}

    function isPresent(el) {{
      const val = el.type === 'checkbox' ? el.checked : el.value;
      return val !== '' && val !== false;
    }}

    function conditionallyRequiredIds() {{
      // Which fields are required *right now*, given the current value of whatever field
      // triggers each conditional -- re-evaluated live as the user fills in the form, and again
      // on Submit, so the rule is enforced for real rather than just described in a note.
      const extra = new Set();
      CONDITIONALS.forEach(function(cond) {{
        const el = document.getElementById(cond.triggerId);
        if (!el) return;
        // dependentRequired has no specific triggerValue -- any non-empty value satisfies it,
        // unlike an if/then conditional which only fires on one exact match.
        const satisfied = cond.presence ? isPresent(el) : String(el.value) === String(cond.triggerValue);
        if (satisfied) {{
          cond.thenRequired.forEach(function(id) {{ extra.add(id); }});
        }}
      }});
      return extra;
    }}

    function updateConditionalMarkers() {{
      const extra = conditionallyRequiredIds();
      CONDITIONALS.forEach(function(cond) {{
        cond.thenRequired.forEach(function(id) {{
          const marker = document.getElementById(id + '__reqmarker');
          if (marker) marker.style.display = extra.has(id) ? 'inline' : 'none';
        }});
      }});
    }}

    function updateDependentSchemaVisibility() {{
      DEPENDENT_SCHEMA_TRIGGERS.forEach(function(triggerId) {{
        const el = document.getElementById(triggerId);
        const group = document.getElementById(triggerId + '__dependentSchema');
        if (!el || !group) return;
        group.style.display = isPresent(el) ? 'block' : 'none';
      }});
    }}

    function validateNode(node, errors, extraRequired) {{
      if (node.kind === 'group') {{
        node.children.forEach(function(child) {{ validateNode(child, errors, extraRequired); }});
        return;
      }}
      if (node.kind === 'oneOf') {{
        const radios = document.getElementsByName(node.radioName);
        let chosen = 0;
        for (let i = 0; i < radios.length; i++) {{ if (radios[i].checked) {{ chosen = i; break; }} }}
        const alt = node.alternatives[chosen];
        if (alt) alt.children.forEach(function(child) {{ validateNode(child, errors, extraRequired); }});
        return;
      }}
      if (node.kind === 'dependentSchema') {{
        const triggerEl = document.getElementById(node.triggerId);
        if (triggerEl && isPresent(triggerEl)) {{
          node.children.forEach(function(child) {{ validateNode(child, errors, extraRequired); }});
        }}
        return;
      }}
      if (node.kind !== 'field') return;
      const el = document.getElementById(node.id);
      if (!el) return;
      const label = node.label;
      if (node.type === 'boolean') return;
      const raw = el.value;
      const isRequired = node.required || extraRequired.has(node.id);
      if (raw === '') {{
        if (isRequired) errors.push(label + ' is required.');
        return;
      }}
      if (node.type === 'integer' || node.type === 'number') {{
        const num = Number(raw);
        if (Number.isNaN(num)) {{ errors.push(label + ' must be a number.'); return; }}
        if (node.type === 'integer' && !Number.isInteger(num)) {{
          errors.push(label + ' must be a whole number.'); return;
        }}
        if (node.minimum !== undefined && num < node.minimum) {{
          errors.push(label + ' must be at least ' + node.minimum + '.');
        }}
        if (node.maximum !== undefined && num > node.maximum) {{
          errors.push(label + ' must be at most ' + node.maximum + '.');
        }}
        return;
      }}
      if (node.minLength !== undefined && raw.length < node.minLength) {{
        errors.push(label + ' must be at least ' + node.minLength + ' character(s) (currently ' + raw.length + ').');
      }}
      if (node.maxLength !== undefined && raw.length > node.maxLength) {{
        errors.push(label + ' must be at most ' + node.maxLength + ' character(s) (currently ' + raw.length + ').');
      }}
      if (node.pattern) {{
        const re = new RegExp(node.pattern);
        if (!re.test(raw)) {{
          errors.push(label + ' ' + (node.patternHint || ('must match the required format: ' + node.pattern)) + '.');
        }}
      }}
      if (node.notPattern && new RegExp(node.notPattern).test(raw)) {{
        errors.push(label + ' must not match the pattern: ' + node.notPattern + '.');
      }}
      if (node.notConst !== undefined && String(node.notConst) === raw) {{
        errors.push(label + " must not be exactly '" + node.notConst + "'.");
      }}
      if (node.notEnum && node.notEnum.some(function(v) {{ return String(v) === raw; }})) {{
        errors.push(label + ' must not be one of the banned values.');
      }}
    }}

    const previewForm = document.getElementById('schemaPreviewForm');
    previewForm.addEventListener('input', updateConditionalMarkers);
    previewForm.addEventListener('change', updateConditionalMarkers);
    previewForm.addEventListener('input', updateDependentSchemaVisibility);
    previewForm.addEventListener('change', updateDependentSchemaVisibility);
    updateConditionalMarkers();
    updateDependentSchemaVisibility();

    previewForm.addEventListener('submit', function(e) {{
      e.preventDefault();
      const result = document.getElementById('validationResult');
      if (NEEDS_COMPLETENESS_ACK) {{
        const ack = document.getElementById('completenessAck');
        if (!ack || !ack.checked) {{
          result.innerHTML = '<div class="validation-errors"><b>This would not be accepted:</b><ul>' +
            '<li>Please acknowledge the preview limitations above before continuing.</li></ul></div>';
          return;
        }}
      }}
      const errors = [];
      const extraRequired = conditionallyRequiredIds();
      VALIDATION_SCHEMA.forEach(function(node) {{ validateNode(node, errors, extraRequired); }});
      validateDynamicFields(errors);
      if (errors.length) {{
        result.innerHTML = '<div class="validation-errors"><b>This would not be accepted:</b><ul>' +
          errors.map(function(msg) {{ return '<li>' + msg + '</li>'; }}).join('') + '</ul></div>';
      }} else {{
        result.innerHTML = '<div class="validation-ok">This record would be accepted.</div>';
      }}
    }});
  </script>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html_doc)
    return out_path


def _format_cell_value(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if value is None:
        return ""
    return str(value)


def render_data_preview(schema: SchemaRequest, records: list[dict], out_path: str) -> str:
    field_names = list(schema.definition.properties.keys())
    header = "".join(f"<th>{html.escape(name)}</th>" for name in field_names)
    body_rows = []
    for i, record in enumerate(records, start=1):
        cells = "".join(f"<td>{html.escape(_format_cell_value(record.get(name)))}</td>" for name in field_names)
        body_rows.append(f"<tr><td>{i}</td>{cells}</tr>")

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Data preview -- {html.escape(schema.schemaCode)}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{html.escape(schema.schemaCode)} -- new records</h1>
  <div class="subtitle">{len(records)} record(s) about to be created.</div>
  <table>
    <tr><th>#</th>{header}</tr>
    {''.join(body_rows)}
  </table>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html_doc)
    return out_path
