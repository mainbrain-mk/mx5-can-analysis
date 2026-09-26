"""Tempomat-aktiv-Flag verifizieren (2026-09-26, Anlass: Gaspedal-Kachel im Dash soll bei
regelndem Tempomat die Drosselklappe in Gruen zeigen, siehe dash_gui.py _gas_card_source).

Wir haben KEIN eigenes verifiziertes CAN-Signal fuer "Tempomat regelt". Kandidaten aus
fremden Quellen (beide nie gegen ein eigenes Log geprueft):
  - opendbc mazda_2017 (data/can/external/): 0x21F CRZ_EVENTS, CRUISE_ACTIVE_CAR_MOVING
    = Byte2 Bit0; CRZ_SPEED = Byte0-1 (kph, 0.005*raw-0.5). 0x21F ist bei uns eine LEERE
    HS_PCM-Botschaft - genau die, die can_backend.py fuer das Dash jetzt roh ausliest.
  - opendbc: 0x21C CRZ_CTRL, CRZ_ACTIVE = Byte0 Bit3 (kommt vom FSC/MRCC-Modul, das der ND
    ohne Radar-Tempomat wohl nicht so fuellt - trotzdem mitgeprueft).
  - berumiya-Upstream (unsere DBC, 0x165 HS_PCM): CC_Mode_Related, CC_Mode_Related2 (VAL
    0=OFF/1=ON), CC_SetSpeed (km/h).

Referenz ist die Fahrsituation selbst, nicht ein anderes Signal: der Tempomat regelt genau
dann, wenn das Pedal losgelassen ist (APP ~0), das Fahrzeug zuegig faehrt (>40 km/h) UND die
Drosselklappe trotzdem deutlich offen steht bzw. der Motor Last liefert. Im Schubbetrieb
(Pedal los, Tempomat aus) ist die Drosselklappe dagegen praktisch zu und das Drehmoment
negativ/null. Fuer jeden Kandidaten wird ausgegeben, wie oft er in den drei Situationen
"Tempomat plausibel", "Schubbetrieb" und "Fahrer gibt Gas" gesetzt ist - ein brauchbares
Flag ist in der ersten ~100 %, in den beiden anderen ~0 %. Zusaetzlich werden ALLE 64 Bits
von 0x21F so bewertet, falls das opendbc-Bit beim ND doch woanders sitzt.

Aufruf:  python scripts/can_cruise_flag_check.py data/can/candump-<...>.log [--window t0 t1]
--window (Sekunden relativ zum Log-Anfang) schraenkt auf ein Fenster ein, in dem der Nutzer
sicher weiss, dass der Tempomat an war (z.B. die 5 Tempomat-Kurven vom 06.09.2026, siehe
docs/logs/projekt-stand.md "Kurvendetektion fuer schnelle Grossradius-Kurven").

Drosselklappe: bevorzugt OBD Mode-1 PID 0x11 (seit 2026-09-19 von tpms_poller.py schnell
gepollt, im Log als 0x7E8-Antwort), sonst Mode-22 ETC_ACT (0x093C, Rohwert - nur relativ
ausgewertet), sonst ActualEnginePercentTorque@0x167 als Lastproxy.
"""
import argparse
import sys

import numpy as np
import pandas as pd

from can_log_parser import parse_candump, load_db
from obd_from_can import decode_obd_traffic, extract_did_series

SPEED_MIN_KMH = 40.0
APP_RELEASED_MAX = 2.0
APP_PRESSED_MIN = 10.0
# Drosselklappe bei losgelassenem Pedal im Schub liegt typischerweise <10-12 % (PID 0x11
# meldet nie exakt 0); ein regelnder Tempomat bei >40 km/h braucht spuerbar mehr.
THROTTLE_OPEN_MIN_PCT = 15.0
THROTTLE_CLOSED_MAX_PCT = 10.0
TORQUE_OPEN_MIN_PCT = 10.0
TORQUE_CLOSED_MAX_PCT = 2.0
GRID_HZ = 10.0


def decode_signal(raw, db, can_id, sig_name):
    msg = db.get_message_by_frame_id(can_id)
    sub = raw[raw["can_id"] == can_id]
    t_list, v_list = [], []
    for t, data in sub[["t", "data"]].itertuples(index=False):
        try:
            dec = msg.decode(data, allow_truncated=True, decode_choices=False)
        except Exception:
            continue
        if sig_name in dec:
            t_list.append(t)
            v_list.append(float(dec[sig_name]))
    return np.array(t_list), np.array(v_list)


def raw_bits(raw, can_id):
    """(t, uint64-Frames) einer CAN-ID - fuer die Bit-fuer-Bit-Bewertung."""
    sub = raw[raw["can_id"] == can_id]
    t = sub["t"].to_numpy()
    frames = np.array([int.from_bytes(bytes(d).ljust(8, b"\0"), "big") for d in sub["data"]],
                      dtype=np.uint64)
    return t, frames


def on_grid(grid, t, v, hold_s=1.0):
    """Sample-and-hold auf das Zeitraster; NaN, wo der letzte Wert aelter als hold_s ist."""
    if len(t) == 0:
        return np.full(len(grid), np.nan)
    idx = np.searchsorted(t, grid, side="right") - 1
    out = np.where(idx >= 0, v[np.clip(idx, 0, len(v) - 1)], np.nan).astype(float)
    age = grid - t[np.clip(idx, 0, len(t) - 1)]
    out[(idx < 0) | (age > hold_s)] = np.nan
    return out


def throttle_reference(raw, obd, grid):
    """Drosselklappen-/Lastproxy auf dem Raster + die Schwellen fuer offen/zu."""
    s = extract_did_series(obd, 0x11, mode="mode1") if len(obd) else pd.DataFrame()
    if len(s) > 20:
        return (on_grid(grid, s["t"].to_numpy(), s["raw_value"].to_numpy(dtype=float) * 100 / 255,
                        hold_s=2.0), THROTTLE_OPEN_MIN_PCT, THROTTLE_CLOSED_MAX_PCT,
                "OBD Mode-1 0x11 ThrottlePosition [%]")
    s = extract_did_series(obd, 0x093C, mode="mode22") if len(obd) else pd.DataFrame()
    if len(s) > 20:
        v = s["raw_value"].to_numpy(dtype=float)
        # Rohwert unbekannter Skala: Schwellen relativ zum Log-Minimum (=Drosselklappe zu)
        lo = np.percentile(v, 2)
        span = np.percentile(v, 98) - lo
        g = on_grid(grid, s["t"].to_numpy(), (v - lo) / span * 100 if span > 0 else v, hold_s=2.0)
        return g, THROTTLE_OPEN_MIN_PCT, THROTTLE_CLOSED_MAX_PCT, "OBD Mode-22 ETC_ACT (roh, 0-100 % skaliert)"
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--window", nargs=2, type=float, metavar=("T0", "T1"),
                    help="Auswertefenster in s relativ zum Log-Anfang (sicher bekannte Tempomat-Phase)")
    args = ap.parse_args()

    db = load_db()
    raw = parse_candump(args.log)
    if raw.empty:
        sys.exit("leeres Log")
    t0 = raw["t"].min()
    raw = raw.assign(t=raw["t"] - t0)
    obd = decode_obd_traffic(raw)
    grid = np.arange(0.0, raw["t"].max(), 1.0 / GRID_HZ)

    t, v = decode_signal(raw, db, 0x202, "VehicleSpeed")
    speed = on_grid(grid, t, v)
    t, v = decode_signal(raw, db, 0x202, "APP_Accelerator_Pedal_Position")
    app = on_grid(grid, t, v)

    ref = throttle_reference(raw, obd, grid)
    if ref is None:
        t, v = decode_signal(raw, db, 0x167, "ActualEnginePercentTorque")
        ref = (on_grid(grid, t, v), TORQUE_OPEN_MIN_PCT, TORQUE_CLOSED_MAX_PCT,
               "ActualEnginePercentTorque@0x167 [%] (kein OBD-Traffic im Log)")
    load, open_min, closed_max, ref_name = ref
    print(f"Lastreferenz: {ref_name}")

    fast = speed > SPEED_MIN_KMH
    released = app <= APP_RELEASED_MAX
    situations = {
        "Tempomat plausibel (Pedal los, >40 km/h, Last an)": fast & released & (load >= open_min),
        "Schubbetrieb (Pedal los, >40 km/h, Last aus)": fast & released & (load <= closed_max),
        "Fahrer gibt Gas (APP>10 %)": app >= APP_PRESSED_MIN,
    }
    if args.window:
        win = (grid >= args.window[0]) & (grid <= args.window[1])
        situations = {k: m & win for k, m in situations.items()}
        situations["--window (Nutzer: Tempomat sicher an)"] = win
    for name, mask in situations.items():
        print(f"  {name}: {mask.sum() / GRID_HZ:.0f} s")
    if situations[next(iter(situations))].sum() < 5 * GRID_HZ:
        print("WARNUNG: <5 s plausible Tempomat-Phase - Aussagekraft gering, "
              "besser ein Log mit laengerer Tempomat-Fahrt (oder --window) nehmen.")

    candidates = []
    t, frames = raw_bits(raw, 0x21F)
    if len(t):
        for bit in range(64):  # DBC-Big-Endian-Bitnummer: Byte = bit//8, Bit im Byte = bit%8
            # Frames sind als Big-Endian-uint64 gelesen -> Byte0 ist das hoechstwertige
            shift = np.uint64(8 * (7 - bit // 8) + bit % 8)
            vals = ((frames >> shift) & np.uint64(1)).astype(float)
            g = on_grid(grid, t, vals)
            label = f"0x21F Byte{bit // 8} Bit{bit % 8}"
            if bit == 16:
                label += "  <- opendbc CRUISE_ACTIVE_CAR_MOVING (Dash-Kandidat)"
            candidates.append((label, g))
        crz = ((frames >> np.uint64(48)) & np.uint64(0xFFFF)).astype(float) * 0.005 - 0.5
        candidates.append(("0x21F Byte0-1 als CRZ_SPEED>0 (opendbc)", on_grid(grid, t, crz) > 0))
    else:
        print("0x21F kommt in diesem Log nicht vor!")
    t, frames = raw_bits(raw, 0x21C)
    if len(t):
        g = on_grid(grid, t, ((frames >> np.uint64(3 + 56)) & np.uint64(1)).astype(float))
        candidates.append(("0x21C Byte0 Bit3 (opendbc CRZ_ACTIVE, FSC)", g))
    for sig in ("CC_Mode_Related", "CC_Mode_Related2"):
        t, v = decode_signal(raw, db, 0x165, sig)
        candidates.append((f"0x165 {sig}!=0 (berumiya)", on_grid(grid, t, v) != 0))
    t, v = decode_signal(raw, db, 0x165, "CC_SetSpeed")
    candidates.append(("0x165 CC_SetSpeed>0 (berumiya)", on_grid(grid, t, v) > 0))

    rows = []
    for label, g in candidates:
        g = np.asarray(g, dtype=float)
        row = {"Kandidat": label}
        for name, mask in situations.items():
            sel = g[mask & ~np.isnan(g)]
            row[name] = f"{100 * sel.mean():5.1f} %" if len(sel) else "  n/a "
        # Nur Kandidaten, die sich ueberhaupt irgendwo aendern, sind interessant
        row["_var"] = np.nanstd(g) if np.isfinite(g).any() else 0.0
        rows.append(row)
    df = pd.DataFrame(rows)
    constant = df[df["_var"] == 0]["Kandidat"].tolist()
    df = df[df["_var"] > 0].drop(columns="_var")
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 70)
    print("\nAnteil der Zeit, in der der Kandidat gesetzt ist (Ziel: erste Spalte ~100 %, "
          "die anderen ~0 %):")
    print(df.to_string(index=False) if len(df) else "  kein Kandidat mit veraenderlichem Wert")
    named = [c for c in constant if "Bit" not in c or "<-" in c]
    if named:
        print("\nIm ganzen Log konstant (also als Flag unbrauchbar): " + "; ".join(named))


if __name__ == "__main__":
    main()
