"""
Bremsmodell (MX-5 Projekt) - Punkt 5 der urspruenglichen Roadmap

Zweck: den validierten Bremsdruck-Kanal (BFP_PRE_MZ, siehe
brake_event_analysis.py) und die bremsbasiert kalibrierte
Laengsbeschleunigung a_long (siehe PROJEKT_STAND.md Punkt 4) zu einem
echten Bremsmodell zusammenfuehren: Zusammenhang Bremsdruck <->
Verzoegerung, maximale erreichte Verzoegerung, und fuer echte
Vollbremsungen (bis zum Stillstand) eine Bremsweg-/Bremszeit-Tabelle.

Im Unterschied zu grip_estimation.py wird hier NICHT das Quer-Problem
beruehrt - a_long ist unabhaengig davon weiterhin gut validiert (viele
Bremsereignisse, hohe Konzentration R, siehe PROJEKT_STAND.md).

Methodik:
  1. Alle Bremsereignisse aus `brake_event_summary.json` uebernehmen
     (BFP_PRE_MZ > 300 kPa, siehe brake_event_analysis.py), aber auf
     echte Bremsmanoever eingrenzen (nicht "Fuss auf der Bremse im
     Stand"): Dauer <= EVENT_MAX_DURATION_S UND OBD-Geschwindigkeit
     faellt um mindestens MIN_SPEED_DROP_KMH.
  2. Pro Ereignis a_long (X/Z-Rotation, log-spezifisches theta) im
     Zeitfenster [t_start, t_end] auswerten. WICHTIG: hier bewusst KEINE
     physical_clip-Ausschlussmaske wie in grip_estimation.py - erster
     Versuch mit dieser Maske zeigte, dass sie waehrend echter, starker
     Bremsungen fast alle Samples entfernt (fast jedes physical_clip-
     Ereignis ist als "IMU-only" geflaggt, weil die grobe ~2Hz-OBD-
     Referenz kurze echte Verzoegerungsspitzen oft nicht bestaetigen
     kann - "IMU-only" heisst NICHT zwingend Artefakt, nur "OBD konnte
     es nicht bestaetigen"). Ergebnis war eine absurd niedrige mittlere
     Verzoegerung (~0g) obwohl OBD im selben Fenster einen klaren
     Geschwindigkeitsabfall zeigte. Der MIN_SPEED_DROP_KMH-Check unten
     ist hier die eigentliche, direktere Bestaetigung "das ist eine
     echte Bewegung" - eine zusaetzliche Clip-Maske ist ueberfluessig
     und schaedlich.
  3. OBD-Geschwindigkeit an t_start/t_end (naechster Messwert) fuer
     Geschwindigkeitsabfall, sowie per Trapezregel ueber die rohen
     OBD-Samples integrierte Bremsstrecke.

  ZUSATZBEFUND (29.08.2026): der rohe a_long (nur mit dem 6Hz-Tiefpass
  aus vibration_analysis.py) oszilliert waehrend harter Bremsungen
  stark (z.B. +3.5 bis -2.7 m/s^2 mit ~0.3-0.4s Periode, also ~2.5-3Hz)
  - das ist NICHT die bekannte 21-23Hz-Halterungsresonanz (die waere
    vom 6Hz-Tiefpass laengst entfernt), sondern vermutlich eine echte
  Fahrzeug-NICKSCHWINGUNG (Aufbau taucht beim Bremsen vorne ein und
  schwingt nach) - am erhoehten Handy-Montageort (weit vom
  Fahrzeugschwerpunkt/Nickpol entfernt, Hebelarm-Effekt) stark verstaerkt
  wahrgenommen. Der simple Mittelwert von a_long ueber ein Ereignis kann
  dadurch nahe 0 herauskommen, obwohl OBD einen klaren
  Geschwindigkeitsabfall zeigt (beobachtet, urspruenglich fuer einen Bug
  gehalten). Deshalb zusaetzlicher Tiefpass BRAKING_SMOOTH_CUTOFF_HZ auf
  a_long NUR fuer dieses Skript, um eine sinnvolle "effektive"
  Verzoegerung zu bekommen - der direktere, von diesem Effekt unberuehrte
  Referenzwert bleibt `obd_avg_decel_g` (aus Geschwindigkeitsabfall).

  NACH DEM TEST: selbst geglaettet bleibt a_long_mean_g deutlich UNTER
  dem OBD-Wert (z.B. 0.01g vs. OBD 0.36-0.40g bei einer bestaetigt
  harten Bremsung) - Korrelation ueber alle Ereignisse nur ~0.2-0.3.
  Vermutung: die Nick-Ausschlagsrichtung (Bremsen = Nase runter, Loesen
  = Nase rauf/Rebound) liegt am Sensorort ungefaehr auf derselben Achse
  wie die echte Laengsbeschleunigung, sodass die RICHTUNG (und damit
  theta/die Vorzeichen-Kalibrierung aus brake_event_analysis.py)
  weiterhin stimmt, aber ein einfacher Fenster-Mittelwert/Peak die
  BETRAGSGROESSE durch die Bremsen-Loesen-Schwingung systematisch
  unterschaetzt. **Deshalb ist `obd_avg_decel_g` die einzige
  verlaessliche Kennzahl fuer die tatsaechliche Verzoegerungshoehe in
  diesem Skript - die IMU-Werte dienen nur der Einordnung/als
  Cross-Check, NICHT als Hauptergebnis.**
  4. Ereignisse mit Endgeschwindigkeit nahe 0 (< FULL_STOP_KMH) gelten
     als "Vollbremsung bis zum Stillstand" - fuer diese wird eine
     Bremsweg-/Bremszeit-Tabelle nach Anfangsgeschwindigkeit gebildet.
  5. Bremsdruck-vs-Verzoegerung-Streudiagramm ueber alle Ereignisse
     (PNG), zeigt u.a. ob/wo eine Saettigung auftritt (Reifen-/ABS-
     Limit statt weiter steigender Verzoegerung bei mehr Druck).

WICHTIGE EINSCHRAENKUNGEN:
  - a_long-Werte sind lowpass-gefiltert (<6Hz) - sehr kurze Verzoege-
    rungsspitzen (ABS-Pulsieren) werden geglaettet, das Modell bildet
    eher die "effektive" Verzoegerung ab, nicht die Millisekunden-Spitze.
  - OBD-Geschwindigkeit (1 km/h Aufloesung, ~3Hz) begrenzt die Praezision
    von Bremsstrecke/-zeit bei kurzen/schwachen Bremsungen - fuer klare
    Vollbremsungen (mehrere Sekunden, grosser Geschwindigkeitsabfall)
    unkritisch.
  - Manche Logs haben durch den vorherigen 50-Ereignis-Deckel in
    `brake_event_analysis.py` ggf. schwache Bremsungen zunaechst
    verpasst - wurde vor diesem Skript behoben (max_events jetzt 500).

Aufruf: python braking_model.py
"""
import glob
import os
import json
import duckdb
import numpy as np
import pandas as pd
from scipy import signal
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"
RESULTS_DIR = "results"
DB_PATH = "data/datalake.duckdb"

# .dlg-Log -> gepaarter CAN-log_id im Datalake (siehe CAN_GPS_PAIRS in
# build_datalake.py fuers gleiche Muster). Nur die Y-Splitter-Fahrt
# (2026-09-12) hat bisher gleichzeitig OBD+CAN - siehe mx5_can_bus_status.md.
# Fuer diese Logs zusaetzlich CAN-basierte Kennzahlen (VehicleSpeed ~50Hz statt
# OBD ~2-4Hz, BrakePressure_CAN [heute erst kalibriert, R²=0.986], Rad-
# geschwindigkeiten einzeln) berechnen und den Ereignissen beimischen - ergaenzt
# die bestehende OBD-Auswertung, ersetzt sie nicht (die ~100 anderen Logs haben
# kein CAN).
CAN_LOG_PAIRS = {"2026-09-12 211851.dlg": "candump-2026-09-12_211833"}
CAN_START_EPOCH = {"candump-2026-09-12_211833": 1789240713.611747}

G = 9.81
FS_UNIFORM = 50.0
EVENT_MIN_DURATION_S = 1.0      # kuerzere Ereignisse: OBD-Quantisierungsrauschen
                                  # kann bei sehr kurzer Dauer eine absurd hohe
                                  # "Verzoegerung" vortaeuschen (1 km/h-Aufloesung
                                  # geteilt durch <0.2s), siehe PROJEKT_STAND.md
EVENT_MAX_DURATION_S = 12.0
MIN_SPEED_DROP_KMH = 3.0
FULL_STOP_KMH = 3.0
CORE_PRESSURE_FRAC = 0.7
BRAKING_SMOOTH_CUTOFF_HZ = 1.0   # zusaetzlicher Tiefpass gegen die
                                  # Nickschwingung, siehe Docstring


def load_obd_speed(db_file, t0):
    import sqlite3
    TICKS_OFFSET = 621355968000000000
    conn = sqlite3.connect(os.path.join(RAW_DIR, db_file))
    q = """
        SELECT pde.Time AS raw_time, pde.Value AS value
        FROM PidDataEntry pde
        LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        WHERE pme.PidName = "VehicleSpeed"
        ORDER BY pde.Time ASC
    """
    df = pd.read_sql_query(q, conn)
    conn.close()
    df["datetime"] = pd.to_datetime((df["raw_time"] - TICKS_OFFSET) / 10, unit="us")
    df["t"] = (df["datetime"] - t0).dt.total_seconds()
    return df[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")


def accel_t0(db_file):
    import sqlite3
    TICKS_OFFSET = 621355968000000000
    conn = sqlite3.connect(os.path.join(RAW_DIR, db_file))
    q = """
        SELECT pde.Time AS raw_time
        FROM PidDataEntry pde
        LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        WHERE pme.PidName IN ("AccelerationX","AccelerationY","AccelerationZ")
        ORDER BY pde.Time ASC LIMIT 1
    """
    row = conn.execute(q).fetchone()
    conn.close()
    return pd.to_datetime((row[0] - TICKS_OFFSET) / 10, unit="us")


def _can_enrich_event(con, can_log_id, t_start_epoch, t_end_epoch):
    """Zusaetzliche Kennzahlen aus dem gepaarten CAN-Log fuer ein einzelnes
    Bremsereignis (Zeitfenster in absoluten Unix-Epoch-Sekunden). Liefert ein
    dict mit can_*-Feldern, oder ein dict mit can_available=False falls das
    Fenster ausserhalb des CAN-Logs liegt oder zu wenige Punkte hat - kein
    Abbruch, die OBD-Auswertung bleibt in jedem Fall gueltig."""
    t0 = CAN_START_EPOCH[can_log_id]

    def channel(name):
        df = con.execute(
            "select t_elapsed_s, value from measurements where log_id=? and channel=? "
            "and t_elapsed_s between ? and ? order by t_elapsed_s",
            [can_log_id, name, t_start_epoch - t0 - 1.0, t_end_epoch - t0 + 1.0],
        ).fetchdf()
        return df

    sp = channel("VehicleSpeed")
    if len(sp) < 5:
        return {"can_available": False}

    t_can = sp["t_elapsed_s"].values + t0  # zurueck in absolute Epoch, fuer Klarheit
    v_ms = sp["value"].values / 3.6
    m = (t_can >= t_start_epoch) & (t_can <= t_end_epoch)
    if m.sum() < 5:
        return {"can_available": False}

    t_seg, v_seg = t_can[m], v_ms[m]
    # Vorzeichenkonvention wie obd_avg_decel_g: negativ = Verzoegerung (KEINE
    # zusaetzliche Negation - die Steigung v(t) ist beim Bremsen schon negativ).
    can_decel_g = float(np.polyfit(t_seg - t_seg[0], v_seg, 1)[0] / G)
    can_distance_m = float(np.trapezoid(v_seg, t_seg))
    can_v_start_kmh = float(v_seg[0] * 3.6)
    can_v_end_kmh = float(v_seg[-1] * 3.6)

    bp = channel("BrakePressure_CAN")
    can_peak_brake_bar = float(bp["value"].max()) if len(bp) else None

    wheel_spread_kmh = None
    wheel_dfs = [channel(f"WheelSpeed_CAN_{i}") for i in (1, 2, 3, 4)]
    if all(len(w) >= 3 for w in wheel_dfs):
        # grobe ABS-/Radschlupf-Kennzahl: max. Spannweite zwischen den 4
        # Radgeschwindigkeiten auf einem gemeinsamen Zeitgitter (Blockieren/
        # ungleicher Schlupf zeigt sich als Aufspreizung) - kein vollstaendiges
        # ABS-Erkennungsmodell, nur ein erster Indikator.
        grid = np.linspace(t_start_epoch, t_end_epoch, 50)
        vals = [np.interp(grid, w["t_elapsed_s"].values + t0, w["value"].values) for w in wheel_dfs]
        spread = np.max(vals, axis=0) - np.min(vals, axis=0)
        wheel_spread_kmh = float(spread.max())

    return {
        "can_available": True,
        "can_decel_g": can_decel_g,
        "can_distance_m": can_distance_m,
        "can_v_start_kmh": can_v_start_kmh,
        "can_v_end_kmh": can_v_end_kmh,
        "can_n_speed_samples": int(m.sum()),
        "can_peak_brake_bar": can_peak_brake_bar,
        "can_wheel_speed_spread_kmh": wheel_spread_kmh,
    }


def process_log(db_file, theta_deg, brake_events, con=None):
    csv_path = os.path.join(DERIVED_DIR, db_file.replace(".dlg", "_imu_filtered.csv"))
    df = pd.read_csv(csv_path)
    t = df["t"].values
    x = df["AccelerationX_lowpass"].values
    z = df["AccelerationZ_lowpass"].values
    phi = np.radians(theta_deg)
    a_long = x * np.cos(phi) + z * np.sin(phi)
    b, a = signal.butter(4, BRAKING_SMOOTH_CUTOFF_HZ / (FS_UNIFORM / 2), btype="low")
    a_long_smooth = signal.filtfilt(b, a, a_long)

    t0 = accel_t0(db_file)
    t0_epoch = t0.value / 1e9  # naive Timestamp == UTC-Epoch, wie im ganzen Projekt ueblich
    speed = load_obd_speed(db_file, t0)
    st, sv = speed["t"].values, speed["value"].values
    can_log_id = CAN_LOG_PAIRS.get(db_file)

    results = []
    for ev in brake_events:
        dur = ev["duration_s"]
        if dur > EVENT_MAX_DURATION_S or dur < EVENT_MIN_DURATION_S:
            continue
        t_start, t_end = ev["t_start"], ev["t_end"]

        s_mask = (st >= t_start - 0.5) & (st <= t_end + 0.5)
        if s_mask.sum() < 2:
            continue
        v_start = sv[s_mask][0]
        v_end = sv[s_mask][-1]
        speed_drop = v_start - v_end
        if speed_drop < MIN_SPEED_DROP_KMH:
            continue

        wmask = (t >= t_start) & (t <= t_end)
        if wmask.sum() < 3:
            continue
        a_seg_raw = a_long[wmask]
        a_seg_smooth = a_long_smooth[wmask]
        a_peak_g = float(a_seg_smooth.min() / G)   # staerkste Verzoegerung (negativ), geglaettet
        a_mean_g = float(a_seg_smooth.mean() / G)
        a_peak_raw_g = float(a_seg_raw.min() / G)  # ungeglaettet, nur zur Diagnose

        v_ms = sv[s_mask] / 3.6
        t_s = st[s_mask]
        distance_m = float(np.trapezoid(v_ms, t_s))

        obd_avg_decel_g = float(-((v_start - v_end) / 3.6) / dur / G)

        result = {
            "file": db_file, "t_start": t_start, "t_end": t_end, "duration_s": dur,
            "peak_kpa": ev["peak_kpa"], "v_start_kmh": float(v_start), "v_end_kmh": float(v_end),
            "speed_drop_kmh": float(speed_drop), "full_stop": bool(v_end < FULL_STOP_KMH),
            "a_long_peak_g": a_peak_g, "a_long_mean_g": a_mean_g,
            "a_long_peak_raw_g": a_peak_raw_g,
            "obd_avg_decel_g": obd_avg_decel_g, "distance_m": distance_m,
            "n_samples_used": int(wmask.sum()),
        }
        if can_log_id is not None and con is not None:
            result.update(_can_enrich_event(con, can_log_id, t0_epoch + t_start, t0_epoch + t_end))
        results.append(result)
    return results


def plot_pressure_vs_decel(events, out_path):
    kpa = np.array([e["peak_kpa"] for e in events])
    decel_obd = np.array([-e["obd_avg_decel_g"] for e in events])
    decel_imu = np.array([-e["a_long_mean_g"] for e in events])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6), sharey=True)
    ax1.scatter(kpa, decel_obd, s=10, alpha=0.4, color="firebrick")
    ax1.set_xlabel("Spitzen-Bremsdruck [kPa]")
    ax1.set_ylabel("mittlere Verzoegerung [g]")
    ax1.set_title("...vs. OBD-Geschwindigkeitsabfall (primaer)")
    ax1.grid(alpha=0.3)
    ax2.scatter(kpa, decel_imu, s=10, alpha=0.4, color="steelblue")
    ax2.set_xlabel("Spitzen-Bremsdruck [kPa]")
    ax2.set_title("...vs. IMU a_long, geglaettet (Vergleich)")
    ax2.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    with open(os.path.join(RESULTS_DIR, "brake_event_summary.json"), encoding="utf-8") as f:
        brake_data = {r["file"]: r for r in json.load(f) if "error" not in r}

    con = duckdb.connect(DB_PATH, read_only=True)
    all_events = []
    for csv_path in sorted(glob.glob(f"{DERIVED_DIR}/*_imu_filtered.csv")):
        db_file = os.path.basename(csv_path).replace("_imu_filtered.csv", ".dlg")
        rec = brake_data.get(db_file)
        if rec is None or rec.get("forward_direction_deg") is None:
            continue
        events = process_log(db_file, rec["forward_direction_deg"], rec["events"], con=con)
        all_events.extend(events)
        n_can = sum(1 for e in events if e.get("can_available"))
        can_note = f", davon {n_can} mit CAN-Anreicherung" if db_file in CAN_LOG_PAIRS else ""
        print(f"{db_file}: {len(events)} auswertbare Bremsmanoever "
              f"(von {rec['n_events']} erkannten Druck-Ereignissen){can_note}")
    con.close()

    if not all_events:
        print("Keine auswertbaren Bremsereignisse gefunden.")
        return

    can_events = [e for e in all_events if e.get("can_available")]
    if can_events:
        print(f"\n=== CAN-Anreicherung ({len(can_events)} Ereignisse mit gepaartem CAN-Log) ===")
        print(f"{'t_start':>8s} {'v_start':>8s} {'OBD':>6s} {'CAN':>6s} {'BFP_kPa':>8s} {'CAN_bar':>8s} {'Radspreizung':>13s}")
        for e in can_events:
            bar_s = f"{e['can_peak_brake_bar']:.1f}" if e["can_peak_brake_bar"] is not None else "n/a"
            spread_s = f"{e['can_wheel_speed_spread_kmh']:.1f}km/h" if e["can_wheel_speed_spread_kmh"] is not None else "n/a"
            print(f"{e['t_start']:8.1f} {e['v_start_kmh']:7.0f}  {-e['obd_avg_decel_g']:5.2f}g "
                  f"{-e['can_decel_g']:5.2f}g {e['peak_kpa']:8.0f} {bar_s:>8s} {spread_s:>13s}")
        obd_c = np.array([-e["obd_avg_decel_g"] for e in can_events])
        can_c = np.array([-e["can_decel_g"] for e in can_events])
        print(f"Verzoegerung OBD vs. CAN: RMSE={np.sqrt(np.mean((obd_c-can_c)**2)):.3f}g, "
              f"Korrelation={np.corrcoef(obd_c,can_c)[0,1]:.3f}")
        print("CAN-Geschwindigkeit (~50Hz) liefert die Verzoegerung ueber eine Regression statt nur "
              "zwei OBD-Punkte - bei kurzen/schwachen Bremsungen deutlich robuster. Radspreizung ist "
              "ein grober ABS-/Schlupf-Indikator (max. Differenz zwischen den 4 Raedern), kein "
              "vollstaendiges ABS-Modell.")

    # OBD-Geschwindigkeitsabfall ist die PRIMAERE Kennzahl (unbeeinflusst von
    # der Nickschwingung, siehe Docstring); a_long (geglaettet) nur als Vergleich.
    obd_decels = np.array([-e["obd_avg_decel_g"] for e in all_events])
    imu_decels = np.array([-e["a_long_mean_g"] for e in all_events])
    print(f"\n=== Gesamtstatistik ueber {len(all_events)} Bremsmanoever ===")
    print(f"Mittlere Verzoegerung (OBD, primaer): max={obd_decels.max():.2f}g  "
          f"p95={np.percentile(obd_decels,95):.2f}g  p50={np.percentile(obd_decels,50):.2f}g")
    print(f"Mittlere Verzoegerung (IMU a_long, geglaettet, Vergleich): max={imu_decels.max():.2f}g  "
          f"p95={np.percentile(imu_decels,95):.2f}g  p50={np.percentile(imu_decels,50):.2f}g")
    corr = np.corrcoef(obd_decels, imu_decels)[0, 1]
    print(f"Korrelation OBD- vs. IMU-Verzoegerung ueber alle Ereignisse: {corr:.2f}")

    hardest = max(all_events, key=lambda e: -e["obd_avg_decel_g"])
    print(f"Haerteste Bremsung (OBD): {hardest['file']} bei t={hardest['t_start']:.1f}s, "
          f"{hardest['v_start_kmh']:.0f}->{hardest['v_end_kmh']:.0f} km/h, "
          f"{-hardest['obd_avg_decel_g']:.2f}g, Druck={hardest['peak_kpa']:.0f} kPa")

    full_stops = [e for e in all_events if e["full_stop"] and e["v_start_kmh"] >= 20]
    full_stops.sort(key=lambda e: e["v_start_kmh"])
    print(f"\n=== Vollbremsungen bis zum Stillstand (v_start >= 20 km/h, n={len(full_stops)}) ===")
    print(f"{'v_start':>8s} {'Dauer':>6s} {'Strecke':>8s} {'OBD':>6s} {'IMU':>6s}  Datei")
    for e in full_stops:
        print(f"{e['v_start_kmh']:7.0f}  {e['duration_s']:5.2f}s {e['distance_m']:7.1f}m "
              f"{-e['obd_avg_decel_g']:5.2f}g {-e['a_long_mean_g']:5.2f}g  {e['file']}")

    plot_pressure_vs_decel(all_events, os.path.join(RESULTS_DIR, "braking_pressure_vs_decel.png"))
    print("\nStreudiagramm: results/braking_pressure_vs_decel.png")

    with open(os.path.join(RESULTS_DIR, "braking_model_summary.json"), "w", encoding="utf-8") as f:
        json.dump(all_events, f, indent=2, ensure_ascii=False)
    print("Details: results/braking_model_summary.json")


if __name__ == "__main__":
    main()
