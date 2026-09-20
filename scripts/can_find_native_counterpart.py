"""
Gibt es zu einem per OBD abgefragten Wert ein natives CAN-Broadcast-Gegenstueck?

Frage dahinter: alles, was wir per Diagnose-Anfrage holen (Oeltemperatur, Lambda, MAF,
Zuendwinkel, ...), kommt mit 1-2 Hz und belastet den Bus. Wird dieselbe Groesse zusaetzlich
periodisch gebroadcastet, haetten wir sie mit voller Rate und ohne eigene Anfragen.

Abgrenzung zu den bestehenden Werkzeugen: `can_byte_search.py` sweept gegen ALLE Anker mit
einer Doppelschwelle (roh UND detrended). Das ist fuer schnell veraenderliche Groessen genau
richtig, versagt aber systematisch bei langsamen Monotonien wie der Oeltemperatur:

  - roh korreliert dort ALLES, was ueber die Fahrt steigt (Kuehlwasser, Kat-Temperatur,
    Kilometerstand, Betriebsstunden) - das bekannte Trend-Artefakt,
  - detrended (20s-Median) bleibt vom eigentlichen Signal nichts uebrig, weil es sich um
    ~2 Grad/min bewegt - ein ECHTES Gegenstueck wuerde also verworfen.

Deshalb hier ein anderer Diskriminator: **Partialkorrelation gegen eine Kontrollgroesse**.
Fuer die Oeltemperatur ist das Kuehlwasser die richtige Kontrolle - beide steigen im
Warmlauf, aber das Oel hinkt um bis zu 39 Grad hinterher und hat damit eigene Struktur.
Ein Kandidat zaehlt nur, wenn er Varianz erklaert, die das Kuehlwasser NICHT erklaert.

    partial_r(x, y | z) = (r_xy - r_xz*r_yz) / sqrt((1-r_xz^2)(1-r_yz^2))

Ein reines Kuehlwasser-Duplikat bekommt so partial_r ~ 0, ein echtes Oeltemperatursignal
ein hohes. Ohne `--control` verhaelt sich das Skript wie eine gewoehnliche Korrelationssuche.

Durchsucht werden alle Byte- und Byte-Paar-Lesungen (big/little endian, signed/unsigned)
jeder Botschaft - vektorisiert ueber eine Bitmatrix, nicht per cantools-Frame-Dekodierung.

Aufruf:
    .venv/bin/python scripts/can_find_native_counterpart.py --self-test
    .venv/bin/python scripts/can_find_native_counterpart.py --log <log> --ref OilTemp --control CoolantTemp
    .venv/bin/python scripts/can_find_native_counterpart.py --log <log> --ref OBD1_TimingAdvance
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from can_log_parser import parse_candump, load_db
from can_field_segmentation import bit_matrix
from obd_from_can import decode_obd_traffic, extract_did_series

OUT_CSV = "results/can_native_counterpart.csv"
MIN_FRAMES = 500
GRID_S = 5.0          # Referenzen kommen mit 1-2 Hz - feiner lohnt nicht
MIN_POINTS = 60

# OBD-Referenzen: (mode, id). Die Oeltemperatur ist der Anlass fuer dieses Skript.
OBD_REFS = {
    "OilTemp": ("mode22", 0x1310, lambda r: r / 100 - 40),
    "OBD1_MAF": ("mode1", 0x10, lambda r: r / 100),
    "OBD1_LambdaCommanded": ("mode1", 0x44, lambda r: r / 32768),
    "OBD1_TimingAdvance": ("mode1", 0x0E, lambda r: r / 2 - 64),
    "OBD1_EnginePercentTorque": ("mode1", 0x62, lambda r: r - 125),
    "KnockRetard": ("mode22", 0x03EC, lambda r: (r - 65536 if r >= 32768 else r) / 512),
    "STEER_ANGL_EPS": ("mode22", 0x3302, lambda r: r),
}

# Kontroll-/Referenzgroessen aus der DBC (can_id, signalname)
DBC_REFS = {
    "CoolantTemp": (0x420, "CoolantTemp"),
    "EngineRPM": (0x202, "EngineRPM"),
    "VehicleSpeed": (0x202, "VehicleSpeed"),
    "IAT": (0x202, "IAT_Sensor_No1"),
}


def dbc_series(raw_df, db, can_id, signal):
    msg = db.get_message_by_frame_id(can_id)
    sub = raw_df[raw_df["can_id"] == can_id]
    t, v = [], []
    for tt, data in sub[["t", "data"]].itertuples(index=False):
        try:
            dec = msg.decode(data, allow_truncated=True)
        except Exception:
            continue
        if signal in dec:
            t.append(tt)
            v.append(float(dec[signal]))
    return np.array(t), np.array(v, dtype=float)


def to_grid(t, v, grid):
    """Auf ein gemeinsames Zeitraster bringen. Ausserhalb des Messbereichs -> NaN, damit
    nicht ueber Luecken hinweg extrapoliert wird."""
    if len(t) < 2:
        return np.full_like(grid, np.nan)
    order = np.argsort(t)
    out = np.interp(grid, t[order], v[order], left=np.nan, right=np.nan)
    out[(grid < t.min()) | (grid > t.max())] = np.nan
    return out


def detect_multiplexor(byte_vals, n_frames):
    """Multiplexte Botschaft erkennen. -> Byte-Index des Multiplexors oder None.

    Ohne das dominiert jede multiplexte Botschaft die Ergebnisliste mit Scheintreffern: eine
    flache Byte-Lesung mischt dann Werte VERSCHIEDENER Bedeutung, und das blosse
    Rotationsmuster korreliert mit allem, was ueber die Fahrt monoton laeuft. Genau so ist
    2026-09-15 die Botschaft 0x45B (Byte0 zykliert 1..5) mit partial_r=0,91 als angebliches
    Oeltemperatur-Gegenstueck aufgetaucht.

    Kriterium: wenige verschiedene Werte (2..8), alle etwa gleich haeufig (echter Zyklus,
    kein Statusbyte, das mal hier mal da steht)."""
    for i, v in enumerate(byte_vals):
        uniq, counts = np.unique(v, return_counts=True)
        if not (2 <= len(uniq) <= 8):
            continue
        expected = n_frames / len(uniq)
        if np.all(np.abs(counts - expected) < 0.05 * expected):
            return i
    return None


def candidate_fields(bits, dlc):
    """Alle Byte- und Byte-Paar-Lesungen. -> (label, werte)."""
    n = dlc * 8
    byte_vals = [bits[:, i * 8:(i + 1) * 8].astype(np.int64) @ (1 << np.arange(7, -1, -1))
                 for i in range(dlc)]
    for i, v in enumerate(byte_vals):
        yield f"B{i}", v.astype(float)
        yield f"B{i}s", np.where(v >= 128, v - 256, v).astype(float)
    for i in range(dlc - 1):
        be = byte_vals[i] * 256 + byte_vals[i + 1]
        le = byte_vals[i + 1] * 256 + byte_vals[i]
        for lbl, w in (("BE", be), ("LE", le)):
            yield f"B{i}-{i+1}{lbl}", w.astype(float)
            yield f"B{i}-{i+1}{lbl}s", np.where(w >= 32768, w - 65536, w).astype(float)
    _ = n


def partial_r(x, y, z):
    """r(x,y | z) - wie stark haengen x und y zusammen, NACHDEM z herausgerechnet ist."""
    m = ~np.isnan(x) & ~np.isnan(y) & ~np.isnan(z)
    if m.sum() < MIN_POINTS:
        return np.nan, np.nan
    x, y, z = x[m], y[m], z[m]
    if min(x.std(), y.std(), z.std()) < 1e-9:
        return np.nan, np.nan
    rxy = np.corrcoef(x, y)[0, 1]
    rxz = np.corrcoef(x, z)[0, 1]
    ryz = np.corrcoef(y, z)[0, 1]
    denom = np.sqrt(max(1 - rxz ** 2, 1e-12) * max(1 - ryz ** 2, 1e-12))
    return rxy, (rxy - rxz * ryz) / denom


def search(log_path, ref_name, control_name=None, verbose=True):
    raw_df = parse_candump(log_path)
    db = load_db("hscan")

    if ref_name in OBD_REFS:
        mode, id_, formula = OBD_REFS[ref_name]
        dec = decode_obd_traffic(raw_df)
        s = extract_did_series(dec, id_, mode=mode)
        if s.empty:
            raise SystemExit(f"Referenz {ref_name} kommt in diesem Log nicht vor")
        t_ref = s["t"].to_numpy(dtype=float)
        v_ref = formula(s["raw_value"].to_numpy(dtype=float))
    else:
        t_ref, v_ref = dbc_series(raw_df, db, *DBC_REFS[ref_name])

    t0, t1 = float(t_ref.min()), float(t_ref.max())
    grid = np.arange(t0, t1, GRID_S)
    Y = to_grid(t_ref, v_ref, grid)

    if control_name:
        t_c, v_c = dbc_series(raw_df, db, *DBC_REFS[control_name])
        Z = to_grid(t_c, v_c, grid)
    else:
        Z = np.zeros_like(grid)

    if verbose:
        print(f"  Referenz {ref_name}: {len(v_ref)} Werte, {np.nanmin(v_ref):.2f}..{np.nanmax(v_ref):.2f}, "
              f"Fenster {(t1-t0)/60:.1f} min, {len(grid)} Rasterpunkte")
        if control_name:
            m = ~np.isnan(Y) & ~np.isnan(Z)
            print(f"  Kontrolle {control_name}: r gegen die Referenz = {np.corrcoef(Y[m], Z[m])[0,1]:+.3f} "
                  f"(hoch ist normal - genau deshalb wird partial gerechnet)")

    rows = []
    for can_id, sub in raw_df.groupby("can_id"):
        if len(sub) < MIN_FRAMES:
            continue
        payloads = list(sub["data"])
        dlc = max(len(p) for p in payloads)
        if dlc < 1:
            continue
        bits = bit_matrix(payloads, dlc)
        t = sub["t"].to_numpy(dtype=float)
        byte_vals = [bits[:, i * 8:(i + 1) * 8].astype(np.int64) @ (1 << np.arange(7, -1, -1))
                     for i in range(dlc)]
        mux_idx = detect_multiplexor(byte_vals, len(t))
        # Bei einer multiplexten Botschaft wird jede Multiplex-Gruppe EINZELN durchsucht -
        # nur innerhalb einer Gruppe haben die Bytes eine einheitliche Bedeutung.
        groups = ([(f"m{int(mv)}", byte_vals[mux_idx] == mv) for mv in np.unique(byte_vals[mux_idx])]
                  if mux_idx is not None else [("", np.ones(len(t), dtype=bool))])
        for suffix, sel in groups:
            if sel.sum() < MIN_POINTS:
                continue
            for label, vals in candidate_fields(bits[sel], dlc):
                _analyse(rows, can_id, f"{label}{('@' + suffix) if suffix else ''}",
                         t[sel], vals, grid, Y, Z, control_name)
    df = pd.DataFrame(rows)
    df["score"] = df["partial_r"].abs()
    return df.sort_values("score", ascending=False)


def _analyse(rows, can_id, label, t, vals, grid, Y, Z, control_name):
    if np.std(vals) < 1e-9:
        return
    if True:
            X = to_grid(t, vals, grid)
            if control_name:
                r, pr = partial_r(X, Y, Z)
            else:
                m = ~np.isnan(X) & ~np.isnan(Y)
                r = np.corrcoef(X[m], Y[m])[0, 1] if m.sum() >= MIN_POINTS and X[m].std() > 0 else np.nan
                pr = r
            if np.isnan(r):
                return
            rows.append(dict(can_id=f"0x{can_id:03X}", field=label,
                             r=r, partial_r=pr,
                             n_unique=int(len(np.unique(vals))),
                             vmin=float(np.min(vals)), vmax=float(np.max(vals))))


def transfer_check(df, logs, ref_name, control_name, top=40):
    """Der entscheidende Test: eine auf Log A gefittete Kalibrierung auf Log B anwenden.

    Innerhalb EINER Fahrt korreliert bei langsamen Groessen praktisch alles, was ueber die
    Fahrt monoton laeuft (Kilometerstand, Betriebsstunden, Kat-Temperatur). Erst die
    Uebertragung auf eine zweite Fahrt mit anderer Vorgeschichte trennt ein echtes
    Gegenstueck von einem Trend-Artefakt: das echte Signal behaelt seine Kalibrierung, das
    Artefakt liefert ein negatives R2 (schlechter als der blosse Mittelwert).

    Gemessen 2026-09-15 an der Oeltemperatur: alle Kandidaten hatten R2 0,85-0,93 innerhalb
    der Fahrt und -5 bis -24 bei der Uebertragung."""
    if len(logs) < 2:
        return df
    best = (df.groupby(["can_id", "field"], as_index=False)["score"].min()
              .sort_values("score", ascending=False).head(top))
    cache = {p: _log_context(p, ref_name, control_name) for p in logs[:2]}
    a, b = logs[0], logs[1]
    out = []
    for _, row in best.iterrows():
        fa = _fit_candidate(cache[a], row["can_id"], row["field"])
        fb = _fit_candidate(cache[b], row["can_id"], row["field"])
        if fa is None or fb is None:
            continue
        (ka, ba, r2a) = fa[:3]
        (_, _, r2b, Xb, Yb, mb) = fb
        pred = ka * Xb[mb] + ba
        ss = 1 - ((Yb[mb] - pred) ** 2).sum() / max(((Yb[mb] - Yb[mb].mean()) ** 2).sum(), 1e-12)
        out.append(dict(can_id=row["can_id"], field=row["field"],
                        r2_logA=r2a, r2_logB=r2b, r2_transfer=ss))
    return pd.DataFrame(out).sort_values("r2_transfer", ascending=False)


def _log_context(path, ref_name, control_name):
    raw_df = parse_candump(path)
    db = load_db("hscan")
    if ref_name in OBD_REFS:
        mode, id_, formula = OBD_REFS[ref_name]
        s = extract_did_series(decode_obd_traffic(raw_df), id_, mode=mode)
        t_ref = s["t"].to_numpy(dtype=float)
        v_ref = formula(s["raw_value"].to_numpy(dtype=float))
    else:
        t_ref, v_ref = dbc_series(raw_df, db, *DBC_REFS[ref_name])
    grid = np.arange(float(t_ref.min()), float(t_ref.max()), GRID_S)
    return dict(raw=raw_df, grid=grid, Y=to_grid(t_ref, v_ref, grid))


def _fit_candidate(ctx, can_id_hex, field):
    import re
    cid = int(can_id_hex, 16)
    sub = ctx["raw"][ctx["raw"]["can_id"] == cid]
    if len(sub) < MIN_FRAMES:
        return None
    payloads = list(sub["data"])
    dlc = max(len(p) for p in payloads)
    bits = bit_matrix(payloads, dlc)
    t = sub["t"].to_numpy(dtype=float)
    base, _, mux = field.partition("@")
    byte_vals = [bits[:, i * 8:(i + 1) * 8].astype(np.int64) @ (1 << np.arange(7, -1, -1))
                 for i in range(dlc)]
    if mux:
        idx = detect_multiplexor(byte_vals, len(t))
        if idx is None:
            return None
        sel = byte_vals[idx] == int(mux[1:])
        bits, t = bits[sel], t[sel]
    fields = dict(candidate_fields(bits, dlc))
    if base not in fields:
        return None
    X = to_grid(t, fields[base], ctx["grid"])
    Y = ctx["Y"]
    m = ~np.isnan(X) & ~np.isnan(Y)
    if m.sum() < MIN_POINTS or X[m].std() < 1e-9:
        return None
    k, b = np.polyfit(X[m], Y[m], 1)
    r2 = np.corrcoef(k * X[m] + b, Y[m])[0, 1] ** 2
    _ = re
    return k, b, r2, X, Y, m


def annotate_known(df, db):
    from can_opendbc_crosscheck import signal_bit_indices
    owner = {}
    for msg in db.messages:
        for sig in msg.signals:
            for bidx in signal_bit_indices(sig):
                owner.setdefault((msg.frame_id, bidx // 8), set()).add(sig.name)

    import re

    def lookup(row):
        cid = int(row["can_id"], 16)
        # Feldnamen wie "B1-2BEs@m3" -> betroffene Byte-Indizes 1 und 2
        byts = [int(x) for x in re.findall(r"\d+", row["field"].split("@")[0])]
        names = set()
        for byi in byts:
            names |= owner.get((cid, byi), set())
        return ";".join(sorted(names))

    df["bekannt"] = df.apply(lookup, axis=1)
    return df


def self_test():
    """Der Kern ist die Partialkorrelation: ein Kandidat, der NUR die Kontrollgroesse
    kopiert, muss durchfallen - ein echtes, verzoegertes Signal muss durchkommen."""
    n = 400
    t = np.arange(n, dtype=float)
    coolant = np.clip(t / 2, 0, 90)                 # steigt schnell, saettigt
    oil = np.clip((t - 60) / 4, 0, 90)              # steigt langsamer, hinkt hinterher
    rng = np.random.default_rng(0)

    dup = coolant + rng.normal(0, 0.5, n)           # reines Kuehlwasser-Duplikat
    real = oil + rng.normal(0, 0.5, n)              # echtes Oelsignal

    r_dup, pr_dup = partial_r(dup, oil, coolant)
    r_real, pr_real = partial_r(real, oil, coolant)
    assert r_dup > 0.80, r_dup                      # roh sieht das Duplikat gut aus ...
    assert abs(pr_dup) < 0.35, pr_dup               # ... partial faellt es durch
    assert pr_real > 0.9, pr_real                   # echtes Signal kommt durch
    assert pr_real > pr_dup + 0.5, (pr_real, pr_dup)

    # Multiplex-Erkennung: Byte0 zykliert 1..5 wie in 0x45B -> muss erkannt werden,
    # ein normales Datenbyte dagegen nicht
    mux = np.tile(np.arange(1, 6), 200)
    ramp = (np.arange(1000) * 7) % 256
    assert detect_multiplexor([mux, ramp], 1000) == 0
    assert detect_multiplexor([ramp], 1000) is None
    # Statusbyte mit wenigen, aber ungleich verteilten Werten ist KEIN Multiplexor
    lopsided = np.concatenate([np.zeros(950), np.ones(50)])
    assert detect_multiplexor([lopsided], 1000) is None

    # Kandidatenfelder: 16-Bit-BE-Lesung muss exakt rekonstruiert werden
    payloads = [bytes([0x12, 0x34, 0, 0, 0, 0, 0, i % 256]) for i in range(50)]
    bits = bit_matrix(payloads, 8)
    fields = dict(candidate_fields(bits, 8))
    assert fields["B0-1BE"][0] == 0x1234, fields["B0-1BE"][0]
    assert fields["B0-1LE"][0] == 0x3412, fields["B0-1LE"][0]
    assert fields["B0"][0] == 0x12
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="append", required=False)
    ap.add_argument("--ref", default="OilTemp", choices=sorted(set(OBD_REFS) | set(DBC_REFS)))
    ap.add_argument("--control", default=None, choices=sorted(DBC_REFS))
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    db = load_db("hscan")
    frames = []
    for path in args.log:
        print(f"{os.path.basename(path)}:")
        df = search(path, args.ref, args.control)
        df["log"] = os.path.basename(path)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df = annotate_known(df, db)
    os.makedirs("results", exist_ok=True)
    out = OUT_CSV.replace(".csv", f"_{args.ref}.csv")
    df.to_csv(out, index=False)

    if len(args.log) >= 2:
        print("\n=== Uebertragungstest (Kalibrierung aus Log A auf Log B angewandt) ===")
        tc = transfer_check(df, args.log, args.ref, args.control)
        if tc.empty:
            print("  keine gemeinsamen Kandidaten")
        else:
            with pd.option_context("display.width", 200):
                print(tc.head(12).to_string(index=False, formatters={
                    "r2_logA": "{:.3f}".format, "r2_logB": "{:.3f}".format,
                    "r2_transfer": "{:+.3f}".format}))
            ok = tc[tc.r2_transfer > 0.7]
            print(f"\n  -> {len(ok)} Kandidat(en) mit uebertragbarer Kalibrierung (R2 > 0,7)."
                  + ("" if len(ok) else " Also KEIN natives Gegenstueck gefunden."))
            if len(ok):
                # Ein bestandener Uebertragungstest heisst NICHT automatisch "dieselbe
                # Groesse". Ein Flag mit zwei Zustaenden kann eine analoge Referenz gut
                # vorhersagen, wenn deren Varianz von genau diesem Zustand dominiert wird -
                # 2026-09-15 passiert: 0x0FD Byte4-5 sagte das Soll-Lambda mit R2=0,88
                # vorher, ist aber ein Schubabschaltungs-Bit mit zwei Werten, kein Lambda.
                # Deshalb die Kardinalitaet mit ausgeben und darauf hinweisen.
                card = (df.groupby(["can_id", "field"])["n_unique"].max()
                          .reindex(list(zip(ok.can_id, ok.field))).to_numpy())
                low = card < 10
                if low.any():
                    print(f"  ACHTUNG: {int(low.sum())} davon haben weniger als 10 verschiedene "
                          f"Rohwerte - das sind Flags/Stufen, keine analogen Messwerte. Sie "
                          f"koennen die Referenz trotzdem gut vorhersagen, wenn deren Varianz "
                          f"von genau diesem Zustand dominiert wird. Einzeln pruefen.")

    print(f"\n=== Top {args.top} Kandidaten fuer '{args.ref}'"
          f"{f' (Kontrolle: {args.control})' if args.control else ''} -> {out} ===")
    cols = ["log", "can_id", "field", "r", "partial_r", "n_unique", "vmin", "vmax", "bekannt"]
    with pd.option_context("display.width", 210, "display.max_rows", 200):
        print(df.head(args.top)[cols].to_string(index=False,
              formatters={"r": "{:+.3f}".format, "partial_r": "{:+.3f}".format}))


if __name__ == "__main__":
    main()
