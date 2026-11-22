"""
Absender-Fingerabdruck per Taktdrift (PCM-Bits-Plan, Strang A1, docs/plans/pcm-bits-plan.md).

Jedes Steuergeraet taktet seine zyklischen Botschaften aus dem eigenen Quarz. Gemessen gegen
die Pi-Uhr hat jeder Quarz eine eigene Abweichung (einige ppm bis einige 10 ppm), die mit der
Temperatur langsam wandert. Botschaften DESSELBEN Steuergeraets zeigen also dieselbe
ppm-Kurve ueber die Fahrt, Botschaften verschiedener Geraete nicht. Die Pi-Uhr selbst
kuerzt sich beim Vergleich zweier IDs heraus.

Vorgehen je Log und ID: Nennperiode = Median der Abstaende auf 0,1 ms gerundet; in Fenstern
von WIN_S Sekunden wird t gegen die Frame-Nummer k = round((t - t_0)/T_nenn) regressiert
(robust gegen einzelne verlorene Frames), Steigung -> ppm. Vergleich zweier IDs: Median und
Streuung der ppm-Differenz ueber alle gemeinsamen Fenster.

Grenzen: eine ID, die ereignisgesteuert oder als Echo einer fremden Botschaft gesendet wird,
erbt deren Takt (dann landet sie beim Ausloeser). IDs mit < MIN_FRAMES Frames oder starkem
Jitter werden ausgelassen.

Aufruf: .venv/bin/python scripts/can_sender_clock.py [--logs N] [--self-test]
Ausgabe: results/pcm_campaign/sender_clock_ppm.csv (ID x Log x Fenster) und
         results/pcm_campaign/sender_clock_pairs.csv (Paarvergleich)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab

WIN_S = 60.0
MIN_FRAMES = 2000
OUT_DIR = "results/pcm_campaign"
# Bezugs-IDs je Steuergeraet (DBC-Absender, Takt untereinander geprueft): PCM, ABS, EPS
REF_IDS = [0x0FD, 0x215, 0x086]


def period_windows(t, win_s=WIN_S):
    """[(t_mitte, T_fit)] fuer eine aufsteigende Zeitreihe zyklischer Frames: je Fenster die
    per Regression geschaetzte Periode in Sekunden (gegen die Pi-Uhr), oder [] wenn nicht
    periodisch genug.

    Die Frame-Nummer k wird JE FENSTER ab dessen erstem Frame gezaehlt, mit der mittleren
    Periode des Logs als Schrittweite. Ueber ein ganzes Log (100.000+ Frames) gezaehlt, wuerde
    schon ein ppm-Fehler der Schrittweite k verrutschen lassen (erster Versuch 22.11.: die
    gerundete Nennperiode verschob 0x4D9/0x4DA um ~50 ppm)."""
    if len(t) < MIN_FRAMES:
        return []
    dt = np.diff(t)
    t_med = np.median(dt)
    if t_med <= 0:
        return []
    # nur Abstaende nahe der Periode mitteln: Luecken (verlorene Frames) und Schwaelle kurzer Frames
    # (0x4FE sendet beim Abstellen ~20 Null-Frames im 20-ms-Takt) verfaelschen sonst t_mean; in einem
    # 12-min-Log (22.11. Pilot) waren das -0,18 % und k verrutschte im Fenster (-2460 ppm Scheinabweichung)
    ok = np.abs(dt - t_med) < 0.3 * t_med
    # nur zyklische IDs: >= 80 % der Abstaende innerhalb +-30 % der mittleren Periode
    t_mean = np.mean(dt[ok])
    if np.mean(np.abs(dt - t_mean) < 0.3 * t_mean) < 0.8:
        return []
    out = []
    for a in np.arange(t[0], t[-1], win_s):
        m = (t >= a) & (t < a + win_s)
        if m.sum() < max(20, 0.5 * win_s / t_mean):
            continue
        tt = t[m]
        kk = np.round((tt - tt[0]) / t_mean)
        sel = np.ones(len(tt), bool)
        for _ in range(2):                      # Ausreisser (Arbitrierung, USB) abschneiden
            p = np.polyfit(kk[sel], tt[sel], 1)
            r = tt - np.polyval(p, kk)
            s = np.median(np.abs(r[sel])) * 1.4826 + 1e-7
            sel = np.abs(r) < 4 * s
        out.append((a + win_s / 2, p[0]))
    return out


def ratio_ppm(ta, tb, max_den=20):
    """Abweichung des Periodenverhaeltnisses ta/tb vom naechsten einfachen Bruch p/q, in ppm.
    Derselbe Quarz -> ~0 in jedem Fenster; fremde Quarze -> zweistellig und wandernd."""
    from fractions import Fraction
    r = np.asarray(ta) / np.asarray(tb)
    nom = float(Fraction(float(np.median(r))).limit_denominator(max_den))
    return (r / nom - 1) * 1e6, nom


def _analyse_one(path):
    fr = lab.frames(path)
    log = os.path.basename(path)[8:25]
    rows = []
    for cid, val in fr.items():
        if not isinstance(cid, int):
            continue
        for tm, per in period_windows(val[0]):
            rows.append((log, cid, tm, per))
    return rows


def analyse(paths, workers=None):
    """Je Log ein Prozess (Nutzerwunsch 22.11.: mehrere Kerne nutzen)."""
    from concurrent.futures import ProcessPoolExecutor
    rows = []
    with ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 2)) as ex:
        for i, r in enumerate(ex.map(_analyse_one, paths)):
            rows += r
            print(f"{i + 1}/{len(paths)}", flush=True)
    return pd.DataFrame(rows, columns=["log", "can_id", "t_mid", "period_s"])


def pairs(df, ref_ids):
    """Je Referenz-ID und anderer ID, je Log: Median der Bruch-Abweichung (ppm) ueber die
    gemeinsamen Fenster."""
    df = df.assign(w=(df.t_mid // WIN_S).astype(int))
    piv = df.pivot_table(index=["log", "w"], columns="can_id", values="period_s")
    out = []
    for ref in ref_ids:
        for cid in piv.columns:
            if cid == ref:
                continue
            for log, sub in piv[[ref, cid]].dropna().groupby(level="log"):
                if len(sub) < 5:
                    continue
                dev, nom = ratio_ppm(sub[cid].to_numpy(), sub[ref].to_numpy())
                out.append((ref, cid, log, len(sub), nom, float(np.median(dev))))
    return pd.DataFrame(out, columns=["ref", "can_id", "log", "n_win", "ratio_nom", "dev_ppm"])


def self_test():
    rng = np.random.default_rng(1)
    n = 150000                                   # 50 min bei 20 ms: lang genug, dass ein
                                                 # Zaehlfehler ueber das ganze Log auffiele

    def ser(period, ppm, jitter=None):
        t = np.arange(n) * period * (1 + ppm * 1e-6)
        t = t + (jitter(n) if jitter else rng.normal(0, 2e-4, n))
        return np.sort(np.delete(t, rng.choice(n, 300, replace=False)))   # verlorene Frames

    def per(t):
        return np.array([p for _, p in period_windows(t)])

    a = per(ser(0.020, 40.0))
    # gleicher Quarz, 15-ms-Raster mit 12/18-ms-Jitter (wie 0x20A; Median 15,05 ms)
    b = per(ser(0.015, 40.0, lambda k: np.where(np.arange(k) % 4 == 1, 0.003, 0.0) + rng.normal(0, 1e-4, k)))
    # gleicher Quarz, 100 ms
    c = per(ser(0.100, 40.0))
    # fremder Quarz, 25 ppm daneben
    d = per(ser(0.020, 15.0))
    m = min(len(a), len(b), len(c), len(d))
    for other, want in ((b, 0.0), (c, 0.0), (d, -25.0)):
        dev, _ = ratio_ppm(other[:m], a[:m])
        assert abs(np.median(dev) - want) < 1.5, (want, np.median(dev))
    # kurzes 100-ms-Log mit Abstell-Schwall (17 Frames im 20-ms-Takt, wie 0x4FE am 22.11.)
    t = np.arange(7240) * 0.1 * (1 + 40e-6) + rng.normal(0, 2e-4, 7240)
    t = np.sort(np.concatenate([t, t[-1] + 0.021 * np.arange(1, 18)]))
    e = per(t)
    dev, _ = ratio_ppm(e, a[:len(e)])
    assert abs(np.median(dev)) < 3, np.median(dev)
    assert period_windows(np.sort(rng.uniform(0, 1000, 5000))) == []    # nicht zyklisch
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", type=int, default=12, help="Anzahl Logs (gleichmaessig ueber die Zeit verteilt)")
    ap.add_argument("--holdout", action="store_true",
                    help="alle Logs AUSSER den --logs Herleitungs-Logs (Bestaetigung an unbenutzten Daten)")
    ap.add_argument("--tag", default="", help="Suffix der Ausgabedateien")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    all_logs = lab.logs(min_bytes=100_000_000)
    idx = np.unique(np.linspace(0, len(all_logs) - 1, a.logs).round().astype(int))
    chosen = [all_logs[i] for i in idx]
    if a.holdout:
        chosen = [p for p in lab.logs(min_bytes=20_000_000) if p not in chosen]
    df = analyse(chosen)
    os.makedirs(OUT_DIR, exist_ok=True)
    df.to_csv(f"{OUT_DIR}/sender_clock_ppm{a.tag}.csv", index=False)
    pairs(df, REF_IDS).to_csv(f"{OUT_DIR}/sender_clock_pairs{a.tag}.csv", index=False)
    print(f"{df.can_id.nunique()} IDs, {df.log.nunique()} Logs")


if __name__ == "__main__":
    main()
