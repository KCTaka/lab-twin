#!/usr/bin/env python3
"""Stage 3a - STEP/IGES -> raw USD with Isaac Sim's CAD Converter (HOOPS), headless.

Works over plain ssh: it launches Kit with --no-window and the converter's own
CLI entry point (omni.services.convert.cad .../process/hoops_main.py), exactly as
documented in that extension's docs/Usage.md section 3.0.

Input : spec with family "cad" and cad.file = path to the vendor file (under sources/)
Output: build/<id>/cad_raw.usda        (converter units: mm, Z-up; NOT yet an asset)
        build/<id>/cad_convert.json    (source sha256, options, timings, log path)

The raw file is wrapped into a metre-scale asset with collision and mass by
s4_author_usd.py; keep converter output and authored asset separate so a
re-conversion never clobbers hand-tuned physics.

Usage:  python pipeline/s3_step_convert.py <asset_id> [...]   [--isaac ~/isaacsim]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BUILD, ROOT, load_spec, set_status, sha256  # noqa: E402

# Converter options (omni.kit.converter.hoops_core docs/SKILL.md, "Converter Options").
#  instancingStyle 0 : no instancing, so CollisionAPI can be applied to every mesh
#  upAxis 2          : force Z-up
#  tessLOD 2         : medium tessellation (0 extra-low .. 4 extra-high)
OPTIONS = {"instancingStyle": 0, "instancing": False, "upAxis": 2, "tessLOD": 2,
           "materialType": 1, "useNormals": True, "dedup": True}


def convert(aid: str, isaac: Path) -> dict:
    spec = load_spec(aid)
    src = (ROOT / spec["cad"]["file"]).resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    out_dir = BUILD / aid
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = dict(OPTIONS, **spec["cad"].get("converter_options", {}))
    (out_dir / "cad_options.json").write_text(json.dumps(opts, indent=2))
    svc = sorted(glob.glob(str(isaac / "extscache" / "omni.services.convert.cad-*")))
    if not svc:
        raise RuntimeError("omni.services.convert.cad not found in extscache")
    script = Path(svc[-1]) / "omni/services/convert/cad/services/process/hoops_main.py"
    out_usd = out_dir / "cad_raw.usda"
    log = out_dir / "cad_convert.log"
    cmd = [str(isaac / "kit" / "kit"), "--enable", "omni.kit.converter.hoops_core", "--no-window",
           "--exec", f'{script} --input-path "{src}" --output-path "{out_usd}" '
                     f'--config-path "{out_dir / "cad_options.json"}"',
           "--/app/fastShutdown=1", "--info"]
    t0 = time.time()
    with open(log, "w") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=isaac, timeout=1800).returncode
    text = log.read_text(errors="replace")
    ok = rc == 0 and "File conversion is successful" in text and out_usd.exists()
    rec = {"asset_id": aid, "source": str(src.relative_to(ROOT)), "source_sha256": sha256(src),
           "options": opts, "returncode": rc, "ok": ok, "seconds": round(time.time() - t0, 1),
           "log": str(log.relative_to(ROOT)), "converter_script": str(script)}
    if ok:   # independent B-rep reading: ground-truth bbox, exact volume, auto key points
        from cad_analyze import analyze
        t1 = time.time()
        res = analyze(src)
        res["source_sha256"] = rec["source_sha256"]
        (out_dir / "cad_analysis.json").write_text(json.dumps(res, indent=2))
        rec["analysis_seconds"] = round(time.time() - t1, 1)
        rec["analysis"] = {"n_solids": res["n_solids"], "bbox_mm": res["bbox_mm"]["size"],
                           "volume_mm3": res["volume_mm3"], "key_points": sorted(res["key_points"])}
    (out_dir / "cad_convert.json").write_text(json.dumps(rec, indent=2))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--isaac", default=os.environ.get("ISAAC_SIM_DIR", str(Path.home() / "isaacsim")))
    a = ap.parse_args()
    for aid in a.ids:
        try:
            rec = convert(aid, Path(a.isaac))
            set_status(aid, "s3_geometry", "done" if rec["ok"] else "blocked:cad_convert_failed")
            print(f"S3a {'OK  ' if rec['ok'] else 'FAIL'} {aid}  {rec['seconds']} s  log={rec['log']}")
        except Exception as e:
            set_status(aid, "s3_geometry", f"blocked:{type(e).__name__}")
            print(f"S3a FAIL {aid}: {e}")


if __name__ == "__main__":
    main()
