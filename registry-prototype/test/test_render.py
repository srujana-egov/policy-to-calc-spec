"""Stress tests for render.py -- offline-safety and structural correctness as a permanent
regression check, mirroring ../workflow-prototype/test_render.py's reasoning (a CDN dependency
there caused a silently blank preview; there's none here to begin with, but it's worth locking in
rather than assuming).
"""

from __future__ import annotations


import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
from pathlib import Path

from render import render_data_preview, render_schema_preview
from test_schema_builder import build_license_registry, build_pgr2, build_trade_license

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


EXTERNAL_REF_PATTERNS = ["http://", "https://", "@import", "fetch(", "XMLHttpRequest"]


def test_render_01_schema_preview_no_external_references():
    schema = build_license_registry().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_preview(schema, str(out))
        html = out.read_text()
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render01-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_02_schema_preview_has_one_row_per_field():
    schema = build_license_registry().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_preview(schema, str(out))
        html = out.read_text()
    check("render02-row-count", html.count('class="field-row"') == len(schema.definition.properties),
          (html.count('class="field-row"'), len(schema.definition.properties)))
    for name in schema.definition.properties:
        check(f"render02-has-{name}", name in html)


def test_render_03_data_preview_no_external_references():
    schema = build_license_registry().build()
    records = [{"licenseNumber": "DL-001", "holderName": "Jane Citizen",
                "issueDate": "2024-01-10", "status": "ACTIVE"}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render03-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_04_data_preview_one_row_per_record():
    schema = build_license_registry().build()
    records = [
        {"licenseNumber": "DL-001", "holderName": "Jane Citizen", "issueDate": "2024-01-10", "status": "ACTIVE"},
        {"licenseNumber": "DL-002", "holderName": "Bob Builder", "issueDate": "2024-02-01", "status": "SUSPENDED"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render04-row-count", html.count("<tr><td>") == len(records), html.count("<tr><td>"))
    check("render04-has-dl-001", "DL-001" in html)
    check("render04-has-dl-002", "DL-002" in html)


def test_render_05_data_preview_missing_optional_field_renders_blank_not_crashing():
    schema = build_license_registry().build()
    records = [{"licenseNumber": "DL-003", "issueDate": "2024-03-01", "status": "ACTIVE"}]  # no holderName
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render05-does-not-crash-and-renders", "DL-003" in html)


def test_render_06_nested_group_labeled_and_no_external_refs():
    schema = build_trade_license().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_preview(schema, str(out))
        html = out.read_text()
    check("render06-group-labeled", "group of 2 field(s)" in html, html)
    check("render06-nested-fields-in-detail-json", "city" in html and "pincode" in html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render06-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_07_pattern_and_minmax_shown_and_no_external_refs():
    schema = build_pgr2().build()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "s.html"
        render_schema_preview(schema, str(out))
        html = out.read_text()
    # mobile's pattern is a top-level field, so it shows directly in the table's constraint hint.
    check("render07-top-level-pattern-shown-in-table", "pattern: ^[0-9]{10}$" in html, "mobile's pattern")
    # latitude/longitude are nested two levels deep (address.latitude) -- their minimum/maximum
    # only show in the click-to-expand detail JSON (which dumps the whole nested PropertyDef),
    # not the outer table row, since that's scoped to top-level fields only.
    check("render07-nested-min-in-detail-json", '"minimum": -90' in html, "latitude's minimum")
    check("render07-nested-max-in-detail-json", '"maximum": 90' in html, "latitude's maximum")
    check("render07-does-not-crash-on-two-level-nesting", "auditDetails" in html)
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render07-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_08_nested_data_value_formatted_readably():
    schema = build_trade_license().build()
    records = [{"applicantId": "A1", "businessName": "Acme", "tradeType": "RETAIL",
                "address": {"city": "Springfield", "pincode": "62704"}}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "d.html"
        render_data_preview(schema, records, str(out))
        html = out.read_text()
    check("render08-nested-value-readable", "city: Springfield" in html and "pincode: 62704" in html, html)
    check("render08-not-python-repr", "{'city'" not in html, html)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll render.py checks passed -- offline-safe and structurally correct.")
