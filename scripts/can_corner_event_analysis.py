"""
Kurvenereignis-Erkennung direkt aus dem validierten CAN-RCM-Signal
(LateralAcc_CAN, 0x76) - CAN-natives Gegenstueck zu corner_event_analysis.py.

Zweck: corner_event_analysis.py braucht eine GPS+Gyro-Kreuzvalidierung, weil
das Handy-Gyroskop (Halterungswackeln) allein nicht vertrauenswuerdig ist
(siehe dortiger Docstring). Fuer Logs mit CAN-Daten ist das nicht mehr
noetig: `LateralAcc_CAN`/`YawRate_CAN`/`Steering_Wheel_Absolute_Angle`
kommen direkt vom fahrzeugeigenen RCM/EPS und wurden bereits quantitativ
gegen das validierte OBD-Lenkwinkelmodell bestaetigt (r=0.91-0.95, siehe
can_lateral_validation.py, mx5_can_bus_status.md). Deshalb reicht hier eine
einfache Schwellwert-Erkennung auf dem durchgehenden Signal, OHNE die
1Hz-GPS-Fensterei, die bei corner_event_analysis.py zu fragmentierten
Ereignissen (Luecken >2.5s trennen faelschlich eine zusammenhaengende Kurve)
und uebersehenen kurzen/schnellen Sweepern fuehren konnte.

Bestaetigt am ersten Log mit echten Kurven (2026-09-13, Nutzer-Review):
- verschmilzt 4 kuenstlich fragmentierte OBD-Ereignisse (LKW voraus,
  Geschwindigkeit schwankte durch Hinterherfahren) zu einer einzigen
  33s-Kurve.
- findet eine vom OBD/GPS-Detektor komplett uebersehene Kurve (beide
  Richtungen, bis -0.54g bei 131 km/h) - vermutlich weil die GPS-Kursrate
  bei diesem kurzen, schnellen Sweeper knapp unter der alten Schwelle lag.

Methodik: |LateralAcc_CAN| > A_LAT_THRESHOLD_G als Kandidat, Luecken bis
MAX_GAP_S werden ueberbrueckt (deckt Kurzunterbrechungen im durchgehenden
CAN-Signal ab, nicht die GPS-Abtastluecken von corner_event_analysis.py),
Mindestdauer MIN_EVENT_DURATION_S. Pro Ereignis: Peak-a_lat (nicht Mittel -
bei kurzen Sweepern ist der Mittelwert ueber ein grobes Zeitfenster
irrefuehrend, siehe mx5_tires-Memory zur peak-vs-mean-Problematik in die
andere Richtung), mittlere/maximale Geschwindigkeit, Dauer.

EINSCHRAENKUNG (Nutzer-Feedback 2026-09-13, noch nicht automatisiert):
kurze, aber kraeftige Ereignisse an einer Kreuzung ("Abbiegen") sind
physikalisch nicht von einer echten Kurve unterscheidbar - reine
Lenkwinkel-/g-Kennzahlen reichen dafuer nicht, das braucht einen
Strassennetz-Abgleich (OSM, analog steering_zero_offset.py), der hier noch
NICHT gebaut ist. Ebenso keine automatische "zu schwach/irrelevant"-Filterung
- alle Ereignisse ueber der Schwelle werden ausgegeben, Bewertung der
Relevanz bleibt manuell.

Aufruf: python scripts/can_corner_event_analysis.py <can_log_id>
(can_log_id wie in der DuckDB, z.B. "candump-2026-09-12_211833")
"""
import sys
import json
import os
import duckdb
import numpy as np

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"

A_LAT_THRESHOLD_G = 0.15
MAX_GAP_S = 3.0
MIN_EVENT_DURATION_S = 1.0

# Manuell ausgeschlossene False Positives, per (log_id, t_start) identifiziert
# (analog EXCLUDED_CALIBRATION_EVENTS in steering_lateral_model.py). Nutzer-
# bestaetigt 2026-09-13: t=1048.1s in candump-2026-09-12_211833 (alte
# Nummerierung: Kurve 30) ist eine Geradeaus-Passage, kein echter Kurven-
# einschlag (+0.20g bei ~122-126km/h - vermutlich Spurwechsel/Fahrbahn-
# kamber, kein Steuerkorrektur-Wert vorhanden um das automatisch zu pruefen).
EXCLUDED_EVENTS = {
    ("candump-2026-09-12_211833", 1048.1179609298706),
}


def load_channel(con, log_id, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = ? "
        "AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()


def detect_events(t, a_lat_g, v_kmh, log_id=None):
    """Gruppiert Samples ueber der Schwelle zu Ereignissen - Luecken bis
    MAX_GAP_S werden nur INNERHALB desselben Vorzeichens ueberbrueckt, ein
    Vorzeichenwechsel beendet immer das laufende Ereignis. OHNE diese
    Trennung wurden S-Kurven/Schikanen (Rechts->Links direkt hintereinander,
    z.B. Luecke <3s aber Kurvenrichtung wechselt) faelschlich zu EINEM
    Ereignis mit nur dem Vorzeichen der staerksten Teilkurve verschmolzen -
    vom Nutzer 2026-09-13 an zwei konkreten Faellen (alte Ereignisse 11 und
    26) erkannt und bestaetigt (11 war in Wahrheit rechts->links->rechts->
    links, 26 rechts->links, je 4-7s lange, eindeutig echte Teilkurven,
    keine Rauschartefakte)."""
    mask = np.abs(a_lat_g) > A_LAT_THRESHOLD_G
    groups, start, last_true, cur_sign = [], None, None, None
    for i, m in enumerate(mask):
        if m:
            sign = 1 if a_lat_g[i] > 0 else -1
            if start is not None and sign != cur_sign:
                groups.append((start, last_true))
                start = None
            if start is None:
                start, cur_sign = i, sign
            last_true = i
        elif start is not None and (t[i] - t[last_true]) > MAX_GAP_S:
            groups.append((start, last_true))
            start = None
    if start is not None:
        groups.append((start, last_true))

    events = []
    for s, e in groups:
        dur = t[e] - t[s]
        if dur < MIN_EVENT_DURATION_S:
            continue
        if log_id is not None and (log_id, float(t[s])) in EXCLUDED_EVENTS:
            continue
        seg = a_lat_g[s:e + 1]
        peak_idx = np.argmax(np.abs(seg))
        peak = seg[peak_idx]
        events.append({
            "t_start": float(t[s]), "t_end": float(t[e]), "duration_s": float(dur),
            "direction": "rechts" if peak > 0 else "links",
            "a_lat_peak_g": float(peak),
            "speed_at_peak_kmh": float(v_kmh[s:e + 1][peak_idx]),
            "speed_mean_kmh": float(v_kmh[s:e + 1].mean()),
            "speed_max_kmh": float(v_kmh[s:e + 1].max()),
        })
    return events


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Aufruf: python scripts/can_corner_event_analysis.py <can_log_id>")
    log_id = sys.argv[1]
    con = duckdb.connect(DB_PATH, read_only=True)
    lat = load_channel(con, log_id, "LateralAcc_CAN")
    speed = load_channel(con, log_id, "VehicleSpeed")
    if lat.empty:
        raise SystemExit(f"Kein LateralAcc_CAN fuer log_id={log_id!r}")

    t = lat["t"].values
    a = lat["value"].values
    v = np.interp(t, speed["t"].values, speed["value"].values)
    events = detect_events(t, a, v, log_id=log_id)

    print(f"{log_id}: {len(events)} Kurvenereignisse (Schwelle {A_LAT_THRESHOLD_G}g, "
          f"max Luecke {MAX_GAP_S}s, min Dauer {MIN_EVENT_DURATION_S}s)")
    for i, ev in enumerate(events, 1):
        print(f"{i:2d}  t={ev['t_start']:7.1f}-{ev['t_end']:7.1f}s  dauer={ev['duration_s']:5.1f}s  "
              f"{ev['direction']:6s}  a_lat_peak={ev['a_lat_peak_g']:+.2f}g  "
              f"v={ev['speed_mean_kmh']:5.0f}(max{ev['speed_max_kmh']:5.0f})km/h")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f"can_corner_event_summary_{log_id.replace(' ', '_')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"log_id": log_id, "threshold_g": A_LAT_THRESHOLD_G, "events": events}, f,
                   indent=2, ensure_ascii=False)
    print(f"\nDetails: {out_path}")


if __name__ == "__main__":
    main()
