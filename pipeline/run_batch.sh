#!/usr/bin/env bash
# Run stages 3-gate -> 6 (+ gallery render) for every manifest row that is ready.
# Headless; safe over ssh. Each stage only touches rows whose previous stage is `done`.
#   pipeline/run_batch.sh            # all ready rows
#   pipeline/run_batch.sh <id> ...   # specific assets (re-runs them)
set -euo pipefail
cd "$(dirname "$0")/.."
ISAAC="${ISAAC_SIM_DIR:-$HOME/isaacsim}"
PY=.venv/bin/python
mkdir -p build
echo "== S3 gate (spec check)";   $PY pipeline/s3_check_spec.py "$@" --mark || true
echo "== S4 author USD";          "$ISAAC/python.sh" pipeline/s4_author_usd.py "$@" > build/s4.log 2>&1; grep -E "^S4" build/s4.log || true
echo "== S5 validate";            "$ISAAC/python.sh" pipeline/s5_validate.py "$@" > build/s5.log 2>&1; grep -E "^S5" build/s5.log || true
echo "== S6 register";            $PY pipeline/s6_register.py
echo "== gallery + thumbnails";   "$ISAAC/python.sh" scenes/build_gallery.py > build/gallery.log 2>&1; grep -E "^(GALLERY|RENDER)" build/gallery.log || true
echo "== contact sheet";         $PY scenes/contact_sheet.py
echo "== status report";         $PY pipeline/report.py
