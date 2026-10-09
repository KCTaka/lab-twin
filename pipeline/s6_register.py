#!/usr/bin/env python3
"""Stage 6 - Register: validated assets -> assets/library.json

The library is what scene configs reference (cf. MATTERIX Fig. 3: a scene is a
config dict of asset ids + poses; the asset carries geometry, physics and key
points). Only assets whose s5_validate starts with `done` are registered.

Each entry records: usd path, family, semantic class, footprint/height (m),
key points (m, asset frame), body type, mass, provisional params, and the
validation report path, so a scene builder never needs to open the spec.

Usage:  python pipeline/s6_register.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generators  # noqa: E402
from common import ASSETS, BUILD, LIBRARY, ROOT, load_spec, provisional_params, read_manifest, set_status  # noqa: E402


def entry(row):
    aid = row["asset_id"]
    spec = load_spec(aid)
    val = json.loads((ASSETS / aid / "validation.json").read_text())
    ax = {a["axis"]: a["got_mm"] for a in val["checks"]["C2_bbox"]["axes"]}
    kps = {}
    if spec["family"] != "cad":
        _, kp_list = generators.build(spec)
        kps = {k.name: {"pos_m": [round(c / 1000, 5) for c in k.pos], "normal": list(k.normal), "support": k.is_support(),
                        **({"data": k.data} if k.data else {})} for k in kp_list}
    else:
        kps = {k: {"pos_m": [c / 1000 for c in v["pos_mm"]], "normal": v.get("normal", [0, 0, 1])}
               for k, v in spec.get("key_points", {}).items()}
    authored = json.loads((BUILD / aid / "authored.json").read_text())
    mass = (authored.get("mass_kg") or {}).get("value")   # stage 4 may compute it (CAD volume x density)
    return {
        "usd": str((ASSETS / aid / f"{aid}.usda").relative_to(ROOT)),
        "family": spec["family"], "semantic_class": spec.get("semantic_class", ""),
        "manufacturer": spec["inventory"]["manufacturer"], "part_number": spec["inventory"]["part_number"],
        "instances_owned": int(spec["inventory"].get("instances") or 0),
        "size_m": [ax["x"] / 1000, ax["y"] / 1000, ax["z"] / 1000],
        "body": spec["physics"]["body"], "mass_kg": mass,
        "key_points": kps,
        "provisional": provisional_params(spec),
        "validation": str((ASSETS / aid / "validation.json").relative_to(ROOT)),
        "thumbnail": str((ASSETS / aid / "thumb.png").relative_to(ROOT)),
    }


def main():
    lib = {}
    for r in read_manifest():
        if r["s5_validate"].startswith("done"):
            lib[r["asset_id"]] = entry(r)
            set_status(r["asset_id"], "s6_register", "done")
    LIBRARY.write_text(json.dumps({"schema": "lab_twin.library/1", "assets": lib}, indent=2) + "\n")
    print(f"S6 registered {len(lib)} assets -> {LIBRARY.relative_to(ROOT)}")
    for aid, e in lib.items():
        print(f"   {aid:45s} {e['family']:16s} {'x'.join(f'{v:.3f}' for v in e['size_m'])} m"
              + (f"   PROVISIONAL {e['provisional']}" if e["provisional"] else ""))


if __name__ == "__main__":
    main()
