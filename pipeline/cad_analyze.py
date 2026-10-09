#!/usr/bin/env python3
"""B-rep analysis of a vendor STEP file, independent of the USD converter.

    .venv/bin/python pipeline/cad_analyze.py <asset_id>        # reads spec cad.file
    .venv/bin/python pipeline/cad_analyze.py --file part.step  # ad hoc, prints JSON

Why a second reader: the HOOPS converter (S3a) produces the render/collision mesh,
but checking that mesh against its own bbox would be circular. OpenCASCADE (via
CadQuery) reads the same STEP exactly, so it supplies
  * the ground-truth bbox (catches unit and axis-permutation errors in S5 C2),
  * exact solid volumes -> mass = sum(V_i) x density (mesh volume is a cross-check),
  * automatic key points from the B-rep, in the asset frame used by S4
    (footprint centre on the floor, z = 0 at the lowest point):
      mount_hole_k     cylindrical holes opening on the bottom plane (normal -Z)
      top_hole_k       holes opening on the top plane (normal +Z)
      optical_axis     largest through-bore with a horizontal axis (mounts, plates)
      top_surface      a point that is ON the largest upward planar face at the top
                       (support=True, so S5 drop-tests it); omitted if none exists
Every automatic key point carries data.auto = true and the evidence used, so a
human can confirm or override it in the spec.

Output: build/<asset_id>/cad_analysis.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.gp import gp_Pnt2d
from OCP.TopAbs import TopAbs_IN

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BUILD, ROOT, load_spec  # noqa: E402

TOL = 0.05          # mm, geometric coincidence
PLANE_TOL = 0.5     # mm, "opens on the bottom/top plane"

# Thread identification from the modelled hole diameter (mm). Vendors model tapped
# holes at the tap-drill, the minor or the major diameter, so each thread lists all.
THREADS = {
    "4-40": (2.26, 2.39, 2.84), "6-32": (2.69, 2.85, 3.51), "8-32": (2.95, 3.18, 4.17),
    "1/4-20": (5.11, 5.35, 6.35), "M3": (2.5, 2.46, 3.0), "M4": (3.3, 3.24, 4.0),
    "M6": (5.0, 4.92, 6.0), "SM05 (0.535-40)": (12.7, 12.9, 13.59),
    "SM1 (1.035-40)": (25.4, 25.6, 26.29),
}


def thread_guess(d):
    best = min(((abs(d - c), name, which) for name, cands in THREADS.items()
                for which, c in zip(("tap_drill", "minor", "major"), cands)), key=lambda t: t[0])
    return {"thread": best[1], "matched": best[2], "err_mm": round(best[0], 3)} if best[0] < 0.15 else None


def cylinders(shape):
    """Full or partial cylindrical faces: axis, radius, hole-vs-boss, axial extent."""
    out = []
    for f in shape.Faces():
        if f.geomType() != "CYLINDER":
            continue
        cyl = BRepAdaptor_Surface(f.wrapped).Cylinder()
        ax, r = cyl.Axis(), cyl.Radius()
        o = ax.Location()
        d = ax.Direction()
        org = (o.X(), o.Y(), o.Z())
        dirv = (d.X(), d.Y(), d.Z())
        p = f.positionAt(0.5, 0.5)
        n = f.normalAt(p)
        # radial vector from the axis to the sample point
        t = sum((p.toTuple()[i] - org[i]) * dirv[i] for i in range(3))
        foot = [org[i] + t * dirv[i] for i in range(3)]
        rad = [p.toTuple()[i] - foot[i] for i in range(3)]
        is_hole = sum(n.toTuple()[i] * rad[i] for i in range(3)) < 0     # normal points at the axis
        bb = f.BoundingBox()
        # axial extent: project the face bbox corners on the axis
        corners = [(x, y, z) for x in (bb.xmin, bb.xmax) for y in (bb.ymin, bb.ymax) for z in (bb.zmin, bb.zmax)]
        ts = [sum((c[i] - org[i]) * dirv[i] for i in range(3)) for c in corners]
        out.append({"r": r, "d": 2 * r, "axis_pt": org, "axis_dir": dirv, "hole": is_hole,
                    "bb": (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax),
                    "t_range": (min(ts), max(ts)), "area": f.Area()})
    return out


def _merge_coaxial(cyls):
    """Faces of one hole are often split in two half-cylinders: merge by axis + radius."""
    groups = []
    for c in cyls:
        for g in groups:
            a = g[0]
            same_dir = abs(abs(sum(a["axis_dir"][i] * c["axis_dir"][i] for i in range(3))) - 1) < 1e-6
            v = [c["axis_pt"][i] - a["axis_pt"][i] for i in range(3)]
            along = sum(v[i] * a["axis_dir"][i] for i in range(3))
            perp = math.sqrt(max(sum(x * x for x in v) - along * along, 0))
            if same_dir and perp < TOL and abs(a["r"] - c["r"]) < TOL and a["hole"] == c["hole"]:
                g.append(c)
                break
        else:
            groups.append([c])
    merged = []
    for g in groups:
        bb = [min(c["bb"][i] for c in g) for i in range(3)] + [max(c["bb"][i] for c in g) for i in range(3, 6)]
        merged.append({**g[0], "bb": tuple(bb), "area": sum(c["area"] for c in g), "faces": len(g)})
    return merged


def top_support_point(shape, zmax):
    """A point lying ON the largest upward planar face at the top of the part."""
    best = None
    for f in shape.Faces():
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        n = f.normalAt(c)
        if n.z < 0.999 or abs(c.z - zmax) > PLANE_TOL:
            continue
        u, v = f.paramAt(c)
        inside = BRepClass_FaceClassifier(f.wrapped, gp_Pnt2d(u, v), 1e-6).State() == TopAbs_IN
        if inside and (best is None or f.Area() > best[1]):
            best = (c.toTuple(), f.Area())
    return best


def analyze(step_path: Path) -> dict:
    wp = cq.importers.importStep(str(step_path))      # OCCT converts to mm on import
    shape = cq.Compound.makeCompound(wp.vals()) if len(wp.vals()) > 1 else wp.val()
    solids = shape.Solids()
    bb = shape.BoundingBox()
    cx, cy, z0 = (bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, bb.zmin
    to_asset = lambda p: [round(p[0] - cx, 4), round(p[1] - cy, 4), round(p[2] - z0, 4)]  # noqa: E731

    vols = [abs(s.Volume()) for s in solids]
    cyls = _merge_coaxial(cylinders(shape))
    kps, k_mount, k_top = {}, 0, 0
    for c in sorted(cyls, key=lambda c: (c["axis_pt"][0], c["axis_pt"][1])):
        if not c["hole"]:
            continue
        vertical = abs(abs(c["axis_dir"][2]) - 1) < 1e-6
        if vertical:
            x, y = c["axis_pt"][0], c["axis_pt"][1]
            tg = thread_guess(c["d"])
            data = {"auto": True, "diameter_mm": round(c["d"], 3), **({"thread_guess": tg} if tg else {})}
            if abs(c["bb"][2] - bb.zmin) < PLANE_TOL:
                kps[f"mount_hole_{k_mount}"] = {"pos_mm": to_asset((x, y, bb.zmin)), "normal": [0, 0, -1],
                                                "support": False, "data": data}
                k_mount += 1
            if abs(c["bb"][5] - bb.zmax) < PLANE_TOL:
                kps[f"top_hole_{k_top}"] = {"pos_mm": to_asset((x, y, bb.zmax)), "normal": [0, 0, 1],
                                            "support": False, "data": data}
                k_top += 1
    # optical axis: largest horizontal bore that runs through the whole part along its axis
    bores = []
    for c in cyls:
        if not c["hole"] or abs(c["axis_dir"][2]) > 1e-6:
            continue
        ax = [i for i in range(3) if abs(c["axis_dir"][i]) > 0.999]
        if not ax:
            continue
        i = ax[0]
        span = (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)
        through = c["bb"][i] - span[i] < PLANE_TOL and span[i + 3] - c["bb"][i + 3] < PLANE_TOL
        if through and c["r"] > 2.0:
            bores.append(c)
    if bores:
        c = max(bores, key=lambda c: c["r"])
        # the point on the axis at the middle of the part along the axis direction
        ctr = [(bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2, (bb.zmin + bb.zmax) / 2]
        t = sum((ctr[i] - c["axis_pt"][i]) * c["axis_dir"][i] for i in range(3))
        p = [c["axis_pt"][i] + t * c["axis_dir"][i] for i in range(3)]
        tg = thread_guess(c["d"])
        kps["optical_axis"] = {"pos_mm": to_asset(p), "normal": [round(v, 6) for v in c["axis_dir"]],
                               "support": False,
                               "data": {"auto": True, "bore_diameter_mm": round(c["d"], 3),
                                        **({"thread_guess": tg} if tg else {})}}
    top = top_support_point(shape, bb.zmax)
    if top:
        kps["top_surface"] = {"pos_mm": to_asset(top[0]), "normal": [0, 0, 1], "support": True,
                              "data": {"auto": True, "face_area_mm2": round(top[1], 2)}}
    return {
        "source": str(step_path),
        "units": "mm (OCCT import)",
        "n_solids": len(solids),
        "bbox_mm": {"min": [bb.xmin, bb.ymin, bb.zmin], "max": [bb.xmax, bb.ymax, bb.zmax],
                    "size": [bb.xlen, bb.ylen, bb.zlen]},
        "volume_mm3": round(sum(vols), 3),
        "solid_volumes_mm3": [round(v, 3) for v in vols],
        "n_cylindrical_features": len(cyls),
        "key_points": kps,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asset_id", nargs="?")
    ap.add_argument("--file")
    a = ap.parse_args()
    if a.file:
        print(json.dumps(analyze(Path(a.file)), indent=2))
        return
    spec = load_spec(a.asset_id)
    res = analyze(ROOT / spec["cad"]["file"])
    out = BUILD / a.asset_id / "cad_analysis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(f"CAD {a.asset_id}: {res['n_solids']} solids, {res['volume_mm3']:.1f} mm^3, "
          f"bbox {[round(v, 2) for v in res['bbox_mm']['size']]} mm, key points {sorted(res['key_points'])}")


if __name__ == "__main__":
    main()
