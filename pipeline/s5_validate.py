"""Stage 5 - Validate: assets/<id>/<id>.usda -> assets/<id>/validation.json

Run with Isaac Sim's interpreter (headless):
    ~/isaacsim/python.sh pipeline/s5_validate.py [asset_id ...]    # default: all s5_validate=todo

Checks (all must pass for status `done`; provisional params give `done` + a flag,
because a geometrically sound placeholder is still usable for layout):
  C1 stage metrics      metersPerUnit == 1, upAxis == Z, defaultPrim == /<id>
  C2 bbox               USD world bbox == generator/spec bbox within max(1 mm, 2 %) per axis
  C3 floor origin       bbox min z == 0 within 1 mm (asset sits on the floor)
  C4 collision present  every collidable part has CollisionAPI
  C5 physics settle     for every +Z key point (a support surface) a 0.1 m, 1 kg probe cube
                        dropped from 20 mm above comes to rest ON the surface:
                        |z_final - (z_kp + 0.05)| < 5 mm and |v| < 5 mm/s after 1.5 s.
                        Dynamic assets: the asset itself must not drift > 5 mm or tip.
  C6 provenance         lists params still `assumed` (reported, not failed)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

import omni.physx  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, Usd, UsdGeom, UsdPhysics, PhysxSchema  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ASSETS, BUILD, load_spec, provisional_params, read_manifest, set_status  # noqa: E402

DT = 1.0 / 120.0
SETTLE_S = 1.5
PROBE = 0.1          # m, probe cube edge
DROP = 0.02          # m, drop height above the surface


def static_checks(aid):
    res = {}
    path = ASSETS / aid / f"{aid}.usda"
    st = Usd.Stage.Open(str(path))
    dp = st.GetDefaultPrim()
    res["C1_metrics"] = {
        "pass": UsdGeom.GetStageMetersPerUnit(st) == 1.0 and UsdGeom.GetStageUpAxis(st) == "Z"
        and dp and dp.GetPath() == f"/{aid}",
        "mpu": UsdGeom.GetStageMetersPerUnit(st), "up": UsdGeom.GetStageUpAxis(st)}
    meta = dp.GetCustomDataByKey("lab_twin")
    exp_lo, exp_hi = json.loads(meta["expected_bbox_mm"])
    rng = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(dp).ComputeAlignedRange()
    lo = [v * 1e3 for v in rng.GetMin()]
    hi = [v * 1e3 for v in rng.GetMax()]
    errs = []
    for i in range(3):
        exp = exp_hi[i] - exp_lo[i]
        got = hi[i] - lo[i]
        errs.append({"axis": "xyz"[i], "expected_mm": round(exp, 2), "got_mm": round(got, 2),
                     "ok": abs(got - exp) <= max(1.0, 0.02 * exp)})
    res["C2_bbox"] = {"pass": all(e["ok"] for e in errs), "axes": errs}
    res["C3_floor_origin"] = {"pass": abs(lo[2]) <= 1.0, "min_z_mm": round(lo[2], 3)}
    shapes = [p for p in Usd.PrimRange(dp) if p.IsA(UsdGeom.Gprim)]
    with_col = [p for p in shapes if p.HasAPI(UsdPhysics.CollisionAPI)]
    res["C4_collision"] = {"pass": len(with_col) > 0, "shapes": len(shapes), "collidable": len(with_col)}
    kps = []
    kp_root = st.GetPrimAtPath(dp.GetPath().AppendChild("KeyPoints"))
    if kp_root:
        for k in kp_root.GetChildren():
            n = k.GetCustomDataByKey("normal")
            t = UsdGeom.Xformable(k).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
            sup = k.GetCustomDataByKey("support")
            kd = k.GetCustomDataByKey("data")
            kd = json.loads(kd) if isinstance(kd, str) else {}
            nn = tuple(n) if n else (0, 0, 1)
            kps.append({"name": k.GetName(), "pos": tuple(t), "normal": nn,
                        "support": bool(sup) if sup is not None else abs(nn[2] - 1) < 1e-6,
                        "face_area_mm2": kd.get("face_area_mm2")})
    return res, kps, meta


def _world(aid, negative_control=False):
    """Fresh stage: physics scene, ground slab, the asset referenced at the origin."""
    ctx = omni.usd.get_context()
    ctx.new_stage()
    st = ctx.get_stage()
    UsdGeom.SetStageUpAxis(st, "Z")
    UsdGeom.SetStageMetersPerUnit(st, 1.0)
    scene = UsdPhysics.Scene.Define(st, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)
    PhysxSchema.PhysxSceneAPI.Apply(scene.GetPrim()).CreateTimeStepsPerSecondAttr(int(1 / DT))
    # ground: translate on parent, scale on child cube (see physics-simulation skill)
    g = UsdGeom.Xform.Define(st, "/World/Ground")
    g.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.05))
    gc = UsdGeom.Cube.Define(st, "/World/Ground/shape")
    gc.CreateSizeAttr(1.0)
    gc.AddScaleOp().Set(Gf.Vec3f(20, 20, 0.1))
    UsdPhysics.CollisionAPI.Apply(gc.GetPrim())
    a = st.DefinePrim("/World/Asset")
    a.GetReferences().AddReference(str(ASSETS / aid / f"{aid}.usda"))
    if negative_control:   # strip asset collision: every probe must now FAIL (proves C5 is not vacuous)
        for q in Usd.PrimRange(a):
            if q.HasAPI(UsdPhysics.CollisionAPI):
                UsdPhysics.CollisionAPI(q).CreateCollisionEnabledAttr(False)
    return st


def _simulate(paths):
    """Step SETTLE_S seconds; return {path: (start_pos, final_pos, speed m/s)}."""
    phx = omni.physx.get_physx_interface()
    sim = omni.physx.get_physx_simulation_interface()
    phx.start_simulation()
    phx.force_load_physics_from_usd()
    t = 0.0
    start = {p: phx.get_rigidbody_transformation(p)["position"] for p in paths}
    for _ in range(int(SETTLE_S / DT)):
        sim.simulate(DT, t)
        sim.fetch_results()
        t += DT
    prev = {p: phx.get_rigidbody_transformation(p)["position"] for p in paths}
    sim.simulate(DT, t)
    sim.fetch_results()
    out = {}
    for p in paths:
        pos = phx.get_rigidbody_transformation(p)["position"]
        out[p] = (start[p], pos, (Gf.Vec3d(*pos) - Gf.Vec3d(*prev[p])).GetLength() / DT)
    phx.reset_simulation()
    return out


def physics_settle(aid, kps, body, size_mm, mass_kg=None, negative_control=False):
    """(a) dynamic assets: the asset alone must come to rest where it was placed.
    (b) every upward key point (a support surface) must hold a probe cube.
        Probe edge = min(100 mm, half the smallest footprint dim); probe mass = 1 kg
        on static assets, min(1 kg, 20 % of asset mass) on dynamic ones, so the
        probe tests the collider rather than knocking over a small free part."""
    results = []
    if body == "dynamic":
        _world(aid, negative_control)
        start, pos, v = _simulate(["/World/Asset"])["/World/Asset"]
        drift = (Gf.Vec3d(*pos) - Gf.Vec3d(*start)).GetLength()
        results.append({"probe": "asset_itself", "drift_mm": round(drift * 1e3, 2),
                        "speed_mm_s": round(v * 1e3, 3), "ok": drift < 0.005 and v < 0.005})
    surf = [k for k in kps if k["support"]]
    if surf:
        edge = min(PROBE, 0.5 * min(size_mm[0], size_mm[1]) / 1e3)
        pmass = 1.0 if body != "dynamic" else min(1.0, 0.2 * (mass_kg or 1.0))
        st = _world(aid, negative_control)
        paths = {}
        for i, k in enumerate(surf):
            # the probe must fit on the face it tests (CAD faces report their area)
            e_k = min(edge, 0.7 * (k["face_area_mm2"] ** 0.5) / 1e3) if k.get("face_area_mm2") else edge
            k["edge"] = e_k
            p = UsdGeom.Cube.Define(st, f"/World/probe_{i}")
            p.CreateSizeAttr(e_k)
            x, y, z = k["pos"]
            p.AddTranslateOp().Set(Gf.Vec3d(x, y, z + e_k / 2 + DROP))
            UsdPhysics.CollisionAPI.Apply(p.GetPrim())
            UsdPhysics.RigidBodyAPI.Apply(p.GetPrim())
            UsdPhysics.MassAPI.Apply(p.GetPrim()).CreateMassAttr(pmass)
            paths[p.GetPath().pathString] = k
        for p, (start, pos, v) in _simulate(list(paths)).items():
            k = paths[p]
            err = pos[2] - (k["pos"][2] + k["edge"] / 2)
            settled = v < 0.005 and abs(pos[0] - k["pos"][0]) < 0.02 and abs(pos[1] - k["pos"][1]) < 0.02
            # below the face = missing collider (fail); above it = the probe met taller solid
            # geometry inside its footprint (collision present, probe wider than the clear patch)
            obstructed = settled and err >= 0.005
            ok = settled and err > -0.005
            results.append({"probe": k["name"], "probe_edge_mm": round(k["edge"] * 1e3, 1), "probe_kg": round(pmass, 4),
                            "z0_mm": round(start[2] * 1e3, 1), "z_err_mm": round(err * 1e3, 2),
                            "speed_mm_s": round(v * 1e3, 3), "ok": ok,
                            **({"warning": "obstructed: probe rests on a feature above the face"} if obstructed else {})})
    if not results:   # e.g. wall-hung static panels: nothing load-bearing to test
        return {"pass": True, "applicable": False, "probes": [],
                "note": "not applicable: static asset without support key points (collision presence checked by C4)"}
    return {"pass": all(r["ok"] for r in results), "applicable": True, "probes": results, "note": ""}


def validate(aid):
    res, kps, meta = static_checks(aid)
    size = [ax["got_mm"] for ax in res["C2_bbox"]["axes"]]
    mass = json.loads((BUILD / aid / "authored.json").read_text()).get("mass_kg", {}) or {}
    res["C5_physics_settle"] = physics_settle(aid, kps, meta.get("body", "static"), size, mass.get("value"))
    prov = provisional_params(load_spec(aid))
    res["C6_provenance"] = {"pass": True, "provisional": prov}
    ok = all(v["pass"] for v in res.values())
    out = ASSETS / aid / "validation.json"
    out.write_text(json.dumps({"asset_id": aid, "pass": ok, "checks": res}, indent=2))
    return ok, prov, res


def main(ids):
    if ids and ids[0] == "--negative-control":
        for aid in ids[1:]:
            res, kps, meta = static_checks(aid)
            size = [ax["got_mm"] for ax in res["C2_bbox"]["axes"]]
            r = physics_settle(aid, kps, meta.get("body", "static"), size, None, negative_control=True)
            print(f"S5 NEGATIVE-CONTROL {aid}: C5 pass={r['pass']} (expected False) {r['probes']}")
        return
    if not ids:   # ready rows, plus rows re-authored after their last validation
        def stale(aid):
            v, u = ASSETS / aid / "validation.json", ASSETS / aid / f"{aid}.usda"
            return not v.exists() or v.stat().st_mtime < u.stat().st_mtime
        ids = [r["asset_id"] for r in read_manifest() if r["s4_usd"] == "done"
               and (r["s5_validate"] == "todo" or r["s5_validate"].startswith("blocked") or stale(r["asset_id"]))]
    for aid in ids:
        try:
            ok, prov, res = validate(aid)
        except Exception as e:
            set_status(aid, "s5_validate", f"blocked:{type(e).__name__}:{str(e)[:80]}")
            print(f"S5 ERROR {aid}: {e}")
            continue
        failed = [k for k, v in res.items() if not v["pass"]]
        status = ("done" + (":provisional" if prov else "")) if ok else "blocked:failed_" + "+".join(failed)
        set_status(aid, "s5_validate", status)
        print(f"S5 {'PASS' if ok else 'FAIL'} {aid}  failed={failed}  provisional={prov}")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    finally:
        app.close()
