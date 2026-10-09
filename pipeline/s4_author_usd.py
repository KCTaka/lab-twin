"""Stage 4 - Author USD: specs/<id>.json (+ build/<id>/cad_raw.usda) -> assets/<id>/<id>.usda

Run with Isaac Sim's interpreter (headless, works over ssh):
    ~/isaacsim/python.sh pipeline/s4_author_usd.py [asset_id ...]     # default: all s4_usd=todo

Asset contract (what every asset USD guarantees; checked again in stage 5):
  * metersPerUnit = 1, upAxis = Z, kilogramsPerUnit = 1, defaultPrim = /<asset_id>
  * frame: origin at footprint centre on the floor, +Y = back, front face at -Y
  * /<id>/Geometry/<part>          Xform (translate only)
        /<id>/Geometry/<part>/shape  Cube|Cylinder|Mesh, scale on this prim, CollisionAPI here
  * /<id>/KeyPoints/<name>         Xform (translate); customData: normal, data
  * static body  -> CollisionAPI only (immovable collider)
    dynamic body -> RigidBodyAPI + MassAPI on /<id>
  * customData["lab_twin"] on /<id>: provenance (route, family, spec sha, provisional params)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generators  # noqa: E402
from common import ASSETS, BUILD, read_manifest, set_status, load_spec, spec_path, provisional_params  # noqa: E402

MM = 1e-3
# code version that shapes the geometry: a change here re-authors every asset
GEN_SHA = hashlib.sha256((Path(generators.__file__)).read_bytes() + Path(__file__).read_bytes()).hexdigest()[:12]

# name -> (base colour RGB, roughness, metallic). Visual only; physics friction below.
PALETTE = {
    "stainless_steel": ((0.45, 0.46, 0.47), 0.45, 0.8),
    "painted_steel_dark": ((0.10, 0.10, 0.11), 0.6, 0.2),
    "painted_steel_blue": ((0.08, 0.12, 0.30), 0.55, 0.2),   # "midnight blue steel"
    "painted_steel_grey": ((0.72, 0.73, 0.74), 0.6, 0.1),    # "light grey"
    "painted_steel_white": ((0.88, 0.88, 0.86), 0.6, 0.1),
    "laminate_black": ((0.04, 0.04, 0.04), 0.5, 0.0),
    "cork": ((0.62, 0.46, 0.30), 0.9, 0.0),
    "aluminium": ((0.80, 0.81, 0.82), 0.35, 1.0),
    "screen_matte_white": ((0.95, 0.95, 0.95), 0.95, 0.0),
    "laser_curtain_black": ((0.03, 0.03, 0.03), 0.8, 0.0),
    "rubber_black": ((0.02, 0.02, 0.02), 0.9, 0.0),
    "cad_default": ((0.75, 0.75, 0.75), 0.5, 0.5),
}
# Coulomb friction (static, dynamic), restitution. Order-of-magnitude engineering values.
PHYS_MAT = {"default": (0.6, 0.5, 0.0)}


def _material(stage, root, name):
    path = root.AppendPath(f"Looks/{name}")
    if stage.GetPrimAtPath(path):
        return UsdShade.Material(stage.GetPrimAtPath(path))
    rgb, rough, metal = PALETTE[name]
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, path.AppendChild("PreviewSurface"))
    sh.CreateIdAttr("UsdPreviewSurface")
    sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*rgb))
    sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(rough)
    sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metal)
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


def _physics_material(stage, root):
    path = root.AppendPath("Looks/PhysicsMaterial")
    mat = UsdShade.Material.Define(stage, path)
    pm = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
    sf, df, rest = PHYS_MAT["default"]
    pm.CreateStaticFrictionAttr(sf)
    pm.CreateDynamicFrictionAttr(df)
    pm.CreateRestitutionAttr(rest)
    return mat


def _bind(prim, mat, phys_mat=None):
    api = UsdShade.MaterialBindingAPI.Apply(prim)
    api.Bind(mat)
    if phys_mat is not None:
        api.Bind(phys_mat, UsdShade.Tokens.weakerThanDescendants, "physics")


def _semantics(prim, label):
    """Class label for perception/SDG. Prefer UsdSemantics (OpenUSD >= 24.11)."""
    try:
        from pxr import UsdSemantics
        UsdSemantics.LabelsAPI.Apply(prim, "class").CreateLabelsAttr().Set([label])
    except Exception:  # older USD: fall back to a plain attribute
        prim.CreateAttribute("semantic:class", Sdf.ValueTypeNames.String).Set(label)


def _new_stage(asset_id):
    out = ASSETS / asset_id / f"{asset_id}.usda"
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, f"/{asset_id}")
    stage.SetDefaultPrim(root.GetPrim())
    Usd.ModelAPI(root.GetPrim()).SetKind("component")
    return stage, root, out


def _keypoints(stage, root_path, kps):
    UsdGeom.Scope.Define(stage, root_path.AppendChild("KeyPoints"))
    for kp in kps:
        x = UsdGeom.Xform.Define(stage, root_path.AppendPath(f"KeyPoints/{kp.name}"))
        x.AddTranslateOp().Set(Gf.Vec3d(*[c * MM for c in kp.pos]))
        x.GetPrim().SetCustomDataByKey("normal", Gf.Vec3d(*kp.normal))
        x.GetPrim().SetCustomDataByKey("support", bool(kp.is_support()))
        if kp.data:
            x.GetPrim().SetCustomDataByKey("data", json.dumps(kp.data))


def author_parametric(spec):
    aid = spec["asset_id"]
    parts, kps = generators.build(spec)
    stage, root, out = _new_stage(aid)
    rp = root.GetPath()
    UsdGeom.Scope.Define(stage, rp.AppendChild("Looks"))
    pmat = _physics_material(stage, rp)
    UsdGeom.Scope.Define(stage, rp.AppendChild("Geometry"))
    for pt in parts:
        xf = UsdGeom.Xform.Define(stage, rp.AppendPath(f"Geometry/{pt.name}"))
        xf.AddTranslateOp().Set(Gf.Vec3d(*[c * MM for c in pt.center]))
        if pt.shape == "box":
            g = UsdGeom.Cube.Define(stage, xf.GetPath().AppendChild("shape"))
            g.CreateSizeAttr(1.0)
            g.AddScaleOp().Set(Gf.Vec3f(*[s * MM for s in pt.size]))
        elif pt.shape == "cylinder":
            g = UsdGeom.Cylinder.Define(stage, xf.GetPath().AppendChild("shape"))
            g.CreateAxisAttr("Z")
            g.CreateRadiusAttr(pt.size[0] * MM / 2)
            g.CreateHeightAttr(pt.size[2] * MM)
        else:
            raise ValueError(pt.shape)
        _bind(g.GetPrim(), _material(stage, rp, pt.material), pmat if pt.collide else None)
        if pt.collide:
            UsdPhysics.CollisionAPI.Apply(g.GetPrim())
    _keypoints(stage, rp, kps)
    return stage, root, out, parts


def author_cad(spec):
    """Wrap converter output (mm, Z-up) into a metre-scale asset with collision + mass."""
    aid = spec["asset_id"]
    raw = BUILD / aid / "cad_raw.usda"
    if not raw.exists():
        raise FileNotFoundError(f"{raw} (run s3_step_convert first)")
    raw_stage = Usd.Stage.Open(str(raw))
    raw_mpu = UsdGeom.GetStageMetersPerUnit(raw_stage)
    bb = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(
        raw_stage.GetPseudoRoot()).ComputeAlignedRange()
    lo, hi = bb.GetMin(), bb.GetMax()

    stage, root, out = _new_stage(aid)
    rp = root.GetPath()
    UsdGeom.Scope.Define(stage, rp.AppendChild("Looks"))
    pmat = _physics_material(stage, rp)
    geo = UsdGeom.Xform.Define(stage, rp.AppendChild("Geometry"))
    # recenter: footprint centre to origin, lowest point to z = 0; then convert units
    s = raw_mpu
    geo.AddTranslateOp().Set(Gf.Vec3d(-(lo[0] + hi[0]) / 2 * s, -(lo[1] + hi[1]) / 2 * s, -lo[2] * s))
    geo.AddScaleOp().Set(Gf.Vec3f(s, s, s))
    cad = stage.DefinePrim(rp.AppendPath("Geometry/cad"))
    # copy converter output next to the asset so the asset folder is self-contained
    import shutil
    (ASSETS / aid / "cad").mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw, ASSETS / aid / "cad" / "cad_raw.usda")
    cad.GetReferences().AddReference("./cad/cad_raw.usda")
    approx = spec["physics"].get("collision", "convexHull")
    total_vol = 0.0
    import numpy as np
    for p in Usd.PrimRange(cad):
        if p.IsA(UsdGeom.Mesh):
            UsdPhysics.CollisionAPI.Apply(p)
            UsdPhysics.MeshCollisionAPI.Apply(p).CreateApproximationAttr(approx)
            _bind(p, _material(stage, rp, "cad_default"), pmat)
            m = UsdGeom.Mesh(p)
            pts = np.array(m.GetPointsAttr().Get(), dtype=float)
            fvc = np.array(m.GetFaceVertexCountsAttr().Get())
            if (fvc == 3).all():   # signed volume of a closed triangle mesh (divergence theorem)
                tri = pts[np.array(m.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)]
                total_vol += abs(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum()) / 6.0
    kps = [generators.KeyPoint(k, tuple(v["pos_mm"]), tuple(v.get("normal", (0, 0, 1))), v.get("data", {}))
           for k, v in spec.get("key_points", {}).items()]
    _keypoints(stage, rp, kps)
    vol_m3 = total_vol * s ** 3
    rho = spec["physics"].get("density_kg_m3")
    if rho and spec["physics"].get("mass_kg", {}).get("value") is None:
        spec["physics"]["mass_kg"] = {"value": round(vol_m3 * rho, 6), "src": "vendor_cad",
                                      "quote": f"mesh volume {vol_m3:.3e} m^3 x density {rho} kg/m^3"}
    # after recentering, the CAD bbox is centred in X/Y and starts at z = 0 (sizes in mm)
    size_mm = tuple((hi[i] - lo[i]) * s / MM for i in range(3))
    parts = [generators.Part("cad", "box", (0.0, 0.0, size_mm[2] / 2), size_mm, "cad_default")]
    return stage, root, out, parts


def author(asset_id):
    spec = load_spec(asset_id)
    spec_sha = hashlib.sha256(spec_path(asset_id).read_bytes()).hexdigest()[:12]
    if spec["family"] == "cad":
        stage, root, out, parts = author_cad(spec)
    else:
        stage, root, out, parts = author_parametric(spec)
    prim = root.GetPrim()
    phys = spec["physics"]
    mass = (phys.get("mass_kg") or {}).get("value")
    if phys.get("body") == "dynamic":
        UsdPhysics.RigidBodyAPI.Apply(prim)
        if mass:
            UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(float(mass))
    _semantics(prim, spec.get("semantic_class") or spec["family"])
    lo, hi = generators.expected_bbox(parts)
    prim.SetAssetInfo({"identifier": Sdf.AssetPath(out.name), "name": asset_id, "version": spec_sha})
    prim.SetCustomDataByKey("lab_twin", {
        "route": spec["route"], "family": spec["family"], "spec_sha": spec_sha,
        "body": phys.get("body", "static"), "mass_kg": float(mass) if mass else -1.0,
        "provisional_params": ",".join(provisional_params(spec)),
        "expected_bbox_mm": json.dumps([lo, hi]),
        "manufacturer": spec["inventory"]["manufacturer"], "part_number": spec["inventory"]["part_number"],
    })
    stage.GetRootLayer().Save()
    rec = {"asset_id": asset_id, "spec_sha": spec_sha, "gen_sha": GEN_SHA, "usd": str(out.relative_to(ASSETS.parent)),
           "mass_kg": phys.get("mass_kg"), "expected_bbox_mm": [lo, hi]}
    (BUILD / asset_id).mkdir(parents=True, exist_ok=True)
    (BUILD / asset_id / "authored.json").write_text(json.dumps(rec, indent=2))
    return out


def main(ids):
    if not ids:   # ready rows, plus rows whose spec changed since they were authored (stale)
        def stale(aid):
            rec = BUILD / aid / "authored.json"
            cur = hashlib.sha256(spec_path(aid).read_bytes()).hexdigest()[:12]
            if not rec.exists():
                return True
            old = json.loads(rec.read_text())
            return old["spec_sha"] != cur or old.get("gen_sha") != GEN_SHA
        ids = [r["asset_id"] for r in read_manifest() if r["s3_geometry"] == "done"
               and (r["s4_usd"] == "todo" or r["s4_usd"].startswith("blocked") or stale(r["asset_id"]))]
    for aid in ids:
        try:
            out = author(aid)
            set_status(aid, "s4_usd", "done")
            print(f"S4 OK   {aid} -> {out.relative_to(ASSETS.parent)}")
        except Exception as e:  # record and continue: one bad spec must not stop the batch
            set_status(aid, "s4_usd", f"blocked:{type(e).__name__}:{str(e)[:80]}")
            print(f"S4 FAIL {aid}: {e}")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    finally:
        app.close()
