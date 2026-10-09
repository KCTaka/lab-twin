"""Parametric geometry generators for layout-class assets.

A generator turns a family name + resolved parameters (mm) into a list of
box/cylinder parts and a dict of key points, all in the ASSET FRAME:

    origin : centre of the footprint, on the floor (z = 0)
    +X     : width, left -> right as seen from the front
    +Y     : depth, front -> back  (the front face is at y = -d/2)
    +Z     : up

Wall-mounted families (wall_panel, projector_screen, curtain) use the same frame
with the BACK face at y = +d/2 (against the wall) and z = 0 at the bottom edge;
the layout supplies the mounting height.

Everything here is plain Python (no pxr) so it can be unit-tested anywhere and
called from Isaac Sim's interpreter at authoring time. Units are mm throughout;
conversion to metres happens only in s4_author_usd.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Part:
    name: str
    shape: str                 # "box" | "cylinder"
    center: tuple              # (x, y, z) mm, asset frame
    size: tuple                # box: (sx, sy, sz); cylinder: (diameter, diameter, height) along Z
    material: str
    collide: bool = True


@dataclass
class KeyPoint:
    name: str
    pos: tuple                 # mm, asset frame
    normal: tuple = (0.0, 0.0, 1.0)
    data: dict = field(default_factory=dict)   # extra structured info (hole grid etc.)
    support: bool | None = None                # True = load-bearing surface (S5 drop-tests it);
                                               # None -> True iff normal is +Z

    def is_support(self) -> bool:
        if self.support is not None:
            return self.support
        return tuple(float(c) for c in self.normal) == (0.0, 0.0, 1.0)


def _box(name, x0, x1, y0, y1, z0, z1, material, collide=True):
    return Part(name, "box", ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
                (x1 - x0, y1 - y0, z1 - z0), material, collide)


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------
def optical_table(p):
    """Rectangular optical table: top slab on N legs (isolators).

    params: w, d, top_t, surface_h, leg_w, leg_inset_x, leg_inset_y,
            n_legs_x (2 or 3), hole_pitch, hole_offset_x, hole_offset_y,
            tie_bar_h (0 = none)
    """
    w, d, t, H = p["w"], p["d"], p["top_t"], p["surface_h"]
    lw, ix, iy = p["leg_w"], p["leg_inset_x"], p["leg_inset_y"]
    parts = [_box("top", -w / 2, w / 2, -d / 2, d / 2, H - t, H, "stainless_steel")]
    nx = int(p.get("n_legs_x", 2))
    xs = [-w / 2 + ix + k * (w - 2 * ix) / (nx - 1) for k in range(nx)]
    ys = [-d / 2 + iy, d / 2 - iy]
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            parts.append(_box(f"leg_{i}{j}", x - lw / 2, x + lw / 2, y - lw / 2, y + lw / 2,
                              0.0, H - t, "painted_steel_dark"))
    tb = p.get("tie_bar_h", 0) or 0
    if tb > 0:   # tie bars run along X between legs on each side, 150 mm off floor
        for j, y in enumerate(ys):
            parts.append(_box(f"tie_bar_{j}", xs[0] + lw / 2, xs[-1] - lw / 2,
                              y - 25, y + 25, 150, 150 + tb, "painted_steel_dark"))
    pitch = p["hole_pitch"]
    ox, oy = p["hole_offset_x"], p["hole_offset_y"]
    n_hx = int(round((w - 2 * ox) / pitch)) + 1
    n_hy = int(round((d - 2 * oy) / pitch)) + 1
    kps = [
        KeyPoint("surface_center", (0, 0, H)),
        KeyPoint("hole_grid_origin", (-w / 2 + ox, -d / 2 + oy, H),
                 data={"pitch_mm": pitch, "n_x": n_hx, "n_y": n_hy, "thread": p.get("thread", "1/4-20")}),
        KeyPoint("front_edge_center", (0, -d / 2, H), normal=(0, -1, 0)),
    ]
    return parts, kps


def workbench(p):
    """Bench top on two pedestals (left: drawers, right: door/shelf) with bases.

    params: w, d, h, top_t, ped_w, ped_d, base_h
    """
    w, d, h, t = p["w"], p["d"], p["h"], p["top_t"]
    pw, pd, bh = p["ped_w"], p["ped_d"], p["base_h"]
    ped_top = h - t
    parts = [_box("top", -w / 2, w / 2, -d / 2, d / 2, ped_top, h, "laminate_black")]
    yb0, yb1 = -pd / 2, pd / 2
    for side, x0 in (("left", -w / 2), ("right", w / 2 - pw)):
        parts.append(_box(f"base_{side}", x0 + 10, x0 + pw - 10, yb0 + 10, yb1 - 10, 0, bh, "painted_steel_dark"))
        parts.append(_box(f"pedestal_{side}", x0, x0 + pw, yb0, yb1, bh, ped_top, "painted_steel_blue"))
    kps = [KeyPoint("work_surface_center", (0, 0, h)),
           KeyPoint("knee_space_center", (0, -d / 2, (bh + ped_top) / 2), normal=(0, -1, 0))]
    return parts, kps


def shelving_closed(p):
    """Closed shelving unit: two sides, back, top, bottom and N shelves (open front).

    params: w, d, h, panel_t, n_shelves (count includes bottom and top surfaces
            only if stated; here n_shelves = number of usable shelf levels), kick_h
    """
    w, d, h, t = p["w"], p["d"], p["h"], p["panel_t"]
    n, kick = int(p["n_shelves"]), p.get("kick_h", 0)
    mat = p.get("material", "painted_steel_grey")
    parts = [
        _box("side_left", -w / 2, -w / 2 + t, -d / 2, d / 2, 0, h, mat),
        _box("side_right", w / 2 - t, w / 2, -d / 2, d / 2, 0, h, mat),
        _box("back", -w / 2 + t, w / 2 - t, d / 2 - t, d / 2, 0, h, mat),
        _box("top", -w / 2 + t, w / 2 - t, -d / 2, d / 2 - t, h - t, h, mat),
    ]
    kps = []
    # usable levels evenly spaced from kick height to below the top panel
    z0, z1 = kick, h - t
    for i in range(n):
        z = z0 + i * (z1 - z0) / n
        parts.append(_box(f"shelf_{i}", -w / 2 + t, w / 2 - t, -d / 2, d / 2 - t, z, z + t, mat))
        kps.append(KeyPoint(f"shelf_{i}_center", (0, 0, z + t)))
    return parts, kps


def wall_panel(p):
    """Flat panel hung on a wall (cork board, whiteboard). Back face at y = +d/2.

    params: w, h, t, frame_w, face_material, frame_material
    """
    w, h, t, fw = p["w"], p["h"], p["t"], p.get("frame_w", 0)
    parts = [_box("face", -w / 2 + fw, w / 2 - fw, -t / 2, t / 2, fw, h - fw, p.get("face_material", "cork"))]
    if fw > 0:
        fm = p.get("frame_material", "aluminium")
        parts += [
            _box("frame_bottom", -w / 2, w / 2, -t / 2, t / 2, 0, fw, fm),
            _box("frame_top", -w / 2, w / 2, -t / 2, t / 2, h - fw, h, fm),
            _box("frame_left", -w / 2, -w / 2 + fw, -t / 2, t / 2, fw, h - fw, fm),
            _box("frame_right", w / 2 - fw, w / 2, -t / 2, t / 2, fw, h - fw, fm),
        ]
    kps = [KeyPoint("face_center", (0, -t / 2, h / 2), normal=(0, -1, 0)),
           KeyPoint("wall_mount_center", (0, t / 2, h / 2), normal=(0, 1, 0))]
    return parts, kps


def projector_screen(p):
    """Wall-mounted pull-down screen: roller case at top, optional deployed sheet.

    params: w (viewing width), h (viewing height), case_len, case_h, case_d, deployed (0/1)
    """
    cl, ch, cd = p["case_len"], p["case_h"], p["case_d"]
    deployed = bool(p.get("deployed", 0))
    # z = 0 is the bottom of what is modelled: the sheet if deployed, else the case
    H = (p["h"] + ch) if deployed else ch
    parts = [_box("case", -cl / 2, cl / 2, -cd / 2, cd / 2, H - ch, H, "painted_steel_white")]
    kps = [KeyPoint("case_center", (0, 0, H - ch / 2), normal=(0, -1, 0))]
    if deployed:
        parts.append(_box("sheet", -p["w"] / 2, p["w"] / 2, -1, 1, 0, H - ch, "screen_matte_white", collide=False))
        kps.append(KeyPoint("sheet_center", (0, -1, (H - ch) / 2), normal=(0, -1, 0)))
    return parts, kps


def curtain(p):
    """Laser barrier curtain approximated as a rigid hanging panel (no cloth yet).

    params: w, h, t
    """
    w, h, t = p["w"], p["h"], p["t"]
    parts = [_box("panel", -w / 2, w / 2, -t / 2, t / 2, 0, h, "laser_curtain_black")]
    return parts, [KeyPoint("panel_center", (0, -t / 2, h / 2), normal=(0, -1, 0))]


def cart(p):
    """Utility / AV cart: 4 corner posts, N shelves, casters.

    params: w, d, h, n_shelves, shelf_t, post_w, caster_h
    """
    w, d, h, n = p["w"], p["d"], p["h"], int(p["n_shelves"])
    st, pw, ch = p["shelf_t"], p["post_w"], p["caster_h"]
    parts = []
    for i, x in enumerate((-w / 2 + pw / 2, w / 2 - pw / 2)):
        for j, y in enumerate((-d / 2 + pw / 2, d / 2 - pw / 2)):
            parts.append(_box(f"post_{i}{j}", x - pw / 2, x + pw / 2, y - pw / 2, y + pw / 2, ch, h, "painted_steel_dark"))
            parts.append(Part(f"caster_{i}{j}", "cylinder", (x, y, ch / 2), (ch, ch, ch), "rubber_black"))
    kps = []
    for k in range(n):
        z = ch + k * (h - ch - st) / max(n - 1, 1)
        parts.append(_box(f"shelf_{k}", -w / 2, w / 2, -d / 2, d / 2, z, z + st, "painted_steel_dark"))
        kps.append(KeyPoint(f"shelf_{k}_center", (0, 0, z + st)))
    return parts, kps


def appliance_box(p):
    """Closed rectangular box (fridge, compressor, controller, bench-top unit).

    params: w, d, h, material
    """
    w, d, h = p["w"], p["d"], p["h"]
    parts = [_box("body", -w / 2, w / 2, -d / 2, d / 2, 0, h, p.get("material", "painted_steel_white"))]
    return parts, [KeyPoint("top_center", (0, 0, h)),
                   KeyPoint("front_center", (0, -d / 2, h / 2), normal=(0, -1, 0))]


def overhead_shelf(p):
    """Overhead shelf carried by uprights clamped to an optical table edge.

    Origin: at the table surface level, centred on the shelf footprint (the
    layout places this asset relative to its host table's 'surface_center').
    params: shelf_len, shelf_d, shelf_t, shelf_z (above table surface),
            post_w, n_posts
    """
    L, D, T, Z = p["shelf_len"], p["shelf_d"], p["shelf_t"], p["shelf_z"]
    pw, n = p["post_w"], int(p.get("n_posts", 2))
    parts = [_box("shelf", -L / 2, L / 2, -D / 2, D / 2, Z, Z + T, "painted_steel_grey")]
    xs = [-L / 2 + pw / 2 + k * (L - pw) / (n - 1) for k in range(n)]
    for k, x in enumerate(xs):
        parts.append(_box(f"post_{k}", x - pw / 2, x + pw / 2, D / 2 - pw, D / 2, 0, Z, "painted_steel_dark"))
    return parts, [KeyPoint("shelf_top_center", (0, 0, Z + T)),
                   KeyPoint("shelf_underside_center", (0, 0, Z), normal=(0, 0, -1), support=False)]


def hanging_shelf(p):
    """Shelf hung on four rods from the underside of another shelf.

    Origin: floor convention kept (shelf bottom at z = 0); the rods rise to the
    hang plane. Place it in a layout with `host: <shelf>.shelf_underside_center`
    and `anchor: hang_plane_center`.
    params: shelf_len, shelf_d, shelf_t, rod_len, rod_dia
    """
    L, D, T, R, rd = p["shelf_len"], p["shelf_d"], p["shelf_t"], p["rod_len"], p["rod_dia"]
    parts = [_box("shelf", -L / 2, L / 2, -D / 2, D / 2, 0, T, "painted_steel_grey")]
    for i, x in enumerate((-L / 2 + 25, L / 2 - 25)):
        for j, y in enumerate((-D / 2 + 25, D / 2 - 25)):
            parts.append(Part(f"rod_{i}{j}", "cylinder", (x, y, T + R / 2), (rd, rd, R), "stainless_steel"))
    return parts, [KeyPoint("shelf_top_center", (0, 0, T)),
                   KeyPoint("hang_plane_center", (0, 0, T + R), support=False)]


# Required params per family and plausibility ranges (mm) used by s3_check_spec.
# Ranges are deliberately wide: they catch unit errors (in vs mm, m vs mm), not design choices.
REQUIRED = {
    "optical_table": {"w": (300, 6000), "d": (300, 2500), "top_t": (25, 610), "surface_h": (500, 1200),
                      "leg_w": (50, 600), "leg_inset_x": (0, 1500), "leg_inset_y": (0, 1000),
                      "hole_pitch": (6, 50), "hole_offset_x": (5, 200), "hole_offset_y": (5, 200)},
    "workbench": {"w": (600, 4000), "d": (300, 1500), "h": (600, 1200), "top_t": (10, 80),
                  "ped_w": (200, 800), "ped_d": (300, 1500), "base_h": (0, 200)},
    "shelving_closed": {"w": (300, 2500), "d": (150, 1200), "h": (300, 3000), "panel_t": (0.5, 40),
                        "n_shelves": (1, 12)},
    "wall_panel": {"w": (100, 5000), "h": (100, 3000), "t": (2, 100)},
    "projector_screen": {"w": (500, 5000), "h": (500, 5000), "case_len": (500, 5500),
                         "case_h": (30, 300), "case_d": (30, 300)},
    "curtain": {"w": (300, 10000), "h": (300, 5000), "t": (0.1, 30)},
    "cart": {"w": (300, 1500), "d": (300, 1200), "h": (300, 1800), "n_shelves": (1, 6),
             "shelf_t": (1, 60), "post_w": (10, 80), "caster_h": (30, 200)},
    "appliance_box": {"w": (50, 3000), "d": (50, 3000), "h": (20, 3000)},
    "overhead_shelf": {"shelf_len": (300, 6000), "shelf_d": (100, 800), "shelf_t": (1, 150),
                       "shelf_z": (200, 1500), "post_w": (10, 150)},
    "hanging_shelf": {"shelf_len": (100, 2000), "shelf_d": (100, 1000), "shelf_t": (1, 60),
                      "rod_len": (20, 1000), "rod_dia": (3, 40)},
}

FAMILIES = {
    "optical_table": optical_table,
    "workbench": workbench,
    "shelving_closed": shelving_closed,
    "wall_panel": wall_panel,
    "projector_screen": projector_screen,
    "curtain": curtain,
    "cart": cart,
    "appliance_box": appliance_box,
    "overhead_shelf": overhead_shelf,
    "hanging_shelf": hanging_shelf,
}


def resolve_params(spec: dict) -> dict:
    """Flatten {name: {"value": v, ...}} -> {name: v}. Raises on unresolved values."""
    out, missing = {}, []
    for k, v in spec["params"].items():
        val = v["value"] if isinstance(v, dict) else v
        if val is None:
            missing.append(k)
        out[k] = val
    if missing:
        raise ValueError(f"{spec['asset_id']}: unresolved params {missing}")
    return out


def build(spec: dict):
    """Return (parts, keypoints) for a parametric spec."""
    fam = spec["family"]
    if fam not in FAMILIES:
        raise KeyError(f"unknown family {fam!r}; known: {sorted(FAMILIES)}")
    return FAMILIES[fam](resolve_params(spec))


def expected_bbox(parts):
    """Axis-aligned bbox (min, max) of the generated parts, mm."""
    lo = [min(pt.center[i] - pt.size[i] / 2 for pt in parts) for i in range(3)]
    hi = [max(pt.center[i] + pt.size[i] / 2 for pt in parts) for i in range(3)]
    return lo, hi
