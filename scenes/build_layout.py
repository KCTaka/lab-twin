"""Build a lab layout USD from a YAML layout file + assets/library.json.

    ~/isaacsim/python.sh scenes/build_layout.py scenes/layouts/<name>.yaml [--render]

Layout file (all lengths in metres, angles in degrees, z up; see docs/stage_sheets/LAYOUT.md):

    name: SF4202
    status: draft | measured          # "draft" layouts are stamped as such in the USD
    room:
      outline: [[0,0],[8.5,0],[8.5,6.2],[0,6.2]]   # floor polygon, counter-clockwise
      wall_height: 3.0
      wall_thickness: 0.15
      openings:                       # doors/windows cut as gaps in the wall segment
        - {wall: 0, offset: 1.0, width: 0.95, height: 2.1}
    placements:
      - {id: table_1, asset: <asset_id>, at: [2.0, 1.5], yaw: 90}
      - {id: board_1, asset: <asset_id>, wall: 2, offset: 3.0, z: 0.9}     # wall-mounted
      - {id: shelf_1, asset: <asset_id>, host: table_1.surface_center, at: [0, 0.3], yaw: 0}

Placement rules
  at       : floor position of the asset origin (footprint centre), world XY
  wall     : index i of the outline segment (i -> i+1); the asset back face goes
             against the inner wall face, `offset` along the segment, `z` up
  host     : <placement id>.<key point>; `at` is then an XY offset in the host frame
             (NOT `on:` -- YAML 1.1 parses a bare `on` key as boolean True)
  anchor   : with host, which of THIS asset's key points sits on the host key point
             (default: the asset origin), e.g. a hanging shelf's hang_plane_center
Checks run on build: overlapping footprints (2D) and assets outside the room are
reported as warnings; nothing is silently moved.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("layout")
ap.add_argument("--render", action="store_true")
args = ap.parse_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "width": 1600, "height": 900, "renderer": "RayTracedLighting"})

import yaml  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402

lay = yaml.safe_load(Path(args.layout).read_text())
lib = json.loads((ROOT / "assets" / "library.json").read_text())["assets"]
out = ROOT / "scenes" / f"lab_layout_{lay['name']}.usda"
stage = Usd.Stage.CreateNew(str(out))
UsdGeom.SetStageUpAxis(stage, "Z")
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
world = UsdGeom.Xform.Define(stage, "/World")
stage.SetDefaultPrim(world.GetPrim())
world.GetPrim().SetCustomDataByKey("lab_twin_layout", {"name": lay["name"], "status": lay.get("status", "draft"),
                                                       "source": str(Path(args.layout).name)})
UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
UsdLux.DomeLight.Define(stage, "/World/Lights/Dome").CreateIntensityAttr(250)

# ---- room shell --------------------------------------------------------------------
room = lay["room"]
P = [tuple(p) for p in room["outline"]]
H, T = room.get("wall_height", 3.0), room.get("wall_thickness", 0.15)
xs, ys = [p[0] for p in P], [p[1] for p in P]


def box(path, cx, cy, cz, sx, sy, sz, yaw=0.0, color=(0.8, 0.8, 0.78)):
    xf = UsdGeom.Xform.Define(stage, path)
    xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))
    xf.AddRotateZOp().Set(yaw)
    c = UsdGeom.Cube.Define(stage, path + "/shape")
    c.CreateSizeAttr(1.0)
    c.AddScaleOp().Set(Gf.Vec3f(sx, sy, sz))
    c.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdPhysics.CollisionAPI.Apply(c.GetPrim())


box("/World/Room/Floor", (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, -0.025,
    max(xs) - min(xs) + 2 * T, max(ys) - min(ys) + 2 * T, 0.05, color=(0.45, 0.46, 0.48))
walls = []
for i in range(len(P)):
    (x0, y0), (x1, y1) = P[i], P[(i + 1) % len(P)]
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = -uy, ux                       # inward normal for a CCW outline
    yaw = math.degrees(math.atan2(uy, ux))
    walls.append(((x0, y0), (ux, uy), (nx, ny), L, yaw))
    cuts = sorted([o for o in room.get("openings", []) if o["wall"] == i], key=lambda o: o["offset"])
    segs, s = [], 0.0
    for o in cuts:
        segs.append((s, o["offset"], 0.0, H))
        if o.get("height", H) < H:          # lintel above a door / sill below a window
            segs.append((o["offset"], o["offset"] + o["width"], o.get("sill", 0) + o["height"], H))
        if o.get("sill", 0) > 0:
            segs.append((o["offset"], o["offset"] + o["width"], 0.0, o["sill"]))
        s = o["offset"] + o["width"]
    segs.append((s, L, 0.0, H))
    for k, (a, b, z0, z1) in enumerate(segs):
        if b - a <= 1e-6:
            continue
        m = (a + b) / 2
        cx, cy = x0 + ux * m - nx * T / 2, y0 + uy * m - ny * T / 2   # wall sits outside the outline
        box(f"/World/Room/Wall_{i}_{k}", cx, cy, (z0 + z1) / 2, b - a, T, z1 - z0, yaw)

# ---- placements ---------------------------------------------------------------------
placed, warnings = {}, []


def kp_world(pid, kp):
    (px, py, pz), yaw, aid = placed[pid]
    lx, ly, lz = lib[aid]["key_points"][kp]["pos_m"]
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return px + c * lx - s * ly, py + s * lx + c * ly, pz + lz, yaw


ALLOWED = {"id", "asset", "at", "yaw", "z", "wall", "offset", "host", "anchor"}
for pl in lay["placements"]:
    bad = [k for k in pl if k not in ALLOWED]
    if bad:   # fail loudly: a silently ignored key puts an asset in the wrong place
        sys.exit(f"placement {pl.get('id')}: unknown keys {bad} (allowed: {sorted(ALLOWED)})")
    aid = pl["asset"]
    if aid not in lib:
        warnings.append(f"{pl['id']}: asset {aid} not in library (not validated yet?) - skipped")
        continue
    if "wall" in pl:
        (x0, y0), (ux, uy), (nx, ny), L, wyaw = walls[pl["wall"]]
        d = lib[aid]["size_m"][1]
        x, y = x0 + ux * pl["offset"] + nx * d / 2, y0 + uy * pl["offset"] + ny * d / 2
        z, yaw = pl.get("z", 0.0), wyaw + 180.0     # asset +Y (back) points into the wall
    elif "host" in pl:
        host, kp = pl["host"].split(".", 1)
        hx, hy, hz, hyaw = kp_world(host, kp)
        ox, oy = pl.get("at", [0, 0])
        c, s = math.cos(math.radians(hyaw)), math.sin(math.radians(hyaw))
        x, y, z, yaw = hx + c * ox - s * oy, hy + s * ox + c * oy, hz, hyaw + pl.get("yaw", 0.0)
        if "anchor" in pl:   # this asset's own key point coincides with the host key point
            ax, ay, az = lib[aid]["key_points"][pl["anchor"]]["pos_m"]
            c2, s2 = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
            x, y, z = x - (c2 * ax - s2 * ay), y - (s2 * ax + c2 * ay), z - az
    else:
        x, y = pl["at"]
        z, yaw = pl.get("z", 0.0), pl.get("yaw", 0.0)
    xf = UsdGeom.Xform.Define(stage, f"/World/Lab/{pl['id']}")
    xf.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
    xf.AddRotateZOp().Set(yaw)
    xf.GetPrim().GetReferences().AddReference(str(Path("..") / lib[aid]["usd"]))
    xf.GetPrim().SetCustomDataByKey("lab_twin", {"asset_id": aid, "placement": json.dumps(pl)})
    placed[pl["id"]] = ((x, y, z), yaw, aid)

# ---- sanity checks (2D oriented footprints) ---------------------------------------------
def corners(pid):
    (x, y, _), yaw, aid = placed[pid]
    sx, sy, _ = lib[aid]["size_m"]
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [(x + c * a - s * b, y + s * a + c * b) for a, b in
            ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2))]


def sat_overlap(A, B):   # separating axis test for convex polygons
    for poly in (A, B):
        for i in range(len(poly)):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
            ax, ay = y0 - y1, x1 - x0
            pa = [ax * x + ay * y for x, y in A]
            pb = [ax * x + ay * y for x, y in B]
            if max(pa) <= min(pb) + 1e-4 or max(pb) <= min(pa) + 1e-4:
                return False
    return True


ids = list(placed)
for i, a in enumerate(ids):
    for b in ids[i + 1:]:
        za = (placed[a][0][2], placed[a][0][2] + lib[placed[a][2]]["size_m"][2])
        zb = (placed[b][0][2], placed[b][0][2] + lib[placed[b][2]]["size_m"][2])
        if za[1] <= zb[0] + 1e-3 or zb[1] <= za[0] + 1e-3:
            continue                         # stacked (e.g. shelf above table): no clash
        if sat_overlap(corners(a), corners(b)):
            warnings.append(f"footprint overlap: {a} <-> {b}")
    if any(not (min(xs) - 1e-3 <= x <= max(xs) + 1e-3 and min(ys) - 1e-3 <= y <= max(ys) + 1e-3) for x, y in corners(a)):
        warnings.append(f"{a} extends outside the room bounding box")

stage.GetRootLayer().Save()
print(f"LAYOUT {lay['name']} ({lay.get('status', 'draft')}): {len(placed)} placements -> {out.relative_to(ROOT)}")
for w in warnings:
    print(f"LAYOUT WARNING {w}")

if args.render:
    import carb
    import numpy as np
    import omni.replicator.core as rep
    import omni.usd
    from PIL import Image
    ctx = omni.usd.get_context()
    ctx.open_stage(str(out))
    st = ctx.get_stage()
    carb.settings.get_settings().set("/rtx/post/aa/op", 3)
    # ceiling-less top view and a 3/4 view
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    cam = UsdGeom.Camera.Define(st, "/World/ReviewCam")
    cam.CreateFocalLengthAttr(18.0)
    cam.CreateClippingRangeAttr(Gf.Vec2f(0.05, 500))
    op = UsdGeom.Xformable(cam).AddTransformOp()
    rp = rep.create.render_product("/World/ReviewCam", (1600, 900))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([rp])
    views = {"top": ((cx, cy - 0.01, span * 1.25), (cx, cy, 0)),
             "persp": ((cx - span * 0.55, cy - span * 0.75, span * 0.6), (cx, cy, 0.6))}
    for name, (eye, tgt) in views.items():
        op.Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*tgt), Gf.Vec3d(0, 0, 1)).GetInverse())
        for _ in range(90):
            app.update()
        rep.orchestrator.step()
        p = ROOT / "build" / f"layout_{lay['name']}_{name}.png"
        p.parent.mkdir(exist_ok=True)
        Image.fromarray(np.asarray(rgb.get_data())[:, :, :3]).save(p)
        print(f"RENDER {p.relative_to(ROOT)}")
app.close()
