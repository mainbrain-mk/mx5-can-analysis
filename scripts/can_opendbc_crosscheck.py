"""
Abgleich fremder DBC-Definitionen (comma.ai/opendbc `mazda_2017.dbc`) gegen unsere Logs.

Hintergrund: opendbc pflegt eine DBC fuer Mazda CX-5/Mazda3 ab MY2017. Diese Fahrzeuge
sitzen auf derselben SkyActiv-Busarchitektur wie unser MX-5 ND - 84 von 102 CAN-IDs sind
identisch, inklusive vieler Botschaften, die in unserer eigenen DBC noch komplett leer sind
(0x415 TRACTION, 0x217 CURVE_CTRS, 0x21F CRZ_EVENTS, 0x340 SEATBELT, ...).

Dieses Skript uebernimmt NICHTS blind (siehe die Lehre aus der gitgc-Quelle, 2026-09-15:
eine Fremdformel war goldrichtig, eine andere fuer unser Fahrzeug unbrauchbar). Es dekodiert
jedes Fremdsignal aus unseren eigenen Logs und bewertet es:

1. Kommt die Botschaft in unseren Logs ueberhaupt vor?
2. Variiert das Signal, oder ist es konstant / ein Rollzaehler?
3. Korreliert es mit einem unserer bereits validierten Anker (Drehzahl, Speed, Gas, IMU,
   Bremsdruck, Lenkwinkel, ...)? Pearson+Spearman, roh UND detrended - dieselbe Doppel-
   schwelle wie can_byte_search.py, gegen Trend-Scheinkorrelationen.
4. Ueberlappt es bitweise mit einem Signal, das wir schon haben (-> Bestaetigung oder
   Widerspruch), oder ist es echtes Neuland?

Aufruf:
    .venv/bin/python scripts/can_opendbc_crosscheck.py                  # alle reichhaltigen Logs
    .venv/bin/python scripts/can_opendbc_crosscheck.py --log <pfad>     # einzelnes Log
    .venv/bin/python scripts/can_opendbc_crosscheck.py --self-test

Die Fremd-DBC liegt als `data/can/external/opendbc_mazda_2017.dbc` (CC-BY? -> opendbc ist
MIT-lizenziert, Herkunft im Kopf der Datei vermerkt). Sie wurde beim Import minimal
gepatcht, damit cantools sie laedt (Message-/Signalnamen duerfen nicht mit einer Ziffer
beginnen, VAL_-Tabellen ohne Semikolon entfernt) - keine inhaltliche Aenderung.
"""
import argparse
import glob
import logging
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cantools

from can_log_parser import parse_candump, load_db
from can_byte_search import (
    extract_anchors,
    prepare_anchors_for_window,
    correlate_candidate,
    is_rolling_counter,
)
from obd_from_can import decode_obd_traffic

EXTERNAL_DBC = "data/can/external/opendbc_mazda_2017.dbc"
OUT_CSV = "results/can_opendbc_crosscheck.csv"

# Botschaften, die wir gar nicht erst pruefen: Radar/MRCC gibt es in unserem Fahrzeug nicht
# (0x361-0x366 tauchen in keinem Log auf), und reine Zaehler/Checksummen-Signalnamen sind
# per Definition keine Messwerte.
COUNTER_NAME_HINTS = ("CTR", "CHK", "COUNTER", "CHECKSUM", "ALIVE")


def load_external_db(path=EXTERNAL_DBC):
    logging.disable(logging.WARNING)
    db = cantools.database.load_file(path, strict=False)
    logging.disable(logging.NOTSET)
    return db


def our_signal_bits(db):
    """{can_id: {bitindex: signalname}} fuer unsere eigene DBC - um zu erkennen, ob ein
    Fremdsignal auf bekanntem oder auf jungfraeulichem Gebiet sitzt."""
    out = {}
    for msg in db.messages:
        bits = {}
        for sig in msg.signals:
            for b in signal_bit_indices(sig):
                bits[b] = sig.name
        out[msg.frame_id] = bits
    return out


def signal_bit_indices(sig):
    """Absolute Bitindizes (0..63, MSB-first ueber die 8 Bytes, dieselbe Konvention wie
    can_field_segmentation.py::bit_matrix()/np.unpackbits) eines cantools-Signals.

    Big-Endian (Motorola/DBC "@0"): DBC-eigene Startbit-Zaehlung laeuft byteweise gespiegelt
    zur sequenziellen MSB-first-Zaehlung (Bit 7..0 = Byte0, Bit 15..8 = Byte1, ...) - fuer
    einen vollen Byte- oder Byte-Paar-umfassenden Bereich stimmen beide Zaehlungen als MENGE
    zufaellig ueberein, bei einem Feld, das NICHT bytegrenz-ausgerichtet ueber eine Bytegrenze
    laeuft (z.B. 9 Bit ab Bit 49), driften sie auseinander (2026-09-20 gefunden: fuehrte dazu,
    dass unsere eigene "covered"-Pruefung fuer so ein Feld beim Feld-Clustering die FALSCHEN
    zwei Bits als belegt gemeldet haette). Deshalb hier zweistufig: erst die DBC-eigene
    Bitfolge ablaufen (Abwaertszaehlung + Sprung +15 an der Byte-Grenze, das ist die Reihen-
    folge, die cantools beim Dekodieren tatsaechlich nutzt), dann JEDE einzelne DBC-Bitnummer
    in die sequenzielle Nummer zurueckrechnen (dieselbe Formel ist ihre eigene Umkehrung:
    seq = 8*(dbc//8) + (7 - dbc%8))."""
    idx = []
    if sig.byte_order == "big_endian":
        pos = sig.start
        for _ in range(sig.length):
            idx.append(8 * (pos // 8) + (7 - pos % 8))
            if pos % 8 == 0:
                pos += 15
            else:
                pos -= 1
    else:
        for i in range(sig.length):
            idx.append(sig.start + i)
    return [b for b in idx if 0 <= b < 64]


def decode_signal_series(raw_df, ext_msg, sig_name):
    """(t, v) eines einzelnen Fremdsignals aus den Rohframes."""
    sub = raw_df[raw_df["can_id"] == ext_msg.frame_id]
    if sub.empty:
        return None
    t_list, v_list = [], []
    for t, data in sub[["t", "data"]].itertuples(index=False):
        try:
            dec = ext_msg.decode(data, allow_truncated=True, decode_choices=False)
        except Exception:
            continue
        v = dec.get(sig_name)
        if v is None:
            continue
        t_list.append(t)
        v_list.append(float(v))
    if len(t_list) < 50:
        return None
    return np.array(t_list), np.array(v_list, dtype=float)


def check_log(can_path, ext_db, our_db, verbose=True):
    raw_df = parse_candump(can_path)
    present_ids = set(raw_df["can_id"].unique())

    decoded_obd = decode_obd_traffic(raw_df)
    has_obd = decoded_obd is not None and len(decoded_obd) > 0
    anchors = extract_anchors(raw_df, our_db, decoded_obd, has_obd)
    if verbose:
        print(f"  {os.path.basename(can_path)}: {len(raw_df)} Frames, "
              f"{len(present_ids)} IDs, {len(anchors)} Anker, OBD={has_obd}")

    our_bits = our_signal_bits(our_db)
    rows = []
    for ext_msg in ext_db.messages:
        if ext_msg.frame_id not in present_ids:
            continue
        sub_t = raw_df.loc[raw_df["can_id"] == ext_msg.frame_id, "t"]
        t0, t1 = float(sub_t.min()), float(sub_t.max())
        if t1 - t0 < 30:
            continue
        prepared = prepare_anchors_for_window(anchors, t0, t1)

        for sig in ext_msg.signals:
            series = decode_signal_series(raw_df, ext_msg, sig.name)
            if series is None:
                continue
            t, v = series
            n_uniq = len(np.unique(v))
            known = our_bits.get(ext_msg.frame_id, {})
            overlap = sorted({known[b] for b in signal_bit_indices(sig) if b in known})

            rec = dict(
                log=os.path.basename(can_path),
                can_id=f"0x{ext_msg.frame_id:03X}",
                ext_msg=ext_msg.name,
                ext_sig=sig.name,
                start=sig.start, length=sig.length,
                byte_order=sig.byte_order, signed=sig.is_signed,
                scale=float(sig.scale), offset=float(sig.offset),
                unit=sig.unit or "",
                n=len(v), n_unique=n_uniq,
                vmin=float(np.min(v)), vmax=float(np.max(v)),
                vstd=float(np.std(v)),
                is_counter=bool(is_rolling_counter(v)),
                name_says_counter=any(h in sig.name.upper() for h in COUNTER_NAME_HINTS),
                overlaps_ours=";".join(overlap),
                anchor="", method="", r_raw=np.nan, r_detrend=np.nan,
            )
            if n_uniq > 2 and not rec["is_counter"]:
                hit = correlate_candidate(t, v, t0, t1, prepared)
                if hit:
                    rec["anchor"], rec["method"], rec["r_raw"], rec["r_detrend"] = hit
            rows.append(rec)
    return pd.DataFrame(rows)


def consolidate(df):
    """Pro (can_id, ext_sig) ueber alle Logs zusammenfassen - cross-log-Stabilitaet ist in
    diesem Projekt das entscheidende Kriterium (siehe die verworfenen Kandidaten 0x0FD/0x20A,
    deren Gewinner-Bitlage zwischen Logs wechselte)."""
    g = df.groupby(["can_id", "ext_msg", "ext_sig"], as_index=False).agg(
        logs=("log", "nunique"),
        n_unique_med=("n_unique", "median"),
        vmin=("vmin", "min"), vmax=("vmax", "max"),
        counter_logs=("is_counter", "sum"),
        overlaps_ours=("overlaps_ours", lambda s: ";".join(sorted({x for x in s if x}))),
        anchor=("anchor", lambda s: s.mode().iat[0] if len(s.mode()) else ""),
        r_min=("r_raw", lambda s: np.nanmin(np.abs(s)) if np.isfinite(s).any() else np.nan),
        r_max=("r_raw", lambda s: np.nanmax(np.abs(s)) if np.isfinite(s).any() else np.nan),
        hit_logs=("anchor", lambda s: int((s != "").sum())),
        unit=("unit", "first"),
        scale=("scale", "first"), offset=("offset", "first"),
        start=("start", "first"), length=("length", "first"),
        byte_order=("byte_order", "first"), signed=("signed", "first"),
    )
    return g.sort_values(["hit_logs", "r_min"], ascending=False)


def self_test():
    """Bitindex-Berechnung gegen ein Signal mit bekannter Lage pruefen."""
    db = load_external_db()
    msg = db.get_message_by_frame_id(0x78)
    sig = {s.name: s for s in msg.signals}["CTR"]
    bits = signal_bit_indices(sig)
    # CTR: 55|8@0+ -> big-endian ab Bit 55 = genau Byte 6 (Bits 48..55)
    assert sorted(bits) == list(range(48, 56)), sorted(bits)

    sig = {s.name: s for s in db.get_message_by_frame_id(0x415).signals}["CTR3"]
    bits = signal_bit_indices(sig)  # 8|4@1+ little-endian -> Bits 8..11
    assert sorted(bits) == [8, 9, 10, 11], sorted(bits)

    # Regressionstest 2026-09-20: ein big-endian-Feld, das NICHT byte-ausgerichtet ist (auch
    # ohne Bytegrenzenkreuzung), driftet ohne die seq<->dbc-Rueckrechnung auseinander - hier
    # gegen unsere eigene DBC + echten Log per cantools-Dekodierung ground-truth-geprueft
    # (MT_Gear_Actual: DBC "19|3@0+" dekodiert tatsaechlich Bits 20-22, NICHT 17-19, siehe
    # docs/logs/can-bus-status.md "Bit-Indizierungs-Bug ...").
    hs_db = load_db("hscan")
    sig = {s.name: s for s in hs_db.get_message_by_frame_id(0xFD).signals}["MT_Gear_Actual"]
    assert sorted(signal_bit_indices(sig)) == [20, 21, 22], sorted(signal_bit_indices(sig))

    # dasselbe fuer ein Feld, das eine Bytegrenze NICHT ausgerichtet ueberquert (der urspruenglich
    # gefundene Fall: SteeringWheelSpeed_related, 9 Bit ab DBC-Bit 54, quert Byte6/Byte7).
    sig = {s.name: s for s in hs_db.get_message_by_frame_id(0x082).signals}["SteeringWheelSpeed_related"]
    assert sorted(signal_bit_indices(sig)) == list(range(49, 58)), sorted(signal_bit_indices(sig))
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="append", help="einzelnes candump-Log (mehrfach moeglich)")
    ap.add_argument("--min-frames", type=int, default=50000,
                    help="nur Logs ab dieser Framezahl (Default: nur reichhaltige Logs)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    logs = args.log or sorted(glob.glob("data/can/candump-*.log"))
    ext_db = load_external_db()
    our_db = load_db("hscan")

    frames = []
    for path in logs:
        if not args.log and os.path.getsize(path) < args.min_frames * 40:
            continue
        try:
            frames.append(check_log(path, ext_db, our_db))
        except Exception as exc:  # ein kaputtes Log darf den Lauf nicht killen
            print(f"  {os.path.basename(path)}: FEHLER {exc}")
    if not frames:
        print("keine Logs verarbeitet")
        return

    df = pd.concat(frames, ignore_index=True)
    os.makedirs("results", exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    cons = consolidate(df)
    cons.to_csv(OUT_CSV.replace(".csv", "_consolidated.csv"), index=False)

    print(f"\n{len(df)} Signal-Log-Paare -> {OUT_CSV}")
    print(f"{len(cons)} eindeutige Fremdsignale -> {OUT_CSV.replace('.csv', '_consolidated.csv')}")
    hits = cons[cons["hit_logs"] > 0]
    print(f"\n=== {len(hits)} Fremdsignale mit Anker-Treffer (cross-log) ===")
    with pd.option_context("display.width", 200, "display.max_columns", 30, "display.max_rows", 200):
        print(hits[["can_id", "ext_msg", "ext_sig", "logs", "hit_logs", "anchor",
                    "r_min", "r_max", "overlaps_ours", "unit"]].to_string(index=False))


if __name__ == "__main__":
    main()
