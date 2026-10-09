# CAD pilot: 8 Thorlabs STEP files (manual download)

Thorlabs' robots.txt disallows automated fetching of `*.step` / `*.zip`, so these are
downloaded by a person. All 8 part numbers were verified against the Thorlabs product
sitemap (2026-10-09).

**How:** in the Isaac Sim machine's browser (VNC session, so files land on the machine),
open each link, then *Support Documents -> Step -> Download*. Save everything into
`~/Downloads/lab-twin-cad/` without renaming. Zip files are fine.
Then run `.venv/bin/python pipeline/s2_ingest_downloads.py ~/Downloads/lab-twin-cad`.

| # | Part | Why it is in the pilot | Page |
|---|---|---|---|
| 1 | TR1 | simple solid; units + mass baseline | https://www.thorlabs.com/item/TR1 |
| 2 | PH3 | concave bore + thumbscrew sub-assembly (convex hull is wrong) | https://www.thorlabs.com/item/PH3 |
| 3 | BA2 | slot cut-out; aluminium | https://www.thorlabs.com/item/BA2 |
| 4 | CP02 | SM1 thread + 4 cage-rod holes (key-point detection) | https://www.thorlabs.com/item/CP02 |
| 5 | SM1L10 | long fine thread (triangle-count blow-up) | https://www.thorlabs.com/item/SM1L10 |
| 6 | KM100 | multi-body kinematic mount, springs; optical axis | https://www.thorlabs.com/item/KM100 |
| 7 | PT1 | translation stage; moving carriage (future prismatic joint) | https://www.thorlabs.com/item/PT1 |
| 8 | MAX311D | NanoMax 3-axis; fibre alignment workhorse; worst-case assembly | https://www.thorlabs.com/item/MAX311D |
