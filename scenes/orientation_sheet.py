"""Render every stable resting pose of a CAD part side by side, for orientation review.

    ~/isaacsim/python.sh scenes/orientation_sheet.py <asset_id> [...]
    -> build/<asset_id>/orientation_sheet.png

Poses come from build/<id>/cad_analysis.json (orientation.stable_candidates, i.e.
statically stable flat bases). The proposed pose is outlined in green. The reviewer
(person or vision-capable agent) then either accepts the proposal or writes the chosen
direction into the spec:

    "cad": {..., "down_axis": "+Y",
            "orientation_review": {"by": "...", "date": "YYYY-MM-DD", "basis": "..."}}
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from isaacsim import SimulationApp  # noqa: E402

W, H = 1600, 600
app = SimulationApp({"headless": True, "width": W, "height": H, "renderer": "RayTracedLighting"})

import numpy as np  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdLux  # noqa: E402

DOWN_ROT = {"-Z": ("X", 0.0), "+Z": ("X", 180.0), "-Y": ("X", 90.0), "+Y": ("X", -90.0),
            "-X": ("Y", -90.0), "+X": ("Y", 90.0)}


def sheet(aid, only=None):
    ana = json.loads((ROOT / "build" / aid / "cad_analysis.json").read_text())
    o = ana["orientation"]
    stable = o.get("stable_candidates") or [o["down_in_file"]]
    # stable poses first, then the rest: an as-used pose (e.g. a mirror mount on a post)
    # need not be one the part can hold on its own
    cands = stable + [d for d in DOWN_ROT if d not in stable]
    if only:
        cands = [d for d in cands if d in only]
    raw = ROOT / "build" / aid / "cad_raw.usda"
    ctx = omni.usd.get_context()
    ctx.new_stage()
    st = ctx.get_stage()
    UsdGeom.SetStageUpAxis(st, "Z")
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    UsdLux.DomeLight.Define(st, "/World/Dome").CreateIntensityAttr(350)
    sun = UsdLux.DistantLight.Define(st, "/World/Sun")
    sun.CreateIntensityAttr(1800)
    UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-45, 20, 0))
    mpu = UsdGeom.GetStageMetersPerUnit(Usd.Stage.Open(str(raw)))
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
    x, sizes = 0.0, []
    for i, d in enumerate(cands):
        outer = UsdGeom.Xform.Define(st, f"/World/pose_{i}")
        top = outer.AddTranslateOp()
        inner = UsdGeom.Xform.Define(st, f"/World/pose_{i}/rot")
        ax, deg = DOWN_ROT[d]
        (inner.AddRotateXOp() if ax == "X" else inner.AddRotateYOp()).Set(deg)
        inner.AddScaleOp().Set(Gf.Vec3f(mpu, mpu, mpu))
        st.DefinePrim(f"/World/pose_{i}/rot/cad").GetReferences().AddReference(str(raw))
        r = bc.ComputeWorldBound(inner.GetPrim()).ComputeAlignedRange()
        lo, hi = r.GetMin(), r.GetMax()
        size = hi - lo
        gap = 0.4 * max(size[0], size[1], size[2])
        x += size[0] / 2 + (gap if i else 0)
        top.Set(Gf.Vec3d(x - (lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
        sizes.append((x, size))
        x += size[0] / 2
        bc.Clear()
    span = x
    hmax = max(s[1][2] for s in sizes)
    floor = UsdGeom.Cube.Define(st, "/World/floor")
    floor.CreateSizeAttr(1.0)
    floor.AddTranslateOp().Set(Gf.Vec3d(span / 2, 0, -0.0005))
    floor.AddScaleOp().Set(Gf.Vec3f(span * 3, span * 3, 0.001))
    floor.CreateDisplayColorAttr([Gf.Vec3f(0.85, 0.85, 0.85)])
    cam = UsdGeom.Camera.Define(st, "/World/cam")
    cam.CreateFocalLengthAttr(35.0)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.001, 100))
    # fit the bounding sphere of the whole row in the vertical field of view
    dmax = max(s[1][1] for s in sizes)
    r = 0.5 * math.sqrt(span ** 2 + dmax ** 2 + hmax ** 2)
    fov_v = 2 * math.atan(20.955 * H / W / 2 / 35.0)
    fov_h = 2 * math.atan(20.955 / 2 / 35.0)
    dist = 1.15 * max(r / math.sin(fov_v / 2), (span / 2) / math.tan(fov_h / 2))
    view = Gf.Vec3d(0, -0.88, 0.47).GetNormalized()
    eye = Gf.Vec3d(span / 2, 0, hmax / 2) + view * dist
    UsdGeom.Xformable(cam).AddTransformOp().Set(
        Gf.Matrix4d().SetLookAt(eye, Gf.Vec3d(span / 2, 0, hmax / 2), Gf.Vec3d(0, 0, 1)).GetInverse())
    rp = rep.create.render_product("/World/cam", (W, H))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([rp])
    for _ in range(80):
        app.update()
    rep.orchestrator.step()
    img = Image.fromarray(np.asarray(rgb.get_data())[:, :, :3]).convert("RGB")
    rgb.detach()
    rp.destroy()
    # label each pose at its approximate image column (camera looks along +Y, centred on the row)
    dr = ImageDraw.Draw(img)
    for i, (cx, size) in enumerate(sizes):
        # horizontal image position of the pose centre (pinhole projection, camera on the row axis)
        u = int(W / 2 + (cx - span / 2) / (dist * math.tan(fov_h / 2) * 0.88) * (W / 2))
        prop = cands[i] == o["down_in_file"]
        dr.rectangle([u - 70, 8, u + 70, 40], outline=(0, 160, 0) if prop else (90, 90, 90), width=4 if prop else 1)
        tag = "PROPOSED" if prop else ("stable" if cands[i] in stable else "not stable")
        dr.text((u - 62, 16), f"{i}: down {cands[i]}  {tag}", fill=(0, 0, 0))
    dr.text((10, H - 22), f"{aid}  rule: {o.get('rule', '')}  confidence: {o.get('confidence')}", fill=(0, 0, 0))
    out = ROOT / "build" / aid / ("orientation_sheet.png" if not only else f"orientation_sheet_{'_'.join(only)}.png")
    img.save(out)
    print(f"SHEET {aid}: {cands} proposed {o['down_in_file']} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        args = [a for a in sys.argv[1:] if not a.startswith("--poses=")]
        only = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--poses=")), None)
        for a in args:
            sheet(a, only)
    finally:
        app.close()
