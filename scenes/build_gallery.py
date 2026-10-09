"""Build scenes/asset_gallery.usda from assets/library.json and render review images.

Run headless (ssh) with Isaac Sim's interpreter:
    ~/isaacsim/python.sh scenes/build_gallery.py [--no-render]

Outputs
  scenes/asset_gallery.usda            every registered asset in a row, 0.5 m gaps, labelled prims
  build/gallery_overview.png           3/4 view of the whole row
  assets/<asset_id>/thumb.png          3/4 view of each asset, framed by its bbox

The gallery is a REVIEW scene (is every asset the right size and shape?), not
the lab layout; the layout is built from scenes/lab_layout.yaml once room
geometry is known.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--no-render", action="store_true")
ap.add_argument("--width", type=int, default=1280)
ap.add_argument("--height", type=int, default=720)
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": args.width, "height": args.height, "renderer": "RayTracedLighting"})

import carb  # noqa: E402
import numpy as np  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

GAP = 0.5
lib = json.loads((ROOT / "assets" / "library.json").read_text())["assets"]
order = sorted(lib, key=lambda a: -lib[a]["size_m"][0] * lib[a]["size_m"][1])

out_path = ROOT / "scenes" / "asset_gallery.usda"
stage = Usd.Stage.CreateNew(str(out_path))
UsdGeom.SetStageUpAxis(stage, "Z")
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
world = UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(world.GetPrim())
UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
floor = UsdGeom.Xform.Define(stage, "/World/Floor")
floor.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.01))
fc = UsdGeom.Cube.Define(stage, "/World/Floor/shape")
fc.CreateSizeAttr(1.0)
fc.CreateDisplayColorAttr([Gf.Vec3f(0.55, 0.56, 0.58)])
UsdPhysics.CollisionAPI.Apply(fc.GetPrim())
UsdLux.DomeLight.Define(stage, "/World/Dome").CreateIntensityAttr(300)
sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
sun.CreateIntensityAttr(1500)
sun.CreateAngleAttr(1.0)
UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-40, 25, 0))

gallery = UsdGeom.Xform.Define(stage, "/World/Gallery")   # must be Xformable or its translate is ignored
x = 0.0
placed = {}
for aid in order:
    sx, sy, sz = lib[aid]["size_m"]
    x += sx / 2
    xf = UsdGeom.Xform.Define(stage, f"/World/Gallery/{aid}")
    xf.AddTranslateOp().Set(Gf.Vec3d(x, 0, 0))
    xf.GetPrim().GetReferences().AddReference(str(Path("..") / lib[aid]["usd"]))
    placed[aid] = (x, sx, sy, sz)
    x += sx / 2 + GAP
total = x - GAP
# shift row so it is centred on the origin; size the floor to fit
gallery.AddTranslateOp().Set(Gf.Vec3d(-total / 2, 0, 0))
UsdGeom.Xformable(fc).AddScaleOp().Set(Gf.Vec3f(total + 4, 8, 0.02))
stage.GetRootLayer().Save()
print(f"GALLERY {len(placed)} assets, row length {total:.2f} m -> {out_path.relative_to(ROOT)}")

if args.no_render:
    app.close()
    sys.exit(0)

import omni.replicator.core as rep  # noqa: E402

ctx = omni.usd.get_context()
ctx.open_stage(str(out_path))
for _ in range(10):
    app.update()
st = ctx.get_stage()
s = carb.settings.get_settings()
s.set("/rtx/post/aa/op", 3)
s.set("/omni/replicator/captureOnPlay", False)

cam = UsdGeom.Camera.Define(st, "/World/ReviewCam")
cam.CreateFocalLengthAttr(24.0)
cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000))
cam_op = UsdGeom.Xformable(cam).AddTransformOp()
rp = rep.create.render_product("/World/ReviewCam", (args.width, args.height))
rgb = rep.AnnotatorRegistry.get_annotator("rgb")
rgb.attach([rp])


def look(eye, target):
    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1))
    cam_op.Set(view.GetInverse())


def shot(path, settle=60):
    for _ in range(settle):
        app.update()
    rep.orchestrator.step()
    img = rgb.get_data()[:, :, :3]
    from PIL import Image
    Image.fromarray(np.asarray(img)).save(path)
    m = float(np.asarray(img).mean())
    return m


APERTURE_V = 20.955 * args.height / args.width   # USD default horizontalAperture, 16:9 render


def frame(cx, sx, sy, sz, fov=2 * math.atan(APERTURE_V / 2 / 24)):
    """Eye position for a 3/4 front-left view that fits a bbox of the given size."""
    r = 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)
    dist = 1.15 * r / math.sin(fov / 2)   # fit the bounding sphere in the vertical FOV
    d = Gf.Vec3d(-0.55, -1.0, 0.6).GetNormalized() * dist
    tgt = (cx, 0, sz / 2)
    return (cx + d[0], d[1], sz / 2 + d[2]), tgt


eye, tgt = frame(0, total, 3, 2.0)
look(eye, tgt)
m = shot(str(ROOT / "build" / "gallery_overview.png"), settle=120)
print(f"RENDER overview mean={m:.1f}")
for aid, (cx, sx, sy, sz) in placed.items():
    eye, tgt = frame(cx - total / 2, sx, sy, sz)
    look(eye, tgt)
    p = ROOT / "assets" / aid / "thumb.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    m = shot(str(p))
    print(f"RENDER {aid} mean={m:.1f}" + ("  WARNING: near-black or blown-out frame" if m < 8 or m > 247 else ""))
app.close()
