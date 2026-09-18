"""
Erstes empirisches Kurvengeschwindigkeitsmodell (MX-5 Projekt) - v_max(R)
aus real gefahrenen, CAN-validierten Kurven statt dem bisherigen Literatur-
Bracket mu=1.0-1.3 in spreewaldring_lap_simulation.py.

Datenbasis: ALLE `results/can_corner_event_summary_candump-*.json` (von
`can_corner_event_analysis.py`, laeuft inzwischen automatisch fuer jedes
CAN-Log im taeglichen Pipeline-Lauf) - urspruenglich nur die 43
nutzerbestaetigten Kurven aus dem Y-Splitter-Log candump-2026-09-12_211833,
seit 2026-09-14 auf alle vorhandenen CAN-Logs mit echten Kurven erweitert
(6 Fahrten, 220 Kurven insgesamt - siehe docs/logs/projekt-stand.md "Kurvenmodell").
Die urspruengliche manuelle Nutzer-Review (S-Kurven-Trennung, Geradeaus-
Ausschluss) betraf nur den 211833-Log und steckt bereits in
`can_corner_event_analysis.py`s `EXCLUDED_EVENTS`/Vorzeichen-Logik - gilt
automatisch fuer alle Logs mit, keine Log-spezifische Nacharbeit noetig.
Aus Speed+a_lat am jeweiligen Peak-Sample wird der implizite Kurvenradius
R = v^2/a_lat berechnet (kinematische Definition der Zentripetal-
beschleunigung, kein Fit - v und a_lat sind beide direkt aus dem
validierten CAN-RCM-Signal gemessen).

WICHTIGE EINSCHRAENKUNG (siehe auch mx5_tires-Memory): das sind ganz
normale Landstrassen-/Autobahnauffahrt-Kurven, KEINE Grenzbereichsfahrt
(kein Reifenquietschen/Rutschen zu erwarten, keine Rennstrecke). Der
beobachtete Peak-a_lat ist deshalb eine UNTERGRENZE fuer den echten
Reifengrip, keine Grenzwert-Messung - das Fahrzeug hat diese Werte
nachweislich sicher gehalten, koennte aber am echten Limit mehr. Das
Ergebnis dient hier als Sanity-Check/Untergrenze fuer das bestehende
mu=1.0-1.3-Bracket, NICHT als Ersatz dafuer - eine echte Grenzwertmessung
braucht eine Grenzbereichsfahrt (Track-Tag mit CAN-Logging), die es bisher
nicht gibt (siehe docs/logs/can-bus-status.md, Testplan-Punkt "Kurven beidseitig").

Methodik:
  1. Pro Kurve: v_peak [m/s] (Geschwindigkeit am a_lat-Spitzenwert-Sample,
     nicht das Fenstermittel - fuer eine korrekte R=v^2/a_lat-Berechnung
     muessen beide Groessen zum selben Zeitpunkt gehoeren),
     a_lat_peak [m/s^2], R_implied = v_peak^2/a_lat_peak [m].
  2. Empirische Grip-Obergrenze: max(|a_lat_peak_g|) ueber alle Kurven -
     das ist die staerkste sicher gefahrene Kurve im Datensatz.
  3. Trendpruefung v vs. a_lat_peak_g (Pearson r) - prueft, ob die
     Grip-Nutzung mit der Geschwindigkeit systematisch variiert (z.B.
     vorsichtigeres Fahren bei hohem Tempo) oder flach ist ueber den
     beobachteten Bereich.
  4. Vergleichsplot: die 43 Datenpunkte (R, v_peak) gegen die bestehenden
     v=sqrt(mu*g*R)-Kurven fuer mu=1.0/1.3 aus spreewaldring_lap_simulation.py -
     liegen die echten Punkte darunter (Bracket ist eine gueltige Obergrenze)
     oder ueberschreiten sie es (Bracket muesste angehoben werden)?

Aufruf: python scripts/corner_speed_model.py
"""
import glob
import json
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS_DIR = "results"
CORNER_JSON_GLOB = "results/can_corner_event_summary_candump-*.json"
G = 9.81
LAP_SIM_MU_RANGE = (1.0, 1.3)  # siehe spreewaldring_lap_simulation.py

# Zusaetzliche Referenzpunkte OHNE CAN-Bestaetigung (nur GPS+Gyro), auf
# Nutzer-Wunsch (2026-09-13) ergaenzt: dieselbe Kreuzung/S-Kurve wie die
# CAN-Kurven 32/33 (Autobahnauffahrt bei 52.738/13.2096), aber eine Woche
# vorher (2026-09-09, Log 170146) "am Limit" gefahren - bekannter, mehrfach
# dokumentierter ESP-Oversteer-Fall (siehe mx5_steering_angle-Memory). Der
# alte GPS/Gyro-Detektor hatte auch HIER die Rechts- und Linkskurve zu einem
# einzigen "links"-Ereignis verschmolzen (derselbe Bug wie bei den CAN-
# Kurven 11/26, hier nicht nachtraeglich in corner_event_analysis.py
# gefixt) - Werte deshalb direkt aus den rohen Kanaelen (RotationRateZ,
# VehicleSpeed, kinematisch v*omega wie in corner_event_analysis.py/
# grip_estimation.py) neu extrahiert, mit dem tatsaechlichen Peak-Sample
# statt der irrefuehrenden Fenster-Mittelung.
# VORSICHT: peak a_lat waehrend eines dokumentierten ESP-Eingriffs kann den
# nachhaltig haltbaren Grip UEBERSCHAETZEN (siehe mx5_tires-Memory,
# a_lat_peak vs. a_lat_mean bei Uebersteuern/Schleudern) - diese Punkte sind
# deshalb NICHT ohne Weiteres als "sicherer" Grenzwert zu interpretieren,
# nur als zusaetzlicher Anhaltspunkt.
REFERENCE_EVENTS = [
    {"label": "170146 rechts (ESP-Fall, t=2003.7s)", "v_peak_kmh": 111.0, "a_lat_peak_g": 1.143,
     "note": "ESP griff hier nachweislich ein - Peak vermutlich ueber dem nachhaltigen Grip-Limit"},
    {"label": "170146 links (t=2008.7s)", "v_peak_kmh": 89.0, "a_lat_peak_g": -1.015,
     "note": "Ausgang derselben S-Kurve, kein dokumentierter ESP-Eingriff hier"},
]


def load_all_events():
    events = []
    for path in sorted(glob.glob(CORNER_JSON_GLOB)):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for ev in data["events"]:
            events.append({**ev, "log_id": data["log_id"]})
    return events


def main():
    events = load_all_events()
    source_logs = sorted({ev["log_id"] for ev in events})
    print(f"Quell-Logs ({len(source_logs)}): {', '.join(source_logs)}")

    v_peak_ms = np.array([ev["speed_at_peak_kmh"] / 3.6 for ev in events])
    a_lat_g = np.array([abs(ev["a_lat_peak_g"]) for ev in events])
    a_lat_ms2 = a_lat_g * G
    r_implied_m = v_peak_ms ** 2 / a_lat_ms2

    print(f"Datenbasis: {len(events)} Kurven aus {len(source_logs)} Logs")
    print(f"v_peak: {v_peak_ms.min()*3.6:.0f}-{v_peak_ms.max()*3.6:.0f} km/h")
    print(f"a_lat_peak: {a_lat_g.min():.2f}-{a_lat_g.max():.2f}g (max = staerkste sicher gefahrene Kurve)")
    print(f"R_implied: {r_implied_m.min():.0f}-{r_implied_m.max():.0f}m")

    r_trend = float(np.corrcoef(v_peak_ms, a_lat_g)[0, 1])
    print(f"\nTrend v_peak vs. a_lat_peak_g: r={r_trend:+.2f} "
          f"({'kein klarer Trend' if abs(r_trend) < 0.3 else 'moderater/starker Zusammenhang'})")

    a_lat_max_g = float(a_lat_g.max())
    a_lat_p90_g = float(np.percentile(a_lat_g, 90))
    print(f"\nEmpirische Untergrenze fuer den Reifengrip (Landstrassenfahrt, NICHT Limit):")
    print(f"  max      = {a_lat_max_g:.2f}g")
    print(f"  p90      = {a_lat_p90_g:.2f}g")
    print(f"  Bestehende Lap-Sim-Annahme: mu={LAP_SIM_MU_RANGE[0]:.1f}-{LAP_SIM_MU_RANGE[1]:.1f}")
    if a_lat_max_g < LAP_SIM_MU_RANGE[0]:
        print(f"  -> Konsistent: alle {len(events)} Kurven liegen unter der unteren Lap-Sim-Grenze "
              f"({LAP_SIM_MU_RANGE[0]:.1f}g), keine Anpassung noetig, Bracket bleibt eine gueltige Obergrenze.")
    else:
        print(f"  -> ACHTUNG: mindestens eine Kurve ({a_lat_max_g:.2f}g) erreicht/uebersteigt "
              f"die untere Lap-Sim-Grenze ({LAP_SIM_MU_RANGE[0]:.1f}g) - Bracket pruefen.")

    ref_v_ms = np.array([r["v_peak_kmh"] / 3.6 for r in REFERENCE_EVENTS])
    ref_a_lat_g = np.array([abs(r["a_lat_peak_g"]) for r in REFERENCE_EVENTS])
    ref_r_m = ref_v_ms ** 2 / (ref_a_lat_g * G)
    print(f"\nZusaetzliche Referenzpunkte (GPS/Gyro, KEINE CAN-Bestaetigung, siehe Docstring):")
    for r, v, a, rad in zip(REFERENCE_EVENTS, ref_v_ms * 3.6, ref_a_lat_g, ref_r_m):
        flag = " ** UEBER der unteren Lap-Sim-Grenze **" if a > LAP_SIM_MU_RANGE[0] else ""
        print(f"  {r['label']}: v={v:.0f}km/h a_lat={a:.2f}g R_implied={rad:.0f}m{flag}")
        print(f"    ({r['note']})")

    # Plot 1: v_peak vs a_lat_peak (Grip-Nutzung ueber Geschwindigkeit)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    ax1.scatter(v_peak_ms * 3.6, a_lat_g, c=[abs(ev["duration_s"]) for ev in events],
                cmap="viridis", s=40, zorder=3)
    ax1.axhline(a_lat_max_g, color="tomato", ls="--", lw=1, label=f"max (CAN)={a_lat_max_g:.2f}g")
    for mu in LAP_SIM_MU_RANGE:
        ax1.axhline(mu, color="gray", ls=":", lw=1)
        ax1.text(2, mu + 0.01, f"Lap-Sim mu={mu:.1f}", fontsize=8, color="gray")
    ax1.scatter(ref_v_ms * 3.6, ref_a_lat_g, marker="*", s=260, color="red", edgecolor="black",
                zorder=4, label="Referenz (GPS/Gyro, ESP-Fall 170146)")
    ax1.set_xlabel("v am a_lat-Peak [km/h]")
    ax1.set_ylabel("|a_lat_peak| [g]")
    ax1.set_title(f"Grip-Nutzung vs. Geschwindigkeit (r={r_trend:+.2f})")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.grid(alpha=0.3)

    # Plot 2: R vs v_max, Datenpunkte gegen v=sqrt(mu*g*R)-Kurven
    r_range = np.linspace(5, max(r_implied_m.max(), ref_r_m.max(), 200), 200)
    ax2.scatter(r_implied_m, v_peak_ms * 3.6, c=a_lat_g, cmap="plasma", s=40, zorder=3,
                vmin=min(a_lat_g.min(), ref_a_lat_g.min()), vmax=max(a_lat_g.max(), ref_a_lat_g.max()))
    ax2.scatter(ref_r_m, ref_v_ms * 3.6, c=ref_a_lat_g, cmap="plasma", marker="*", s=280,
                edgecolor="black", linewidth=1.2, zorder=4,
                vmin=min(a_lat_g.min(), ref_a_lat_g.min()), vmax=max(a_lat_g.max(), ref_a_lat_g.max()),
                label="Referenz (GPS/Gyro, ESP-Fall 170146)")
    for mu, style in zip(LAP_SIM_MU_RANGE, ["--", "-"]):
        v_curve = np.sqrt(mu * G * r_range) * 3.6
        ax2.plot(r_range, v_curve, style, color="steelblue", lw=1.2, label=f"v=sqrt({mu:.1f}*g*R)")
    ax2.set_xlabel("impliziter Kurvenradius R = v²/a_lat [m]")
    ax2.set_ylabel("v am a_lat-Peak [km/h]")
    ax2.set_title("Kurvenpunkte vs. bestehendes Lap-Sim-Bracket")
    ax2.legend(fontsize=8, loc="upper left")
    ax2.grid(alpha=0.3)
    cb = fig.colorbar(ax2.collections[0], ax=ax2)
    cb.set_label("a_lat_peak [g]")

    plt.tight_layout()
    out_png = os.path.join(RESULTS_DIR, "corner_speed_model.png")
    plt.savefig(out_png, dpi=130)
    plt.close(fig)

    out = {
        "source_logs": source_logs, "n_events": len(events),
        "a_lat_peak_g_max": a_lat_max_g, "a_lat_peak_g_p90": a_lat_p90_g,
        "v_vs_a_lat_trend_r": r_trend,
        "lap_sim_mu_range": LAP_SIM_MU_RANGE,
        "consistent_with_lap_sim_bracket": a_lat_max_g < LAP_SIM_MU_RANGE[0],
        "events": [
            {**ev, "v_peak_ms": float(v_peak_ms[i]), "r_implied_m": float(r_implied_m[i])}
            for i, ev in enumerate(events)
        ],
        "reference_events_gps_gyro_only": [
            {**r, "r_implied_m": float(ref_r_m[i])} for i, r in enumerate(REFERENCE_EVENTS)
        ],
    }
    out_json = os.path.join(RESULTS_DIR, "corner_speed_model_summary.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nPlot: {out_png}")
    print(f"Details: {out_json}")


if __name__ == "__main__":
    main()
