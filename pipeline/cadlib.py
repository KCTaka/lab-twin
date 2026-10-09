"""Helpers for the vendor-CAD route: geometry keys, filename matching, material density.

Pure Python (no pxr, no OCP) so every stage can import it.
"""
from __future__ import annotations

import re

# --- geometry identity -----------------------------------------------------------
_PACK = re.compile(r"-P\d+$", re.I)          # TR1-P5 is five TR1 posts: same geometry


def cad_key(part_number: str) -> str:
    """Canonical geometry key: upper case, pack suffix removed. 'TR1-P5' -> 'TR1'.
    Metric variants stay distinct ('TR20/M' != 'TR20'): their threads differ."""
    return _PACK.sub("", part_number.strip().upper())


def file_tokens(name: str) -> set[str]:
    """Candidate part numbers in a vendor CAD file name.

    Vendors decorate names ('TR1-Step.zip', 'TR20_M-Step.stp', 'KM100 - STEP.step');
    strip the decoration and also try '_M' -> '/M' because '/' cannot appear in names.
    """
    stem = re.sub(r"\.(zip|step|stp|igs|iges)$", "", name.strip(), flags=re.I)
    stem = re.sub(r"[\s_-]*(step|stp|solidworks|3d|cad|model|assembly|asm)([\s_-]*\(\d+\))?$", "", stem, flags=re.I)
    stem = re.sub(r"\s*\(\d+\)$", "", stem)          # browser duplicate suffix 'TR1 (1)'
    base = stem.strip(" -_").upper()
    toks = {base, base.replace("_M", "/M"), base.replace(" ", "")}
    return {t for t in toks if t}


# --- material -> density ---------------------------------------------------------
# Handbook values (kg/m^3). Thorlabs posts are 303 stainless; most mounts are 6061 Al.
DENSITY = {
    "stainless": 7930,      # AISI 303/304
    "steel": 7850,
    "aluminium": 2700,      # 6061-T6
    "brass": 8500,
    "titanium": 4430,       # Ti-6Al-4V
    "invar": 8050,
}
_MAT_RULES = [   # (regex on description/title, material)
    (r"\bSS\b|stainless", "stainless"),
    (r"\bbrass\b", "brass"),
    (r"\btitanium\b", "titanium"),
    (r"\binvar\b", "invar"),
    (r"\balumin(i)?um\b|\bAl\b", "aluminium"),
    (r"\bsteel\b", "steel"),
]
# Family defaults where the vendor's construction is uniform and documented.
_FAMILY_DEFAULT = [
    (r"^(TR|RS|ER)\d", "stainless", "Thorlabs TR posts, RS pillars and ER cage rods are stainless steel"),
]


def infer_density(part_number: str, text: str) -> dict:
    """Density with provenance, in the same {value, src, ...} form as spec params."""
    for rx, mat in _MAT_RULES:
        m = re.search(rx, text, re.I)
        if m:
            return {"value": DENSITY[mat], "unit": "kg/m^3", "src": "inventory",
                    "quote": text[max(0, m.start() - 30): m.end() + 30].strip(), "material": mat}
    for rx, mat, why in _FAMILY_DEFAULT:
        if re.search(rx, part_number, re.I):
            return {"value": DENSITY[mat], "unit": "kg/m^3", "src": "assumed", "note": why, "material": mat}
    return {"value": DENSITY["aluminium"], "unit": "kg/m^3", "src": "assumed",
            "note": "no material stated; anodised 6061 aluminium is the common optomechanics default",
            "material": "aluminium"}
