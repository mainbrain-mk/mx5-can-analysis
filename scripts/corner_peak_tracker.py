"""
Kurven-Spitzenwert-Tracker (MX-5 Projekt)

Analog zu `shift_time_best.json`/`shift_time_analysis.py`: haelt fest, wann
die staerkste je per CAN gemessene Kurve (hoechstes |a_lat_peak_g|) erreicht
wurde - getrennt nach Richtung (rechts/links), da Asymmetrien zwischen
beiden moeglich sind (siehe mx5_tires-Memory).

WICHTIGE EINSCHRAENKUNG: liest ALLE `results/can_corner_event_summary_*.json`
(ein File pro CAN-Log, von `can_corner_event_analysis.py` erzeugt), nicht nur
die 43 nutzerbestaetigten Kurven aus `corner_speed_model.py` - dieses Skript
laeuft automatisch/deterministisch fuer jeden neuen CAN-Log OHNE manuelle
Durchsicht (anders als `corner_speed_model.py`s kuratierte Kalibrierbasis,
siehe dortiger Docstring/PROJEKT_STAND.md "Kurvenmodell"). Ein neuer
Spitzenwert hier ist deshalb ein KANDIDAT fuer eine echte Grenzbereichs-
Kurve, kein verifizierter Messwert - genau wie der bestehende
`check_corner_events`-Befund (a_lat_peak/mean-Verhaeltnis) in
pipeline_checks.py dient das als Hinweis fuer manuelle Durchsicht, nicht
als automatisch bestaetigte Tatsache.

Aufruf: .venv/bin/python scripts/corner_peak_tracker.py
"""
import glob
import json
import os

RESULTS_DIR = "results"


def main():
    best = {}
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "can_corner_event_summary_*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        log_id = data.get("log_id", os.path.basename(path))
        for ev in data.get("events", []):
            direction = ev["direction"]
            peak_g = abs(ev["a_lat_peak_g"])
            cur = best.get(direction)
            if cur is None or peak_g > cur["a_lat_peak_g"]:
                best[direction] = {
                    "a_lat_peak_g": peak_g, "log_id": log_id, "t_start": ev["t_start"],
                    "speed_at_peak_kmh": ev.get("speed_at_peak_kmh"), "duration_s": ev["duration_s"],
                }

    print("Staerkste je per CAN erfasste Kurve, nach Richtung:")
    for direction, v in sorted(best.items()):
        print(f"  {direction}: {v['a_lat_peak_g']:.2f}g  ({v['log_id']} @ t={v['t_start']:.1f}s, "
              f"v={v.get('speed_at_peak_kmh', 0):.0f}km/h)")

    out_path = os.path.join(RESULTS_DIR, "corner_peak_best.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(best, f, indent=2, ensure_ascii=False)
    print(f"\nBestwerte: {out_path}")


if __name__ == "__main__":
    main()
