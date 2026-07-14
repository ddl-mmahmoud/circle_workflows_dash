#!/usr/bin/env bash
# Launch the CircleCI workflow search Dash app.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec uv run python app.py
