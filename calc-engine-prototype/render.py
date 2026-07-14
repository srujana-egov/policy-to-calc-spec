"""Generates a self-contained HTML table preview of a CalculationRuleSet for a non-technical
business user -- one row per rule, not a JSON dump, mirroring
../registry-prototype/render.py's table approach (a rule set is a list, like a schema's field
list or a set of data records, not a graph like a workflow). No external dependencies, same
reasoning as the other two prototypes' render.py: a CDN dependency would silently break offline.
"""

from __future__ import annotations

import json

from models import CalculationRuleSet

_STYLE = """
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 24px; }
  h1 { font-size: 18px; margin: 0 0 4px 0; }
  .subtitle { color: #888; font-size: 13px; margin-bottom: 12px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
  th, td { border: 1px solid #ddd; padding: 8px 12px; font-size: 13px; text-align: left; vertical-align: top; }
  th { background: #f5f5f5; }
  tr.rule-row { cursor: pointer; }
  tr.rule-row:hover { background: #f0f6ff; }
  .rule-type-tag { background: #e8f0fe; color: #3a6fc4; border-radius: 10px; padding: 2px 8px; font-size: 11px; }
  #detail { margin-top: 16px; padding: 12px; background: #fafafa; border-radius: 6px; font-size: 13px; display: none; white-space: pre-wrap; font-family: ui-monospace, monospace; }
  .assumptions { background: #fff8e1; border: 1px solid #f0d878; border-radius: 6px; padding: 12px 16px; margin-top: 16px; }
  .assumptions h3 { margin-top: 0; font-size: 14px; }
  .assumptions li { font-size: 13px; margin-bottom: 6px; }
  .examples h2 { font-size: 16px; margin-top: 28px; }
  .examples .subtitle { margin-bottom: 12px; }
  .line-item { display: inline-block; background: #f0f6ff; border-radius: 8px; padding: 2px 8px; margin: 2px 4px 2px 0; font-size: 12px; }
  .total-cell { font-weight: bold; }
"""


def _flatten_for_display(value, prefix="") -> list[str]:
    """Turns a scenario's payload into short, readable 'path: value' bits -- lists of dicts (a
    repeating sub-entity array) collapse to their length plus each item's own values, rather
    than a raw JSON dump, so a business user can read what was actually fed in at a glance."""
    bits = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else k
            bits.extend(_flatten_for_display(v, path))
    elif isinstance(value, list):
        bits.append(f"{prefix}: {len(value)} item(s)")
        for i, item in enumerate(value):
            for bit in _flatten_for_display(item, f"{prefix}[{i}]"):
                bits.append(bit)
    else:
        bits.append(f"{prefix}: {value}")
    return bits


def _line_items_html(line_items: list[dict]) -> str:
    if not line_items:
        return "&mdash; (no rule matched)"
    return "".join(
        f'<span class="line-item">{li["component"]}: {li["amount"]}</span>' for li in line_items)


def _amount_summary(rule) -> str:
    if rule.calculationType == "SLAB":
        n = len(rule.slabs or [])
        return f"{n} tier(s) on {rule.appliesOn.jsonPath if rule.appliesOn else '?'}"
    if rule.calculationType == "FORMULA":
        if rule.aggregateFunction:
            return f"{rule.aggregateFunction} of {rule.sourceAttribute.jsonPath if rule.sourceAttribute else '?'}"
        return "formula (see detail)"
    if rule.calculationType == "PERCENTAGE":
        target = rule.appliesOn.componentRef if rule.appliesOn else "?"
        return f"{rule.value}% of {target}"
    if rule.calculationType == "PER_UNIT":
        target = rule.appliesOn.jsonPath if rule.appliesOn else "?"
        return f"{rule.value} per unit of {target}"
    return str(rule.value) if rule.value is not None else ""


def _conditions_summary(rule) -> str:
    if not rule.conditions:
        return "&mdash; (always applies)"
    bits = []
    for name, cond in rule.conditions.items():
        if cond.derivedFrom:
            bits.append(f"{name} (derived from {cond.derivedFrom})")
        elif cond.equals is not None:
            bits.append(f"{name} = {cond.equals}")
        elif cond.from_ is not None or cond.to is not None:
            lo = cond.from_ if cond.from_ is not None else ""
            hi = cond.to if cond.to is not None else ""
            bits.append(f"{lo} &le; {name} &le; {hi}" if lo != "" and hi != "" else f"{name}: {lo}{hi}")
        else:
            bits.append(f"{name} (present)")
    return "; ".join(bits)


def render_ruleset_preview(rule_set: CalculationRuleSet, out_path: str, scenario_results=None) -> str:
    """scenario_results: optional list of {"label", "payload", "result"} from
    example_generator.run_scenarios() -- the "few representative worked examples" CONFIG-
    PIPELINE.md's design calls for, not just the rule table. Lets someone notice "wait, a 1000
    sq ft shop pays 2000 but 1000.01 pays 5000 -- did I mean to draw the line there?" by seeing
    real computed numbers, not just reading rule definitions and mentally simulating them."""
    rows = []
    rule_details = {}
    for i, rule in enumerate(rule_set.rules):
        rows.append(f'''
        <tr class="rule-row" onclick="showRule({i})">
          <td><b>{rule.component}</b></td>
          <td><span class="rule-type-tag">{rule.ruleType}</span></td>
          <td>{rule.calculationType}</td>
          <td>{_conditions_summary(rule)}</td>
          <td>{_amount_summary(rule)}</td>
          <td>{', '.join(rule.dependsOn) or '&mdash;'}</td>
          <td>{rule.effectiveFrom}{f" &rarr; {rule.effectiveTo}" if rule.effectiveTo else ""}</td>
        </tr>''')
        rule_details[i] = json.loads(rule.model_dump_json(by_alias=True, exclude_none=True))

    assumptions_html = ""
    if rule_set.assumptions:
        items = "".join(f"<li>{a}</li>" for a in rule_set.assumptions)
        assumptions_html = f'<div class="assumptions"><h3>Assumptions made</h3><ul>{items}</ul></div>'

    examples_html = ""
    if scenario_results:
        example_rows = []
        for scenario in scenario_results:
            inputs = "; ".join(_flatten_for_display(scenario["payload"])) or "(none)"
            result = scenario["result"]
            example_rows.append(f'''
            <tr>
              <td>{scenario["label"]}</td>
              <td>{inputs}</td>
              <td>{_line_items_html(result["lineItems"])}</td>
              <td class="total-cell">{result["totalAmount"]}</td>
            </tr>''')
        examples_html = f'''
  <div class="examples">
    <h2>Worked examples</h2>
    <div class="subtitle">What this configuration actually computes for {len(scenario_results)}
      representative scenario(s) -- each chosen to show a specific decision your answers made
      (a condition boundary, a slab tier, an aggregation threshold), not a random value.</div>
    <table>
      <tr><th>Scenario</th><th>Input</th><th>Line items</th><th>Total</th></tr>
      {''.join(example_rows)}
    </table>
  </div>'''

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Calculation rules preview -- {rule_set.module}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{rule_set.module}</h1>
  <div class="subtitle">Click a row for the exact rule definition. {len(rule_set.rules)} rule(s).</div>
  <table>
    <tr><th>Component</th><th>Rule Type</th><th>Calc Type</th><th>Conditions</th><th>Amount</th><th>Depends On</th><th>Effective</th></tr>
    {''.join(rows)}
  </table>
  {assumptions_html}
  {examples_html}
  <div id="detail"></div>
  <script>
    const RULE_DETAILS = {json.dumps(rule_details, indent=2)};
    function showRule(i) {{
      const d = document.getElementById('detail');
      d.style.display = 'block';
      d.textContent = 'Rule ' + (i + 1) + ': ' + JSON.stringify(RULE_DETAILS[i], null, 2);
    }}
  </script>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path
