#!/usr/bin/env python3
"""Stage 2 - Record a source document (STEP, IGES, datasheet PDF, photo) for an asset.

Vendor sites block scripted downloads (newport.com answers 403; thorlabs.com is a
JavaScript app), so files are fetched by a person or a browser agent and then
registered here. This makes every downstream number traceable to a file whose
hash, origin URL and retrieval date are on record.

    python pipeline/s2_record_source.py <asset_id> <file> --url <where it came from> \
        [--kind step|iges|datasheet|drawing|photo|measurement] [--note "..."] [--done]

Effects
  * copies <file> to sources/<asset_id>/<file name>   (sources/ is git-ignored:
    vendor CAD licences generally forbid redistribution)
  * appends an entry to sources/<asset_id>/SOURCE.json
  * --done sets s2_source = done in the manifest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SOURCES, ROOT, read_manifest, set_status, sha256  # noqa: E402

KINDS = ("step", "iges", "datasheet", "drawing", "photo", "measurement")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asset_id")
    ap.add_argument("file")
    ap.add_argument("--url", required=True, help="page or link the file was obtained from ('n/a' for own measurements)")
    ap.add_argument("--kind", choices=KINDS)
    ap.add_argument("--note", default="")
    ap.add_argument("--done", action="store_true")
    a = ap.parse_args()

    if a.asset_id not in {r["asset_id"] for r in read_manifest()}:
        sys.exit(f"unknown asset_id {a.asset_id}")
    src = Path(a.file).expanduser().resolve()
    if not src.is_file():
        sys.exit(f"no such file {src}")
    kind = a.kind or {".step": "step", ".stp": "step", ".igs": "iges", ".iges": "iges",
                      ".pdf": "datasheet"}.get(src.suffix.lower())
    if not kind:
        sys.exit("cannot infer --kind from extension; pass it explicitly")

    dest_dir = SOURCES / a.asset_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src != dest:
        shutil.copy2(src, dest)
    rec_path = dest_dir / "SOURCE.json"
    recs = json.loads(rec_path.read_text()) if rec_path.exists() else []
    recs.append({"file": str(dest.relative_to(ROOT)), "kind": kind, "sha256": sha256(dest),
                 "bytes": dest.stat().st_size, "url": a.url, "note": a.note,
                 "retrieved": dt.date.today().isoformat()})
    rec_path.write_text(json.dumps(recs, indent=2) + "\n")
    if a.done:
        set_status(a.asset_id, "s2_source", "done")
    print(f"S2 recorded {kind} for {a.asset_id}: {dest.relative_to(ROOT)}  sha256={recs[-1]['sha256'][:12]}")


if __name__ == "__main__":
    main()
