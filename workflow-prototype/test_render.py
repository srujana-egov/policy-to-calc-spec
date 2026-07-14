"""Stress tests for render.py -- previously only checked ad hoc (grep for external refs, parse
the SVG once) during manual verification, never as a repeatable test. The CDN-dependency bug
that caused a blank preview lived exactly here, so this locks in "works fully offline" as a
checked fact rather than a one-time assertion.
"""

from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from builder import WorkflowBuilder
from render import render_html
from test_workflow_builder import build_trade_license_approval

PASSED = []


def check(name, condition, detail=""):
    assert condition, f"{name} FAILED: {detail}"
    PASSED.append(name)


def render_to_string(process) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{process.code}_preview.html"
        render_html(process, str(out))
        return out.read_text()


def extract_svg(html: str) -> str:
    match = re.search(r"<svg[^>]*>.*</svg>", html, re.DOTALL)
    assert match, "no <svg>...</svg> block found in rendered HTML"
    return match.group(0)


EXTERNAL_REF_PATTERNS = ["http://", "https://", "@import", "fetch(", "XMLHttpRequest"]


def build_self_loop_process():
    """A single state whose only action points back to itself -- the self-loop/backward-edge
    lane, the one part of the layout that isn't a straightforward forward BFS column."""
    b = WorkflowBuilder(name="Loop", code="LOOP")
    start = b.add_initial_state("Start")
    b.add_action_to_existing_state(start, "Retry", start, roles=["ADMIN"])
    b.add_action_to_new_state(start, "Finish", "Done", new_state_type="TERMINAL_SUCCESS")
    return b.build()


def test_render_01_no_external_references_of_any_kind():
    html = render_to_string(build_trade_license_approval().build())
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render01-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


def test_render_02_svg_is_well_formed_xml():
    html = render_to_string(build_trade_license_approval().build())
    svg = extract_svg(html)
    try:
        ET.fromstring(svg)
        parsed = True
    except ET.ParseError as e:
        parsed = False
        detail = str(e)
    check("render02-svg-parses", parsed, detail if not parsed else "")


def test_render_03_node_and_edge_counts_match_process():
    process = build_trade_license_approval().build()
    html = render_to_string(process)
    node_count = html.count('class="node"')
    edge_count = html.count('class="edge"')
    expected_actions = sum(len(s.actions) for s in process.states)
    check("render03-node-count", node_count == len(process.states), (node_count, len(process.states)))
    check("render03-edge-count", edge_count == expected_actions, (edge_count, expected_actions))


def test_render_04_self_loop_routed_into_backward_lane_not_crashing():
    process = build_self_loop_process()
    html = render_to_string(process)
    svg = extract_svg(html)
    ET.fromstring(svg)  # must still be well-formed with a self-loop present
    check("render04-parses-with-self-loop", True)
    check("render04-two-nodes", html.count('class="node"') == 2)
    check("render04-two-edges", html.count('class="edge"') == 2)
    check("render04-retry-role-in-detail-json", "ADMIN" in html)


def test_render_05_no_external_refs_even_with_self_loop():
    html = render_to_string(build_self_loop_process())
    for pattern in EXTERNAL_REF_PATTERNS:
        check(f"render05-no-{pattern.strip('(:/@')}", pattern not in html, pattern)


if __name__ == "__main__":
    test_functions = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in test_functions:
        fn()
    print(f"PASSED: {len(PASSED)} check(s) -> {PASSED}")
    print("\nAll render.py checks passed -- offline-safe and structurally correct.")
