"""Generates a single, self-contained interactive HTML preview of a ProcessDefinitionInput --
click a state to expand its SLA and every action's roles; click an arrow to see that one
action's detail. Built for a non-technical business user: no JSON, no code, just boxes, arrows,
and click-to-expand detail -- collapsed by default so the overall shape is readable at a glance,
same reasoning as the "whole-structure preview" argument in CONFIG-PIPELINE.md.

Uses vis-network (loaded from a CDN) for graph layout/rendering -- that's a solved problem with
mature libraries; the value here is producing the right data for it, not re-implementing graph
drawing from scratch.
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


def render_html(process: ProcessDefinitionInput, out_path: str) -> str:
    nodes = []
    edges = []
    state_details = {}

    for s in process.states:
        color = _TYPE_COLORS.get(s.type, _TYPE_COLORS["INTERMEDIATE"])
        nodes.append({
            "id": s.code, "label": s.name, "shape": "box",
            "color": {"background": color["background"], "border": color["border"]},
            "font": {"size": 16},
            "borderWidth": 3 if s.type == "INITIAL" else 1,
        })
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
            edge_id = f"{s.code}__{a.code}"
            edges.append({
                "id": edge_id, "from": s.code, "to": a.nextState,
                "label": a.label or a.code, "arrows": "to",
                "font": {"size": 12, "align": "top"},
            })

    data_json = json.dumps({"nodes": nodes, "edges": edges, "stateDetails": state_details}, indent=2)

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Workflow preview -- {process.name}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; display: flex; height: 100vh; }}
  #graph {{ flex: 3; border-right: 2px solid #ddd; }}
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
</style>
</head>
<body>
<div style="flex-direction: column; display: flex; flex: 3;">
  <h1>{process.name} <span style="font-weight:normal;color:#888">({process.code})</span></h1>
  <div class="legend">
    <span><span class="swatch" style="background:{_TYPE_COLORS['INITIAL']['background']}"></span>Start</span>
    <span><span class="swatch" style="background:{_TYPE_COLORS['INTERMEDIATE']['background']}"></span>In progress</span>
    <span><span class="swatch" style="background:{_TYPE_COLORS['TERMINAL_SUCCESS']['background']}"></span>Good outcome</span>
    <span><span class="swatch" style="background:{_TYPE_COLORS['TERMINAL_FAILURE']['background']}"></span>Bad outcome</span>
    <span style="color:#888">Click a box or an arrow for details &rarr;</span>
  </div>
  <div id="graph"></div>
</div>
<div id="panel"><div class="placeholder">Click any box (a stage) or arrow (an action) on the left to see its details -- who can do it, and how long it should take.</div></div>
<script>
const DATA = {data_json};

const nodes = new vis.DataSet(DATA.nodes);
const edges = new vis.DataSet(DATA.edges);
const network = new vis.Network(document.getElementById('graph'), {{nodes, edges}}, {{
  layout: {{ hierarchical: {{ direction: 'LR', sortMethod: 'directed', nodeSpacing: 160, levelSeparation: 220 }} }},
  physics: false,
  interaction: {{ hover: true }},
  edges: {{ smooth: {{ type: 'cubicBezier', roundness: 0.4 }} }},
}});

function showState(code) {{
  const s = DATA.stateDetails[code];
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

network.on('click', function(params) {{
  if (params.nodes.length > 0) {{
    showState(params.nodes[0]);
  }} else if (params.edges.length > 0) {{
    const edge = edges.get(params.edges[0]);
    showState(edge.from);
  }}
}});
</script>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path
