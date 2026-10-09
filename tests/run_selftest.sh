#!/usr/bin/env bash
# Fast self-tests (no Isaac Sim): CAD analyser against ground-truth fixtures, spec gate.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python tests/make_cad_fixtures.py >/dev/null
.venv/bin/python tests/test_cad_analyze.py | tail -1
.venv/bin/python pipeline/s3_check_spec.py FIXTURE_cad_tr2_like_post FIXTURE_cad_ph_like_holder FIXTURE_cad_cp_like_cage_plate
