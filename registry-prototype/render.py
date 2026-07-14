"""Generates self-contained HTML previews for a non-technical business user -- a table, not a
JSON dump, matching how the registry's own contract is table-shaped (a schema is a list of
fields; data is a list of records). No external dependencies, same reasoning as
../workflow-prototype/render.py: a CDN script tag silently produced a blank page there when
opened offline, so this has none to begin with.
"""

from __future__ import annotations

import json

from models import SchemaRequest

_STYLE = """
  body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; padding: 24px; }
  h1 { font-size: 18px; margin: 0 0 4px 0; }
  .subtitle { color: #888; font-size: 13px; margin-bottom: 18px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 8px 12px; font-size: 13px; text-align: left; vertical-align: top; }
  th { background: #f5f5f5; }
  tr.field-row { cursor: pointer; }
  tr.field-row:hover { background: #f0f6ff; }
  .required { color: #a01f1f; font-weight: bold; }
  .optional { color: #888; }
  .role-tag { background: #e8f0fe; color: #3a6fc4; border-radius: 10px; padding: 2px 8px; font-size: 11px; margin-right: 4px; display: inline-block; }
  #detail { margin-top: 16px; padding: 12px; background: #fafafa; border-radius: 6px; font-size: 13px; display: none; white-space: pre-wrap; font-family: ui-monospace, monospace; }
"""


def render_schema_preview(schema: SchemaRequest, out_path: str) -> str:
    definition = schema.definition
    rows = []
    field_details = {}
    for name, prop in definition.properties.items():
        required = name in definition.required
        enum_html = "".join(f'<span class="role-tag">{v}</span>' for v in (prop.enum or [])) or "&mdash;"
        type_label = prop.type
        if prop.format:
            type_label += f" ({prop.format})"
        if prop.type == "object" and prop.properties:
            type_label += f" -- group of {len(prop.properties)} field(s)"
        constraint_bits = []
        if prop.pattern:
            constraint_bits.append(f"pattern: {prop.pattern}")
        if prop.minimum is not None:
            constraint_bits.append(f"min: {prop.minimum}")
        if prop.maximum is not None:
            constraint_bits.append(f"max: {prop.maximum}")
        if prop.minLength is not None:
            constraint_bits.append(f"minLength: {prop.minLength}")
        if prop.maxLength is not None:
            constraint_bits.append(f"maxLength: {prop.maxLength}")
        constraint_html = " / ".join(constraint_bits)
        rows.append(f'''
        <tr class="field-row" onclick="showField('{name}')">
          <td><b>{name}</b></td>
          <td>{type_label}</td>
          <td class="{'required' if required else 'optional'}">{"required" if required else "optional"}</td>
          <td>{enum_html}{f"<br>{constraint_html}" if constraint_html else ""}</td>
          <td>{prop.description or ""}</td>
        </tr>''')
        field_details[name] = json.loads(prop.model_dump_json(exclude_none=True))

    unique_html = ""
    if schema.x_unique:
        items = "".join(f"<li>{' + '.join(c)}</li>" for c in schema.x_unique)
        unique_html = f"<h3>Must be unique across every record</h3><ul>{items}</ul>"

    index_html = ""
    if schema.x_indexes:
        items = "".join(f"<li>{i.fieldPath} ({i.method})</li>" for i in schema.x_indexes)
        index_html = f"<h3>Indexed for fast search/filter</h3><ul>{items}</ul>"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Schema preview -- {schema.schemaCode}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{schema.schemaCode}</h1>
  <div class="subtitle">Click a row for the exact field definition. {len(definition.properties)} field(s).</div>
  <table>
    <tr><th>Field</th><th>Type</th><th>Required?</th><th>Allowed values</th><th>Description</th></tr>
    {''.join(rows)}
  </table>
  {unique_html}
  {index_html}
  <div id="detail"></div>
  <script>
    const FIELD_DETAILS = {json.dumps(field_details, indent=2)};
    function showField(name) {{
      const d = document.getElementById('detail');
      d.style.display = 'block';
      d.textContent = name + ': ' + JSON.stringify(FIELD_DETAILS[name], null, 2);
    }}
  </script>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def _format_cell_value(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if value is None:
        return ""
    return str(value)


def render_data_preview(schema: SchemaRequest, records: list[dict], out_path: str) -> str:
    field_names = list(schema.definition.properties.keys())
    header = "".join(f"<th>{name}</th>" for name in field_names)
    body_rows = []
    for i, record in enumerate(records, start=1):
        cells = "".join(f"<td>{_format_cell_value(record.get(name))}</td>" for name in field_names)
        body_rows.append(f"<tr><td>{i}</td>{cells}</tr>")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Data preview -- {schema.schemaCode}</title>
<style>{_STYLE}</style>
</head>
<body>
  <h1>{schema.schemaCode} -- new records</h1>
  <div class="subtitle">{len(records)} record(s) about to be created.</div>
  <table>
    <tr><th>#</th>{header}</tr>
    {''.join(body_rows)}
  </table>
</body>
</html>
"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path
