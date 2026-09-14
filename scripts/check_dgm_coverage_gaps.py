"""Prueft results/top_speed_validation_summary.json auf Segmente ohne
Steigungskorrektur (grade_pct=None) und listet sie kompakt auf. Genutzt
von der Scheduled-Task-Routine (process-new-mx5-logs, Schritt 10) - als
festes Skript statt Inline-Python, damit der Bash-Aufruf fuer die
Berechtigungs-Allowlist stabil bleibt. Faengt NUR die Symptome ab (fehlende
Steigungskorrektur) - das eigentliche Nachladen fehlender Hoehendaten-
Kacheln ist bewusst NICHT Teil dieser Routine (netzwerk-/zeitintensiv,
siehe PROJEKT_STAND.md "Neue Hoehendaten-Quelle gefunden", 06.09.2026)."""

import json
import os

RESULTS_DIR = "results"

with open(os.path.join(RESULTS_DIR, "top_speed_validation_summary.json")) as f:
    segments = json.load(f)

missing = [s for s in segments if s.get("grade_pct") is None]
print(f"{len(missing)} von {len(segments)} Segmenten ohne Steigungskorrektur (DGM-Luecke)")
for s in missing:
    print(f"  {s['log_id']}  t={s['t_start']:.0f}-{s['t_end']:.0f}s  "
          f"v={s['v_min_kmh']:.0f}-{s['v_max_kmh']:.0f}km/h  "
          f"Quellen={s.get('elevation_sources')}")

with open(os.path.join(RESULTS_DIR, "dgm_coverage_gaps.json"), "w", encoding="utf-8") as f:
    json.dump([
        {"log_id": s["log_id"], "t_start": s["t_start"], "t_end": s["t_end"],
         "v_min_kmh": s["v_min_kmh"], "v_max_kmh": s["v_max_kmh"],
         "elevation_sources": s.get("elevation_sources")}
        for s in missing
    ], f, indent=2, ensure_ascii=False)
