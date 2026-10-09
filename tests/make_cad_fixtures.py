"""Synthetic STEP parts with known features: ground truth for pipeline/cad_analyze.py."""
import json, sys
from pathlib import Path
import cadquery as cq

out = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures")
out.mkdir(parents=True, exist_ok=True)
truth = {}

# PH-like post holder: OD 19.05, H 76.2, 12.7 bore from the top (70 deep),
# 1/4-20 tap (5.11 tap drill, 8 deep) on the bottom, 8-32 side thumbscrew hole.
ph = (cq.Workplane("XY").circle(19.05 / 2).extrude(76.2)
      .faces(">Z").workplane().hole(12.7, 70.0)
      .faces("<Z").workplane().hole(5.11, 8.0))
ph = ph.cut(cq.Workplane("YZ").workplane(offset=0).center(0, 60).circle(4.17 / 2).extrude(15))
cq.exporters.export(ph, str(out / "ph_like_holder.step"))
truth["ph_like_holder"] = {"bbox": [19.05, 19.05, 76.2],
    "mount_holes": [{"pos": [0, 0, 0], "thread": "1/4-20"}], "top_holes": [{"pos": [0, 0, 76.2], "d": 12.7}],
    "optical_axis": None, "top_surface": False}   # top is an annulus: centroid lies in the bore

# CP02-like cage plate standing upright: 41 x 8.9 (Y) x 41, SM1 bore (25.6) along Y,
# 4 x 6 mm rod holes on a 30 mm square, 8-32 tap (2.95) at the bottom centre.
cp = (cq.Workplane("XZ").rect(41, 41).extrude(-8.9).translate((0, 0, 20.5)))
cp = cp.faces(">Y").workplane(centerOption="CenterOfBoundBox").hole(25.6)
cp = cp.faces(">Y").workplane(centerOption="CenterOfBoundBox").rect(30, 30, forConstruction=True).vertices().hole(6.0)
cp = cp.faces("<Z").workplane(centerOption="CenterOfBoundBox").hole(2.95, 6.0)
cq.exporters.export(cp, str(out / "cp_like_cage_plate.step"))
truth["cp_like_cage_plate"] = {"bbox": [41, 8.9, 41],
    "mount_holes": [{"pos": [0, 0, 0], "thread": "8-32"}], "top_holes": [],
    "optical_axis": {"pos": [0, 0, 20.5], "axis": "y", "thread": "SM1 (1.035-40)"}, "top_surface": True}

truth["tr2_like_post"] = {"bbox": [12.7, 12.7, 50.8],
    "mount_holes": [{"pos": [0, 0, 0], "thread": "M4"}], "top_holes": [{"pos": [0, 0, 50.8], "thread": "M4"}],
    "optical_axis": None, "top_surface": False}
(out / "cad_truth.json").write_text(json.dumps(truth, indent=2))
print("fixtures:", sorted(truth))
