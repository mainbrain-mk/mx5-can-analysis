"""
Natuerliche Experimente (Plan-Schritte 1+3, docs/plans/can-offline-ausbeute-plan.md):
Ereignisse, die in fast jedem Log ohnehin vorkommen, jeweils gegen eine GLEICHARTIGE
Vergleichssituation ohne das Merkmal - und dann je Bit die Setzquote innen vs. aussen.

Ereignisse (Maske, Kontrolle):
  reverse       Rueckwaertsfahrt, erkannt an der Gierrate: bei Vorwaertsfahrt hat sie relativ
                zum Lenkwinkel ein festes Vorzeichen, rueckwaerts das umgekehrte (1-12 km/h,
                |Lenkwinkel|>60 deg). Kontrolle: Vorwaerts-Rangieren im selben Bereich.
  engine_start  0-3 s nach Drehzahl > 400 | 3-10 s davor (Zuendung an)
  engine_stop   Motor aus bei Zuendung an (i-stop / Abwuergen) | Leerlauf davor
  clutch        Kupplung durchgetreten | nicht getreten, jeweils fahrend
  neutral       fahrend im Leerlauf (Gang 0) | fahrend mit Gang
  brake_light   Bremsdruck > 3 bar | 0 bar, jeweils fahrend
  turn_left/right  Blinker | kein Blinker, fahrend
  headlight     Abblendlicht an | aus
  door          Tuer offen | zu (Zuendung an)
  cold          Kuehlwasser < 60 Grad | > 80 Grad, Motor laeuft
  high_rpm      > 5500 1/min | 2500-4500, Last
  standstill    steht mit laufendem Motor | faehrt

Ausgabe: results/can_natural_events.csv (je Log/Ereignis/Bit), Konsole: Bits, die in >= 60 %
der Logs mit diesem Ereignis Lift >= 0,5 und Kontroll-Setzquote <= 0,15 haben (oder
umgekehrt, Zustand 0).
Aufruf: .venv/bin/python scripts/can_natural_events.py [--self-test] [--event NAME ...]
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_offline_lab as lab
from can_rare_bits import dbc_bits, known_owner

HZ = 20.0
MIN_S = 1.0          # Mindestdauer innen und in der Kontrolle (s)
LIFT = 0.5
CTRL_MAX = 0.15


def _s(fr, g, name):
    try:
        return lab.on_grid(*lab.sig(fr, name), g)
    except KeyError:
        return np.full(len(g), np.nan)


def _dilate(m, n):
    return np.convolve(m.astype(float), np.ones(2 * n + 1), "same") > 0 if n else m


def _after(m, a, b):
    """Maske der Zeitpunkte a..b Sekunden nach steigenden Flanken von m."""
    out = np.zeros(len(m), bool)
    for i in np.flatnonzero(np.diff(m.astype(int)) == 1) + 1:
        out[max(0, i + int(a * HZ)):max(0, i + int(b * HZ))] = True
    return out


def event_masks(fr, g):
    v = _s(fr, g, "VehicleSpeed")
    rpm = _s(fr, g, "EngineRPM")
    steer = _s(fr, g, "Steering_Wheel_Absolute_Angle")
    yaw = _s(fr, g, "YawRate_Raw")
    key = _s(fr, g, "KeyState")
    clutch = _s(fr, g, "Clutch_Pedal_Position_raw")
    gear = _s(fr, g, "MT_Gear_Actual")
    bp = _s(fr, g, "BrakePressure")
    turn = _s(fr, g, "Turn")
    head = _s(fr, g, "Headlight")
    dl, dr = _s(fr, g, "DoorLeft"), _s(fr, g, "DoorRight")
    cool = _s(fr, g, "CoolantTemp")
    app = _s(fr, g, "APP_Accelerator_Pedal_Position")
    run = rpm > 400
    moving = v > 5
    E = {}

    man = (v > 0.5) & (v < 12) & (np.abs(steer) > 60) & (np.abs(yaw) > 2)
    same = np.sign(yaw) == np.sign(steer)
    fwd_same = (man & same).sum() < (man & ~same).sum()     # Vorwaerts = Mehrheit
    rev = man & (same if fwd_same else ~same)
    rev = np.convolve(rev.astype(float), np.ones(int(HZ)) / HZ, "same") > 0.6   # >=0,6 s am Stueck
    E["reverse"] = (rev, man & ~rev & ~_dilate(rev, int(3 * HZ)))

    E["engine_start"] = (_after(run, 0, 3), _after(run, -10, -3) & ~run)
    stop = (~run) & (key >= 2) & _dilate(run, int(20 * HZ))
    E["engine_stop"] = (stop, _after(~run & (key >= 2), -10, -2) & run & (v < 1))
    E["clutch"] = ((clutch > 150) & moving, (clutch < 20) & moving)
    E["neutral"] = ((gear == 0) & moving & (clutch < 20), (gear > 0) & moving & (clutch < 20))
    E["brake_light"] = ((bp > 3) & moving, (bp < 0.5) & moving)
    E["turn_left"] = ((turn == 1) & moving, (turn == 0) & moving)
    E["turn_right"] = ((turn == 2) & moving, (turn == 0) & moving)
    E["headlight"] = ((head > 0) & run, (head == 0) & run)
    E["door"] = (((dl > 0) | (dr > 0)) & (key >= 2), (dl == 0) & (dr == 0) & (key >= 2) & (v < 1))
    E["cold"] = ((cool < 60) & run, (cool > 80) & run)
    E["high_rpm"] = ((rpm > 5500) & (app > 50), (rpm > 2500) & (rpm < 4500) & (app > 50))
    E["standstill"] = ((v < 0.5) & run, moving & run)
    return E


def lift_table(fr, g, inside, ctrl, owner, log, event):
    rows = []
    for cid, (t, d) in fr.items() if False else ((k, v) for k, v in fr.items() if isinstance(k, int)):
        if cid >= 0x700 or len(t) < 200:
            continue
        idx = np.searchsorted(g, t).clip(0, len(g) - 1)
        i_in, i_ct = inside[idx], ctrl[idx]
        if i_in.sum() < 10 or i_ct.sum() < 10:
            continue
        bits = dbc_bits(d)
        p_in = bits[i_in].mean(0)
        p_ct = bits[i_ct].mean(0)
        for b in np.flatnonzero(np.abs(p_in - p_ct) >= 0.3):
            rows.append((log, event, f"0x{cid:03X}", int(b), float(p_in[b]), float(p_ct[b]),
                         owner.get((cid, int(b)), "")))
    return rows


def summarize(df, n_logs):
    df = df.copy()
    df["hit"] = ((df.p_in - df.p_ctrl >= LIFT) & (df.p_ctrl <= CTRL_MAX)) | \
                ((df.p_ctrl - df.p_in >= LIFT) & (df.p_ctrl >= 1 - CTRL_MAX))
    g = df.groupby(["event", "can_id", "bit", "known"], as_index=False).agg(
        hits=("hit", "sum"), p_in_med=("p_in", "median"), p_ctrl_med=("p_ctrl", "median"))
    g["logs_with_event"] = g["event"].map(n_logs)
    g["hit_share"] = g["hits"] / g["logs_with_event"]
    return g.sort_values(["event", "hit_share", "hits"], ascending=[True, False, False])


def self_test():
    g = np.arange(0, 10, 1 / HZ)
    m = (g > 2) & (g < 3)
    a = _after(m, 0, 1)
    assert a.sum() == int(HZ) and a[np.searchsorted(g, 2.5)], a.sum()
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    owner = known_owner()
    rows, n_logs = [], {}
    for p in lab.logs():
        fr = lab.frames(p)
        g = lab.grid(fr, HZ)
        log = os.path.basename(p)[8:25]
        for ev, (inside, ctrl) in event_masks(fr, g).items():
            if only and ev not in only:
                continue
            if inside.sum() < MIN_S * HZ or ctrl.sum() < MIN_S * HZ:
                continue
            n_logs[ev] = n_logs.get(ev, 0) + 1
            rows += lift_table(fr, g, inside, ctrl, owner, log, ev)
        print(log, flush=True)
    df = pd.DataFrame(rows, columns=["log", "event", "can_id", "bit", "p_in", "p_ctrl", "known"])
    os.makedirs("results", exist_ok=True)
    df.to_csv("results/can_natural_events.csv", index=False)
    s = summarize(df, n_logs)
    s.to_csv("results/can_natural_events_summary.csv", index=False)
    print("Logs je Ereignis:", n_logs)
    with pd.option_context("display.width", 200, "display.max_rows", 400):
        print(s[(s.hit_share >= 0.6) & (s.hits >= 2)].to_string(index=False))


if __name__ == "__main__":
    main()
