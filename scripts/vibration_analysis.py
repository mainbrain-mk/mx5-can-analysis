"""
Schwingungsanalyse fuer OBD-Fusion .dlg-Logs (MX-5 Projekt)

Zweck: automatisiert pruefen, ob die IMU-Beschleunigungsdaten von einer
mechanischen Resonanz (z.B. Saugnapf-Halterung) ueberlagert sind, und
ein Tiefpass-/Notch-Filter anwenden, um das SNR fuer die
Fahrdynamik-Auswertung (Quer-/Laengsbeschleunigung) zu verbessern.

Ausgabe:
  - <DB_FILE>_vibration_summary.json   Kompakte Kennzahlen, gedacht zum Teilen
  - <DB_FILE>_imu_filtered.csv         Zeit, Roh-/bereinigtes/gefiltertes Signal (alle 3 Achsen)

Methodik kurz:
  1. IMU-Kanaele (AccelerationX/Y/Z) sind unregelmaessig getaktet (~50-60 Hz,
     siehe dlg_database.py-Analyse). Wir resamplen linear auf ein
     gleichmaessiges Raster (fs_uniform), damit FFT/Filter ueberhaupt
     anwendbar sind.
  2. Ausreisserbehandlung (NEU): bevor irgendeine Frequenzanalyse laeuft,
     durchlaeuft das Rohsignal einen Hampel-Filter (robuste, frequenz-
     unabhaengige Ausreissererkennung auf Basis von gleitendem Median/MAD)
     und danach ein physikalisches Clipping (harte Grenze in m/s^2).
     Beides wird geloggt (Anzahl, Zeitpunkt, Wert), nichts wird still
     verworfen. Hintergrund: kurze, breitbandige Stoesse (Halterungs-
     "Knacken", Schlagloch, Einlegen der Parkposition) enthalten auch
     niederfrequente Energie und rutschen sonst durch den Tiefpass bzw.
     verzerren sogar die PSD-basierte Resonanz-Erkennung selbst (siehe
     2026-08-28 15:18 Log: kurze Fahrt, wenige Mittelungsfenster, zwei
     Stoss-Cluster kippen den erkannten Peak von ~21Hz auf ~1Hz).
  3. Kreuzvalidierung (NEU): fuer jedes erkannte Ausreisser-Ereignis wird
     grob geprueft, ob der OBD-Kanal VehicleSpeed (~2Hz) im selben
     Zeitfenster eine dazu passende Geschwindigkeitsaenderung zeigt.
     Passt es zusammen -> eher reales Fahrereignis (Bremsen/Beschleunigen).
     Passt es nicht zusammen -> eher IMU-/Halterungsartefakt. Das ist nur
     ein grober Plausibilitaetscheck (2Hz-Aufloesung), kein Beweis.
  4. Welch-PSD pro (jetzt bereinigter) Achse -> dominante Frequenz oberhalb
     0.5 Hz finden (0.5 Hz Grenze, um reine Fahrzeugbeschleunigung/DC nicht
     als "Peak" misszuverstehen).
  5. Resonanz-Check: Zuendfrequenz aus RPM ableiten (RPM/60 * Zylinderzahl/2)
     und pruefen, ob die dominante Frequenz mit der Zuendfrequenz mitwandert
     (Motorordnung) oder ueber die Fahrt konstant bleibt (mechanische
     Resonanz der Halterung). Kennzahl: Korrelation zwischen lokal
     dominanter Frequenz (gleitendes Fenster) und Zuendfrequenz.
  6. Tiefpass (Butterworth, phasenfrei via filtfilt) als Standardmassnahme,
     da Fahrzeugdynamik (Bremsen/Kurven/Beschleunigen) i.d.R. < 8 Hz liegt.
     Optional zusaetzlich ein Notch-Filter exakt auf der gefundenen
     Resonanzfrequenz, falls diese oberhalb der Tiefpass-Grenze liegt
     und nicht mit der Motorordnung korreliert.
"""

import sys
import os
import sqlite3
import json
import numpy as np
import pandas as pd
from scipy import signal
from scipy.interpolate import interp1d

# ----------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------
# Verzeichnisstruktur (siehe PROJEKT_STAND.md): Rohdaten in data/raw/,
# abgeleitete Pro-Log-Dateien in data/derived/. Skript wird vom
# Projekt-Wurzelverzeichnis aus aufgerufen (python scripts/vibration_analysis.py ...).
RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"

DB_FILE = "2026-08-28 143138.dlg"   # <- ggf. anpassen, wie in dlg_database.py
if len(sys.argv) > 1:
    DB_FILE = sys.argv[1]           # Alternativ: python vibration_analysis.py "<datei>.dlg"
DB_BASENAME = os.path.basename(DB_FILE)
DB_PATH = os.path.join(RAW_DIR, DB_BASENAME)

FS_UNIFORM = 50.0        # Ziel-Abtastrate nach Resampling [Hz]
                          # (native IMU-Rate liegt bei ~50-60 Hz, siehe Analyse;
                          #  50 Hz ist konservativ und vermeidet Aliasing knapp
                          #  unterhalb der nativen Rate)
LOWPASS_CUTOFF_HZ = 6.0   # Fahrzeugdynamik-Nutzband, siehe Docstring
NOTCH_Q = 15.0            # Guete des Notch-Filters (Bandbreite der Kerbe)
CYLINDERS = 4             # fuer Zuendfrequenz-Berechnung (MX-5 Skyactiv-G 2.0)
PEAK_MIN_FREQ_HZ = 0.5    # unterhalb davon nicht als "Schwingungs-Peak" werten
RESONANCE_SEARCH_MIN_HZ = 5.0  # Peak-Suche fuer die Halterungsresonanz NUR oberhalb
                           # dieser Grenze: echte Fahrzeugdynamik (Bremsen/Kurven/
                           # Beschleunigen) liegt praktisch immer darunter (vgl.
                           # LOWPASS_CUTOFF_HZ), oberhalb bleibt nur die mechanische
                           # Resonanz. Behebt die Fehlklassifikation bei kurzen/
                           # dynamischen Fahrten, bei denen ein staerkerer
                           # niederfrequenter Fahrdynamik-Peak sonst den globalen
                           # PSD-Peak dominiert und die Resonanz "versteckt"
                           # (siehe 15:18- und 17:03-Log).
TOP_N_PEAKS = 5            # Anzahl gemeldeter Nebenpeaks fuer die Diagnose
PEAK_MIN_SEPARATION_HZ = 1.0  # Mindestabstand zwischen gemeldeten Peaks
TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz

# --- Ausreisserbehandlung (NEU) ---
HAMPEL_WINDOW_S = 0.3     # Fenster fuer gleitenden Median/MAD [s] (=15 Samples @50Hz);
                           # muss deutlich breiter sein als ein einzelner Stoss
                           # (die beobachteten Stoesse dauern ~0.02-0.1s), damit
                           # genug "normale" Nachbarwerte fuer Median/MAD bleiben
HAMPEL_N_SIGMAS = 4.0      # Schwelle in robusten Sigmas (1.4826*MAD); konservativ
                           # gewaehlt, um echtes hartes Bremsen/Kurven nicht
                           # faelschlich als Ausreisser zu werten
HAMPEL_SIGMA_FLOOR = 0.05  # m/s^2 - verhindert Overflagging in sehr ruhigen
                           # Signalabschnitten (MAD ~ 0)
PHYSICAL_CLIP_MS2 = 12.0   # ~1.2g - grosszuegige Plausibilitaetsgrenze fuer
                           # Laengs-/Querbeschleunigung eines Strassenfahrzeugs;
                           # dient als harter Sicherheitsnetz NACH dem Hampel-
                           # Filter (falls dessen Fenster einen Stoss doch
                           # durchlaesst, z.B. am Signalrand)
CROSSVAL_WINDOW_S = 1.0    # Zeitfenster um ein Ereignis, in dem die OBD-
                           # Geschwindigkeit fuer die grobe dv/dt-Schaetzung
                           # herangezogen wird
CROSSVAL_MIN_OBD_MS2 = 0.3 # unterhalb davon gilt die OBD-Geschwindigkeits-
                           # aenderung als "nicht nennenswert"
CROSSVAL_TOL_FACTOR = 3.0  # IMU-Wert darf hoechstens das X-fache des OBD-
                           # dv/dt betragen, um noch als "bestaetigt" zu gelten
MAX_EVENTS_LOGGED = 25     # pro Achse/Quelle max. so viele Einzelereignisse
                           # im JSON auflisten (nach Betrag sortiert)


# ----------------------------------------------------------------------
# 1. Rohdaten aus .dlg laden
# ----------------------------------------------------------------------
def load_channel(conn, pid_names):
    """Laedt einen oder mehrere PidName-Kanaele mit rohem Zeitstempel."""
    placeholders = ",".join("?" for _ in pid_names)
    q = f"""
        SELECT pde.Time AS raw_time, pme.PidName AS sensor_name, pde.Value AS value
        FROM PidDataEntry pde
        LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        WHERE pme.PidName IN ({placeholders})
        ORDER BY pde.Time ASC
    """
    df = pd.read_sql_query(q, conn, params=pid_names)
    df["datetime"] = pd.to_datetime((df["raw_time"] - TICKS_OFFSET) / 10, unit="us")
    return df


def to_elapsed_seconds(df, t0):
    df = df.copy()
    df["t"] = (df["datetime"] - t0).dt.total_seconds()
    return df


# ----------------------------------------------------------------------
# 2. Resampling auf gleichmaessiges Raster
# ----------------------------------------------------------------------
def resample_uniform(df_channel, t_uniform, kind="linear"):
    """df_channel: DataFrame mit Spalten ['t','value'], eindeutig+sortiert."""
    sub = df_channel[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    f = interp1d(sub["t"].values, sub["value"].values, kind=kind,
                 bounds_error=False, fill_value=(sub["value"].values[0], sub["value"].values[-1]))
    return f(t_uniform)


# ----------------------------------------------------------------------
# 3. Ausreisserbehandlung: Hampel-Filter + physikalisches Clipping
# ----------------------------------------------------------------------
def hampel_filter(x, fs, window_s=HAMPEL_WINDOW_S, n_sigmas=HAMPEL_N_SIGMAS,
                   sigma_floor=HAMPEL_SIGMA_FLOOR):
    """Robuste, frequenzunabhaengige Ausreissererkennung ueber gleitenden
    Median/MAD. Ausreisser werden durch den lokalen Median ersetzt (nicht
    verworfen, nicht interpoliert -> keine Sprungstellen fuer den Tiefpass
    danach). Gibt bereinigtes Signal + boolsche Ausreisser-Maske zurueck."""
    window = max(3, int(round(window_s * fs)))
    if window % 2 == 0:
        window += 1
    s = pd.Series(x)
    med = s.rolling(window, center=True, min_periods=1).median()
    dev = (s - med).abs()
    mad = dev.rolling(window, center=True, min_periods=1).median()
    sigma = (1.4826 * mad).clip(lower=sigma_floor)
    mask = (dev > (n_sigmas * sigma)).values
    x_clean = s.values.copy()
    x_clean[mask] = med.values[mask]
    return x_clean, mask


def clip_physical(x, limit=PHYSICAL_CLIP_MS2):
    """Hartes Sicherheitsnetz nach dem Hampel-Filter: alles ausserhalb
    +-limit wird auf die Grenze gekappt. Gibt geklipptes Signal + Maske
    der betroffenen Samples zurueck (geloggt, nicht stillschweigend)."""
    mask = np.abs(x) > limit
    x_clipped = np.clip(x, -limit, limit)
    return x_clipped, mask


def group_events(t, x_raw, mask, source_label, max_events=MAX_EVENTS_LOGGED):
    """Fasst zusammenhaengende geflaggte Samples zu einzelnen Ereignissen
    zusammen (sonst wuerde ein 0.1s-Stoss bei 50Hz zu 5 Einzeleintraegen).
    Jedes Ereignis traegt Zeitpunkt+Wert des betragsmaessigen Peaks."""
    events = []
    in_event = False
    start_idx = None
    n = len(mask)
    for i in range(n):
        if mask[i] and not in_event:
            in_event = True
            start_idx = i
        elif not mask[i] and in_event:
            in_event = False
            seg = slice(start_idx, i)
            peak_i = start_idx + int(np.argmax(np.abs(x_raw[seg])))
            events.append({
                "t": float(t[peak_i]), "raw_value": float(x_raw[peak_i]),
                "duration_s": float(t[i - 1] - t[start_idx]), "source": source_label,
            })
    if in_event:
        seg = slice(start_idx, n)
        peak_i = start_idx + int(np.argmax(np.abs(x_raw[seg])))
        events.append({
            "t": float(t[peak_i]), "raw_value": float(x_raw[peak_i]),
            "duration_s": float(t[-1] - t[start_idx]), "source": source_label,
        })
    events.sort(key=lambda e: abs(e["raw_value"]), reverse=True)
    return events[:max_events], len(events)


# ----------------------------------------------------------------------
# 4. Kreuzvalidierung gegen OBD-Geschwindigkeit
# ----------------------------------------------------------------------
def obd_local_dvdt(t_event, speed_t, speed_v_ms, window_s=CROSSVAL_WINDOW_S):
    """Grobe lokale Beschleunigung [m/s^2] aus einer linearen Ausgleichs-
    geraden durch die OBD-Geschwindigkeitspunkte um t_event. Nur ein
    Plausibilitaetscheck (OBD liegt bei ~2Hz), kein praezises Signal."""
    mask = (speed_t >= t_event - window_s) & (speed_t <= t_event + window_s)
    if mask.sum() < 2:
        return None
    slope = np.polyfit(speed_t[mask], speed_v_ms[mask], 1)[0]
    return float(slope)


def crossvalidate_events(events, speed_t, speed_v_ms, axis_name):
    for ev in events:
        dv = obd_local_dvdt(ev["t"], speed_t, speed_v_ms)
        ev["obd_dv_dt_ms2"] = dv
        if dv is None:
            ev["plausibility"] = "keine OBD-Geschwindigkeit im Zeitfenster"
        elif axis_name == "AccelerationX":
            same_sign = (dv * ev["raw_value"]) > 0
            magnitude_ok = abs(dv) > CROSSVAL_MIN_OBD_MS2 and \
                (abs(ev["raw_value"]) / max(abs(dv), 1e-6)) < CROSSVAL_TOL_FACTOR
            ev["plausibility"] = (
                "plausibel (OBD-Geschwindigkeit bestaetigt Aenderung)"
                if (same_sign and magnitude_ok)
                else "IMU-only (OBD zeigt keine passende Geschwindigkeitsaenderung -> moeglich Halterungs-/Sensorartefakt)"
            )
        else:
            ev["plausibility"] = (
                "OBD zeigt gleichzeitige Geschwindigkeitsaenderung (Hinweis, kein Beweis fuer Quer/Vertikal)"
                if abs(dv) > CROSSVAL_MIN_OBD_MS2
                else "OBD zeigt keine nennenswerte Geschwindigkeitsaenderung (fuer Quer/Vertikal nicht direkt pruefbar)"
            )
    return events


# ----------------------------------------------------------------------
# 5. PSD / Resonanz-Diagnose
# ----------------------------------------------------------------------
def dominant_peak(freqs, psd, min_freq=PEAK_MIN_FREQ_HZ):
    mask = freqs > min_freq
    idx = np.argmax(psd[mask])
    return freqs[mask][idx], psd[mask][idx]


def find_top_peaks(freqs, psd, n_peaks=TOP_N_PEAKS, min_freq=PEAK_MIN_FREQ_HZ,
                    min_separation_hz=PEAK_MIN_SEPARATION_HZ):
    """Findet bis zu n_peaks lokale PSD-Maxima oberhalb min_freq (echte
    Mehrfachpeak-Suche statt nur des globalen Maximums), sortiert nach
    Staerke, mit Mindestabstand zueinander (verhindert mehrere Eintraege
    von derselben Spektrallinie/-flanke). Rein diagnostisch, um sichtbar
    zu machen, wenn ein niederfrequenter Fahrdynamik-Peak den globalen
    dominanten Peak stellt, waehrend die Resonanz als Nebenpeak noch da ist."""
    mask = freqs > min_freq
    f, p = freqs[mask], psd[mask]
    if len(f) < 2:
        return []
    df = f[1] - f[0]
    distance = max(1, int(round(min_separation_hz / df)))
    peak_idx, _ = signal.find_peaks(p, distance=distance)
    if len(peak_idx) == 0:
        return []
    order = peak_idx[np.argsort(p[peak_idx])[::-1]][:n_peaks]
    return [{"freq_hz": float(f[i]), "psd": float(p[i])} for i in order]


def half_power_bandwidth(freqs, psd, f_peak):
    """Grobe -3dB-Breite um den Peak, fuer sinnvolle Notch-Guete."""
    p_peak = np.interp(f_peak, freqs, psd)
    half = p_peak / 2.0
    below = freqs[(freqs < f_peak) & (psd < half)]
    above = freqs[(freqs > f_peak) & (psd < half)]
    f_lo = below.max() if len(below) else f_peak * 0.9
    f_hi = above.min() if len(above) else f_peak * 1.1
    return f_hi - f_lo


def resonance_vs_engine_order(t_uniform, rpm_uniform, ax_signal, fs,
                               window_s=8.0, step_s=2.0, cylinders=CYLINDERS,
                               min_freq=RESONANCE_SEARCH_MIN_HZ):
    """Prueft per gleitendem Fenster, ob die lokal dominante Frequenz mit
    der Zuendfrequenz korreliert (Motorordnung) oder konstant bleibt
    (mechanische Resonanz). Gibt Korrelationskoeffizient + mittlere
    Peak-Frequenz + deren Standardabweichung zurueck.

    Peak-Suche pro Fenster ist auf min_freq (Standard: RESONANCE_SEARCH_MIN_HZ)
    beschraenkt: sonst kippt ein einzelnes Fenster mit starker realer
    Fahrdynamik (Bremsen/Beschleunigen) den lokal erkannten Peak auf eine
    niederfrequente Scheinfrequenz und verzerrt die Korrelationspruefung."""
    firing_hz = rpm_uniform / 60.0 * (cylinders / 2.0)
    win = int(window_s * fs)
    step = int(step_s * fs)
    local_peaks, local_firing = [], []
    for start in range(0, len(ax_signal) - win, step):
        seg = ax_signal[start:start + win]
        f_seg, p_seg = signal.welch(seg, fs=fs, nperseg=min(256, win))
        fp, _ = dominant_peak(f_seg, p_seg, min_freq=min_freq)
        local_peaks.append(fp)
        local_firing.append(np.mean(firing_hz[start:start + win]))
    local_peaks = np.array(local_peaks)
    local_firing = np.array(local_firing)
    if len(local_peaks) < 3:
        return {"n_windows": len(local_peaks), "correlation": None,
                "peak_mean": None, "peak_std": None}
    corr = float(np.corrcoef(local_peaks, local_firing)[0, 1])
    return {
        "n_windows": len(local_peaks),
        "correlation_peak_vs_firing_freq": corr,
        "peak_freq_mean_hz": float(local_peaks.mean()),
        "peak_freq_std_hz": float(local_peaks.std()),
        "interpretation": (
            "Hohe Korrelation (>0.5) spricht fuer Motorordnung; "
            "niedrige Korrelation + geringe Standardabweichung der "
            "Peak-Frequenz spricht fuer feste mechanische Resonanz "
            "(z.B. Halterung)."
        ),
    }


# ----------------------------------------------------------------------
# 6. Filter
# ----------------------------------------------------------------------
def lowpass_filter(x, fs, cutoff_hz=LOWPASS_CUTOFF_HZ, order=4):
    b, a = signal.butter(order, cutoff_hz / (fs / 2), btype="low")
    return signal.filtfilt(b, a, x)


def notch_filter(x, fs, f0, q=NOTCH_Q):
    b, a = signal.iirnotch(f0 / (fs / 2), q)
    return signal.filtfilt(b, a, x)


# ----------------------------------------------------------------------
# Hauptablauf
# ----------------------------------------------------------------------
def main():
    conn = sqlite3.connect(DB_PATH)

    accel = load_channel(conn, ["AccelerationX", "AccelerationY", "AccelerationZ"])
    rpm = load_channel(conn, ["EngineRPM"])
    speed = load_channel(conn, ["VehicleSpeed"])
    conn.close()

    t0 = accel["datetime"].min()
    accel = to_elapsed_seconds(accel, t0)
    rpm = to_elapsed_seconds(rpm, t0)
    speed = to_elapsed_seconds(speed, t0)

    axes = {}
    for name in ["AccelerationX", "AccelerationY", "AccelerationZ"]:
        axes[name] = accel[accel.sensor_name == name][["t", "value"]]

    t_min = max(a["t"].min() for a in axes.values())
    t_max = min(a["t"].max() for a in axes.values())
    t_uniform = np.arange(t_min, t_max, 1 / FS_UNIFORM)

    signals_uniform = {name: resample_uniform(df, t_uniform) for name, df in axes.items()}
    rpm_uniform = resample_uniform(rpm.rename(columns={"value": "value"}), t_uniform) \
        if len(rpm) > 1 else np.zeros_like(t_uniform)

    # OBD-Geschwindigkeit fuer die Kreuzvalidierung vorbereiten (eigenes,
    # natives ~2Hz-Raster - NICHT auf 50Hz hochsampeln, das wuerde nur
    # linear interpolierte Scheingenauigkeit vortaeuschen)
    speed_sub = speed[["t", "value"]].dropna().drop_duplicates(subset="t").sort_values("t")
    speed_t = speed_sub["t"].values
    speed_v_ms = speed_sub["value"].values / 3.6  # km/h -> m/s

    summary = {
        "source_file": DB_BASENAME,
        "duration_s": float(t_uniform[-1] - t_uniform[0]),
        "fs_uniform_hz": FS_UNIFORM,
        "outlier_handling": {
            "hampel_window_s": HAMPEL_WINDOW_S,
            "hampel_n_sigmas": HAMPEL_N_SIGMAS,
            "physical_clip_ms2": PHYSICAL_CLIP_MS2,
            "crossval_window_s": CROSSVAL_WINDOW_S,
        },
        "axes": {},
    }

    filtered_out = {"t": t_uniform}

    for name, sig_raw in signals_uniform.items():
        # --- Ausreisserbehandlung vor jeglicher Frequenzanalyse ---
        sig_hampel, hampel_mask = hampel_filter(sig_raw, FS_UNIFORM)
        sig_clean, clip_mask = clip_physical(sig_hampel)

        hampel_events, n_hampel_total = group_events(t_uniform, sig_raw, hampel_mask, "hampel")
        clip_events, n_clip_total = group_events(t_uniform, sig_raw, clip_mask, "physical_clip")
        hampel_events = crossvalidate_events(hampel_events, speed_t, speed_v_ms, name)
        clip_events = crossvalidate_events(clip_events, speed_t, speed_v_ms, name)

        # --- Frequenzanalyse auf dem bereinigten Signal ---
        freqs, psd = signal.welch(sig_clean, fs=FS_UNIFORM, nperseg=1024)
        # Globaler Peak (kann bei kurzen/dynamischen Fahrten von realer
        # niederfrequenter Fahrdynamik dominiert werden -> rein diagnostisch).
        f_peak_global, p_peak_global = dominant_peak(freqs, psd)
        # Resonanz-Peak: Suche auf > RESONANCE_SEARCH_MIN_HZ beschraenkt, dort
        # gibt es praktisch keine echte Fahrzeugdynamik mehr. Das ist der
        # massgebliche Peak fuer Notch-Entscheidung und Motorordnungs-Check.
        f_peak, p_peak = dominant_peak(freqs, psd, min_freq=RESONANCE_SEARCH_MIN_HZ)
        top_peaks = find_top_peaks(freqs, psd)
        peak_mismatch = abs(f_peak_global - f_peak) > PEAK_MIN_SEPARATION_HZ

        bw = half_power_bandwidth(freqs, psd, f_peak)
        res_check = resonance_vs_engine_order(t_uniform, rpm_uniform, sig_clean, FS_UNIFORM)

        sig_lowpass = lowpass_filter(sig_clean, FS_UNIFORM)
        apply_notch = (
            f_peak > LOWPASS_CUTOFF_HZ
            and res_check.get("correlation_peak_vs_firing_freq") is not None
            and abs(res_check["correlation_peak_vs_firing_freq"]) < 0.4
        )
        sig_notch = notch_filter(sig_clean, FS_UNIFORM, f_peak) if apply_notch else None

        filtered_out[f"{name}_raw"] = sig_raw
        filtered_out[f"{name}_clean"] = sig_clean  # Hampel + physik. Clipping, vor Tiefpass
        filtered_out[f"{name}_lowpass"] = sig_lowpass
        if sig_notch is not None:
            filtered_out[f"{name}_notch"] = sig_notch

        summary["axes"][name] = {
            "global_peak_hz": float(f_peak_global),
            "global_peak_psd": float(p_peak_global),
            "resonance_peak_hz": float(f_peak),
            "resonance_peak_psd": float(p_peak),
            "resonance_search_min_hz": RESONANCE_SEARCH_MIN_HZ,
            "global_vs_resonance_peak_mismatch": bool(peak_mismatch),
            "top_peaks_hz": top_peaks,
            "half_power_bandwidth_hz": float(bw),
            "resonance_check": res_check,
            "notch_applied": bool(apply_notch),
            "rms_raw": float(np.sqrt(np.mean(sig_raw ** 2))),
            "rms_clean": float(np.sqrt(np.mean(sig_clean ** 2))),
            "rms_lowpass": float(np.sqrt(np.mean(sig_lowpass ** 2))),
            "hampel_outliers": {
                "n_total_samples": int(hampel_mask.sum()),
                "n_events": n_hampel_total,
                "events": hampel_events,
            },
            "physical_clip": {
                "n_total_samples": int(clip_mask.sum()),
                "n_events": n_clip_total,
                "events": clip_events,
            },
        }

    # ---- Export ----
    summary_path = os.path.join(DERIVED_DIR, DB_BASENAME.replace(".dlg", "_vibration_summary.json"))
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    filtered_csv_path = os.path.join(DERIVED_DIR, DB_BASENAME.replace(".dlg", "_imu_filtered.csv"))
    pd.DataFrame(filtered_out).to_csv(filtered_csv_path, index=False)

    print("Vibrationsanalyse abgeschlossen.")
    print(f"  Zusammenfassung: {summary_path}")
    print(f"  Gefilterte IMU-Daten: {filtered_csv_path}")
    print()
    for name, ax in summary["axes"].items():
        mismatch_flag = " [!] global != Resonanz-Peak" if ax["global_vs_resonance_peak_mismatch"] else ""
        print(f"{name}: Resonanz-Peak {ax['resonance_peak_hz']:.2f} Hz (>{ax['resonance_search_min_hz']:.0f} Hz) | "
              f"global {ax['global_peak_hz']:.2f} Hz{mismatch_flag} | "
              f"RMS raw={ax['rms_raw']:.3f} clean={ax['rms_clean']:.3f} lowpass={ax['rms_lowpass']:.3f} | "
              f"Hampel-Ausreisser={ax['hampel_outliers']['n_total_samples']} Samples "
              f"({ax['hampel_outliers']['n_events']} Ereignisse) | "
              f"Clipping={ax['physical_clip']['n_total_samples']} Samples "
              f"({ax['physical_clip']['n_events']} Ereignisse)")


if __name__ == "__main__":
    main()
