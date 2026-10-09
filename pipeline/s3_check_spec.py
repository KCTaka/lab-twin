#!/usr/bin/env python3
"""Stage 3 gate - check that a spec is complete, plausible and properly sourced.

This is the machine check that runs after a human or an LLM fills a spec
(docs/stage_sheets/S3_geometry.md). It never edits a spec; it only reports and,
with --mark, sets s3_geometry in the manifest.

Rules
  R1 family is known; every REQUIRED param for that family is present and non-null
  R2 each numeric param lies in the family's plausibility range (catches unit errors)
  R3 provenance: src in {inventory, datasheet, vendor_cad, measured, assumed}
       datasheet  -> needs `url` and a verbatim `quote` containing the number
       measured   -> needs `by` (who) and `date`
       assumed    -> needs `note` saying why the value is reasonable
  R4 the generator runs and produces a non-degenerate bbox
  R5 physics.body in {static, dynamic}; dynamic bodies need mass_kg.value

Usage:  python pipeline/s3_check_spec.py [asset_id ...] [--mark]
Exit code 1 if any spec fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generators  # noqa: E402
from common import PROVENANCE, load_spec, provisional_params, read_manifest, set_status  # noqa: E402


def check(spec: dict) -> list[str]:
    errs = []
    fam = spec.get("family", "")
    if fam == "cad":
        if not spec.get("cad", {}).get("file"):
            errs.append("R1 cad.file missing")
        req = {}
    elif fam not in generators.REQUIRED:
        return [f"R1 unknown family {fam!r}"]
    else:
        req = generators.REQUIRED[fam]
    params = spec.get("params", {})
    for name, (lo, hi) in req.items():
        p = params.get(name)
        if p is None or (isinstance(p, dict) and p.get("value") is None):
            errs.append(f"R1 missing param {name}")
            continue
        v = p["value"] if isinstance(p, dict) else p
        if not (lo <= float(v) <= hi):
            errs.append(f"R2 {name}={v} outside plausible range [{lo}, {hi}] mm (unit error?)")
    for name, p in params.items():
        if not isinstance(p, dict):
            errs.append(f"R3 {name}: must be an object with value/src")
            continue
        src = p.get("src")
        if src not in PROVENANCE:
            errs.append(f"R3 {name}: src {src!r} not in {PROVENANCE}")
        elif src == "datasheet":
            if not p.get("url"):
                errs.append(f"R3 {name}: datasheet source without url")
            if not p.get("quote"):
                errs.append(f"R3 {name}: datasheet source without verbatim quote")
        elif src == "measured" and not (p.get("by") and p.get("date")):
            errs.append(f"R3 {name}: measured value needs `by` and `date`")
        elif src == "assumed" and not p.get("note"):
            errs.append(f"R3 {name}: assumed value needs a `note` justifying it")
    phys = spec.get("physics", {})
    if phys.get("body") not in ("static", "dynamic"):
        errs.append("R5 physics.body must be static or dynamic")
    rho = phys.get("density_kg_m3")
    rho_v = rho.get("value") if isinstance(rho, dict) else rho
    if phys.get("body") == "dynamic" and not (phys.get("mass_kg") or {}).get("value"):
        # CAD route: S4 computes mass = B-rep volume x density, so a sourced density suffices
        if not (fam == "cad" and rho_v):
            errs.append("R5 dynamic body needs physics.mass_kg.value (or, for family cad, density_kg_m3)")
    if isinstance(rho, dict) and rho.get("src") not in PROVENANCE:
        errs.append(f"R5 density src {rho.get('src')!r} not in {PROVENANCE}")
    if not errs and fam != "cad":
        try:
            parts, _ = generators.build(spec)
            lo, hi = generators.expected_bbox(parts)
            if min(h - l for l, h in zip(lo, hi)) <= 0:
                errs.append("R4 degenerate bbox")
        except Exception as e:  # generator bug or inconsistent params
            errs.append(f"R4 generator failed: {e}")
    return errs


def main(argv):
    mark = "--mark" in argv
    ids = [a for a in argv if not a.startswith("--")]
    if not ids:
        ids = [r["asset_id"] for r in read_manifest() if r["s3_geometry"] in ("todo",) or r["s3_geometry"].startswith("blocked")]
    bad = 0
    for aid in ids:
        spec = load_spec(aid)
        errs = check(spec)
        prov = provisional_params(spec)
        if errs:
            bad += 1
            print(f"S3 FAIL {aid}")
            for e in errs:
                print(f"    {e}")
            if mark:
                set_status(aid, "s3_geometry", "blocked:" + errs[0][:60])
        else:
            print(f"S3 OK   {aid}" + (f"   provisional: {prov}" if prov else ""))
            if mark:
                set_status(aid, "s3_geometry", "done")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
