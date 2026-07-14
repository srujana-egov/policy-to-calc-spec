"""The `$.` field-reference mechanism: every CalculationRule `jsonPath` (in a condition, in
`appliesOn`/`sourceAttribute`, in a `formulaVariables` entry) needs to name a real field on the
entity this module's rules apply to. Rather than asking someone to type a raw JSONPath by hand
(easy to typo, and the #1 real bug already found twice in ../registry-prototype/ was exactly this
kind of "looks right, isn't" string), this fetches the real registry schema (Step 2's output,
already built and verified in ../registry-prototype/) and lets the wizard *pick* a field from it,
generating the exact `$.path` string deterministically.

Reuses the same verified GET /registry/v3/schema/:schemaCode request shape as
../registry-prototype/add_data.py's fetch_schema() -- same route, same X-Tenant-Id/X-User-Id
headers (not X-Client-Id; see that project's README for why). Kept as an independent, self-
contained copy rather than a cross-directory import, matching how workflow-prototype and
registry-prototype don't import from each other either -- each of these three is a standalone
prototype.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def registry_headers() -> dict[str, str] | None:
    """None if the environment isn't configured for a real registry lookup -- callers fall back
    to manual entry. Same env vars as ../registry-prototype/wizard.py's _registry_headers()."""
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


def fetch_registry_schema(schema_code: str) -> dict:
    """GET /registry/v3/schema/{schemaCode} -- raises SystemExit with a clear message on any
    failure (no env vars set, network error, 404) so a wizard session can catch it and fall back
    to manual jsonPath entry rather than crash."""
    headers = registry_headers()
    if headers is None:
        raise SystemExit("DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID must all be set to look "
                          "up a registry schema.")
    server_url = os.environ["DIGIT_SERVER_URL"]
    url = server_url.rstrip("/") + f"/registry/v3/schema/{schema_code}"
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())["data"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Couldn't fetch schema '{schema_code}' -- {e.code} {e.reason}\n"
                          f"{e.read().decode(errors='replace')}")


def list_schema_fields(schema_data: dict) -> list[str]:
    """Flattens a registry schema's properties into pickable field paths -- one level of nested
    sub-fields as dot notation (e.g. 'address.city'), matching exactly what
    ../registry-prototype/'s own wizard supports authoring (see that project's models.py for why
    nesting stops at one level). Order matches the schema's own property order."""
    properties = (schema_data.get("definition") or {}).get("properties") or {}
    paths = []
    for name, prop in properties.items():
        paths.append(name)
        for sub_name in (prop.get("properties") or {}):
            paths.append(f"{name}.{sub_name}")
    return paths


def field_to_json_path(field_path: str) -> str:
    """'address.city' -> '$.address.city' -- the `$.` convention already used throughout this
    project's earlier CalculationRule work (DEMO-2026-07-13.md, this prototype's own
    fixtures/real_world/chennai_schedule_I_rules.json fixture's own
    '$.tradeLicenseDetail.premisesArea'). Idempotent: a path someone already typed with a '$.'
    prefix is returned unchanged, rather than doubled into '$.$.foo'."""
    field_path = field_path.strip()
    if field_path.startswith("$."):
        return field_path
    return f"$.{field_path.lstrip('.')}"


def field_to_relative_path(field_path: str) -> str:
    """'quantity' -> 'quantity' -- for a jsonPath *inside* a SUBENTITY-scoped rule (PER_ITEM_IN_LIST,
    AGGREGATION's sourceAttribute, or a condition on such a rule), which becomes "relative to one
    array element", not root-absolute. Strips any '$.' prefix a
    user might type out of habit -- simulate.py's resolve_relative_path() doesn't do this
    stripping itself, so a leftover '$.' here would silently fail to resolve against a
    sub-entity dict (a real bug this project found and fixed once already, see wizard.py's
    configure_aggregation())."""
    field_path = field_path.strip()
    if field_path.startswith("$."):
        return field_path[2:]
    return field_path.lstrip(".")
