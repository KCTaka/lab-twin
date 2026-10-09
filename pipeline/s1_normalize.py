#!/usr/bin/env python3
"""Stage 1 - Normalize: triage.csv -> manifest.csv (+ spec stubs for in-scope rows).

What it does, deterministically:
  1. Splits rows whose part-number field bundles several P/Ns ("A; B; C") into
     one child row each (asset_id = <parent>__<pn>), flagged `split_from_group`.
  2. Expands pack rows: instances = qty x pack_size (a "5 pack" is ONE asset type).
  3. Assigns a scope and a geometry family using the explicit rule table below.
     Rules are matched on manufacturer + part-number regex so every decision is
     auditable; anything unmatched keeps scope from its priority and family "".
  4. Writes a spec stub (specs/<asset_id>.json) for every row in an active scope
     that does not already have a spec. Existing specs are NEVER overwritten.

Usage:  python pipeline/s1_normalize.py [--triage data/triage.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, MANIFEST, SPECS, STAGES, spec_path, write_manifest  # noqa: E402

ACTIVE_SCOPES = {"layout_L1", "pipeline_test"}

# Synthetic rows that exercise routes end to end (not inventory items).
FIXTURES = [{
    "asset_id": "FIXTURE_cad_tr2_like_post", "scope": "pipeline_test", "route": "VENDOR_STEP",
    "family": "cad", "category": "Optomechanics", "manufacturer": "FIXTURE",
    "part_number": "TR2-like", "description": "Synthetic 12.7 mm x 50.8 mm post, STEP made by tests/make_fixture_step.py",
    "qty": 1, "pack_size": 1, "instances": 1, "notes": "pipeline self-test for the STEP route",
}]

# (manufacturer regex, part-number regex, scope, family, route override or None, note)
RULES = [
    # --- optical tables / isolation: parametric beats vendor STEP for layout -------------
    (r"Newport", r"^OTS-UT2-48-12-I$", "layout_L1", "optical_table", "PARAMETRIC_PRIMITIVE",
     "parametric preferred over vendor STEP: tapped-hole grids inflate meshes and collision"),
    (r"Newport", r"^VIS3672", "layout_L1", "optical_table", "PARAMETRIC_PRIMITIVE",
     "IsoStation workstation modelled as optical_table (frame legs = isolators)"),
    (r"^TMC", r"^784-655-02R$", "layout_L1", "optical_table", "PARAMETRIC_PRIMITIVE",
     "top only was purchased; support frame is pre-existing, dims needed"),
    (r"Newport", r"^OTS-8$", "layout_L1", "overhead_shelf", "PARAMETRIC_PRIMITIVE", ""),
    (r"Newport", r"^ATS-HS-12$", "layout_L1", "hanging_shelf", "PARAMETRIC_PRIMITIVE",
     "hanging shelf below the OTS overhead shelf"),
    (r"Newport", r"^ACWS$", "layout_L1", "appliance_box", "PARAMETRIC_PRIMITIVE", "air compressor"),
    (r"Laservision", r"^F5P06$", "layout_L1", "curtain", None, "rigid panel until cloth model exists"),
    # --- furniture ------------------------------------------------------------------------
    (r"Rousseau", r"MODULAR-WORKBENCH", "layout_L1", "workbench", None, ""),
    (r"Rousseau", r"SPIDER-SERIES", "layout_L1", "shelving_closed", None, ""),
    (r"Quartet", r"^QRT02304", "layout_L1", "wall_panel", None, "cork board"),
    (r"Quartet", r"^QRT66080", "layout_L1", "projector_screen", None, ""),
    (r"Premier", r"^AV34$", "layout_L1", "cart", "PARAMETRIC_PRIMITIVE", ""),
    (r"UNKNOWN", r"^AVJ42$", "layout_L1", "cart", "PARAMETRIC_PRIMITIVE", "maker unknown"),
    (r"VWR", r"^10819-650$", "layout_L1", "appliance_box", "PARAMETRIC_PRIMITIVE", "undercounter fridge"),
    # --- explicitly NOT layout ------------------------------------------------------------
    (r"Mirrorcle", r"BREADBOARD", "bench_items", "", None, "4.5 in breadboard: bench-scale"),
    (r"Thorlabs", r"^CS2010$", "bench_items", "", None, "bench-top UV curing unit"),
    (r"Harvard", r"^75-0900$", "bench_items", "", None, "bench-top micro drill"),
    # large process tools: footprint matters, but which room they are in is unknown
    (r"Edwards|Julabo|Oxford", r".*", "layout_L2_room_tbd", "appliance_box", None,
     "large process tool; confirm which room before placing"),
]

RE_PACK = re.compile(r"(\d+)\s*[- ]?pack|pack of (\d+)", re.I)


def _slug(s: str, n: int = 24) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s)[:n].strip("_")


def classify(mfr: str, pn: str, priority: str, category: str):
    for rm, rp, scope, fam, route, note in RULES:
        if re.search(rm, mfr) and re.search(rp, pn):
            return scope, fam, route, note
    if priority == "X":
        return "excluded", "", None, ""
    if category == "Instrument":
        return "instruments_deferred", "", None, "instrument: deferred (doc-to-asset pipeline later)"
    return f"backlog_{priority}", "", None, ""


def normalize(triage_rows):
    out = []
    for r in triage_rows:
        pn_field = r["Model / part no."].strip()
        pns = [p.strip() for p in pn_field.split(";")] if ";" in pn_field else [pn_field]
        pack = RE_PACK.search(r["Description"])
        pack_size = int(pack.group(1) or pack.group(2)) if pack else 1
        try:
            qty = int(float(r["Total qty purchased"] or 0))
        except ValueError:
            qty = 0
        for pn in pns:
            aid = r["asset_id"] if len(pns) == 1 else f"{r['asset_id']}__{_slug(pn)}"
            flags = [f for f in r["flags"].split("|") if f]
            if len(pns) > 1:
                flags = [f for f in flags if f != "grouped_row"] + ["split_from_group", "qty_uncertain"]
            scope, fam, route_override, note = classify(r["Manufacturer"], pn, r["priority"], r["Category"])
            route = r["route"]
            if len(pns) > 1 and route == "SPLIT_ROW_FIRST":
                route = "UNROUTED_AFTER_SPLIT"
            if route_override:
                note = (note + "; " if note else "") + f"route {route} -> {route_override}"
                route = route_override
            row = {
                "asset_id": aid,
                "parent_asset_id": r["asset_id"] if len(pns) > 1 else "",
                "scope": scope, "priority": r["priority"], "route": route, "family": fam,
                "category": r["Category"], "manufacturer": r["Manufacturer"], "part_number": pn,
                "description": r["Description"], "qty": qty, "pack_size": pack_size,
                "instances": qty * pack_size,
                "w_mm": r["w_mm"], "d_mm": r["d_mm"], "h_mm": r["h_mm"], "dia_mm": r["dia_mm"],
                "product_page": r["Product page"], "datasheet": r["Datasheet"],
                "link_confidence": r["Link confidence"], "flags": "|".join(flags), "notes": note,
            }
            active = scope in ACTIVE_SCOPES
            for s in STAGES:
                row[s] = "todo" if active else f"skip:{scope}"
            out.append(row)
    return out


def spec_stub(row: dict) -> dict:
    """Minimal spec. Params are filled in stage 3 (see docs/stage_sheets/S3_*.md)."""
    params = {}
    for k_csv, k in (("w_mm", "w"), ("d_mm", "d"), ("h_mm", "h")):
        if row[k_csv]:
            params[k] = {"value": round(float(row[k_csv]), 2), "unit": "mm", "src": "inventory",
                         "quote": row["description"][:120]}
    return {
        "asset_id": row["asset_id"],
        "inventory": {k: row[k] for k in ("manufacturer", "part_number", "description", "qty", "instances")},
        "route": row["route"],
        "family": row["family"],
        "params": params,
        "physics": {"body": "static", "collision": "box", "mass_kg": {"value": None, "src": "assumed"}},
        "semantic_class": row["family"] or row["category"].lower(),
        "notes": row["notes"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--triage", default=str(DATA / "triage.csv"))
    a = ap.parse_args()
    with open(a.triage, newline="", encoding="utf-8-sig") as f:
        rows = normalize(list(csv.DictReader(f)))
    for fx in FIXTURES:
        rows.append({**{k: "" for k in rows[0]}, **fx, **{s: "todo" for s in STAGES}})

    # keep stage progress from a previous manifest (stage 1 must be re-runnable)
    if MANIFEST.exists():
        with open(MANIFEST, newline="", encoding="utf-8") as f:
            prev = {r["asset_id"]: r for r in csv.DictReader(f)}
        for r in rows:
            if r["asset_id"] in prev and not r["s2_source"].startswith("skip"):
                for s in STAGES:
                    r[s] = prev[r["asset_id"]].get(s) or r[s]
    write_manifest(rows)

    SPECS.mkdir(exist_ok=True)
    new = 0
    for r in rows:
        if r["scope"] in ACTIVE_SCOPES and not spec_path(r["asset_id"]).exists():
            spec_path(r["asset_id"]).write_text(json.dumps(spec_stub(r), indent=2) + "\n")
            new += 1

    from collections import Counter
    print(f"manifest rows: {len(rows)}  (from {len(set(r['parent_asset_id'] or r['asset_id'] for r in rows))} inventory rows)")
    for k, v in sorted(Counter(r["scope"] for r in rows).items()):
        print(f"  {k:24s} {v}")
    print(f"new spec stubs: {new}")


if __name__ == "__main__":
    main()
