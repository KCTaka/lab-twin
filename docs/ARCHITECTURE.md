# Architecture

How the pipeline is put together and why. For the per-stage instructions, see
[stage_sheets/](stage_sheets/README.md); for the current state, see [STATUS.md](STATUS.md).

## 1. End-to-end flow

```mermaid
flowchart TD
    subgraph prep["1 · Prepare (deterministic, seconds)"]
        direction LR
        inv[("Inventory CSV<br/>823 rows")] --> S0["S0 triage"] --> tri[("triage.csv")] --> S1["S1 normalize"]
        S1 --> man[("manifest.csv<br/>1 row per asset type<br/>+ stage status")]
        S1 --> stub[/"specs/*.json stubs"/]
    end

    subgraph numbers["2 · Get the numbers (person or LLM; machine-gated)"]
        direction LR
        S2["S2 record source<br/>STEP / PDF + sha256"] --> S3["S3 fill spec<br/>value + src + quote"]
        S3 --> gate{"S3 gate<br/>R1-R5"}
        gate -->|errors| S3
        S2 --> S3a["S3a STEP to USD<br/>HOOPS, headless"]
    end

    subgraph build["3 · Build and check (pipeline/run_batch.sh, ~35 s)"]
        direction LR
        S4["S4 author USD<br/>m, Z-up, collision,<br/>key points"] --> S5["S5 validate<br/>bbox + PhysX drop test"]
        S5 -->|pass| S6["S6 register"]
    end

    subgraph use["4 · Use"]
        direction LR
        lib[("assets/library.json")] --> gal["review gallery<br/>+ thumbnails"]
        lib --> lay["room layout<br/>from YAML"]
    end

    stub --> S3
    gate -->|OK| S4
    S3a -->|"cad_raw.usda (mm)"| S4
    S5 -->|"fail: blocked"| S3
    S6 --> lib
```

Stages S4 to S6 and the scene builders run in one command (`pipeline/run_batch.sh`),
headless. S2 and S3 are where people or LLMs work; the S3 gate is what keeps
their output honest.

## 2. Routes: which path an asset takes

`route` is set in S0 and can be overridden by an explicit rule in S1. In the
layout pass, the Newport tables were rerouted from `VENDOR_STEP` to parametric
because vendor tabletop CAD carries thousands of tapped holes.

```mermaid
flowchart TD
    row["manifest row"] --> r{route}
    r -->|VENDOR_STEP| dl["S2: download STEP<br/>(manual: vendor sites block scripts)"]
    dl --> conv["S3a: HOOPS converter<br/>headless Kit"]
    conv --> cadspec["spec: family = cad<br/>density, key_points"]
    r -->|"PARAMETRIC_PRIMITIVE<br/>DATASHEET_DIMS"| ds["S2: datasheet / drawing"]
    ds --> fill["S3: fill family params<br/>value + src + quote"]
    r -->|MANUAL_RESEARCH| human["human research<br/>or measurement"]
    human --> fill
    r -->|"DEFER_FLEXIBLE<br/>SPLIT_ROW_FIRST<br/>EXCLUDE / MERGE"| parked["not in pipeline yet<br/>(cables need a rope model)"]
    cadspec --> gate{"S3 gate"}
    fill --> gate
    gate -->|OK| s4["S4 author"]
    gate -->|errors| fill
```

## 3. Data model

One inventory row can become several assets (rows such as `A; B; C` are split).
The spec is the contract: everything downstream is generated from it.

```mermaid
erDiagram
    INVENTORY_ROW ||--|{ MANIFEST_ROW : "S1 split 1..n"
    MANIFEST_ROW ||--o| SPEC : "stub if scope active"
    SPEC }o--o{ SOURCE_FILE : "cites (url, quote, sha256)"
    SPEC ||--o| ASSET_USD : "S4 generates"
    ASSET_USD ||--|| VALIDATION_REPORT : "S5"
    ASSET_USD ||--o| LIBRARY_ENTRY : "S6 if S5 passed"
    LIBRARY_ENTRY ||--o{ PLACEMENT : "one per physical instance"
    LAYOUT_YAML ||--|{ PLACEMENT : contains

    MANIFEST_ROW {
        string asset_id PK
        string scope
        string route
        string family
        int instances
        string s2_source
        string s3_geometry
        string s4_usd
        string s5_validate
        string s6_register
    }
    SPEC {
        string family
        object params "value in mm, src, url, quote, note"
        object physics "body, collision, mass_kg"
        object key_points "CAD route only"
    }
    LIBRARY_ENTRY {
        string usd
        float_array size_m
        object key_points "metres, asset frame"
        string_array provisional
    }
    PLACEMENT {
        string id
        string asset
        string mode "at | wall+offset | host+anchor"
    }
```

## 4. From spec to USD (parametric route)

```mermaid
flowchart LR
    p["spec.params<br/>{w: {value: 1828.8, src: inventory}, ...}"] --> rp["resolve_params<br/>(fails on null)"]
    rp --> fam["FAMILIES[family](p)<br/>generators/__init__.py"]
    fam --> parts["Parts<br/>box / cylinder, mm"]
    fam --> kps["KeyPoints<br/>pos, normal, support, data"]
    parts --> s4["s4_author_usd.py<br/>mm to m"]
    kps --> s4
    parts --> bb["expected_bbox"]
    bb -->|"stored in customData"| c2["S5 check C2"]
    s4 --> usd[("asset USD")]
```

Families today: `optical_table`, `workbench`, `shelving_closed`, `wall_panel`,
`projector_screen`, `curtain`, `cart`, `appliance_box`, `overhead_shelf`,
`hanging_shelf`. Adding a family means writing one generator function plus one
`REQUIRED` entry with plausibility ranges.

## 5. What an asset USD contains

```mermaid
flowchart TD
    root["/asset_id  (Xform, kind=component, defaultPrim)<br/>customData lab_twin: route, family, spec_sha, provisional, expected_bbox<br/>semantics: class label<br/>dynamic only: RigidBodyAPI + MassAPI"]
    root --> looks["Looks  (Scope)"]
    looks --> vis["visual materials<br/>UsdPreviewSurface"]
    looks --> pm["PhysicsMaterial<br/>static 0.6 / dynamic 0.5 friction"]
    root --> geo["Geometry  (Scope)"]
    geo --> part["part  (Xform: translate only)"]
    part --> shape["shape  (Cube / Cylinder: scale here)<br/>CollisionAPI, material + physics binding"]
    root --> kp["KeyPoints  (Scope)"]
    kp --> k1["e.g. surface_center  (Xform: translate)<br/>customData: normal, support, data"]
    k1 -.-> grid["hole_grid_origin data:<br/>pitch_mm 25.4, n_x, n_y, thread 1/4-20"]
```

Translate on the parent and scale on the child that carries `CollisionAPI`:
the Isaac Sim physics skill reports wrong collision bounds when a collider is scaled
and translated on the same prim. CAD assets differ only under `Geometry`: one
Xform that recentres (footprint centre to origin, lowest point to z = 0) and scales
by the converter's `metersPerUnit` (0.001), referencing `./cad/cad_raw.usda`, with
`CollisionAPI` + `MeshCollisionAPI` on every mesh.

Asset frame for every family: origin at the footprint centre on the floor, +X width,
+Y back (front face at -Y), +Z up.

## 6. Validation (S5)

```mermaid
sequenceDiagram
    autonumber
    participant V as s5_validate.py
    participant U as asset USD
    participant P as PhysX (headless)
    participant R as validation.json
    V->>U: open stage
    V->>V: C1 metrics, C2 bbox vs expected (max 1 mm or 2 %), C3 min z = 0, C4 colliders present
    alt body is dynamic
        V->>P: fresh stage: ground + asset
        P-->>V: after 1.5 s: drift and speed of the asset
    end
    alt asset has support key points
        V->>P: fresh stage: ground + asset + one probe cube per support point, dropped 20 mm
        P-->>V: after 1.5 s: probe z error and speed
    else wall-hung static panel
        V->>V: C5 not applicable (recorded as such)
    end
    V->>V: C6 list params still assumed
    V->>R: write report, set s5_validate = done / done:provisional / blocked
```

The probe is 100 mm and 1 kg on static assets. On dynamic assets it is scaled to
the surface (at most half the footprint, at most 20 % of the asset mass), so that it
tests the collider instead of knocking a small part over.
`--negative-control` reruns C5 with the asset's collision disabled: every probe must
then fail, which proves C5 is not vacuous.

## 7. Stage status

Each stage column in `manifest.csv` moves through the same states. A stage picks
up rows whose previous stage is `done` and whose own column is `todo`, `blocked`
or stale; it never resets a column to `todo`. S2 is advisory: the S3 gate checks
provenance inside the spec instead.

```mermaid
stateDiagram-v2
    direction LR
    state "done:provisional" as prov
    [*] --> skip: scope not active
    [*] --> todo: S1, scope active
    todo --> done: stage succeeds
    todo --> prov: S5 passes, some dims assumed
    todo --> blocked: check fails / no source
    blocked --> done: next run succeeds after the spec is fixed
    blocked --> blocked: still failing
    done --> done: re-run in place when spec or generator code changes
    prov --> done: assumed values measured or cited
```

S4 detects staleness from `build/id/authored.json` (spec hash and generator-code
hash); S5 re-runs when the USD is newer than its report.

## 8. Where things run

```mermaid
flowchart LR
    subgraph laptop["Your laptop"]
        term["terminal"]
        vnc["VNC viewer"]
    end
    subgraph rtx["jpgroup-rtx (GB449): RTX PRO 6000, Isaac Sim 6.1 standalone"]
        batch["pipeline/run_batch.sh<br/>Kit headless, --no-window"]
        gui["Isaac Sim GUI<br/>open scenes/*.usda"]
    end
    term -->|"ssh over Tailscale"| batch
    vnc -->|"VNC over Tailscale"| gui
    person["person or browser agent"] -->|"download STEP / PDF"| vendors["vendor sites<br/>Newport: 403 to scripts<br/>Thorlabs: JS app"]
    person -->|"s2_record_source.py"| batch
    batch -->|"git push"| gh[("GitHub<br/>KCTaka/lab-twin (private)")]
    batch -.->|"not yet connected"| nucleus[("Omniverse Nucleus<br/>digital-twin-lab")]
    gh -.->|"merge plan open"| ovl["JPPhotonics/omniverse-lab<br/>L490, Keithley 2400 work"]
```

The GPU is shared (other users' Kit sessions run on it); batch runs are short, but
schedule large conversion jobs.

## 9. Inventory coverage (2026-10-09)

```mermaid
pie showData
    title Manifest rows by scope (908 inventory-derived)
    "backlog P2 optomechanics" : 355
    "backlog P3 fibre, RF, electronics" : 229
    "backlog P1 stages, probes, optics" : 223
    "instruments (deferred)" : 50
    "excluded (consumables, options)" : 27
    "layout L1 (active)" : 14
    "layout L2 (room unknown)" : 7
    "bench items" : 3
```

Layout L1 is 14 asset types covering 30 physical instances; 13 types are validated,
and the AVJ42 cart is blocked (maker and dimensions unknown).

---

Diagrams are Mermaid (rendered natively by GitHub). After editing any of them, run
`tests/check_mermaid.sh`; it renders every Mermaid block in the repo and fails on a syntax error.
