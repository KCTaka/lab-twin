"""Compare cad_analyze.py output with the fixture ground truth. Exit 1 on any mismatch."""
import json, sys
from pathlib import Path
sys.path.insert(0, "pipeline")
from cad_analyze import analyze

fx = Path("tests/fixtures")
truth = json.loads((fx / "cad_truth.json").read_text())
fails = 0
def check(name, cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    fails += not cond
for name, t in truth.items():
    print(name)
    r = analyze(fx / f"{name}.step")
    kp = r["key_points"]
    check(name, all(abs(a - b) < 0.01 for a, b in zip(r["bbox_mm"]["size"], t["bbox"])), f"bbox {r['bbox_mm']['size']} == {t['bbox']}")
    mh = [v for k, v in kp.items() if k.startswith("mount_hole")]
    check(name, len(mh) == len(t["mount_holes"]), f"{len(mh)} mount holes (expected {len(t['mount_holes'])})")
    for m, e in zip(mh, t["mount_holes"]):
        check(name, all(abs(a - b) < 0.01 for a, b in zip(m["pos_mm"], e["pos"])), f"mount hole at {m['pos_mm']}")
        check(name, m["data"].get("thread_guess", {}).get("thread") == e["thread"], f"thread {m['data'].get('thread_guess')} == {e['thread']}")
    th = [v for k, v in kp.items() if k.startswith("top_hole")]
    check(name, len(th) == len(t["top_holes"]), f"{len(th)} top holes (expected {len(t['top_holes'])})")
    oa = kp.get("optical_axis")
    if t["optical_axis"] is None:
        check(name, oa is None, "no optical axis")
    else:
        e = t["optical_axis"]
        check(name, oa is not None and all(abs(a - b) < 0.01 for a, b in zip(oa["pos_mm"], e["pos"])), f"optical axis at {oa and oa['pos_mm']}")
        check(name, oa is not None and abs(abs(oa["normal"]["xyz".index(e["axis"])]) - 1) < 1e-6, f"axis along {e['axis']}: {oa and oa['normal']}")
        check(name, oa is not None and oa["data"].get("thread_guess", {}).get("thread") == e["thread"], f"bore thread {oa and oa['data'].get('thread_guess')}")
    check(name, ("top_surface" in kp) == t["top_surface"], f"top_surface present = {'top_surface' in kp}")
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
