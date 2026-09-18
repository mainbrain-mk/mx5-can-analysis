# Mazda MX-5 ND2 OBD/CAN data analyses

A hobby project turning OBD-II and raw CAN-bus logs from a Mazda MX-5 ND
(RF) into a small local data pipeline plus a set of physics-based vehicle
performance analyses: drivetrain/acceleration model validation, coastdown
(CdA/Crr) fits, braking and cornering (lateral-g) analysis, a CAN-based
traction-circle and shift-timing check, and a quasi-steady-state lap-time
simulation for a specific race track.

Not affiliated with, endorsed by, or sponsored by Mazda Motor Corporation.
All vehicle-specific findings here are reverse-engineered from this one
car's own logged data, not from any manufacturer documentation.

## What's in this repo

- `scripts/` — all analysis/ingestion code (Python). Each script's own
  docstring explains its purpose, method, and known limitations.
- `data/can/*.dbc` — CAN signal definitions used to decode the raw
  `candump` logs (see Licensing below).
- `docs/` — project documentation and log/journal, split into a short
  **`docs/status/`** (current state per topic, kept up to date) and the full
  **`docs/logs/`** (running, dated journal — every finding, bug, and decision
  in roughly chronological order; the closest thing this project has to a
  lab notebook, and the best source for the reasoning behind any given
  script). Start at [`docs/README.md`](docs/README.md) for a topic map of
  what lives where.

Project docs and commit messages are largely in German (the author's
working language); code identifiers and comments follow normal
English/German mixed conventions common in this kind of hobby project.

## What's deliberately NOT in this repo

- Raw OBD/CAN logs, the derived DuckDB datalake, and generated plots/results
  (`data/` except the DBCs, `results/`) — regenerable from the scripts, and
  they encode real-world GPS tracks and driving locations that shouldn't be
  public.
- Raw elevation point-cloud data (`höhendaten/`) — a large public dataset
  (ALS ground points), not this project's own content; fetch it yourself
  from the relevant state survey office if you need it.
- A Mazda factory CAN communication matrix used for cross-referencing
  signal names — provenance/license unclear, not redistributed here.

Because of this, the scripts in this repo are shared for the **method**,
not as a turnkey tool — they expect a local `data/datalake.duckdb` (built
by `scripts/build_datalake.py` from your own logs) that isn't included.

## Dependencies

Python 3.12+, and roughly: `numpy`, `pandas`, `scipy`, `matplotlib`,
`duckdb`, `cantools`, `python-can`. No `requirements.txt` yet — this is a
personal project without a packaging story, install what a given script's
imports need.

## Licensing

- Everything in this repository authored by the project owner (code and
  documentation) is licensed under **CC BY 4.0** — see `LICENSE`.
- `data/can/*.dbc` are derived from the community DBC
  [`berumiya/CAN_DBC_6thGenMazda`](https://github.com/berumiya/CAN_DBC_6thGenMazda)
  (CC BY 4.0), with project-specific fixes and additions layered on top
  (see [`docs/logs/can-bus-status.md`](docs/logs/can-bus-status.md) for the
  changelog) — still CC BY 4.0, attribution to the original author retained.
