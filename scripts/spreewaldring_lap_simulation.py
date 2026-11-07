"""
Rundenzeit-Simulation auf der rekonstruierten Spreewaldring-Strecke (MX-5
Projekt)

Nutzerauftrag: "Jetzt wuerde ich tatsaechlich versuchen, ob wir eine
schnelle Runde auf der berechneten Strecke simulieren koennen." Erste
Zusammenfuehrung der bisherigen Bausteine (Streckengeometrie aus
`spreewaldring_track.py`, Volllast-/Traktionsmodell aus
`performance_simulation.py`, Reifen-Grip-Bandbreite aus Memory "mx5-tires")
zu einer Rundenzeit-Schaetzung fuer eine "schnelle Runde" (Hotlap, ans
Limit gefahren).

METHODIK: klassisches quasi-stationaeres Punktmassen-Rundenzeitmodell
(zwei Durchlaeufe entlang der Strecke, Standardansatz im Motorsport-
Engineering, KEIN neues physikalisches Modell - kombiniert nur bereits
vorhandene/validierte Bausteine):

  1. **Kurvengeschwindigkeits-Grenze** pro Streckenpunkt:
     v_kurve(s) = sqrt(mu_lat * g * R(s)), mit R(s) aus der
     Streckenmittellinie (seit 07.11.2026 aus
     `spreewaldring_track_model.py`, vorher `spreewaldring_track.py`) und mu_lat aus der Reifen-Literatur-
     Bandbreite (Nankang NS-R2 Semi-Slick, siehe Memory "mx5-tires",
     TIRE_MU_RANGE).
  2. **Beschleunigungs-Einhuellende** a_accel(v) = das Maximum ueber alle
     6 Gaenge von `accel(v, gear, f_max=TRACTION_MAX_FORCE_N)` aus
     `performance_simulation.py` - genau das Modell, das dort als
     "traktionsbegrenzt" empfohlen wurde (trifft sowohl Startbeschleunigung
     als auch Vmax-Bereich am besten, siehe dortiger Docstring). Ignoriert
     Schaltzeit-Verluste (ATTACK_SHIFT_S) - Vereinfachung, siehe
     EINSCHRAENKUNGEN.
  3. **Brems-Verzoegerung** a_brems = mu_lat * g (KONSTANT, gleiche
     Reifen-Grip-Annahme wie bei Kurven - siehe EINSCHRAENKUNGEN: die
     echten geloggten Bremsungen aus `braking_model.py` erreichen nur bis
     0.48g, deutlich weniger als hier angenommen, ABER das war
     Alltagsfahrten auf oeffentlichen Strassen, keine Hotlap-Bremsungen ans
     Limit - fuer eine Rennstrecken-Simulation wird die volle Reifen-
     Grip-Bandbreite angenommen, NICHT die geloggten Strassen-Werte.
  4. **Zwei-Pass-Algorithmus** (Start am langsamsten Punkt der Strecke,
     das vermeidet die meisten Iterationen):
       a. Vorwaerts-Pass: v_vor(s) = min(v_kurve(s),
          sqrt(v_vor(s-1)^2 + 2*a_accel(v_vor(s-1))*ds)) - wie schnell
          koennte das Auto werden, wenn es ab hier voll beschleunigt.
       b. Rueckwaerts-Pass: v(s) = min(v_vor(s),
          sqrt(v(s+1)^2 + 2*a_brems*ds)) - wo muss spaetestens gebremst
          werden, um die naechste Kurve zu schaffen.
       c. 2 Wiederholungen fuer Konvergenz (geschlossene Schleife).
  5. Rundenzeit = Summe(ds / v(s)) ueber die ganze Runde.

Zwei Szenarien (wie bei performance_simulation.py): "optimistisch"
(mu=1.3) und "konservativ" (mu=1.0) - Bandbreite statt Einzelwert, da mu
nicht fuer DIESES Fahrzeug/DIESE Strecke gemessen ist.

EINSCHRAENKUNGEN (wichtig, nicht kleingedruckt):
  - **Ideallinie:** die Simulation faehrt die geometrische Streckenmittel-
    linie, NICHT eine optimierte Ideallinie (die haette in Kurven einen
    groesseren effektiven Radius, oft 30-50% mehr als die Mittellinie) -
    die Rundenzeit ist dadurch SYSTEMATISCH PESSIMISTISCH (zu langsam),
    vermutlich der groesste einzelne Fehlerfaktor in dieser Simulation.
  - **Reifenkraftkreis optional (simulate_lap(..., friction_circle=True)):**
    standardmaessig (False) werden Laengs- (Brems-/Beschleunigungs-) und
    Quergrenzen weiter UNABHAENGIG behandelt. Mit friction_circle=True wird
    die verfuegbare Laengsbeschleunigung an jedem Punkt per Ellipsen-Kopplung
    an die schon genutzte Querbeschleunigung a_lat=v^2/R reduziert - siehe
    `_lon_limit()`. `main()` rechnet dazu vier Literatur-mu-Szenarien
    (isotroper Kreis, Sonderfall a_lat_max==a_lon_max) PLUS ein fuenftes
    "gemessen"-Szenario mit `a_accel_max_g`/`a_brake_max_g` aus dem
    tatsaechlich gefahrenen Kraftkreis (`can_traction_circle_summary.json`,
    von `can_traction_circle.py`) statt der Literatur-Bandbreite - echte
    Ellipse, keine Annahme mehr gleicher Grenzen laengs/quer/bremsen.
    ACHTUNG: diese gemessene Huelle ist laut eigenem Docstring eine
    UNTERGRENZE aus Alltagsfahrten (bislang max 1.09g quer/0.97g bremsen/
    0.81g beschleunigen bei 26 Logs), keine Grenzwertmessung am Reifenlimit
    - das "gemessen"-Szenario ist deshalb vermutlich zu konservativ/langsam,
    nicht die "echte" Rundenzeit. Bleibt ausserdem eine Vereinfachung: kein
    Gewichtstransfer/keine Achslast-Aufteilung.
  - **Kein Gewichtstransfer, keine Aero:** MX-5 hat keine nennenswerte
    Aero-Abtrieb, aber Gewichtstransfer (mehr Grip vorne beim Bremsen,
    hinten beim Beschleunigen) wird nicht modelliert.
  - **Schaltzeiten ignoriert:** die Beschleunigungs-Einhuellende nimmt
    perfektes, verzoegerungsfreies Schalten an (ATTACK_SHIFT_S aus
    performance_simulation.py hier NICHT angewendet).
  - **mu-Bandbreite ist Literaturschaetzung** fuer die Reifenklasse
    (Nankang NS-R2), nicht an diesem Auto/dieser Strecke gemessen - siehe
    Memory "mx5-tires" fuer Einschraenkungen (Reifenalter/Fahrbahntemperatur
    nicht beruecksichtigt).
  - **Boxengasse nicht beruecksichtigt** - reine Rundenzeit auf der
    Ideallinie (way 172927073), kein Boxenstopp.
  - Fahrerfehler/Konsistenz nicht modelliert (=theoretisches Maximum bei
    perfekter Ausfuehrung, kein realistischer Fahrer-Mittelwert).

Aufruf: .venv/bin/python scripts/spreewaldring_lap_simulation.py
"""
import os
import json
from collections import namedtuple

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from datalake_channels import _TP_TO_ETC
from drivetrain_model_validation import (
    CDA_M2, CRR, ENGINE_INERTIA_KGM2, ETA, FINAL_DRIVE, G, GEAR_RATIOS, MASS_KG, R_DYN_M, RHO_KG_M3, rpm_from_speed,
    torque_nm,
)
from performance_simulation import accel, coast_accel, ATTACK_SHIFT_S, TRACTION_MAX_FORCE_N

RESULTS_DIR = "results"
TRACK_MODEL_PATH = os.path.join(RESULTS_DIR, "spreewaldring_track_model.json")  # spreewaldring_track_model.py
VEHICLE_MODEL_PATH = os.path.join(RESULTS_DIR, "mx5_vehicle_model.json")          # mx5_vehicle_model.py

TIRE_MU_RANGE = (1.0, 1.3)  # siehe Memory "mx5-tires" (Nankang NS-R2 Semi-Slick)
N_PASSES = 2                # Wiederholungen des Vorwaerts-/Rueckwaerts-Paars fuer Konvergenz
V_MIN_MS = 5.0               # numerische Untergrenze (vermeidet Division/sqrt-Problem bei v->0)

# ECU-Soft-Limiter (08.11.2026, scripts/ecu_limiter_events.py, 32 Eingriffe in 68 Vollgaszuegen): ab einer
# gangabhaengigen Drehzahl zieht die ECU das Moment trotz Vollgas zurueck, bis zum Gaswegnehmen. Die ECU rampt in der
# Zeit, hier linear ueber die Drehzahl seit Beginn (im Vollgaszug steigt sie gleichmaessig): Gang 3/4 ~0,44 %/min,
# Gang 2 faellt fast sofort auf ~30 %. Gang 1 nur 3 Eingriffe; Gang 5/6 ohne Eingriff in den Logs, wie Gang 4 angenommen.
# Die Moment-Untergrenze beim Halten (5-30 %) fehlt: haelt die Simulation die Drehzahl, bleibt der Teilwert stehen.
LIMITER_ONSET_RPM = {1: 7303, 2: 7377, 3: 7146, 4: 7194, 5: 7194, 6: 7194}
LIMITER_WIDTH_RPM = {1: 30, 2: 30, 3: 230, 4: 230, 5: 230, 6: 230}


def load_track():
    """Hauptschleife aus dem Streckenmodell (seit 07.11.2026, vorher Mittellinie aus spreewaldring_track.py,
    deren Radien durch Wackeln oft zu klein waren). Hoehe und Querneigung stehen im Modell, gerechnet wird
    weiter in der Ebene."""
    with open(TRACK_MODEL_PATH, encoding="utf-8") as f:
        r = json.load(f)["segments"]["main_loop"]
    s = np.array(r["s_m"])
    radius = np.array([1e6 if x is None else x for x in r["radius_m"]])
    points = np.array(r["center"])
    total_len = r["length_2d_m"]
    return s, radius, points, total_len


# Grip-Ellipse [g], VehicleSpeed->wahre Geschwindigkeit, Zugkraftluecke [s] je (Gang, Gang)
Vehicle = namedtuple("Vehicle", "name lat_g accel_g brake_g speed_factor shift_s")


def load_vehicles():
    """Aus den Logs bestimmte Fahrzeugmodelle je Reifensatz, plus der Name des aktuellen Satzes."""
    with open(VEHICLE_MODEL_PATH, encoding="utf-8") as f:
        m = json.load(f)
    shift_s = {tuple(int(g) for g in pair.split("-")): s["gap_s"] for pair, s in m["shift_gaps"].items()}
    shift_s.update({(b, a): s for (a, b), s in list(shift_s.items())})  # Rueckschaltung: nicht gemessen, gleiche Luecke
    vehicles = {tyre: Vehicle(tyre, t["lat_g"], t["accel_g"], t["brake_g"], t["speed_true_factor"], shift_s)
                for tyre, t in m["tyres"].items()}
    return vehicles, m["default_tyre"]


def literature_vehicle(mu):
    """Fruehere Annahme: isotroper Kreis, ATTACK-Schaltzeiten, VehicleSpeed als wahres Tempo."""
    shift_s = dict(ATTACK_SHIFT_S)
    shift_s.update({(b, a): s for (a, b), s in ATTACK_SHIFT_S.items()})
    return Vehicle(f"mu={mu}", mu, mu, mu, 1.0, shift_s)


def accel_true(v_ms, gear, speed_factor):
    """Beschleunigung und Drehzahl bei wahrer Geschwindigkeit. Das Antriebsmodell rechnet in VehicleSpeed, die Strecke
    in echten Metern: v und a werden umgerechnet, die Modellparameter bleiben (performance-model.md, Punkt 9)."""
    v = v_ms / speed_factor
    a, rpm = accel(v, gear, bias=limiter_factor(rpm_from_speed(v, gear), gear), f_max=TRACTION_MAX_FORCE_N)
    return a * speed_factor, rpm


def _throttle_tables():
    """(Drosselklappe-Achse, Drehzahl-Achse, Moment-%, Pedal-%) aus mx5_vehicle_model.py. Beide steigen hier mit der
    Drosselklappe: duenne Bins und Nearest-Neighbor-Raender (hohe Drehzahl, kaum Gas) springen sonst."""
    global _THROTTLE
    if _THROTTLE is None:
        with open(VEHICLE_MODEL_PATH, encoding="utf-8") as f:
            t = json.load(f)["throttle"]
        _THROTTLE = (np.array(t["etc_deg"]), np.array(t["rpm"]),
                     np.maximum.accumulate(np.array(t["torque_pct"]), axis=0),
                     np.maximum.accumulate(np.array(t["app_pct"]), axis=0))
    return _THROTTLE


_THROTTLE = None


def engine_controls(frac, rpm, full):
    """Anteil am Volllastmoment, Drehzahl, Fahrer gibt Vollgas -> (Motorlast % wie 0x167, Drosselklappe % wie TP,
    Gaspedal %). Die Last ist der Anteil an der gemessenen Vollgas-Last (78-93 % je Drehzahl), Drosselklappe und Pedal
    kommen aus dem Teillastkennfeld rueckwaerts. Schub (Anteil <= 0) zeigt 0, nicht die leicht negativen 0x167-Werte.
    Im Limiter (Vollgas, Anteil < 1) schliesst die Drosselklappe wie im Auto, das Pedal bleibt bei 100."""
    etc_axis, rpm_axis, torque, app = _throttle_tables()
    frac, rpm, full = np.broadcast_arrays(np.asarray(frac, float), np.asarray(rpm, float), np.asarray(full, bool))
    t_rows = np.array([np.interp(rpm, rpm_axis, line) for line in torque]).T + 1e-6 * np.arange(len(etc_axis))
    a_rows = np.array([np.interp(rpm, rpm_axis, line) for line in app]).T
    frac = np.clip(frac, 0.0, 1.0)
    load = frac * t_rows[:, -1]
    etc = np.array([np.interp(x, row, etc_axis) for x, row in zip(load, t_rows)])
    etc = np.where(full & (frac >= 0.98), etc_axis[-1], np.where(frac > 0, etc, 0.0))
    pedal = np.array([np.interp(e, etc_axis, row) for e, row in zip(etc, a_rows)])
    pedal = np.where(full, 100.0, np.where(frac > 0, pedal, 0.0))
    return load, (etc - 0.18) / _TP_TO_ETC + 9.80, pedal  # Umkehrung von datalake_channels SOURCES["ETC_ACT"]


def throttle_trace(v, gear, ds, veh):
    """Motorlast, Drosselklappe und Gaspedal je Punkt einer geschlossenen Runde, rueckwaerts aus dem simulierten Tempo:
    noetige Radkraft = umgekehrtes accel() (Masse mit Drehtraegheit, Luft, Rollen), geteilt durch die Volllast-Radkraft.
    Vollgas, wo die Simulation so stark beschleunigt wie der Motor kann (sonst begrenzt Reifen oder Tempolimit)."""
    f = veh.speed_factor
    v, ds, gear = np.asarray(v, float), np.asarray(ds, float), np.asarray(gear, int)
    a_true = (np.roll(v, -1) ** 2 - v ** 2) / (2 * np.maximum(ds, 1e-6))
    vv, a = v / f, a_true / f
    ratio = np.array([GEAR_RATIOS[g] for g in gear]) * FINAL_DRIVE
    rpm = vv / (2 * np.pi * R_DYN_M) * ratio * 60.0
    m_eff = MASS_KG + ENGINE_INERTIA_KGM2 * (ratio / R_DYN_M) ** 2
    f_req = m_eff * a + 0.5 * RHO_KG_M3 * CDA_M2 * vv ** 2 + CRR * MASS_KG * G
    frac = f_req * R_DYN_M / (ratio * ETA * torque_nm(rpm))
    a_eng = np.array([accel_true(x, g, f)[0] for x, g in zip(v, gear)])
    full = a_true >= a_eng - np.maximum(0.02 * np.abs(a_eng), 0.05)
    return engine_controls(frac, rpm, full)


def limiter_factor(rpm, gear):
    """Anteil des Volllastmoments, den der ECU-Soft-Limiter bei dieser Drehzahl laesst (siehe LIMITER_*)."""
    return min(1.0, max(0.0, 1.0 - (rpm - LIMITER_ONSET_RPM[gear]) / LIMITER_WIDTH_RPM[gear]))


def coast_true(v_ms, speed_factor):
    return coast_accel(v_ms / speed_factor) * speed_factor


def accel_envelope(v_ms, speed_factor=1.0):
    """Beste erreichbare Beschleunigung ueber alle 6 Gaenge (traktions-
    begrenztes Modell, siehe Docstring)."""
    return max(accel_true(v_ms, gear, speed_factor)[0] for gear in GEAR_RATIOS)


def _lon_limit(v, radius_pt, a_lat_max, a_lon_max, accel_cap, friction_circle):
    """Verfuegbare Laengsbeschleunigung an einem Streckenpunkt. Ohne Kraftkreis:
    unveraendert accel_cap (Traktionsgrenze bzw. Bremsgrenze). Mit Kraftkreis:
    Ellipsen-Kopplung an die schon genutzte Querbeschleunigung a_lat=v^2/R -
    a_lat_max und a_lon_max koennen unterschiedlich sein (echte Ellipse, z.B.
    gemessene Kraftkreis-Asymmetrie Bremsen/Beschleunigen/Quer), Sonderfall
    a_lat_max==a_lon_max ist der isotrope Kreis (ein mu fuer alles). Bleibt
    eine Vereinfachung: kein Gewichtstransfer/keine Achslast-Aufteilung."""
    if not friction_circle:
        return accel_cap
    a_lat = min(v * v / radius_pt, a_lat_max)  # min() gegen Diskretisierungs-Ueberschuss am Kurvenlimit
    a_lon_circle = a_lon_max * np.sqrt(max(0.0, 1.0 - (a_lat / a_lat_max) ** 2))
    return min(accel_cap, a_lon_circle)


def simulate_lap(s, radius, mu_lat, n_passes=N_PASSES, friction_circle=False,
                  a_accel_max_g=None, a_brake_max_g=None, speed_factor=1.0):
    """mu_lat bestimmt die Kurvengeschwindigkeit UND (per Default) laengs
    Beschleunigen/Bremsen (isotroper Kreis). a_accel_max_g/a_brake_max_g
    ueberschreiben nur die Laengsgrenzen (echte Ellipse) - z.B. mit den
    gemessenen Kraftkreis-Maxima aus can_traction_circle_summary.json."""
    n = len(s)
    ds = np.full(n, np.median(np.diff(s)))  # gleichmaessiges Netz (RESAMPLE_STEP_M), siehe spreewaldring_track.py

    a_lat_max = mu_lat * G
    a_accel_max = (a_accel_max_g if a_accel_max_g is not None else mu_lat) * G
    a_brake_max = (a_brake_max_g if a_brake_max_g is not None else mu_lat) * G

    v_corner = np.sqrt(np.maximum(a_lat_max * radius, V_MIN_MS ** 2))

    # am langsamsten Punkt starten (minimiert noetige Iterationen, siehe Docstring)
    i0 = int(np.argmin(v_corner))
    order = np.roll(np.arange(n), -i0)
    v_corner_o = v_corner[order]
    ds_o = ds[order]
    radius_o = radius[order]

    v = v_corner_o.copy()
    for _ in range(n_passes):
        # Vorwaerts (Beschleunigung)
        v_fwd = v.copy()
        for i in range(1, n):
            a = _lon_limit(v_fwd[i - 1], radius_o[i - 1], a_lat_max, a_accel_max,
                            accel_envelope(v_fwd[i - 1], speed_factor), friction_circle)
            v_max_accel = np.sqrt(v_fwd[i - 1] ** 2 + 2 * a * ds_o[i - 1])
            v_fwd[i] = min(v_corner_o[i], v_max_accel)
        # Schlusspunkt -> Startpunkt schliessen (Rundkurs)
        a = _lon_limit(v_fwd[-1], radius_o[-1], a_lat_max, a_accel_max,
                        accel_envelope(v_fwd[-1], speed_factor), friction_circle)
        v_max_accel = np.sqrt(v_fwd[-1] ** 2 + 2 * a * ds_o[-1])
        v_fwd[0] = min(v_corner_o[0], v_max_accel, v_fwd[0])

        # Rueckwaerts (Bremsen)
        v_bwd = v_fwd.copy()
        for i in range(n - 2, -1, -1):
            a_brake = _lon_limit(v_bwd[i + 1], radius_o[i + 1], a_lat_max, a_brake_max, a_brake_max, friction_circle)
            v_max_brake = np.sqrt(v_bwd[i + 1] ** 2 + 2 * a_brake * ds_o[i])
            v_bwd[i] = min(v_bwd[i], v_max_brake)
        a_brake = _lon_limit(v_bwd[0], radius_o[0], a_lat_max, a_brake_max, a_brake_max, friction_circle)
        v_max_brake = np.sqrt(v_bwd[0] ** 2 + 2 * a_brake * ds_o[-1])
        v_bwd[-1] = min(v_bwd[-1], v_max_brake)

        v = v_bwd

    # zurueck in urspruengliche Reihenfolge
    v_final = np.empty(n)
    v_final[order] = v
    lap_time_s = float(np.sum(ds / v_final))
    return v_final, lap_time_s


def find_corner_min_speeds(s, radius, v, corner_radius_max=100.0, merge_gap_m=30.0):
    step_m = float(np.median(np.diff(s)))
    mask = radius < corner_radius_max
    runs = []
    in_run = False
    for i, m in enumerate(mask):
        if m and not in_run:
            in_run, start = True, i
        elif not m and in_run:
            in_run = False
            runs.append((start, i - 1))
    if in_run:
        runs.append((start, len(mask) - 1))
    gap_steps = merge_gap_m / step_m
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap_steps:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    out = []
    for a, b in merged:
        i_min = a + int(np.argmin(v[a:b + 1]))
        out.append({"s_apex_m": float(s[i_min]), "radius_m": float(radius[i_min]),
                     "v_min_kmh": float(v[i_min] * 3.6)})
    return out


def plot_speed_map(points, v, out_path):
    fig, ax = plt.subplots(figsize=(9, 9))
    sc = ax.scatter(points[:, 0], points[:, 1], c=v * 3.6, cmap="turbo", s=8)
    plt.colorbar(sc, ax=ax, label="Geschwindigkeit [km/h]")
    ax.set_xlabel("UTM33 Ost [m]")
    ax.set_ylabel("UTM33 Nord [m]")
    ax.set_aspect("equal")
    ax.set_title("Spreewaldring - simuliertes Geschwindigkeitsprofil (Hotlap)")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_speed_trace(s, v_by_scenario, out_path):
    fig, ax = plt.subplots(figsize=(11, 5))
    for label, v in v_by_scenario.items():
        ax.plot(s, v * 3.6, label=label)
    ax.set_xlabel("Streckenposition s [m]")
    ax.set_ylabel("Geschwindigkeit [km/h]")
    ax.set_title("Spreewaldring - Geschwindigkeitsprofil ueber die Runde")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    s, radius, points, total_len = load_track()
    print(f"Strecke: {total_len:.1f} m, {len(s)} Punkte (spreewaldring_track_model.py)")
    print(f"Reifen-mu-Bandbreite: {TIRE_MU_RANGE} (siehe Memory 'mx5-tires', Nankang NS-R2)")

    scenarios = [("konservativ (mu=1.0)", TIRE_MU_RANGE[0], False, None, None),
                 ("optimistisch (mu=1.3)", TIRE_MU_RANGE[1], False, None, None),
                 ("konservativ (mu=1.0, Kraftkreis)", TIRE_MU_RANGE[0], True, None, None),
                 ("optimistisch (mu=1.3, Kraftkreis)", TIRE_MU_RANGE[1], True, None, None)]

    scenarios = [(*sc, 1.0) for sc in scenarios]
    vehicles, _ = load_vehicles()
    for name, veh in vehicles.items():
        print(f"Aus den Logs, {name}: quer {veh.lat_g:.2f}g, beschl. {veh.accel_g:.2f}g, bremsen {veh.brake_g:.2f}g "
              "- Alltagsfahrten, Untergrenze statt Reifenlimit (mx5_vehicle_model.py)")
        scenarios.append((f"gemessen {name}", veh.lat_g, True, veh.accel_g, veh.brake_g, veh.speed_factor))

    results = {}
    for label, mu, fc, a_accel_g, a_brake_g, speed_factor in scenarios:
        v, lap_time = simulate_lap(s, radius, mu, friction_circle=fc, a_accel_max_g=a_accel_g,
                                    a_brake_max_g=a_brake_g, speed_factor=speed_factor)
        v_max = v.max() * 3.6
        v_mean = total_len / lap_time * 3.6
        print(f"\n--- Szenario: {label} ---")
        print(f"Rundenzeit: {lap_time:.2f} s")
        print(f"Topspeed: {v_max:.1f} km/h | Schnittgeschwindigkeit: {v_mean:.1f} km/h")

        corners = find_corner_min_speeds(s, radius, v)
        print(f"{'#':>3s} {'s_apex':>8s} {'R':>6s} {'v_min':>8s}")
        for i, c in enumerate(corners):
            print(f"{i+1:3d} {c['s_apex_m']:7.0f}m {c['radius_m']:5.1f}m {c['v_min_kmh']:7.1f} km/h")

        results[label] = {"mu": mu, "friction_circle": fc, "a_accel_max_g": a_accel_g, "a_brake_max_g": a_brake_g,
                           "lap_time_s": lap_time, "v_max_kmh": v_max,
                           "v_mean_kmh": v_mean, "corners": corners, "v_kmh": (v * 3.6).tolist()}

    v_by_scenario = {label: np.array(r["v_kmh"]) / 3.6 for label, r in results.items()}
    plot_speed_trace(s, v_by_scenario, os.path.join(RESULTS_DIR, "spreewaldring_lap_speed_trace.png"))
    print(f"\nGeschwindigkeitsprofil: {RESULTS_DIR}/spreewaldring_lap_speed_trace.png")

    v_optimistic, _ = simulate_lap(s, radius, TIRE_MU_RANGE[1])
    plot_speed_map(points, v_optimistic, os.path.join(RESULTS_DIR, "spreewaldring_lap_speed_map.png"))
    print(f"Geschwindigkeits-Streckenkarte: {RESULTS_DIR}/spreewaldring_lap_speed_map.png")

    out_json = os.path.join(RESULTS_DIR, "spreewaldring_lap_simulation_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"track_length_m": total_len, "scenarios": results}, f, indent=2, ensure_ascii=False)
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
