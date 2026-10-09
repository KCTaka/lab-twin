#!/usr/bin/env python3
"""Fill layout_L1 specs from research/layout_dims.json (stage 3 for the layout pass).

Kept as a script, not hand edits, so every spec value can be traced to a research
record: `D(item, field)` copies url + quote from the record; derived values say
how they were derived; everything else is `assumed` with a note.

Research performed 2026-10-09 by a web-search agent; two quotes were re-checked by
hand (Rousseau catalogue: WS08 thickness 1 1/2", LA__-182104 18x21x4", L3ABD 18x21x32").
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from common import load_spec, save_spec  # noqa: E402

R = {(r["item"], r["field"]): r for r in json.loads((ROOT / "research" / "layout_dims.json").read_text())}


def D(item, field, value=None, note=""):
    """Datasheet-sourced param from research record (value override for unit/axis mapping)."""
    r = R[(item, field)]
    v = r["value_mm_or_kg"] if value is None else value
    out = {"value": round(float(v), 2), "unit": "mm", "src": "datasheet", "url": r["source_url"], "quote": r["quote"]}
    n = "; ".join(x for x in (r["basis"] != "exact_model" and f"basis={r['basis']}", r.get("notes", "")[:160], note) if x)
    if n:
        out["note"] = n
    return out


def A(v, note):
    return {"value": v, "unit": "mm", "src": "assumed", "note": note}


def I(v, quote):
    return {"value": v, "unit": "mm", "src": "inventory", "quote": quote}


def N(v, note):   # dimensionless count / flag from inventory or datasheet
    return {"value": v, "unit": "1", "src": "inventory", "quote": note}


def mass(v, src, quote_or_note, url=None):
    m = {"value": v, "src": src}
    m["quote" if src != "assumed" else "note"] = quote_or_note
    if url:
        m["url"] = url
    return m


def put(aid, family, params, physics=None, semantic=None, notes=""):
    s = load_spec(aid)
    s["family"] = family
    s["params"] = params
    if physics:
        s["physics"] = physics
    if semantic:
        s["semantic_class"] = semantic
    if notes:
        s["notes"] = notes
    s["route"] = "PARAMETRIC_PRIMITIVE"
    save_spec(s)
    print("filled", aid)


ST = lambda m: {"body": "static", "collision": "box", "mass_kg": m}  # noqa: E731

# 1 Newport OTS-UT2-48-12-I  (asset X = 96 in length, Y = 48 in width: long side faces the user)
ots_leg = 152.4
ots_inset = round((2438.4 - 2326.6) / 2 + ots_leg / 2, 2)   # frame outer extent -> leg centre
put("Newport_OTS_UT2_48_12_I_6fd189", "optical_table", {
    "w": D(1, "tabletop_length"), "d": D(1, "tabletop_width"), "top_t": D(1, "tabletop_thickness"),
    "surface_h": D(1, "working_surface_height_above_floor"),
    "leg_w": D(1, "isolator_base_dimension", note="leg modelled as an isolator-base-sized square column; true leg cross-section not published"),
    "leg_inset_x": D(1, "frame_outer_extent_along_table_length", value=ots_inset,
                     note="derived: (2438.4-2326.6)/2 + leg_w/2 = leg centre inset"),
    "leg_inset_y": D(1, "frame_outer_extent_across_table_width", value=round((1219.2 - 1107.4) / 2 + ots_leg / 2, 2),
                     note="derived: (1219.2-1107.4)/2 + leg_w/2"),
    "hole_pitch": D(1, "hole_pitch"), "hole_offset_x": D(1, "hole_grid_edge_offset"),
    "hole_offset_y": D(1, "hole_grid_edge_offset"),
    "tie_bar_h": A(0, "OTS frame has 2 leg weldments + 3 cross braces (datasheet); not modelled yet"),
}, ST(mass(583.8, "datasheet", "Weight, System 1287 lb", R[(1, "system_mass")]["source_url"])), "optical_table",
    "product discontinued (replacement TF-48-24-I); 'SmartStation' is not a Newport term")

# 2 Newport VIS3672-PG4-325A IsoStation: frame footprint (38.8 x 74.8 in) is LARGER than the 36 x 72 in top
vis_leg = 76.2
put("Newport_VIS3672_PG4_325A_8fb101", "optical_table", {
    "w": I(1828.8, "36 in x 72 in"), "d": I(914.4, "36 in x 72 in"),
    "top_t": D(4, "breadboard_size", value=109.22, note="4.3 in per datasheet; inventory says 4 in"),
    "surface_h": D(4, "working_surface_height"),
    "leg_w": A(vis_leg, "frame tube size not published; 3 in assumed"),
    "leg_inset_x": D(4, "footprint_length_dimB", value=round(vis_leg / 2 - (1899.9 - 1828.8) / 2, 2),
                     note="derived: frame overhangs top by (1899.9-1828.8)/2; leg centre = leg_w/2 - overhang"),
    "leg_inset_y": D(4, "footprint_width_dimA", value=round(vis_leg / 2 - (985.5 - 914.4) / 2, 2),
                     note="derived: frame overhangs top by (985.5-914.4)/2"),
    "hole_pitch": A(25.4, "PG breadboard hole grid not in research; Newport standard 1/4-20 on 1 in"),
    "hole_offset_x": A(12.7, "Newport standard 0.5 in border (from OTS drawing)"),
    "hole_offset_y": A(12.7, "Newport standard 0.5 in border (from OTS drawing)"),
}, ST(mass(None, "assumed", "net mass not published (crated 670 lb)")), "optical_table",
    "discontinued (replacement VIS3672-PG2-I); frame wider than top")

# 3 TMC 784-655-02R top on a pre-existing support of unknown height
put("TMCTechnical_784_655_02R_52b98b", "optical_table", {
    "w": I(2438.4, "48 in x 96 in x 12 in thick"), "d": I(1219.2, "48 in x 96 in x 12 in thick"),
    "top_t": I(304.8, "48 in x 96 in x 12 in thick"),
    "surface_h": A(914.4, "support pre-existing and unknown; TMC posts 12-32 in, 24 in post + 12 in top assumed; MEASURE"),
    "leg_w": A(152.4, "support unknown"), "leg_inset_x": A(304.8, "support unknown"), "leg_inset_y": A(203.2, "support unknown"),
    "hole_pitch": D(5, "hole_pitch"),
    "hole_offset_x": A(12.7, "TMC border not found; Newport value used"), "hole_offset_y": A(12.7, "TMC border not found"),
}, ST(mass(553.9, "datasheet", R[(5, "tabletop_mass")]["quote"] + " (0.265 lb/in^2 x 48 x 96 in; derived)",
           R[(5, "tabletop_mass")]["source_url"])), "optical_table",
    "'SF4202' in the inventory is most likely the room number, not a TMC model")

# 4 Newport OTS-8 overhead shelf (posts mount to the leg frame beyond the table ends)
put("Newport_OTS_8_1344b3", "overhead_shelf", {
    "shelf_len": D(2, "overall_length_along_table"),
    "shelf_d": D(2, "shelf_depth_product_page", note="drawing gives 26 in overall depth (incl. brackets?)"),
    "shelf_t": D(2, "shelf_top_height_range", value=round((33.4 - 28.7) * 25.4, 2),
                 note="derived: top 33.4 in - underside 28.7 in at the lowest position (shelf box incl. outlet strip)"),
    "shelf_z": D(2, "shelf_underside_height_range", value=round((28.7 + 28.7 - 40.7) * 25.4, 2),
                 note="derived for a 12 in OTS table: underside ref (28.7 in above table underside, lowest of 3 positions) "
                      "+ 28.7 in table underside height - 40.7 in surface height"),
    "post_w": A(50.8, "upright cross-section not published"),
}, ST(mass(None, "assumed", "not published")), "overhead_shelf")

# 5 Newport ATS-HS-12 hanging shelf
put("Newport_ATS_HS_12_baa14b", "hanging_shelf", {
    "shelf_len": D(3, "shelf_width"), "shelf_d": D(3, "shelf_length"),
    "shelf_t": A(19.05, "sheet-metal tray thickness not published"),
    "rod_len": D(3, "hang_height"), "rod_dia": A(12.7, "rod diameter not published"),
}, ST(mass(None, "assumed", "not published")), "hanging_shelf",
    "ATS line hardware; fit to OTS-8 nut tracks unconfirmed")

# 6 Newport ACWS compressor (dims from the 220 V twin ACWS-02)
put("Newport_ACWS_42ec99", "appliance_box", {
    "w": D(8, "width"), "d": D(8, "length_depth"), "h": D(8, "height"),
    "material": {"value": "painted_steel_white", "src": "assumed", "note": "visual only"},
}, ST(mass(16.0, "datasheet", "Weight 16 kg (ACWS-02, related model)", R[(8, "mass")]["source_url"])), "air_compressor")

# 7 Rousseau workbench
q = "Rousseau modular workbench 72 in W x 24 in D x 33.75 in H"
put("Rousseau_no_P_N_ROUSSEAU_MODULAR_13dbe5", "workbench", {
    "w": I(1828.8, q), "d": I(609.6, q),
    "h": I(857.25, q + " (catalogue sum 4 + 28 + 1.5 = 33.5 in; 6.35 mm unexplained, MEASURE)"),
    "top_t": D(6, "top_thickness", note="two-column catalogue page; mapped to WS08 by column, checked by hand"),
    "ped_w": D(6, "pedestal_W_x_D_x_H", value=457.2, note="18 in"),
    "ped_d": D(6, "pedestal_W_x_D_x_H", value=533.4, note="21 in"),
    "base_h": D(6, "base_W_x_D_x_H"),
}, ST(mass(None, "assumed", "not published")), "workbench")

# 8 Rousseau Spider closed shelving
q = "36 in W x 24 in D x 75 in H, 5 shelves"
put("Rousseau_no_P_N_ROUSSEAU_SPIDER_89bfee", "shelving_closed", {
    "w": I(914.4, q), "d": I(609.6, q), "h": I(1905.0, q),
    "panel_t": D(7, "shelf_thickness", note="1.25 in box shelf used for all panels; side-panel gauge not published"),
    "n_shelves": N(5, q), "kick_h": D(7, "front_base_height"),
    "material": {"value": "painted_steel_grey", "src": "inventory", "quote": "light grey"},
}, ST(mass(None, "assumed", "not published")), "shelving")

# 9 Quartet cork board (landscape, 48 x 36 in)
put("Quartet_QRT02304_5983b0", "wall_panel", {
    "w": I(1219.2, 'Cork bulletin board 36" x 48"'), "h": I(914.4, 'Cork bulletin board 36" x 48"'),
    "t": A(19.05, "packaged depth 1.5 in is an upper bound; 0.75 in frame assumed"),
    "frame_w": A(19.05, "aluminium frame width not published"),
}, ST(mass(6.9, "datasheet", "Gross weight 15.20lbs (packaged; upper bound)", R[(9, "mass_gross")]["source_url"])),
    "bulletin_board", "orientation (landscape) assumed")

# 10 Quartet 660S 60 x 60 in screen (inventory '66080' does not exist)
put("Quartet_QRT66080_with_606_wall_m_8d033c", "projector_screen", {
    "w": I(1524.0, 'Projector screen 60" x 60"'), "h": I(1524.0, 'Projector screen 60" x 60"'),
    "case_len": A(1699.3, "scaled from 70 in sibling 670S (76.875 in case): 60 + 6.9 in"),
    "case_h": A(76.2, "not published"), "case_d": A(76.2, "not published"),
    "deployed": {"value": 0, "unit": "1", "src": "assumed", "note": "modelled rolled up"},
}, ST(mass(5.4, "datasheet", "Shipping Weight 11.84 lb.", R[(10, "shipping_weight")]["source_url"])), "projection_screen",
    "model is Quartet 660S per research; inventory P/N 66080 not found")

# 11 Laservision F5P06 curtain
put("Laservision_F5P06_f57242", "curtain", {
    "w": I(2540.0, 'Laser barrier curtain panel, 100 W/cm^2, 100" x 84"'),
    "h": I(2133.6, 'Laser barrier curtain panel, 100 W/cm^2, 100" x 84"'),
    "t": A(1.0, "fabric thickness not published"),
}, ST(mass(None, "assumed", "not published")), "laser_curtain", "hangs from 1.44 x 1.25 in ceiling track (datasheet); track not modelled")

# 12 AV34 cart (found as Luxor AV34)
put("PremierAV_AV34_817e78", "cart", {
    "w": D(12, "W_x_D_x_H", value=609.6, note="24 in; Luxor listing"), "d": D(12, "W_x_D_x_H", value=457.2, note="18 in"),
    "h": D(12, "W_x_D_x_H", value=863.6, note="34 in (listing also mentions 26 in)"),
    "n_shelves": {"value": 3, "unit": "1", "src": "datasheet", "url": R[(12, "shelf_count")]["source_url"], "quote": "No. of Shelves: 3"},
    "shelf_t": A(25.4, "not published"), "post_w": A(25.4, "not published"), "caster_h": A(101.6, "4 in casters typical"),
}, ST(mass(None, "assumed", "not published")), "cart", "inventory says Premier; product found as Luxor AV34")

# 13 VWR 10819-650 fridge
put("VWRInternati_10819_650_3fad9a", "appliance_box", {
    "w": D(13, "exterior_W_x_D_x_H", value=514.35, note="20 1/4 in"),
    "d": D(13, "exterior_W_x_D_x_H", value=558.8, note="22 in"),
    "h": D(13, "exterior_W_x_D_x_H", value=800.1, note="31 1/2 in"),
    "material": {"value": "painted_steel_white", "src": "assumed", "note": "visual only"},
}, ST(mass(41.0, "assumed", "41 kg from a search snippet of vwr.com, not verified verbatim")), "refrigerator")
