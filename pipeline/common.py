"""Shared paths, manifest I/O and spec I/O for the lab-twin asset pipeline.

The manifest (data/manifest.csv) is the pipeline's state machine: one row per
asset, one status column per stage. Every stage reads it, works on the rows
whose previous stage is `done`, and writes its own status back. A stage never
edits another stage's column.

Status values: todo | done | blocked:<reason> | skip:<reason>
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SPECS = ROOT / "specs"
SOURCES = ROOT / "sources"        # vendor CAD / datasheets (git-ignored: vendor licences)
BUILD = ROOT / "build"            # intermediate outputs (raw CAD->USD, logs)
ASSETS = ROOT / "assets"          # final USD assets, one folder per asset_id
SCENES = ROOT / "scenes"
MANIFEST = DATA / "manifest.csv"
LIBRARY = ASSETS / "library.json"

STAGES = ["s2_source", "s3_geometry", "s4_usd", "s5_validate", "s6_register"]
MANIFEST_FIELDS = [
    "asset_id", "parent_asset_id", "scope", "priority", "route", "family",
    "category", "manufacturer", "part_number", "description",
    "qty", "pack_size", "instances", "w_mm", "d_mm", "h_mm", "dia_mm",
    "product_page", "datasheet", "link_confidence", "flags", "notes",
] + STAGES

# Provenance tags allowed on every numeric parameter in a spec.
PROVENANCE = ("inventory", "datasheet", "vendor_cad", "measured", "assumed")


def read_manifest(path: Path = MANIFEST) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_manifest(rows: list[dict], path: Path = MANIFEST) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def set_status(asset_id: str, stage: str, status: str, path: Path = MANIFEST) -> None:
    """Update one stage column for one asset (read-modify-write)."""
    assert stage in STAGES, stage
    rows = read_manifest(path)
    hit = [r for r in rows if r["asset_id"] == asset_id]
    if not hit:
        raise KeyError(asset_id)
    hit[0][stage] = status
    write_manifest(rows, path)


def spec_path(asset_id: str) -> Path:
    return SPECS / f"{asset_id}.json"


def load_spec(asset_id: str) -> dict:
    return json.loads(spec_path(asset_id).read_text())


def save_spec(spec: dict) -> None:
    spec_path(spec["asset_id"]).write_text(json.dumps(spec, indent=2) + "\n")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def provisional_params(spec: dict) -> list[str]:
    """Names of parameters whose value is only 'assumed' (need a real source)."""
    out = [k for k, v in spec.get("params", {}).items()   # dimensions only; visual choices don't count
           if isinstance(v, dict) and v.get("src") == "assumed" and v.get("unit") == "mm"]
    m = spec.get("physics", {}).get("mass_kg")
    if isinstance(m, dict) and m.get("src") == "assumed":
        out.append("physics.mass_kg")
    rho = spec.get("physics", {}).get("density_kg_m3")
    if isinstance(rho, dict) and rho.get("src") == "assumed":   # mass derived from it is provisional too
        out.append("physics.density_kg_m3")
    return out


# HTTP identity for the few vendor requests the pipeline makes (sitemaps, product pages).
# Contact address given by the group for this purpose (2026-10-09). Honour robots.txt:
# Thorlabs asks for Crawl-delay 30 and disallows *.step / *.zip for automated agents.
USER_AGENT = "lab-twin-asset-pipeline/0.1 (University of Toronto research; contact: kc.takahashi@mail.utoronto.ca)"
CRAWL_DELAY_S = {"www.thorlabs.com": 30}
