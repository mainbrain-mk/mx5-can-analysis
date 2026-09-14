"""
Referenzfreie Feld-Segmentierung von CAN-Botschaften (READ-Algorithmus).

Das schliesst die strukturelle Luecke aller bisherigen Werkzeuge in diesem Projekt:
`can_byte_search.py` und `can_bitsearch.py` finden nur Signale, fuer die wir schon eine
Referenzgroesse haben (OBD-DID, anderes DBC-Signal, IMU). Fuer die ~100 komplett leeren
Botschaften gibt es per Definition keine Referenz - dort waren wir bisher blind.

READ (Marchetti & Stabili, "Reverse Engineering of Automotive Data Frames",
IEEE TIFS 14(4), 2019) braucht keine Referenz. Es wertet nur aus, wie oft jedes einzelne
Bit einer Botschaft zwischen aufeinanderfolgenden Frames kippt:

  - Innerhalb eines mehrstelligen Zahlenfeldes steigt die Kipprate monoton vom MSB zum LSB.
  - Eine Feldgrenze liegt dort, wo die Kipprate von einem Bit zum naechsten ABFAELLT
    (Ende eines Feldes -> MSB des naechsten Feldes kippt wieder selten).

Die Groessenordnung (floor(log10(rate))) statt der Rohrate macht das robust gegen Rauschen -
das ist READs zentraler Kniff gegenueber dem aelteren, zu strikten Vorgaenger.

Klassifikation je Feld (READs drei Klassen, hier um zwei projektspezifische erweitert):
  CONST     - kippt nie
  COUNTER   - laeuft als Rollzaehler (+1 mod 2^n)
  CRC       - alle Bits nahe Kipprate 0,5, Werte ohne Struktur -> Pruefsumme
  PHYSICAL  - alles andere: ein echter Messwert
  FLAG      - Einzelbit mit niedriger Kipprate -> Status/Schalter

Zusaetzlich (nicht aus READ): ein **Signedness-Verdachtstest**. Wird ein in Wirklichkeit
2er-Komplement-signiertes Feld unsigned gelesen, springen die Werte zwischen "knapp ueber 0"
und "knapp unter 2^n" hin und her, ohne den Bereich dazwischen zu nutzen. Genau dieser
Bug-Typ hat dieses Projekt schon zweimal getroffen (BrakePressure 2026-09-12, AmbientTemp
2026-09-15), beide Male erst per Zufall gefunden. Der Test findet ihn systematisch.

Validierung: das Skript vergleicht seine Funde mit unserer eigenen DBC. Fuer Botschaften mit
bekannten Signalen (0x202, 0x215, 0x82, ...) ist das ein echter Ground-Truth-Test des
Verfahrens; fuer leere Botschaften ist es die eigentliche Ausbeute.

Aufruf:
    .venv/bin/python scripts/can_field_segmentation.py --self-test
    .venv/bin/python scripts/can_field_segmentation.py --log data/can/candump-....log
    .venv/bin/python scripts/can_field_segmentation.py --log ... --id 0x415
    .venv/bin/python scripts/can_field_segmentation.py            # alle reichhaltigen Logs
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from can_log_parser import parse_candump, load_db

OUT_CSV = "results/can_field_segmentation.csv"
MIN_FRAMES = 200          # weniger Frames -> Kipprate statistisch wertlos
CRC_FLIP_MIN = 0.35       # ab hier gilt ein Bit als "kippt praktisch zufaellig"
SIGNED_EDGE_FRAC = 0.02   # "knapp bei 0" / "knapp bei Max" = aeusserste 2% des Wertebereichs
SIGNED_MIDDLE_MAX = 0.05  # verdaechtig, wenn <5% der Werte in der Mitte liegen


def bit_matrix(payloads, dlc=8):
    """(n_frames x 64) uint8-Matrix, Bit 0 = MSB von Byte 0 (DBC-Konvention)."""
    arr = np.frombuffer(b"".join(p.ljust(dlc, b"\x00")[:dlc] for p in payloads), dtype=np.uint8)
    arr = arr.reshape(-1, dlc)
    return np.unpackbits(arr, axis=1)


def flip_rates(bits):
    """Kipprate je Bit ueber aufeinanderfolgende Frames."""
    if len(bits) < 2:
        return np.zeros(bits.shape[1])
    return (bits[1:] != bits[:-1]).mean(axis=0)


def magnitudes(rates):
    """READs Groessenordnung: floor(log10(rate)), Nicht-Kipper auf -inf."""
    with np.errstate(divide="ignore"):
        mag = np.floor(np.log10(rates))
    mag[rates == 0] = -99
    return mag


def segment(rates, dlc=8):
    """READ-Feldgrenzen. Rueckgabe: Liste von (start_bit, length).

    Grenze zwischen Bit i und i+1, wenn die Groessenordnung abfaellt (mag[i] > mag[i+1]).
    Bytegrenzen sind KEINE Zwangsgrenzen - Signale duerfen Bytes ueberspannen (genau das
    kann can_byte_search.py nicht) -, aber das letzte Bit der Botschaft schliesst immer ab.

    Zusaetzlich zur reinen READ-Regel wird jeder Uebergang zwischen "kippt nie" und "kippt"
    als Grenze gesetzt. Ohne das verschmelzen konstante Bits mit dem folgenden Feld (die
    Groessenordnung steigt dort ja an, faellt nicht). Preis: die fuehrenden, faktisch
    ungenutzten High-Bits eines breiten Signals werden als eigenes CONST-Feld abgetrennt -
    fuer unseren Zweck eher nuetzlich, weil das den tatsaechlich genutzten Wertebereich zeigt.
    """
    n = dlc * 8
    mag = magnitudes(rates[:n])
    zero = rates[:n] == 0
    cuts = sorted({i for i in range(n - 1)
                   if mag[i] > mag[i + 1] or zero[i] != zero[i + 1]})
    fields, start = [], 0
    for c in cuts:
        fields.append((start, c - start + 1))
        start = c + 1
    if start < n:
        fields.append((start, n - start))
    return fields


def field_values(bits, start, length):
    """Feldwerte als vorzeichenlose Integer (big-endian ueber die Bitfolge)."""
    seg = bits[:, start:start + length]
    weights = (1 << np.arange(length - 1, -1, -1)).astype(np.int64)
    return seg.astype(np.int64) @ weights


def is_counter(vals, length):
    """Rollzaehler: Differenz aufeinanderfolgender Werte ist ueberwiegend +1 mod 2^n."""
    if len(vals) < 20 or length > 16:
        return False
    d = np.diff(vals) % (1 << length)
    return bool((d == 1).mean() > 0.9)


def signedness_suspect(vals, length):
    """Verdacht auf faelschlich unsigned gelesenes 2er-Komplement-Feld.

    Kennzeichen: Werte clustern an beiden Raendern des Wertebereichs, die Mitte bleibt
    praktisch leer - weil kleine negative Zahlen unsigned gelesen als 2^n-x erscheinen.
    Nur sinnvoll fuer Felder ab 8 Bit mit echter Streuung.
    """
    if length < 8 or len(vals) < 100:
        return False
    span = 1 << length
    lo = vals < span * SIGNED_EDGE_FRAC
    hi = vals > span * (1 - SIGNED_EDGE_FRAC)
    if lo.mean() < 0.05 or hi.mean() < 0.05:
        return False          # nur ein Rand belegt -> normaler Messwert
    middle = ~lo & ~hi
    return bool(middle.mean() < SIGNED_MIDDLE_MAX)


def classify(vals, rates, start, length):
    seg_rates = rates[start:start + length]
    if seg_rates.max() == 0:
        return "CONST"
    if is_counter(vals, length):
        return "COUNTER"
    if length >= 8 and seg_rates.min() > CRC_FLIP_MIN and len(np.unique(vals)) > 0.9 * min(len(vals), 1 << length):
        return "CRC"
    if length == 1:
        return "FLAG"
    return "PHYSICAL"


def our_bit_owner(our_db):
    """{can_id: {bit: signalname}} aus unserer eigenen DBC - als Ground Truth bzw. Luecken-Check."""
    from can_opendbc_crosscheck import signal_bit_indices
    out = {}
    for msg in our_db.messages:
        owner = {}
        for sig in msg.signals:
            for b in signal_bit_indices(sig):
                owner[b] = sig.name
        out[msg.frame_id] = owner
    return out


def analyze_log(path, our_db, only_id=None, verbose=True, correlate=False):
    raw_df = parse_candump(path)
    owners = our_bit_owner(our_db)

    anchors = {}
    if correlate:
        # gleiche Anker + gleiche Doppelschwelle wie can_byte_search.py - die READ-Felder
        # bekommen damit dieselbe Beweislast wie jeder bisherige Byte-Such-Kandidat
        from can_byte_search import extract_anchors, prepare_anchors_for_window, correlate_candidate
        from obd_from_can import decode_obd_traffic
        decoded_obd = decode_obd_traffic(raw_df)
        has_obd = decoded_obd is not None and len(decoded_obd) > 0
        anchors = extract_anchors(raw_df, our_db, decoded_obd, has_obd)

    rows = []
    for can_id, sub in raw_df.groupby("can_id"):
        if only_id is not None and can_id != only_id:
            continue
        if len(sub) < MIN_FRAMES:
            continue
        payloads = list(sub["data"])
        dlc = max(len(p) for p in payloads)
        if dlc == 0:
            continue
        bits = bit_matrix(payloads, dlc)
        rates = flip_rates(bits)
        owner = owners.get(can_id, {})
        t_arr = sub["t"].to_numpy(dtype=float)
        prepared = None
        for start, length in segment(rates, dlc):
            vals = field_values(bits, start, length)
            kind = classify(vals, rates, start, length)
            known = sorted({owner[b] for b in range(start, start + length) if b in owner})
            rec = dict(
                log=os.path.basename(path), can_id=f"0x{can_id:03X}", dlc=dlc,
                start=start, length=length, kind=kind,
                n=len(vals), n_unique=int(len(np.unique(vals))),
                vmin=int(vals.min()), vmax=int(vals.max()),
                flip_min=float(rates[start:start + length].min()),
                flip_max=float(rates[start:start + length].max()),
                signed_suspect=signedness_suspect(vals, length),
                known_signals=";".join(known),
                covered=bool(known),
                anchor="", method="", r_raw=np.nan, r_detrend=np.nan,
            )
            if anchors and kind in ("PHYSICAL", "FLAG") and len(np.unique(vals)) > 2:
                t0, t1 = float(t_arr.min()), float(t_arr.max())
                if t1 - t0 >= 30:
                    if prepared is None:
                        prepared = prepare_anchors_for_window(anchors, t0, t1)
                    hit = correlate_candidate(t_arr, vals.astype(float), t0, t1, prepared)
                    if hit:
                        rec["anchor"], rec["method"], rec["r_raw"], rec["r_detrend"] = hit
            rows.append(rec)
    df = pd.DataFrame(rows)
    if verbose:
        n_msgs = df["can_id"].nunique() if len(df) else 0
        print(f"  {os.path.basename(path)}: {n_msgs} Botschaften, {len(df)} Felder")
    return df


def consolidate(df):
    """Nur Felder behalten, die in JEDEM ausgewerteten Log an derselben Bitlage auftauchen -
    dieselbe cross-log-Stabilitaetsregel, an der schon 0x0FD/0x20A gescheitert sind."""
    n_logs = df["log"].nunique()
    g = df.groupby(["can_id", "start", "length"], as_index=False).agg(
        logs=("log", "nunique"),
        kind=("kind", lambda s: s.mode().iat[0]),
        kinds=("kind", lambda s: ";".join(sorted(set(s)))),
        n_unique_med=("n_unique", "median"),
        vmin=("vmin", "min"), vmax=("vmax", "max"),
        flip_max=("flip_max", "max"),
        signed_suspect=("signed_suspect", "any"),
        known_signals=("known_signals", lambda s: ";".join(sorted({x for x in s if x}))),
        covered=("covered", "any"),
        anchor=("anchor", lambda s: s.mode().iat[0] if len(s.mode()) and s.mode().iat[0] else ""),
        hit_logs=("anchor", lambda s: int((s.fillna("") != "").sum())),
        r_min=("r_raw", lambda s: np.nanmin(np.abs(s)) if np.isfinite(s).any() else np.nan),
        r_max=("r_raw", lambda s: np.nanmax(np.abs(s)) if np.isfinite(s).any() else np.nan),
    )
    g["stable"] = g["logs"] == n_logs
    return g.sort_values(["stable", "covered", "can_id", "start"], ascending=[False, True, True, True])


def self_test():
    """Synthetische Botschaft: 16-Bit-Rampe, 4-Bit-Zaehler, konstantes Byte, Flag."""
    n = 2000
    ramp = (np.arange(n) * 37) % 65536  # Schrittweite so gewaehlt, dass alle 16 Bits genutzt werden
    ctr = np.arange(n) % 16
    flag = (np.arange(n) // 200) % 2
    payloads = []
    for i in range(n):
        b = bytearray(8)
        b[0] = (int(ramp[i]) >> 8) & 0xFF
        b[1] = int(ramp[i]) & 0xFF
        b[2] = 0xA5                      # konstant
        b[3] = (int(ctr[i]) << 4) | (int(flag[i]) << 3)
        payloads.append(bytes(b))
    bits = bit_matrix(payloads)
    rates = flip_rates(bits)
    fields = segment(rates)
    starts = {(s, l) for s, l in fields}
    assert (0, 16) in starts, f"16-Bit-Rampe nicht als ein Feld erkannt: {fields}"
    # konstantes Byte 2 muss in einem CONST-Feld liegen
    assert (16, 8) in starts, f"konstantes Byte nicht sauber abgegrenzt: {fields}"
    assert classify(field_values(bits, 16, 8), rates, 16, 8) == "CONST", "konstantes Byte nicht als CONST erkannt"
    ctr_field = [(s, l) for s, l in fields if s == 24]
    assert ctr_field, f"Zaehler nicht an Bit 24 abgegrenzt: {fields}"
    s, l = ctr_field[0]
    assert classify(field_values(bits, s, l), rates, s, l) == "COUNTER", "Zaehler nicht klassifiziert"

    # Signedness-Test: kleine positive und kleine negative Werte, unsigned gelesen
    sv = np.where(np.arange(1000) % 2, np.arange(1000) % 50, 65536 - (np.arange(1000) % 50) - 1)
    assert signedness_suspect(sv, 16), "signed-Verdacht nicht erkannt"
    assert not signedness_suspect((np.arange(1000) * 60) % 65536, 16), "Falschalarm bei Rampe"
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="append")
    ap.add_argument("--id", help="nur diese CAN-ID, z.B. 0x415")
    ap.add_argument("--min-bytes", type=int, default=2_000_000,
                    help="nur Logs ab dieser Dateigroesse")
    ap.add_argument("--correlate", action="store_true",
                    help="gefundene Felder zusaetzlich gegen die validierten Anker korrelieren")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    only_id = int(args.id, 0) if args.id else None
    logs = args.log or [p for p in sorted(glob.glob("data/can/candump-*.log"))
                        if os.path.getsize(p) >= args.min_bytes]
    our_db = load_db("hscan")

    frames = [analyze_log(p, our_db, only_id, correlate=args.correlate) for p in logs]
    df = pd.concat([f for f in frames if len(f)], ignore_index=True)
    os.makedirs("results", exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    cons = consolidate(df)
    cons.to_csv(OUT_CSV.replace(".csv", "_consolidated.csv"), index=False)

    print(f"\n{len(df)} Feld-Log-Paare -> {OUT_CSV}")
    print(f"{len(cons)} eindeutige Felder -> {OUT_CSV.replace('.csv', '_consolidated.csv')}")

    stable = cons[cons["stable"]]
    new = stable[(~stable["covered"]) & (stable["kind"].isin(["PHYSICAL", "FLAG"]))]
    print(f"\n=== {len(new)} stabile, UNBELEGTE Felder (PHYSICAL/FLAG) ===")
    with pd.option_context("display.width", 200, "display.max_rows", 300):
        cols = ["can_id", "start", "length", "kind", "n_unique_med", "vmin", "vmax",
                "flip_max", "signed_suspect"]
        if args.correlate:
            cols += ["anchor", "hit_logs", "r_min", "r_max"]
        print(new[cols].to_string(index=False))

    if args.correlate:
        hits = cons[(~cons["covered"]) & (cons["hit_logs"] > 0)]
        print(f"\n=== {len(hits)} unbelegte Felder mit Anker-Treffer ===")
        with pd.option_context("display.width", 210, "display.max_rows", 300):
            print(hits[["can_id", "start", "length", "kind", "logs", "hit_logs", "anchor",
                        "r_min", "r_max", "vmin", "vmax"]]
                  .sort_values(["hit_logs", "r_min"], ascending=False).to_string(index=False))

    susp = cons[cons["signed_suspect"] & cons["covered"]]
    if len(susp):
        print(f"\n=== {len(susp)} BELEGTE Felder mit signed/unsigned-Verdacht ===")
        print(susp[["can_id", "start", "length", "known_signals", "vmin", "vmax"]].to_string(index=False))


if __name__ == "__main__":
    main()
