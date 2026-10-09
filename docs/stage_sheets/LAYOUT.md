# Lab layout

**Status: blocked on room geometry.** The inventory records what was bought,
not where it is. A layout needs room data from one of these sources, in order of
effort:

| Source | Accuracy | Effort | Notes |
|---|---|---|---|
| Facilities floor plan (PDF/DWG) of the room | ~1-2 cm | low | UofT Facilities & Services usually holds plans by building/room |
| Tape/laser-measure survey + photos | ~1 cm | 1-2 h | walls, doors, windows, column/sink/fume-hood positions, then each table's corner positions |
| 3D scan (phone LiDAR / photogrammetry) | 1-3 cm, captures clutter | 1 h + processing | best for later visual fidelity; still needs the survey for scale checks |

The inventory hints that at least one room is **SF4202** (the TMC table top was
bought to "replace water-damaged top in SF4202"); confirm the full room list.

## Layout file

Copy `scenes/layouts/EXAMPLE_not_the_lab.yaml` to `scenes/layouts/<room>.yaml`.
Units are metres and degrees. The origin is a room corner you choose and write
down (e.g. "SW corner at floor level, X along the south wall").

```yaml
name: SF4202
status: draft            # set to "measured" once positions come from a survey
room:
  outline: [[0,0],[L,0],[L,W],[0,W]]       # counter-clockwise floor polygon
  wall_height: 3.0
  wall_thickness: 0.15
  openings:
    - {wall: 0, offset: 1.0, width: 0.95, height: 2.1}           # door
    - {wall: 1, offset: 2.0, width: 1.5, sill: 0.9, height: 1.2} # window
placements:
  - {id: table_A, asset: <asset_id>, at: [x, y], yaw: 0}
  - {id: board_1, asset: <asset_id>, wall: 2, offset: 3.0, z: 0.9}
  - {id: shelf_A, asset: <asset_id>, host: table_A.surface_center, at: [0, 0.45]}   # not `on:` (YAML boolean)
```

- `asset` must be an id in `assets/library.json` (validated assets only).
- One `placements` entry per physical instance; the library lists
  `instances_owned`, and the builder does not check counts against it.
- `yaw` 0 means the asset's front (-Y side) faces world -Y.

## Build

```bash
~/isaacsim/python.sh scenes/build_layout.py scenes/layouts/<room>.yaml --render
```

Output: `scenes/lab_layout_<room>.usda`, plus `build/layout_<room>_top.png` and
`build/layout_<room>_persp.png`. The builder warns on overlapping footprints and on
assets outside the room, and never moves anything itself.

## For an LLM filling the layout from a floor plan

- Read dimensions only from dimension strings on the plan, or from the scale bar
  with the measured pixel distance written in a comment. Never estimate by eye
  without saying so.
- Put every number's origin in a YAML comment (`# plan A-201, dim string 8530`).
- Furniture positions on architectural plans are often indicative only: keep
  `status: draft` until someone has measured them.
