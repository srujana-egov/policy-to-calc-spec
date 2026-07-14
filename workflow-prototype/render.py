"""Generates a single, self-contained interactive HTML preview of a ProcessDefinitionInput --
click a state to expand its SLA and every action's roles; click an arrow to see that one
action's detail. Built for a non-technical business user: no JSON, no code, just boxes, arrows,
and click-to-expand detail -- collapsed by default so the overall shape is readable at a glance,
same reasoning as the "whole-structure preview" argument in CONFIG-PIPELINE.md.

Hand-rolled SVG layout, zero external dependencies -- no CDN script tag. An earlier version used
vis-network loaded from a CDN, which silently rendered a blank page whenever the file was opened
without internet access (or a browser/extension blocked the cross-origin load from a `file://`
page) -- no visible error unless the browser console was open. This whole prototype was built to
work fully offline; a CDN dependency contradicted that, so it's gone, not worked around.
"""

from __future__ import annotations

import json

from models import ProcessDefinitionInput

_TYPE_COLORS = {
    "INITIAL": {"background": "#fde9d9", "border": "#b56a1f"},
    "INTERMEDIATE": {"background": "#e8f0fe", "border": "#3a6fc4"},
    "DECISION": {"background": "#fff3c4", "border": "#a17f00"},
    "TERMINAL_SUCCESS": {"background": "#d9f7e8", "border": "#1f8a4c"},
    "TERMINAL_FAILURE": {"background": "#f7d9d9", "border": "#a01f1f"},
}

BOX_W, BOX_H = 200, 64
COL_SPACING, ROW_SPACING = 280, 110
MARGIN = 40


def _ms_to_readable(ms: int | None) -> str:
    if ms is None:
        return "not set"
    days = ms / 86_400_000
    if days >= 1 and days == int(days):
        return f"{int(days)} day(s)"
    hours = ms / 3_600_000
    if hours >= 1:
        return f"{hours:g} hour(s)"
    return f"{ms} ms"


def _layer_states(process: ProcessDefinitionInput) -> dict[str, int]:
    """BFS layer (column) per state, following actions forward from INITIAL. Anything
    unreachable (shouldn't happen -- validate.py already checks this) gets its own trailing
    layer rather than crashing."""
    by_code = {s.code: s for s in process.states}
    initial = next((s.code for s in process.states if s.type == "INITIAL"), process.states[0].code)
    layer = {initial: 0}
    frontier = [initial]
    while frontier:
        nxt = []
        for code in frontier:
            for a in by_code[code].actions:
                if a.nextState in by_code and a.nextState not in layer:
                    layer[a.nextState] = layer[code] + 1
                    nxt.append(a.nextState)
        frontier = nxt
    max_layer = max(layer.values(), default=0)
    for s in process.states:
        if s.code not in layer:
            max_layer += 1
            layer[s.code] = max_layer
    return layer


def _positions(process: ProcessDefinitionInput) -> dict[str, tuple[int, int]]:
    layer = _layer_states(process)
    rows_per_layer: dict[int, int] = {}
    pos = {}
    for s in process.states:
        col = layer[s.code]
        row = rows_per_layer.get(col, 0)
        rows_per_layer[col] = row + 1
        pos[s.code] = (MARGIN + col * COL_SPACING, MARGIN + row * ROW_SPACING)
    return pos


def render_html(process: ProcessDefinitionInput, out_path: str) -> str:
    layer = _layer_states(process)
    pos = _positions(process)
    by_code = {s.code: s for s in process.states}

    width = max(x for x, y in pos.values()) + BOX_W + MARGIN
    height = max(y for x, y in pos.values()) + BOX_H + MARGIN

    state_details = {}
    boxes_svg = []
    edges_svg = []
    backward_index = 0

    for s in process.states:
        x, y = pos[s.code]
        color = _TYPE_COLORS.get(s.type, _TYPE_COLORS["INTERMEDIATE"])
        stroke_width = 3 if s.type == "INITIAL" else 1.5
        boxes_svg.append(f'''
          <g class="node" onclick="showState('{s.code}')">
            <rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="8"
                  fill="{color['background']}" stroke="{color['border']}" stroke-width="{stroke_width}"/>
            <text x="{x + BOX_W / 2}" y="{y + BOX_H / 2}" text-anchor="middle" dominant-baseline="middle"
                  font-size="14" font-family="-apple-system, Segoe UI, Arial, sans-serif">{s.name}</text>
          </g>''')
        state_details[s.code] = {
            "name": s.name, "code": s.code, "type": s.type,
            "sla": _ms_to_readable(s.sla),
            "actions": [
                {"label": a.label or a.code, "code": a.code, "nextState": a.nextState,
                 "roles": a.roles or ["anyone"], "assigneeCheck": a.assigneeCheck}
                for a in s.actions
            ],
        }

        for a in s.actions:
            if a.nextState not in pos:
                continue
            tx, ty = pos[a.nextState]
            label = a.label or a.code
            is_forward = layer[a.nextState] > layer[s.code] and a.nextState != s.code
            if is_forward:
                x1, y1 = x + BOX_W, y + BOX_H / 2
                x2, y2 = tx, ty + BOX_H / 2
                mid_x = (x1 + x2) / 2
                path = f"M {x1} {y1} C {mid_x} {y1}, {mid_x} {y2}, {x2} {y2}"
                lx, ly = mid_x, (y1 + y2) / 2 - 8
            else:
                # Backward edge or self-loop: route below the diagram in its own lane, so it
                # never overlaps a box it isn't related to.
                backward_index += 1
                lane_y = height + backward_index * 34
                x1, y1 = x + BOX_W / 2, y + BOX_H
                x2, y2 = tx + BOX_W / 2, ty + BOX_H
                path = f"M {x1} {y1} L {x1} {lane_y} L {x2} {lane_y} L {x2} {y2}"
                lx, ly = (x1 + x2) / 2, lane_y - 6
            edges_svg.append(f'''
          <g class="edge" onclick="showState('{s.code}')">
            <path d="{path}" fill="none" stroke="#888" stroke-width="1.5" marker-end="url(#arrow)"/>
            <text x="{lx}" y="{ly}" text-anchor="middle" font-size="11" fill="#555"
                  font-family="-apple-system, Segoe UI, Arial, sans-serif">{label}</text>
          </g>''')

    if backward_index:
        height += backward_index * 34 + MARGIN

    data_json = json.dumps(state_details, indent=2)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Workflow preview -- {process.name}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; display: flex; height: 100vh; }}
  #graph {{ flex: 3; border-right: 2px solid #ddd; overflow: auto; }}
  #panel {{ flex: 1; min-width: 320px; padding: 20px; overflow-y: auto; background: #fafafa; }}
  h1 {{ font-size: 18px; padding: 12px 20px; margin: 0; border-bottom: 2px solid #ddd; background: #fff; }}
  #panel h2 {{ font-size: 16px; margin-top: 0; }}
  .placeholder {{ color: #888; font-size: 14px; }}
  .field {{ margin-bottom: 10px; font-size: 13px; }}
  .field b {{ display: block; color: #555; font-size: 11px; text-transform: uppercase; }}
  .action-card {{ border: 1px solid #ddd; border-radius: 6px; padding: 10px; margin: 8px 0; background: #fff; }}
  .action-card .label {{ font-weight: bold; font-size: 14px; }}
  .roles {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }}
  .role-tag {{ background: #e8f0fe; color: #3a6fc4; border-radius: 10px; padding: 2px 8px; font-size: 11px; }}
  .assignee-flag {{ color: #a17f00; font-size: 11px; margin-top: 4px; }}
  .legend {{ display: flex; gap: 14px; padding: 8px 20px; font-size: 12px; background: #fff; border-bottom: 1px solid #eee; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
  .swatch {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
  .node, .edge {{ cursor: pointer; }}
  .node:hover rect {{ filter: brightness(0.95); }}
  .edge:hover path {{ stroke: #333; }}
</style>
</head>
<body>
<div style="flex-direction: column; display: flex; flex: 3; min-width: 0;">
  <h1>{process.name} <span style="font-weight:normal;color:#888">({process.code})</span></h1>
  <div class="legend">
    <span><span class="swatch" style="background:{_TYPE_COLORS['INITIAL']['background']}"></span>Start</span>
    <span><span class="swatch" style="background:{_TYPE_COLORS['INTERMEDIATE']['background']}"></span>In progress</span>
    <span><span class="swatch" style="background:{_TYPE_COLORS['TERMINAL_SUCCESS']['background']}"></span>Good outcome</span>
    <span><span class="swatch" style="background:{_TYPE_COLORS['TERMINAL_FAILURE']['background']}"></span>Bad outcome</span>
    <span style="color:#888">Click a box or an arrow for details &rarr;</span>
  </div>
  <div id="graph">
    <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#888"/>
        </marker>
      </defs>
      {''.join(edges_svg)}
      {''.join(boxes_svg)}
    </svg>
  </div>
</div>
<div id="panel"><div class="placeholder">Click any box (a stage) or arrow (an action) on the left to see its details -- who can do it, and how long it should take.</div></div>
<script>
const STATE_DETAILS = {data_json};

function showState(code) {{
  const s = STATE_DETAILS[code];
  let actionsHtml = s.actions.length ? '' : '<p class="placeholder">This is an ending -- nothing else happens from here.</p>';
  for (const a of s.actions) {{
    const roles = a.roles.map(r => `<span class="role-tag">${{r}}</span>`).join('');
    actionsHtml += `<div class="action-card">
      <div class="label">${{a.label}}</div>
      <div style="font-size:12px;color:#888">leads to: ${{a.nextState}}</div>
      <div class="roles">${{roles}}</div>
      ${{a.assigneeCheck ? '<div class="assignee-flag">Only the assigned person can do this</div>' : ''}}
    </div>`;
  }}
  document.getElementById('panel').innerHTML = `
    <h2>${{s.name}}</h2>
    <div class="field"><b>Internal code</b>${{s.code}}</div>
    <div class="field"><b>Type</b>${{s.type}}</div>
    <div class="field"><b>Time allowed here</b>${{s.sla}}</div>
    <div class="field"><b>What can happen from here</b></div>
    ${{actionsHtml}}
  `;
}}
</script>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path
