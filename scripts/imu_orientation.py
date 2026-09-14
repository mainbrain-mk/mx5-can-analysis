"""
Bestimmt PRO LOG automatisch, welche IMU-Rohachse (X/Y/Z) aktuell vertikal
ist. Grund: die Handyhalterung ist NICHT mehr fest verbaut, sondern kann
sich von Fahrt zu Fahrt aendern (Nutzerangabe 06.09.2026 - Halterung sitzt
jetzt variabel in der Mittelkonsole, Telefon kann "auf dem Ruecken" liegen
statt wie bisher hochkant in der Schale). Ersetzt die bisherige feste
Annahme "Y=vertikal" (galt fuer alle Logs bis inkl. 2026-09-05).

Einzige Quelle der Wahrheit fuer jedes Skript, das eine Achsenzuordnung
braucht: brake_event_analysis.py (Horizontalebene fuer a_long-Rotation),
corner_event_analysis.py (Gierrate-Kanal), grip_estimation.py (beides).

METHODIK: an einer echten, SPAETEN Standphase (VehicleSpeed=0, >=3s) liegt
GENAU eine der drei AccelerationWithGravity{X,Y,Z}-Achsen nahe +-9.81 m/s^2
(Erdanziehung), die anderen beiden nahe 0. Diese Achse ist "vertikal".
Das ALLERERSTE Standfenster eines Logs wird uebersprungen falls ein
zweites existiert - es faellt oft in die Montage-/Handling-Phase vor
Fahrtbeginn und ist entsprechend verrauscht (empirisch beobachtet
06.09.2026: erstes Fenster zeigte Std bis 5.8 m/s^2 auf allen 3 Achsen,
spaetere Fenster < 0.4).

GIERRATE-KONSEQUENZ: die Rotation um die Vertikalachse (= Gieren) kommt
vom gleichnamigen RotationRate<Achse>-Kanal, mit demselben Vorzeichen
GYRO_SIGN_SCALE=-1.0 wie zuvor bei RotationRateY (Y war die vertikale
Achse aller Logs bis 2026-09-05). Am 06.09.2026 fuer ein Log mit Z=vertikal
ERNEUT empirisch bestaetigt (Korrelation RotationRateZ vs. echte
GPS-Kursaenderung: -0.76/-0.80 ueber 2 Logs, exakt dasselbe Muster wie
zuvor bei RotationRateY) - Annahme: das Android-Sensor-Framework definiert
eine feste rechtshaendige Beziehung zwischen Beschleunigungs- und
Rotationsachsen, unabhaengig von der Einbaulage des Telefons. NICHT
separat fuer X=vertikal bestaetigt (bisher kein solcher Log aufgetreten) -
falls das vorkommt, Korrelation gegen GPS-Kursaenderung nachpruefen statt
blind zu vertrauen (siehe corner_event_analysis.py fuer das Verfahren).

FALLBACK: fehlt der Gravitationskanal (aeltere CSV-Logs ohne
AccelerationWithGravity*) oder laesst sich keine eindeutige Standphase
finden, wird auf Y=vertikal zurueckgefallen (korrekt fuer alle Logs vor
der neuen, variablen Halterung) - mit `defaulted=True` im Rueckgabewert
markiert, damit aufrufende Skripte das kenntlich machen koennen.
"""
import sqlite3

import numpy as np
import pandas as pd

GRAVITY_CHANNELS = {
    "X": "AccelerationWithGravityX",
    "Y": "AccelerationWithGravityY",
    "Z": "AccelerationWithGravityZ",
}
G_EXPECTED = 9.81
G_TOLERANCE = 0.5     # |mean| muss innerhalb dieser Toleranz von 9.81 liegen
STD_MAX = 1.0         # Ruhe-Streuung darf hoechstens das sein
OTHER_AXES_MAX = 2.0  # die zwei anderen Achsen muessen deutlich unter g bleiben
MIN_STANDSTILL_S = 3.0
GYRO_SIGN_SCALE = -1.0  # siehe Docstring - gilt fuer die jeweils erkannte Vertikalachse


def _load_raw(conn, pid_name):
    q = ("SELECT pde.Time AS t, pde.Value AS v FROM PidDataEntry pde "
         "JOIN PidMetadataEntry pme ON pde.UniqueId=pme.UniqueId "
         "WHERE pme.PidName=? ORDER BY t")
    return pd.read_sql_query(q, conn, params=[pid_name])


def _standstill_windows(conn, min_duration_s=MIN_STANDSTILL_S):
    speed = _load_raw(conn, "VehicleSpeed")
    if len(speed) == 0:
        return [], None
    t0 = speed["t"].min()
    speed["t_s"] = (speed["t"] - t0) / 1e7
    zero = speed[speed["v"] == 0]
    if len(zero) == 0:
        return [], t0
    gaps = zero["t_s"].diff().fillna(0)
    group = (gaps > 2).cumsum()
    windows = []
    for _, sub in zero.groupby(group):
        dur = sub["t_s"].max() - sub["t_s"].min()
        if dur >= min_duration_s:
            windows.append((float(sub["t_s"].min()), float(sub["t_s"].max())))
    return windows, t0


def detect_vertical_axis(db_path):
    """Liefert dict:
      {'axis': 'X'|'Y'|'Z', 'g_mean':.., 'g_std':.., 'window_used': (t0,t1),
       'defaulted': False}
    oder bei fehlendem Kanal/keiner eindeutigen Standphase:
      {'axis': 'Y', 'defaulted': True, 'reason': '...'}
    (Fallback Y, siehe Docstring)."""
    conn = sqlite3.connect(db_path)
    try:
        windows, t0 = _standstill_windows(conn)
        if not windows or t0 is None:
            return {"axis": "Y", "defaulted": True,
                    "reason": "keine Stillstandsphase >=3s gefunden"}

        # Jeden Gravitationskanal EINMAL fuer die ganze Fahrt laden (nicht pro
        # Kandidatenfenster neu abfragen - war urspruenglich ein Performance-Bug:
        # bei Logs mit vielen Standphasen (Stadtverkehr/Ampeln) wurde derselbe
        # ~50Hz-Kanal pro Kandidat neu von der Datenbank gelesen).
        channel_data = {}
        for axis, pid in GRAVITY_CHANNELS.items():
            df = _load_raw(conn, pid)
            if len(df) == 0:
                continue
            df["t_s"] = (df["t"] - t0) / 1e7
            channel_data[axis] = df

        if len(channel_data) < 3:
            return {"axis": "Y", "defaulted": True,
                    "reason": "kein AccelerationWithGravity*-Kanal in diesem Log"}

        # erstes Fenster ueberspringen falls ein zweites existiert (siehe Docstring)
        candidates = windows[1:] if len(windows) > 1 else windows

        for t_start, t_end in candidates:
            stats = {}
            mid = t_start + (t_end - t_start) * 0.5
            for axis, df in channel_data.items():
                sub = df[(df["t_s"] >= mid) & (df["t_s"] <= t_end)]
                if len(sub) < 5:
                    continue
                stats[axis] = (float(sub["v"].mean()), float(sub["v"].std()))
            if len(stats) < 3:
                continue
            best_axis = max(stats, key=lambda a: abs(stats[a][0]))
            mean, std = stats[best_axis]
            others_ok = all(abs(stats[a][0]) < OTHER_AXES_MAX
                             for a in stats if a != best_axis)
            if abs(abs(mean) - G_EXPECTED) <= G_TOLERANCE and std <= STD_MAX and others_ok:
                return {"axis": best_axis, "g_mean": mean, "g_std": std,
                        "window_used": (t_start, t_end), "defaulted": False}
        return {"axis": "Y", "defaulted": True,
                "reason": "keine Standphase lieferte ein eindeutiges Vertikal-Signal"}
    finally:
        conn.close()


def horizontal_axes(vertical_axis):
    """Die beiden Nicht-Vertikal-Achsen in fester Reihenfolge X<Y<Z -
    legt fest, welche Achse in der theta-Rotation als 'erste' (cos-artig)
    bzw. 'zweite' (sin-artig) Komponente behandelt wird."""
    return tuple(a for a in ("X", "Y", "Z") if a != vertical_axis)


def yaw_channel(vertical_axis):
    return f"RotationRate{vertical_axis}"
