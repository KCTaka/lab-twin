#!/usr/bin/env python3
"""
Triage the Poon Lab digital-twin inventory for USD asset generation.

Input : Poon_Lab_Digital_Twin_Inventory_Twin_Assets_.csv  (latin-1 encoded)
Output: triage.csv  -- one row per inventory line with
        asset_id, priority, route, flags, parsed dims, and a reason string.

Design rules
  * Deterministic, no network access. Every decision is explainable via `reason`.
  * Conservative: when unsure, route to a *human/LLM review* bucket, never guess.
  * Vendor URLs marked `constructed` in the source CSV are NOT trusted
    (they were built from the part number and never verified).

Usage:  python triage_inventory.py INPUT.csv OUTPUT.csv
"""
import re
import sys
import hashlib
import pandas as pd

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
# Vendors that, in general, publish downloadable 3D CAD (STEP) per part number.
# This is a *prior*, not a guarantee: Step 1 of the pipeline must verify per part.
CAD_LIKELY_VENDORS = {
    "Thorlabs", "Newport", "Edmund Optics", "Navitar", "OZ Optics",
    "Mightex", "Mirrorcle", "Nikon", "Olympus", "Cascade Microtech (FormFactor)",
    "Signatone", "Micronix", "MPI Corporation", "Harvard Apparatus", "Everbeing",
    "GGB Industries", "Andor", "Point Grey Research", "The Imaging Source",
}

# Category -> (priority, why).  P0 = scene skeleton, P3 = defer.
CATEGORY_PRIORITY = {
    "Table_Isolation": ("P0", "defines workspace, collision envelope and layout"),
    "Furniture":       ("P0", "defines workspace, collision envelope and layout"),
    "Instrument":      ("P0", "rack/bench footprint; front-panel interaction later"),
    "ProcessTool":     ("P0", "large fixed footprint; safety-critical keep-out volume"),
    "Stage_Positioner":("P1", "actuated devices; core to automation skills"),
    "Probe":           ("P1", "actuated/contact devices; core to automation skills"),
    "Microscopy":      ("P1", "optical path + imaging; perception/alignment"),
    "Camera":          ("P1", "sensor simulation"),
    "MEMS":            ("P1", "beam steering; device functionality"),
    "Detector":        ("P1", "measurement endpoints"),
    "LightSource":     ("P1", "measurement endpoints"),
    "Optomechanics":   ("P2", "high volume, regular families; needed to assemble setups"),
    "FiberComponent":  ("P3", "mostly flexible/small; defer until rope/cable model exists"),
    "RFComponent":     ("P3", "small connectors/cables; defer"),
    "Electronics":     ("P3", "small boxes; defer"),
    "Other":           ("P3", "heterogeneous; review manually"),
}

# ----------------------------------------------------------------------------
# Regexes
# ----------------------------------------------------------------------------
RE_NODESC   = re.compile(r"no description on po", re.I)
RE_NOPN     = re.compile(r"\(no p/?n\)|no part number", re.I)
RE_GROUPED_MODEL = re.compile(r";")            # several P/Ns in the part-number field
RE_GROUPED_DESC  = re.compile(r"^grouped", re.I)  # description explicitly says grouped
RE_OPTION_MODEL = re.compile(r"^(option|opt)\b", re.I)
RE_OPTION_DESC  = re.compile(r"^(extends\b|internal sinusoidal)|\bPNA upgrades\b", re.I)
RE_FLEX     = re.compile(
    r"patch ?cable|patch ?cord|jumper|pigtail|fiber cable|ribbon cable|"
    r"\bcable\b|\bhose\b|\btubing\b|\bwire\b|light ?guide|lightguide", re.I)
RE_PACK     = re.compile(r"(\d+)\s*[- ]?pack|pack of (\d+)", re.I)

# a length token: number + unit
RE_LEN = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|um|µm|in|inch|\"|m)\b", re.I)
# W x D x H style triples / pairs, optional unit after last number
_U = r"(mm|cm|ft|inch|in|\")"
# W x D [x H] where every number may carry its own unit ("4 ft x 8 ft x 12 in").
RE_XYZ = re.compile(
    r"(\d+(?:\.\d+)?)\s*" + _U + r"?\s*[x×]\s*(\d+(?:\.\d+)?)\s*" + _U + r"?"
    r"(?:\s*[x×]\s*(\d+(?:\.\d+)?)\s*" + _U + r"?)?(?![A-Za-z])",
    re.I)
RE_DIA = re.compile(r"(?:dia\.?|diameter|OD|Ø|D\s*=)\s*(\d+(?:\.\d+)?)\s*(mm|in|\")?|"
                    r"(\d+(?:\.\d+)?)\s*(mm|in|\")\s*(?:dia\b|diameter|OD)", re.I)

TO_MM = {"mm": 1.0, "cm": 10.0, "um": 1e-3, "µm": 1e-3,
         "in": 25.4, "inch": 25.4, '"': 25.4, "ft": 304.8, "m": 1000.0}


def _mm(val, unit):
    return float(val) * TO_MM.get((unit or "mm").lower(), 1.0)


def parse_dims(text: str):
    """Return dict with any of w_mm, d_mm, h_mm, dia_mm. Empty dict if none."""
    out = {}
    m = RE_XYZ.search(text)
    if m:
        nums = [m.group(1), m.group(3), m.group(5)]
        units = [m.group(2), m.group(4), m.group(6)]
        last = max(i for i in range(3) if nums[i] is not None)
        # explicit unit on the LAST number required: rejects 320x256 px, 2 x 25 V ...
        if units[last]:
            # a number without its own unit inherits the next unit to its right
            for i in range(last - 1, -1, -1):
                units[i] = units[i] or units[i + 1]
            out["w_mm"], out["d_mm"] = _mm(nums[0], units[0]), _mm(nums[1], units[1])
            if nums[2]:
                out["h_mm"] = _mm(nums[2], units[2])
    m = RE_DIA.search(text)
    if m:
        val = m.group(1) or m.group(3)
        unit = m.group(2) or m.group(4)
        out["dia_mm"] = _mm(val, unit)
    # explicit labelled dims: "72 in W x 24 in D x 33.75 in H"
    lab = re.findall(r"(\d+(?:\.\d+)?)\s*(mm|cm|in|\")\s*([WDHL])\b", text)
    for val, unit, ax in lab:
        out[{"W": "w_mm", "D": "d_mm", "H": "h_mm", "L": "h_mm"}[ax]] = _mm(val, unit)
    return out


def stable_id(row) -> str:
    key = f"{row['Manufacturer']}|{row['Model / part no.']}|{row['Description']}"
    h = hashlib.sha1(key.encode()).hexdigest()[:6]
    mfr = re.sub(r"[^A-Za-z0-9]+", "", str(row["Manufacturer"]))[:12] or "UNK"
    pn = re.sub(r"[^A-Za-z0-9]+", "_", str(row["Model / part no."]))[:24].strip("_")
    return f"{mfr}_{pn}_{h}"


# ----------------------------------------------------------------------------
# Triage logic
# ----------------------------------------------------------------------------
def triage_row(r):
    flags, reason = [], []
    desc = str(r["Description"])
    model = str(r["Model / part no."])
    mfr = str(r["Manufacturer"])
    cat = r["Category"]
    tier = str(r["Twin tier"])[0]
    link_conf = r["Link confidence"] if pd.notna(r["Link confidence"]) else "none"
    has_ds = pd.notna(r["Datasheet"])
    has_url = pd.notna(r["Product page"])

    # ---- hard excludes ---------------------------------------------------
    if tier == "D":
        return "EXCLUDE", "X", ["consumable"], "tier D (consumable) in source", {}, 0
    if RE_OPTION_MODEL.search(model) or RE_OPTION_DESC.search(desc):
        return "MERGE_INTO_PARENT", "X", ["option_or_license"], \
            "option/upgrade/licence: not a physical asset; attach to parent instrument", {}, 0
    if "parent line item" in desc.lower():
        return "MERGE_INTO_PARENT", "X", ["kit_parent"], \
            "kit parent row; children are itemised separately", {}, 0

    prio, why = CATEGORY_PRIORITY.get(cat, ("P3", "unknown category"))
    if prio == "P0" and tier == "C":
        prio, why = "P2", "small part in a scene-skeleton category (breadboard/isolator hardware)"
    reason.append(f"{cat}: {why}")

    dims = parse_dims(desc)
    pack = RE_PACK.search(desc)
    if pack:
        flags.append("pack_row")
    if RE_NOPN.search(model) or mfr == "UNKNOWN":
        flags.append("no_part_number")
    if RE_NODESC.search(desc):
        flags.append("no_description")
    if RE_GROUPED_MODEL.search(model) or RE_GROUPED_DESC.search(desc):
        flags.append("grouped_row")
    if RE_FLEX.search(desc):
        flags.append("flexible")
    if link_conf == "constructed":
        flags.append("url_unverified")
    if link_conf == "low":
        flags.append("url_low_conf")

    # ---- route -----------------------------------------------------------
    if "grouped_row" in flags:
        route = "SPLIT_ROW_FIRST"
        reason.append("one row bundles several part numbers; split before conversion")
    elif "flexible" in flags:
        route = "DEFER_FLEXIBLE"
        reason.append("cable/fiber/tube: needs spline or rope model, not a rigid USD")
    elif "no_part_number" in flags and not dims:
        route = "MANUAL_RESEARCH"
        reason.append("no part number and no dimensions")
    elif mfr in CAD_LIKELY_VENDORS and "no_part_number" not in flags:
        route = "VENDOR_STEP"
        reason.append(f"{mfr} generally publishes STEP per part; verify availability")
    elif has_ds and not dims:
        route = "DATASHEET_DIMS"
        reason.append("datasheet link available; extract outline dimensions")
    elif dims and (("w_mm" in dims and "d_mm" in dims) or
                   ("dia_mm" in dims and "h_mm" in dims)):
        route = "PARAMETRIC_PRIMITIVE"
        reason.append("dimensions parsed from description; build box/cylinder")
    elif has_ds or has_url:
        route = "DATASHEET_DIMS"
        reason.append("product page exists; look up outline dimensions")
    else:
        route = "MANUAL_RESEARCH"
        reason.append("no CAD vendor, no dimensions, no link")

    # a STEP-likely row with no dims is still a primitive fallback candidate
    if route == "VENDOR_STEP" and dims:
        flags.append("has_fallback_dims")

    # ---- fidelity target by tier ----------------------------------------
    # 0 = bounding box, 1 = simplified mesh, 2 = full mesh
    tier_fid = {"A": 1, "B": 2, "C": 2}.get(tier, 0)
    if cat in ("Table_Isolation", "Furniture", "Instrument", "ProcessTool"):
        tier_fid = 0 if route != "VENDOR_STEP" else 1
    return route, prio, flags, "; ".join(reason), dims, tier_fid


def main(inp, outp):
    df = pd.read_csv(inp, encoding="latin-1")
    recs = []
    for _, r in df.iterrows():
        route, prio, flags, reason, dims, fid = triage_row(r)
        recs.append({
            "asset_id": stable_id(r),
            "priority": prio,
            "route": route,
            "fidelity_target": fid,
            "flags": "|".join(flags),
            "w_mm": dims.get("w_mm"), "d_mm": dims.get("d_mm"),
            "h_mm": dims.get("h_mm"), "dia_mm": dims.get("dia_mm"),
            "reason": reason,
        })
    out = pd.concat([df, pd.DataFrame(recs)], axis=1)
    out.to_csv(outp, index=False)

    print(f"rows: {len(out)}")
    print("\nroute x priority")
    print(pd.crosstab(out.route, out.priority).to_string())
    print("\nflag counts")
    print(out["flags"].str.split("|").explode().replace("", pd.NA).value_counts().to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
