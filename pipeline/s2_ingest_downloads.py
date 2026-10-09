#!/usr/bin/env python3
"""Stage 2 (bulk) - ingest vendor CAD downloads from a folder into the pipeline.

    .venv/bin/python pipeline/s2_ingest_downloads.py ~/Downloads/lab-twin-cad [--dry-run]

For each .step/.stp/.igs/.iges/.zip in the folder (not recursive):
  1. zip files are extracted into a fresh build/ingest/<zip stem>/ (only CAD members,
     path-traversal-safe); downloaded files are treated as untrusted data
  2. the part number is read from the file name (pipeline/cadlib.file_tokens) and
     matched to manifest rows by geometry key (pack variants share one file)
  3. for every matching row: the STEP is copied to sources/<asset_id>/, SOURCE.json
     records url + sha256, the spec becomes family "cad", and the row is activated
     (scope cad_ingested; s1_normalize keeps it active on re-runs)
Ambiguous or unmatched files are reported and left alone; nothing is guessed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cadlib import cad_key, file_tokens, infer_density  # noqa: E402
from common import BUILD, ROOT, SOURCES, STAGES, read_manifest, save_spec, sha256, spec_path, write_manifest  # noqa: E402

CAD_EXT = (".step", ".stp", ".igs", ".iges")
PRODUCT_URL = {"Thorlabs": "https://www.thorlabs.com/item/{pn}"}


def extract_cad(zpath: Path) -> list[Path]:
    dest = BUILD / "ingest" / zpath.stem
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    out = []
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            name = Path(m.filename).name           # flatten: drops any ../ or absolute path
            if m.is_dir() or not name.lower().endswith(CAD_EXT):
                continue
            target = dest / name
            with z.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    folder = Path(a.folder).expanduser()
    rows = read_manifest()
    by_key = {}
    for r in rows:
        if r["route"] in ("VENDOR_STEP", "DATASHEET_DIMS") or r["scope"] == "cad_ingested":
            by_key.setdefault(cad_key(r["part_number"]), []).append(r)

    report = {"matched": [], "unmatched": [], "ambiguous": []}
    for f in sorted(folder.iterdir()):
        if not f.is_file() or not f.name.lower().endswith(CAD_EXT + (".zip",)):
            continue
        cands = extract_cad(f) if f.suffix.lower() == ".zip" and not a.dry_run else ([f] if f.suffix.lower() != ".zip" else [])
        toks = set(file_tokens(f.name))
        for c in cands:
            toks |= file_tokens(c.name)
        keys = {cad_key(t) for t in toks} & set(by_key)
        if len(keys) != 1:
            report["ambiguous" if keys else "unmatched"].append({"file": f.name, "tokens": sorted(toks), "keys": sorted(keys)})
            continue
        key = keys.pop()
        if a.dry_run:
            report["matched"].append({"file": f.name, "key": key, "assets": [r["asset_id"] for r in by_key[key]]})
            continue
        if not cands:
            report["unmatched"].append({"file": f.name, "reason": "zip without STEP/IGES members"})
            continue
        # several CAD members: prefer one whose own name names the part, else the largest
        named = [c for c in cands if key in {cad_key(t) for t in file_tokens(c.name)}]
        step = max(named or cands, key=lambda p: p.stat().st_size)
        digest = sha256(step)
        for r in by_key[key]:
            aid = r["asset_id"]
            d = SOURCES / aid
            d.mkdir(parents=True, exist_ok=True)
            dest = d / step.name
            shutil.copy2(step, dest)
            recs = json.loads((d / "SOURCE.json").read_text()) if (d / "SOURCE.json").exists() else []
            if not any(x.get("sha256") == digest for x in recs):
                url = PRODUCT_URL.get(r["manufacturer"], "{pn}").format(pn=key)
                recs.append({"file": str(dest.relative_to(ROOT)), "kind": "step", "sha256": digest,
                             "bytes": dest.stat().st_size, "url": url, "downloaded_as": f.name,
                             "note": "downloaded in a browser by a person or a supervised agent",
                             "retrieved": dt.date.today().isoformat()})
                (d / "SOURCE.json").write_text(json.dumps(recs, indent=2) + "\n")
            spec = json.loads(spec_path(aid).read_text()) if spec_path(aid).exists() else {
                "asset_id": aid,
                "inventory": {k: r[k] for k in ("manufacturer", "part_number", "description", "qty", "instances")},
                "params": {}, "notes": ""}
            spec.update({"route": "VENDOR_STEP", "family": "cad", "semantic_class": r["category"].lower()})
            spec["cad"] = {**spec.get("cad", {}), "file": str(dest.relative_to(ROOT)), "sha256": digest}
            phys = spec.get("physics", {})
            spec["physics"] = {"body": phys.get("body", "dynamic"), "collision": phys.get("collision", "auto"),
                               "density_kg_m3": phys.get("density_kg_m3") or infer_density(r["part_number"], r["description"]),
                               "mass_kg": {"value": None, "src": "vendor_cad"}}
            save_spec(spec)
            r["scope"], r["family"], r["route"] = "cad_ingested", "cad", "VENDOR_STEP"
            r["s2_source"] = "done"
            for s in STAGES[1:]:
                if r[s].startswith("skip") or r[s] == "":
                    r[s] = "todo"
        report["matched"].append({"file": f.name, "key": key, "step": step.name, "assets": [r["asset_id"] for r in by_key[key]]})
    if not a.dry_run:
        write_manifest(rows)
    for k, v in report.items():
        print(f"{k}: {len(v)}")
        for x in v:
            print("   ", json.dumps(x))
    return 1 if report["ambiguous"] else 0


if __name__ == "__main__":
    sys.exit(main())
