#!/usr/bin/env python3
"""
Dash mockup for circle_workflows.py — same data, with --urls and --where-failed
always enabled, rendered as a browser table instead of a terminal table.

Reuses the query logic from the sibling ../circle_workflows.py rather than
duplicating it.

Required environment variable:
  CIRCLE_TOKEN   CircleCI personal API token
"""

import json
import os
import re
import sys
import threading
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from dash import Dash, Input, Output, State, dash_table, dcc, html, no_update

import circle_workflows as cw  # noqa: E402

DEFAULT_PROJECT_SLUG = "gh/cerebrotech/domino"
DEFAULT_WORKFLOW_NAME = "e2e"

STATUS_COLORS = {
    "success": {"bg": "#e6f4ea", "fg": "#1e7e34"},
    "failed": {"bg": "#fdecea", "fg": "#c62828"},
    "error": {"bg": "#fdecea", "fg": "#c62828"},
    "canceled": {"bg": "#f1f3f4", "fg": "#5f6368"},
    "unauthorized": {"bg": "#fdecea", "fg": "#c62828"},
    "running": {"bg": "#e3f2fd", "fg": "#1565c0"},
    "on_hold": {"bg": "#fff8e1", "fg": "#f9a825"},
    "failing": {"bg": "#fff3e0", "fg": "#e65100"},
}

STATUS_PILL_STYLES = [
    {
        "if": {"column_id": "status", "filter_query": f'{{status}} = "{status}"'},
        "backgroundColor": colors["bg"],
        "color": colors["fg"],
        "fontWeight": "600",
        "borderRadius": "12px",
        "textAlign": "center",
    }
    for status, colors in STATUS_COLORS.items()
]

# On-disk store for permalinked snapshots, one JSON file per snapshot, so
# links survive process restarts and are actually shareable.
SNAPSHOTS_DIR = Path(os.environ.get("SNAPSHOTS_DIR", Path(__file__).parent / "snapshots"))
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
_SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_snapshot_write_lock = threading.Lock()


def _snapshot_path(snapshot_id):
    if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id or ""):
        return None
    return SNAPSHOTS_DIR / f"{snapshot_id}.json"


def _save_snapshot(data, summary, search_params):
    snapshot_id = uuid.uuid4().hex
    payload = {
        "data": data,
        "summary": summary,
        "search_params": search_params,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _snapshot_path(snapshot_id)
    tmp_path = path.with_suffix(".json.tmp")
    with _snapshot_write_lock:
        tmp_path.write_text(json.dumps(payload))
        tmp_path.replace(path)
    return snapshot_id


def _load_snapshot(snapshot_id):
    path = _snapshot_path(snapshot_id)
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    payload["created_at"] = datetime.fromisoformat(payload["created_at"])
    return payload

SEARCH_CONTROLS_STYLE = {
    "display": "grid",
    "gridTemplateColumns": "repeat(4, 1fr)",
    "gap": "12px",
    "alignItems": "end",
    "marginBottom": "16px",
}

TABLE_COLUMNS = [
    {"name": "url", "id": "url", "presentation": "markdown"},
    {"name": "created", "id": "created"},
    {"name": "duration", "id": "duration"},
    {"name": "status", "id": "status"},
    {"name": "workflow", "id": "workflow_name"},
    {"name": "branch", "id": "branch"},
    {"name": "#", "id": "pipeline_number"},
    {"name": "first_failed_job", "id": "first_failed_job"},
    {"name": "last_success_job", "id": "last_success_job"},
]

app = Dash(__name__, requests_pathname_prefix=os.environ.get("DOMINO_RUN_HOST_PATH", "/"))
app.title = "CircleCI Workflow Search"

def serve_layout():
    return html.Div(
        style={"fontFamily": "sans-serif", "margin": "24px", "maxWidth": "2000px"},
        children=[
            dcc.Location(id="url", refresh=False),
            html.H2("CircleCI Workflow Search"),
            html.Div(id="permalink-banner", style={"display": "none"}),
            html.Div(
                id="search-controls",
                style=SEARCH_CONTROLS_STYLE,
                children=[
                    html.Div([
                        html.Label("Project slug"),
                        dcc.Input(id="in-project", type="text", value=DEFAULT_PROJECT_SLUG,
                                  style={"width": "100%"}),
                    ]),
                    html.Div([
                        html.Label("Workflow pattern"),
                        dcc.Input(id="in-workflow", type="text", value=DEFAULT_WORKFLOW_NAME,
                                   placeholder="e.g. test-e2e", style={"width": "100%"}),
                    ]),
                    html.Div([
                        html.Label("Branch (optional)"),
                        dcc.Input(id="in-branch", type="text", style={"width": "100%"}),
                    ]),
                    html.Div([
                        html.Label("Status (optional)"),
                        dcc.Dropdown(
                            id="in-status",
                            options=[{"label": s, "value": s} for s in cw.STATUSES],
                            clearable=True,
                        ),
                    ]),
                    html.Div([
                        html.Label("Since"),
                        dcc.DatePickerSingle(id="in-since", display_format="YYYY-MM-DD",
                                              date=date.today().isoformat()),
                    ]),
                    html.Div([
                        html.Label("Until (optional, default: now)"),
                        dcc.DatePickerSingle(id="in-until", display_format="YYYY-MM-DD"),
                    ]),
                    html.Div([
                        dcc.Checklist(
                            id="in-exact",
                            options=[{"label": " Exact name match", "value": "exact"}],
                            value=[],
                        ),
                    ]),
                    html.Div([
                        html.Button("Search", id="btn-search", n_clicks=0,
                                     style={"width": "100%", "padding": "8px"}),
                    ]),
                ],
            ),
            html.Div(
                style={
                    "display": "flex",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                    "gap": "12px",
                },
                children=[
                    html.Div(id="out-summary", style={"color": "#444"}),
                    html.Button("Create Permalink", id="btn-permalink", n_clicks=0,
                                 style={"padding": "8px 12px", "whiteSpace": "nowrap"}),
                ],
            ),
            html.Div(id="out-permalink-link", style={"margin": "8px 0 12px"}),
            dcc.Loading(
                children=[
                    dash_table.DataTable(
                        id="out-table",
                        columns=TABLE_COLUMNS,
                        data=[],
                        sort_action="native",
                        filter_action="native",
                        page_size=25,
                        style_table={"overflowX": "auto", "borderCollapse": "separate"},
                        style_cell={"textAlign": "left", "padding": "6px", "fontSize": "13px"},
                        style_header={"fontWeight": "bold"},
                        style_data_conditional=STATUS_PILL_STYLES,
                    ),
                ],
            ),
        ],
    )


app.layout = serve_layout


def _dur_str(entry):
    s = cw._fmt_secs(entry.get("duration_secs"))
    return f"({s})" if s and entry.get("duration_in_progress") else s


@app.callback(
    Output("out-table", "data"),
    Output("out-summary", "children"),
    Input("btn-search", "n_clicks"),
    State("in-project", "value"),
    State("in-workflow", "value"),
    State("in-since", "date"),
    State("in-until", "date"),
    State("in-branch", "value"),
    State("in-exact", "value"),
    State("in-status", "value"),
    prevent_initial_call=True,
)
def run_search(_n_clicks, project, workflow, since, until, branch, exact, status):
    if not workflow:
        return [], "Enter a workflow name."
    if not since:
        return [], "Pick a --since date."
    if not cw.CIRCLE_TOKEN:
        return [], "error: CIRCLE_TOKEN environment variable is not set."

    since_dt = cw._parse_dt(since)
    until_dt = cw._parse_dt(until) if until else datetime.now(timezone.utc)
    if since_dt > until_dt:
        return [], f"error: --since ({since}) is after --until ({until})"

    is_exact = "exact" in (exact or [])

    results = []
    pipelines_fetched = 0
    pipelines_in_range = 0

    for pipeline in cw._iter_pipelines(project, branch=branch or None):
        created_str = pipeline.get("created_at", "")
        if not created_str:
            continue

        pipeline_dt = cw._parse_dt(created_str)
        if pipeline_dt < since_dt:
            break
        if pipeline_dt > until_dt:
            pipelines_fetched += 1
            continue

        pipelines_fetched += 1
        pipelines_in_range += 1

        pipeline_branch = (pipeline.get("vcs") or {}).get("branch", "")
        pipeline_number = pipeline.get("number")
        pipeline_id = pipeline["id"]

        for wf in cw._iter_workflows(pipeline_id):
            wf_name = wf.get("name", "")
            if is_exact:
                if wf_name != workflow:
                    continue
            else:
                if workflow not in wf_name:
                    continue

            if status and wf.get("status") != status:
                continue

            wf_id = wf["id"]
            wf_status = wf.get("status", "")
            wf_created = wf.get("created_at", "")
            wf_stopped = wf.get("stopped_at", "")
            in_progress = not wf_stopped and wf_status in cw.IN_PROGRESS_STATUSES
            effective_end = (
                datetime.now(timezone.utc).isoformat() if in_progress else wf_stopped
            )
            first_failed, last_success = cw._get_job_summary(wf_id)
            entry = {
                "workflow_id": wf_id,
                "workflow_name": wf_name,
                "status": wf_status,
                "created": cw._fmt_dt(wf_created),
                "duration_secs": cw._duration_secs(wf_created, effective_end),
                "duration_in_progress": in_progress,
                "pipeline_id": pipeline_id,
                "pipeline_number": pipeline_number,
                "branch": pipeline_branch,
                "url": f"[link]({cw._workflow_url(project, pipeline_number, wf_id)})",
                "first_failed_job": first_failed,
                "last_success_job": last_success,
            }
            results.append(entry)

    for entry in results:
        entry["duration"] = _dur_str(entry)

    results.sort(key=lambda r: r["created"], reverse=True)

    summary = (
        f"Checked {pipelines_fetched} pipeline(s) ({pipelines_in_range} in range); "
        f"found {len(results)} matching workflow run(s)."
    )
    return results, summary


@app.callback(
    Output("out-permalink-link", "children", allow_duplicate=True),
    Input("btn-search", "n_clicks"),
    prevent_initial_call=True,
)
def clear_permalink_on_new_search(_n_clicks):
    return ""


@app.callback(
    Output("out-permalink-link", "children"),
    Input("btn-permalink", "n_clicks"),
    State("out-table", "data"),
    State("out-summary", "children"),
    State("url", "href"),
    State("in-project", "value"),
    State("in-workflow", "value"),
    State("in-since", "date"),
    State("in-until", "date"),
    State("in-branch", "value"),
    State("in-exact", "value"),
    State("in-status", "value"),
    prevent_initial_call=True,
)
def create_permalink(_n_clicks, table_data, summary, href, project, workflow, since, until,
                      branch, exact, status):
    if not table_data:
        return html.Div(
            "Run a search before creating a permalink.",
            style={"color": STATUS_COLORS["failed"]["fg"]},
        )

    search_params = {
        "project": project,
        "workflow": workflow,
        "since": since,
        "until": until,
        "branch": branch,
        "exact": "exact" in (exact or []),
        "status": status,
    }
    snapshot_id = _save_snapshot(table_data, summary, search_params)

    parsed = urllib.parse.urlsplit(href or "/")
    query = urllib.parse.parse_qs(parsed.query)
    query["snapshot"] = [snapshot_id]
    permalink_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query, doseq=True), "")
    )

    return html.Div(
        [
            html.Span("Permalink: "),
            dcc.Link(permalink_url, href=permalink_url, refresh=True, target="_blank"),
        ],
        style={
            "padding": "8px 12px",
            "backgroundColor": "#f1f3f4",
            "borderRadius": "4px",
            "fontFamily": "monospace",
            "wordBreak": "break-all",
        },
    )


@app.callback(
    Output("out-table", "data", allow_duplicate=True),
    Output("out-summary", "children", allow_duplicate=True),
    Output("permalink-banner", "children"),
    Output("permalink-banner", "style"),
    Output("search-controls", "style"),
    Input("url", "search"),
    prevent_initial_call="initial_duplicate",
)
def load_from_url(search):
    params = urllib.parse.parse_qs((search or "").lstrip("?"))
    snapshot_id = (params.get("snapshot") or [None])[0]

    snapshot = _load_snapshot(snapshot_id) if snapshot_id else None
    if snapshot is None:
        return no_update, no_update, "", {"display": "none"}, SEARCH_CONTROLS_STYLE

    created_str = snapshot["created_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
    params = snapshot.get("search_params") or {}
    param_parts = [
        f"project={params.get('project')}",
        f"workflow={params.get('workflow')}",
        f"since={params.get('since')}",
        f"until={params.get('until') or 'now'}",
    ]
    if params.get("branch"):
        param_parts.append(f"branch={params['branch']}")
    if params.get("status"):
        param_parts.append(f"status={params['status']}")
    if params.get("exact"):
        param_parts.append("exact match")
    params_str = ", ".join(param_parts)

    banner = html.Div(
        [
            f"\U0001f4cc Permalinked snapshot from {created_str} — {params_str}",
            html.A("Start a new search", href="?",
                    style={"marginLeft": "16px", "color": "#1565c0"}),
        ],
        style={
            "backgroundColor": "#fff8e1",
            "color": "#8a6d00",
            "padding": "10px 14px",
            "borderRadius": "6px",
            "fontWeight": "600",
            "marginBottom": "16px",
        },
    )
    return snapshot["data"], snapshot["summary"], banner, {"display": "block"}, {"display": "none"}


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8888, threaded=True)
