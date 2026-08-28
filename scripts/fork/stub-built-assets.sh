#!/usr/bin/env bash
# Create the build outputs that parts of the Python test suite expect to exist,
# without doing an actual frontend build.
#
# A local dev tree has these because `make fe` produced them, so tests that
# depend on them pass locally and fail in a clean CI checkout. Building the real
# frontend just to run Python tests is minutes of CI time for no added coverage.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Every write below is skipped if the file already exists, so running this in a
# real dev tree never clobbers a genuine `make fe` build.

# Some tests read index.html / favicon.ico out of the static assets directory.
mkdir -p marimo/_static/assets
[[ -e marimo/_static/index.html ]] || cp frontend/index.html marimo/_static/index.html
[[ -e marimo/_static/favicon.ico ]] || cp frontend/public/favicon.ico marimo/_static/favicon.ico

# RLanguageServer.get_command() returns [] when this bundle is absent, so
# start() returns before it ever spawns a process — which silently turns
# tests/_server/test_lsp.py::test_r_start_delegates_to_super into a failure
# rather than a skip. The test mocks subprocess.Popen, so the file only has to
# exist; its contents are never executed.
mkdir -p marimo/_lsp
[[ -e marimo/_lsp/index.cjs ]] || touch marimo/_lsp/index.cjs

echo "stubbed: marimo/_static/{index.html,favicon.ico}, marimo/_lsp/index.cjs"
