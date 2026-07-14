#!/usr/bin/env python3
"""
Dash mockup for circle_workflows.py — same data, with --urls and --where-failed
always enabled, rendered as a browser table instead of a terminal table.

Reuses the query logic from the sibling ../circle_workflows.py rather than
duplicating it.

Required environment variable:
  CIRCLE_TOKEN   CircleCI personal API token
"""

import logging
import os
import sys
from datetime import date, datetime, timezone

from dash import Dash, Input, Output, State, dash_table, dcc, html

import circle_workflows as cw  # noqa: E402

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("app.pathdebug")

_log.info("all env var names: %s", sorted(os.environ.keys()))
_log.info(
    "env vars possibly related to app base path (with values): %s",
    {k: v for k, v in os.environ.items() if "DOMINO" in k.upper() or "PREFIX" in k.upper() or "APP" in k.upper() or "RUN" in k.upper() or "PATH" in k.upper()},
)

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

TABLE_COLUMNS = [
    {"name": "created", "id": "created"},
    {"name": "duration", "id": "duration"},
    {"name": "status", "id": "status"},
    {"name": "workflow", "id": "workflow_name"},
    {"name": "branch", "id": "branch"},
    {"name": "#", "id": "pipeline_number"},
    {"name": "workflow_id", "id": "workflow_id"},
    {"name": "first_failed_job", "id": "first_failed_job"},
    {"name": "last_success_job", "id": "last_success_job"},
    {"name": "url", "id": "url", "presentation": "markdown"},
]

app = Dash(__name__)
app.title = "CircleCI Workflow Search"

_logged_headers = False


@app.server.before_request
def _log_first_request_headers():
    global _logged_headers
    if not _logged_headers:
        _logged_headers = True
        from flask import request
        _log.info("first request path=%r headers=%r", request.path, dict(request.headers))

app.layout = html.Div(
    style={"fontFamily": "sans-serif", "margin": "24px", "maxWidth": "1400px"},
    children=[
        html.H2("CircleCI Workflow Search"),
        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(4, 1fr)",
                "gap": "12px",
                "alignItems": "end",
                "marginBottom": "16px",
            },
            children=[
                html.Div([
                    html.Label("Project slug"),
                    dcc.Input(id="in-project", type="text", value=DEFAULT_PROJECT_SLUG,
                              style={"width": "100%"}),
                ]),
                html.Div([
                    html.Label("Workflow name"),
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
        dcc.Loading(
            children=[
                html.Div(id="out-summary", style={"color": "#444", "marginBottom": "12px"}),
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


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8888, threaded=True)
