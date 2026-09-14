"""
Kraftkreis-Check aus CAN-Daten (MX-5 Projekt) - Datengrundlage fuer die in
`spreewaldring_lap_simulation.py` dokumentierte Einschraenkung "Kein
Reifenkraftkreis: Laengs- und Quergrenzen werden UNABHAENGIG behandelt".

Das RCM liefert LateralAcc_CAN (0x75) und LongitudinalAcc_CAN (0x76)
zeitgleich vom selben Sensor - anders als beim Lenkwinkel-Ansatz (siehe
mx5_steering_angle-Memory) ist hier keine Rueckrechnung noetig, beide
Groessen sind direkt gemessen. Zusaetzlich: 4 separate WheelSpeed_CAN-Kanaele
erlauben einen einfachen Durchdreh-/Blockier-Check (Radschlupf ist ein
direktes Grenzbereichs-Signal, im Gegensatz zu a_lat_peak, das laut
mx5_tires-Memory beim Rutschen/Uebersteuern ueberschaetzen kann).

WICHTIGE EINSCHRAENKUNG: alle bisherigen CAN-Logs sind Alltagsfahrten, keine
Grenzbereichsfahrt (siehe corner_speed_model.py-Docstring) - a_lat/a_lon
bleiben deutlich unter dem Reifenlimit. Das hier gemessene Kraftkreis-Polygon
ist deshalb eine UNTERGRENZE/Formindikator, keine Grenzwertmessung. Nuetzlich
ist trotzdem die FORM (Kreis vs. Ellipse: Bremsen nutzt alle 4 Reifen,
Beschleunigen beim RWD-MX-5 nur die Hinterachse - eine Asymmetrie waere schon
weit unter dem echten Limit sichtbar).

Methodik:
  1. Traktionskreis: pro CAN-Log (a_lat_g, a_lon_g) auf demselben Zeitraster
     (LateralAcc_CAN-Zeitstempel, a_lon linear interpoliert - beide Signale
     kommen von unterschiedlichen CAN-IDs mit leicht versetzten
     Zeitstempeln). Alle Logs zusammengefasst zu einer Punktwolke.
     Empirische Huelle: Winkel-Bins (atan2(a_lon, a_lat)), pro Bin der
     max. beobachtete Radius sqrt(a_lat^2+a_lon^2) - kein Ellipsen-Fit,
     nur die tatsaechlich beobachtete Grenze.
  2. Asymmetrie-Check: max|a_lat| vs. max(a_lon) [Beschleunigen] vs.
     max(-a_lon) [Bremsen] - ist die Huelle ein Kreis oder laenglich?
  3. Radschlupf-Kandidaten: pro Log, nur bei Geradeausfahrt (|a_lat| <
     A_LAT_STRAIGHT_MAX_G, um Kurven-Spurbreiteneffekte auf die
     Radgeschwindigkeit auszuschliessen) und unter Last (v > V_MIN_KMH,
     |a_lon| > A_LON_LOAD_MIN_G): Abweichung jedes Rads vom Median der 4
     Raeder, getrennt fuer Beschleunigen/Bremsen, jeweils nur der staerkste
     Einzel-Kandidat pro Log/Richtung (keine Event-Gruppierung - bei nur 7
     Logs lohnt sich das noch nicht). EINSCHRAENKUNG: WheelSpeed_CAN_1-4
     sind nicht Achsen/Seiten zugeordnet (DBC nennt sie nur generisch, siehe
     mx5_tpms-Memory zum selben Problem bei den Reifendrucksensoren) - ein
     Treffer sagt nur "irgendein Rad weicht ab", nicht welches. Auch reine
     Zeitversatz-Interpolationsartefakte zwischen CAN-IDs koennen aehnlich
     aussehen wie echter Schlupf - als Kandidat, nicht als Beweis ausgeben.

Aufruf: .venv/bin/python scripts/can_traction_circle.py
"""
import json
import os

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"

A_LAT_STRAIGHT_MAX_G = 0.1   # Geradeausfahrt-Schwelle fuer den Radschlupf-Check
V_MIN_KMH = 20.0             # vermeidet Sensor-/Interpolationsrauschen bei sehr niedrigem Tempo
A_LON_LOAD_MIN_G = 0.3       # "unter Last" fuer den Radschlupf-Check
N_ANGLE_BINS = 24
# Kupplung erkennbar betaetigt (Ruhewert bei dieser Fahrzeugserie ~0-9, siehe
# Docstring "Schaltvorgang-Ausschluss") - waehrend der Kupplung offen ist,
# ist die Antriebsachse vom Motor entkoppelt UND das Wiedereinkuppeln selbst
# erzeugt einen kurzen Antriebsstrang-Schlag/Radgeschwindigkeits-Ausschlag,
# der weder echter Reifengrip noch echtes Durchdrehen ist.
CLUTCH_ACTIVE_RAW = 15
# das Wiedereinkuppeln klingt nach - am konkreten Fall (candump-2026-09-12_211833,
# t~709s, 2.->3. Gang) schwingt die Radgeschwindigkeit noch ~0.3-0.4s nach,
# nachdem die Kupplung schon wieder unter der Schwelle ist. Grosszuegig auf 1s.
SHIFT_SETTLE_S = 1.0


def load_channel(con, log_id, channel):
    return con.execute(
        "SELECT t_elapsed_s AS t, value FROM measurements WHERE log_id = ? AND channel = ? "
        "AND value IS NOT NULL ORDER BY t_elapsed_s",
        [log_id, channel],
    ).fetchdf()


def _shifting_mask(t, clutch_df):
    """Kupplung betaetigt, PLUS SHIFT_SETTLE_S danach (das Wiedereinkuppeln
    klingt in der Radgeschwindigkeit nach, siehe Konstanten-Kommentar)."""
    if clutch_df.empty:
        return np.zeros_like(t, dtype=bool)
    active = np.interp(t, clutch_df["t"].values, clutch_df["value"].values) > CLUTCH_ACTIVE_RAW
    last_active_t = np.maximum.accumulate(np.where(active, t, -np.inf))
    return active | ((t - last_active_t) <= SHIFT_SETTLE_S)


def load_log(con, log_id):
    """Alle relevanten Kanaele auf das Zeitraster von LateralAcc_CAN interpoliert."""
    lat = load_channel(con, log_id, "LateralAcc_CAN")
    lon = load_channel(con, log_id, "LongitudinalAcc_CAN")
    speed = load_channel(con, log_id, "VehicleSpeed")
    app = load_channel(con, log_id, "APP")
    clutch = load_channel(con, log_id, "ClutchPosition_CAN_raw")
    if lat.empty or lon.empty or speed.empty:
        return None
    t = lat["t"].values
    out = {
        "t": t,
        "a_lat_g": lat["value"].values,
        "a_lon_g": np.interp(t, lon["t"].values, lon["value"].values),
        "v_kmh": np.interp(t, speed["t"].values, speed["value"].values),
        "app_pct": np.interp(t, app["t"].values, app["value"].values) if not app.empty else None,
        "shifting": _shifting_mask(t, clutch),
    }
    wheels = []
    for i in range(1, 5):
        w = load_channel(con, log_id, f"WheelSpeed_CAN_{i}")
        if w.empty:
            wheels = None
            break
        wheels.append(np.interp(t, w["t"].values, w["value"].values))
    out["wheels"] = np.vstack(wheels) if wheels else None
    return out


def envelope_by_angle(a_lat_g, a_lon_g, n_bins=N_ANGLE_BINS):
    angle = np.arctan2(a_lon_g, a_lat_g)
    radius = np.sqrt(a_lat_g ** 2 + a_lon_g ** 2)
    edges = np.linspace(-np.pi, np.pi, n_bins + 1)
    centers, max_r = [], []
    for i in range(n_bins):
        mask = (angle >= edges[i]) & (angle < edges[i + 1])
        if mask.any():
            centers.append((edges[i] + edges[i + 1]) / 2)
            max_r.append(radius[mask].max())
    centers = np.array(centers)
    max_r = np.array(max_r)
    return centers, max_r


def wheel_slip_candidate(t, v_kmh, a_lat_g, a_lon_g, wheels, straight_and_loaded):
    mask = straight_and_loaded
    if not mask.any():
        return None
    dev = np.abs(wheels - np.median(wheels, axis=0))  # 4 x n
    max_dev_per_sample = dev.max(axis=0)
    wheel_idx = dev.argmax(axis=0)
    i_best = np.argmax(np.where(mask, max_dev_per_sample, -1))
    if max_dev_per_sample[i_best] <= 0:
        return None
    return {
        "t_s": float(t[i_best]), "dev_kmh": float(max_dev_per_sample[i_best]),
        "wheel": int(wheel_idx[i_best]) + 1, "v_kmh": float(v_kmh[i_best]),
        "a_lon_g": float(a_lon_g[i_best]), "a_lat_g": float(a_lat_g[i_best]),
    }


def main():
    con = duckdb.connect(DB_PATH, read_only=True)
    log_ids = con.execute(
        "SELECT DISTINCT log_id FROM measurements WHERE channel = 'LongitudinalAcc_CAN' ORDER BY log_id"
    ).fetchdf()["log_id"].tolist()
    print(f"{len(log_ids)} CAN-Logs mit LongitudinalAcc_CAN: {log_ids}")

    all_lat, all_lon, all_log = [], [], []
    slip_candidates = {}
    n_shift_excluded = 0
    for log_id in log_ids:
        d = load_log(con, log_id)
        if d is None:
            print(f"  {log_id}: LateralAcc_CAN/LongitudinalAcc_CAN/VehicleSpeed fehlt, uebersprungen")
            continue
        not_shifting = ~d["shifting"]
        n_shift_excluded += int(d["shifting"].sum())
        all_lat.append(d["a_lat_g"][not_shifting])
        all_lon.append(d["a_lon_g"][not_shifting])
        all_log.append(np.full(int(not_shifting.sum()), log_id))

        if d["wheels"] is None:
            print(f"  {log_id}: kein WheelSpeed_CAN_1-4, Radschlupf-Check uebersprungen")
            continue
        straight = np.abs(d["a_lat_g"]) < A_LAT_STRAIGHT_MAX_G
        fast_enough = d["v_kmh"] > V_MIN_KMH
        accel_mask = straight & fast_enough & not_shifting & (d["a_lon_g"] > A_LON_LOAD_MIN_G)
        brake_mask = straight & fast_enough & not_shifting & (d["a_lon_g"] < -A_LON_LOAD_MIN_G)
        cand = {
            "accel": wheel_slip_candidate(d["t"], d["v_kmh"], d["a_lat_g"], d["a_lon_g"], d["wheels"], accel_mask),
            "brake": wheel_slip_candidate(d["t"], d["v_kmh"], d["a_lat_g"], d["a_lon_g"], d["wheels"], brake_mask),
        }
        if cand["accel"] or cand["brake"]:
            slip_candidates[log_id] = cand

    a_lat_g = np.concatenate(all_lat)
    a_lon_g = np.concatenate(all_lon)
    log_arr = np.concatenate(all_log)
    n = len(a_lat_g)
    print(f"\nKraftkreis-Datenbasis: {n} Samples aus {len(set(log_arr))} Logs "
          f"({n_shift_excluded} Samples waehrend Kupplung betaetigt ausgeschlossen, "
          f"siehe Docstring Schaltvorgang-Ausschluss)")

    max_lat_g = float(np.abs(a_lat_g).max())
    max_accel_g = float(a_lon_g.max())
    max_brake_g = float(-a_lon_g.min())
    print(f"max |a_lat|   = {max_lat_g:.2f}g")
    print(f"max a_lon (Beschleunigen) = {max_accel_g:.2f}g")
    print(f"max -a_lon (Bremsen)      = {max_brake_g:.2f}g")
    print(f"Verhaeltnis Bremsen/Beschleunigen: {max_brake_g / max_accel_g:.2f}x "
          f"({'deutlich asymmetrisch' if not 0.7 < max_brake_g / max_accel_g < 1.4 else 'in etwa symmetrisch'})")
    print("(alles klar unter dem Reifenlimit - Alltagsfahrten, siehe Docstring/corner_speed_model.py, "
          "das hier ist eine Formindikation, keine Grenzwertmessung)")

    centers, env_r = envelope_by_angle(a_lat_g, a_lon_g)

    if slip_candidates:
        print(f"\nRadschlupf-Kandidaten (Geradeausfahrt, v>{V_MIN_KMH:.0f}km/h, |a_lon|>{A_LON_LOAD_MIN_G:.1f}g, "
              f"NICHT verifiziert - siehe Docstring-Einschraenkung):")
        for log_id, c in slip_candidates.items():
            for regime, v in c.items():
                if v:
                    print(f"  {log_id} [{regime}]: Rad #{v['wheel']} weicht {v['dev_kmh']:.1f}km/h vom Median ab "
                          f"(t={v['t_s']:.1f}s, v={v['v_kmh']:.0f}km/h, a_lon={v['a_lon_g']:+.2f}g)")
    else:
        print("\nKeine Radschlupf-Kandidaten gefunden (oder kein Log hatte alle 4 WheelSpeed-Kanaele).")

    # Plot
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    sc = ax1.scatter(a_lon_g, a_lat_g, c=np.sqrt(a_lat_g ** 2 + a_lon_g ** 2), cmap="viridis", s=6, alpha=0.5)
    env_x, env_y = env_r * np.cos(centers), env_r * np.sin(centers)
    order = np.argsort(centers)
    ax1.plot(env_x[order], env_y[order], "r-", lw=1.5, label="beobachtete Huelle (Winkel-Bins)")
    ax1.axhline(0, color="gray", lw=0.5)
    ax1.axvline(0, color="gray", lw=0.5)
    ax1.set_xlabel("a_lon [g] (+ = Beschleunigen, - = Bremsen)")
    ax1.set_ylabel("a_lat [g]")
    ax1.set_title(f"Kraftkreis, {n} Samples aus {len(set(log_arr))} CAN-Logs")
    ax1.set_aspect("equal")
    ax1.legend(fontsize=8)
    fig.colorbar(sc, ax=ax1, label="kombiniert |a| [g]")

    labels = ["Bremsen\n(max -a_lon)", "Beschleunigen\n(max a_lon)", "Quer\n(max |a_lat|)"]
    values = [max_brake_g, max_accel_g, max_lat_g]
    ax2.bar(labels, values, color=["tomato", "steelblue", "gray"])
    ax2.set_ylabel("[g]")
    ax2.set_title("Asymmetrie-Check (Untergrenze, nicht Limit)")
    ax2.grid(alpha=0.3, axis="y")

    plt.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "can_traction_circle.png")
    plt.savefig(out_png, dpi=130)
    plt.close(fig)

    out = {
        "n_logs": len(set(log_arr)), "n_samples": n, "log_ids": log_ids,
        "max_lat_g": max_lat_g, "max_accel_g": max_accel_g, "max_brake_g": max_brake_g,
        "brake_over_accel_ratio": max_brake_g / max_accel_g,
        "envelope_angle_rad": centers.tolist(), "envelope_radius_g": env_r.tolist(),
        "wheel_slip_candidates": slip_candidates,
    }
    out_json = os.path.join(RESULTS_DIR, "can_traction_circle_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nPlot: {out_png}")
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
