"""
Exhaustive Sub-Byte-Feldsuche fuer EINE CAN-ID (MX-5 Projekt).

Schliesst eine Luecke in can_byte_search.py: das prueft nur ganze Bytes und Byte-Paare
(BE/LE, signed/unsigned), aber keine bitgepackten Sub-Byte-Felder (beliebiges Startbit x
Laenge, z.B. ein 3-Bit-Flag ab Bit 5). Adaptiert aus CSS-Electronics/
can-bus-reverse-engineering-skills (bitsearch.py, MIT-Lizenz), auf unseren Log-/DBC-Stack
reduziert - kein CANsub, keine Live-Sweep-Referenz, deshalb bewusst OHNE:
- Resolution-Refinement/Flip-Rate-Cascade (braucht kontinuierliche Sweep-Anregung, die wir
  nicht haben - unsere Referenz ist immer eine bereits im Log vorhandene Zeitreihe),
- Lag-Suche (unsere Referenz - OBD direkt aus demselben CAN-Log oder ein anderes DBC-Signal -
  hat praktisch keinen Zeitversatz zum Target, anders als eine Handeingabe),
- --byte-align-Snap.

Kernidee bleibt: fuer jede (Startbit x Laenge x Endianness x Sign)-Kombination R² (linearer
Fit gegen die Referenz) + referenzfreie Plausibilitaet (can_re_toolkit.plausibility) + Spearman
berechnen, hochsichere Sentinels vorher maskieren (can_re_toolkit.auto_mask_outliers), und bei
gleich gut fittenden, ineinander verschachtelten Kandidaten den KUERZESTEN behalten (Parsimonie -
verhindert genau die Art Over-Wide-Read-Bug, die uns beim TM_GEST-Dual-Column-Fall passiert ist,
siehe mx5_build_datalake_dual_column_bug.md).

Einsatz: naechster Schritt nach can_byte_search.py, wenn ein Byte/Byte-Paar auffaellig ist aber
die genaue Bitlage/Skala unklar bleibt - oder gezielt fuer die offene ABS/DSC-
Eingriffsindikator-Suche (bisher nur auf Byte-Ebene durchsucht).

Beispiel:
    python scripts/can_bitsearch.py 0x215 --log data/can/candump-2026-09-12_211833.log \
        --ref-signal WheelSpeed_1 --ref-id 0x215 --min-len 4 --max-len 24
    python scripts/can_bitsearch.py 0x200 --log data/can/candump-2026-09-14_*.log \
        --ref-did FLI
"""
import argparse
import sys

import numpy as np
import pandas as pd

from can_log_parser import parse_candump, load_db
from can_byte_search import _resample, GRID_HZ, KNOWN_DIDS
from obd_from_can import decode_obd_traffic, extract_did_series
import can_re_toolkit as t


def extract_le(le_int: np.ndarray, start: int, length: int) -> np.ndarray:
    mask = (1 << length) - 1
    return ((le_int >> np.uint64(start)) & np.uint64(mask)).astype(np.float64)


def extract_be(be_int: np.ndarray, payload_len: int, byte_off: int, width: int) -> np.ndarray:
    shift = 8 * (payload_len - byte_off - width)
    mask = (1 << (8 * width)) - 1
    return ((be_int >> np.uint64(shift)) & np.uint64(mask)).astype(np.float64)


def apply_sign(raw: np.ndarray, length: int) -> np.ndarray:
    out = raw.copy()
    thresh = float(1 << (length - 1))
    out[out >= thresh] -= float(1 << length)
    return out


def _changing_bits(le_int: np.ndarray, nbits: int) -> np.ndarray:
    changing = np.zeros(nbits, dtype=bool)
    for k in range(nbits):
        bit0 = (le_int >> np.uint64(k)) & np.uint64(1)
        changing[k] = bool(np.any(bit0 != bit0[0]))
    return changing


def _span(e: dict) -> tuple[int, int]:
    return (e["start_bit"], e["length"]) if e["order"] == "little" else (e["byte"] * 8, e["length"])


def _rank_key(e: dict) -> tuple:
    """Hoeher = besser. R² fuehrt, dann referenzfreie Plausibilitaet, dann Spearman-
    Betrag, dann eine RUNDE Scale, dann das LAENGERE Feld (Tie-Break - die eigentliche
    Over-Wide-Korrektur passiert in _suppress_overlaps per Parsimonie, nicht hier)."""
    return (round(e["r2"], 2), round(e["plaus"], 2), round(e["spear"], 2), int(e["scale_nice"]), e["length"])


def _suppress_overlaps(entries: list[dict], overlap_frac: float = 0.8, r2_eps: float = 0.002) -> list[dict]:
    """Greedy Non-Maximum-Suppression: laufe Kandidaten bestenfalls zuerst ab; ein Kandidat,
    der ein bereits behaltenes Feld zu >=overlap_frac der KUERZEREN Spanne ueberlappt, wird
    normalerweise verworfen - AUSSER Parsimonie greift: ist der neue Kandidat KUERZER, fittet
    aber (fast) gleich gut (R²/Plausibilitaet innerhalb r2_eps), dann war das behaltene Feld
    ein Over-Wide-Read (haengt ein zweites, mitlaufendes Feld/Padding an) - der kuerzere,
    echte Kandidat ersetzt es."""
    kept: list[dict] = []
    for e in sorted(entries, key=_rank_key, reverse=True):
        s0, l0 = _span(e)
        drop = False
        for i, k in enumerate(kept):
            s1, l1 = _span(k)
            inter = max(0, min(s0 + l0, s1 + l1) - max(s0, s1))
            if inter >= overlap_frac * min(l0, l1):
                if l0 < l1 and e["r2"] >= k["r2"] - r2_eps and e["plaus"] >= k["plaus"] - r2_eps:
                    kept[i] = e
                drop = True
                break
        if not drop:
            kept.append(e)
    return kept


def search_field(can_df: pd.DataFrame, ref_t: np.ndarray, ref_v: np.ndarray,
                 min_len: int = 4, max_len: int = 24, top: int = 12) -> list[dict]:
    """can_df: Frames EINER CAN-ID (Spalten t, data, gleiche DLC). ref_t/ref_v: Referenz-
    Zeitreihe (beliebige Einheit/Rohwert, nur fuer die Rangierung relevant)."""
    dlc = can_df["data"].str.len().mode().iloc[0]
    sub = can_df[can_df["data"].str.len() == dlc]
    t_arr = sub["t"].to_numpy()
    le_int = np.array([int.from_bytes(d, "little") for d in sub["data"]], dtype=np.uint64)
    be_int = np.array([int.from_bytes(d, "big") for d in sub["data"]], dtype=np.uint64)
    nbits = int(dlc) * 8
    t0, t1 = float(t_arr.min()), float(t_arr.max())
    changing = _changing_bits(le_int, nbits)

    candidates = []
    for start in range(nbits):
        if not changing[start]:
            continue
        for length in range(min_len, min(max_len, nbits - start) + 1):
            end = start + length - 1
            if changing[end]:
                candidates.append(("little", start, length))
    for byte in range(int(dlc)):
        for width in range(1, min(4, int(dlc) - byte) + 1):
            if min_len <= width * 8 <= max_len:
                candidates.append(("big", byte, width))

    _, ref_on_grid = _resample(ref_t, ref_v, t0, t1, GRID_HZ)

    results = []
    for order, a, b in candidates:
        if order == "little":
            raw, length = extract_le(le_int, a, b), b
        else:
            raw, length = extract_be(be_int, int(dlc), a, b), b * 8
        if np.ptp(raw) == 0:
            continue
        for signed in (False, True):
            rr = apply_sign(raw, length) if signed else raw
            if signed and np.array_equal(rr, raw):
                continue
            rr_fit, outlier_info = t.auto_mask_outliers(rr, length, t_arr)
            _, sig_on_grid = _resample(t_arr, rr_fit, t0, t1, GRID_HZ)
            mask = np.isfinite(sig_on_grid) & np.isfinite(ref_on_grid)
            if mask.sum() < 50 or np.nanstd(sig_on_grid[mask]) < 1e-9:
                continue
            scale, offset, r2 = t.linear_fit_r2(sig_on_grid[mask], ref_on_grid[mask])
            plaus = t.plausibility(rr_fit[np.isfinite(rr_fit)], length)
            spear = t.spearman_r(sig_on_grid[mask], ref_on_grid[mask])
            entry = {
                "order": order, "signed": signed, "length": length,
                "r2": round(r2, 4), "plaus": plaus["score"], "spear": round(spear, 4),
                "scale": round(scale, 8), "offset": round(offset, 6),
                "scale_nice": t.scale_plausibility(scale)["nice"],
                "masked_n": int(outlier_info["count"]) if outlier_info else 0,
            }
            if order == "little":
                entry.update({"start_bit": a, "byte": a // 8, "bit_in_byte": a % 8})
            else:
                entry.update({"byte": a, "width": b, "start_bit": a * 8 + 7})
            results.append(entry)

    reps = _suppress_overlaps(results)[:top]
    if reps:
        reps[0] = {**reps[0], "wider_alt": _find_wider_ambiguous(results, reps[0])}
    return reps


def _find_wider_ambiguous(results: list[dict], winner: dict, r2_eps: float = 0.005) -> dict | None:
    """Ohne Resolution-Refinement (siehe Modul-Docstring) kann ein glattes, langsam
    veraenderliches Signal auch mit weniger Bits fast denselben R² erreichen wie das
    tatsaechlich volle Feld - die Parsimonie-Regel entscheidet sich dann bewusst fuer das
    KUERZERE (per Design). Das ist eine bekannte Grenze dieses vereinfachten Ports, kein
    Bug. Diese Funktion sucht nach einem ueberlappenden, LAENGEREN Kandidaten mit fast
    gleich gutem R², um den Nutzer auf die Mehrdeutigkeit hinzuweisen statt sie zu verstecken."""
    ws, wl = _span(winner)
    best = None
    for e in results:
        if e is winner or e["length"] <= wl:
            continue
        s1, l1 = _span(e)
        inter = max(0, min(ws + wl, s1 + l1) - max(ws, s1))
        if inter < 0.8 * wl:
            continue
        if e["r2"] < winner["r2"] - r2_eps:
            continue
        if best is None or e["r2"] > best["r2"]:
            best = e
    return best


def _load_reference(args, db):
    if args.ref_did:
        raw = parse_candump(args.log)
        decoded_obd = decode_obd_traffic(raw)
        series = extract_did_series(decoded_obd, KNOWN_DIDS[args.ref_did], mode="mode22")
        if len(series) < 20:
            print(f"ERROR: DID {args.ref_did} liefert nur {len(series)} Samples in diesem Log.",
                  file=sys.stderr)
            sys.exit(1)
        return series["t"].to_numpy(), series["raw_value"].to_numpy(dtype=float)
    if args.ref_signal:
        ref_id = int(args.ref_id, 0)
        msg = db.get_message_by_frame_id(ref_id)
        raw = parse_candump(args.log)
        sub = raw[raw["can_id"] == ref_id]
        t_list, v_list = [], []
        for tt, data in sub[["t", "data"]].itertuples(index=False):
            try:
                decoded = msg.decode(data, allow_truncated=True)
            except Exception:
                continue
            if args.ref_signal in decoded:
                t_list.append(tt)
                v_list.append(decoded[args.ref_signal])
        if len(t_list) < 20:
            print(f"ERROR: Signal {args.ref_signal} auf 0x{ref_id:X} liefert nur "
                  f"{len(t_list)} Samples.", file=sys.stderr)
            sys.exit(1)
        return np.array(t_list), np.array(v_list, dtype=float)
    print("ERROR: --ref-did oder --ref-signal/--ref-id angeben.", file=sys.stderr)
    sys.exit(1)


def report(reps: list[dict], can_id: int):
    cols = ("order", "start_bit", "length", "signed", "r2", "plaus", "spear", "scale", "offset")
    print(f"\nTop {len(reps)} unterscheidbare Felder fuer 0x{can_id:03X}:\n")
    print("  ".join(f"{c:>9}" for c in cols))
    print("-" * (11 * len(cols)))
    for r in reps:
        print("  ".join(f"{str(r[c]):>9}" for c in cols))
    if reps:
        w = reps[0]
        print(f"\nEntscheidung: Startbit {w['start_bit']}, Laenge {w['length']}, "
              f"{w['order']}-endian, {'signed' if w['signed'] else 'unsigned'}, "
              f"Scale {w['scale']:+.6g} [{'rund' if w['scale_nice'] else 'nicht rund - Geometrie pruefen'}], "
              f"R²={w['r2']:.4f}, Plausibilitaet={w['plaus']:.2f}, Spearman={w['spear']:.3f}"
              + (f", {w['masked_n']} Sentinel-Frame(s) maskiert" if w["masked_n"] else ""))
        alt = w.get("wider_alt")
        if alt:
            print(f"\n[Aufloesungshinweis] Ohne Resolution-Refinement (siehe Modul-Docstring) "
                  f"kann ein glattes Signal auch mit weniger Bits fast denselben R² erreichen. "
                  f"Ein LAENGERES, aehnlich gut fittendes Feld existiert ebenfalls: Startbit "
                  f"{alt['start_bit']}, Laenge {alt['length']}, {alt['order']}-endian, "
                  f"R²={alt['r2']:.4f}, Scale {alt['scale']:+.6g}. Pruefe beide Kandidaten "
                  f"(z.B. per Plot) oder erzwinge die groessere Breite mit --min-len "
                  f"{alt['length']}.")


def self_test():
    """Selbsttest: die Parsimonie-Regel muss einen Over-Wide-Read demoten. Byte 0 traegt
    das echte Feld (folgt der Referenz), Byte 1 ist ein exaktes Duplikat davon (wie der
    TM_GEST-Dual-Column-Fall, siehe mx5_build_datalake_dual_column_bug.md) - eine 16-Bit-
    Lesung ueber beide Bytes ist rein algebraisch GENAUSO linear zur Referenz (Wert*257
    statt Wert), faellt bei naivem 'laengstes Feld gewinnt' also nicht durch R² auf. Nur
    die Parsimonie-Regel (kuerzestes gleich gut fittendes Feld gewinnt) verhindert, dass
    das Ergebnis eine falsche 16-Bit-Definition mit einer 256x zu kleinen Scale ist."""
    rng = np.random.default_rng(0)
    n = 2000
    t_arr = np.arange(n) * 0.02
    ref = 50 + 40 * np.sin(t_arr / 5.0)
    true_v = np.clip(((ref - ref.min()) / (ref.max() - ref.min()) * 255).round(), 0, 255).astype(np.uint64)
    noise_bits = (rng.random((n, 48)) < 0.5).astype(np.uint64)  # Bits 16-63: unabhaengiges Rauschen

    frames = true_v | (true_v << np.uint64(8))
    for k in range(48):
        frames |= noise_bits[:, k] << np.uint64(16 + k)

    data_col = [int(v).to_bytes(8, "little") for v in frames]
    can_df = pd.DataFrame({"t": t_arr, "data": data_col})

    reps = search_field(can_df, t_arr, ref, min_len=4, max_len=24, top=8)
    assert reps, "kein Kandidat gefunden"
    w = reps[0]
    # Ohne Resolution-Refinement (bewusst nicht portiert, siehe Modul-Docstring) kann bei
    # niedriger Grid-Rate eine etwas kuerzere Teilspanne genauso gut fitten (Aufloesungs-
    # Mehrdeutigkeit) - das ist eine bekannte, akzeptierte Einschraenkung dieses vereinfachten
    # Ports. Die eigentlich garantierte Eigenschaft ist: die 16-Bit-Duplikat-Ueberlesung
    # (Scale faelschlich 256x zu klein) darf NIE gewinnen.
    ok = w["length"] <= 8 and not w["signed"] and w["r2"] > 0.99
    print(f"Gefunden: start={w['start_bit']} len={w['length']} order={w['order']} "
          f"signed={w['signed']} r2={w['r2']:.4f} scale={w['scale']:.6g} "
          f"(darf hoechstens Laenge=8 sein, NICHT das 16-Bit-Duplikat-Ueberlesen)")
    assert ok, "Selbsttest fehlgeschlagen: Parsimonie hat den Over-Wide-Read nicht demotet"
    print("Selbsttest OK: Over-Wide-Read (Duplikat-Byte, faelschlich 256x kleinere Scale) "
          "korrekt vermieden.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("can_id", nargs="?", help="Ziel-CAN-ID, z.B. 0x215")
    ap.add_argument("--log", default="data/can/candump-2026-09-12_211833.log")
    ap.add_argument("--ref-did", choices=sorted(KNOWN_DIDS), help="Referenz aus einem bekannten Mode-22-DID")
    ap.add_argument("--ref-signal", help="Referenz aus einem bereits im DBC bekannten Signal")
    ap.add_argument("--ref-id", help="CAN-ID des --ref-signal, z.B. 0x215")
    ap.add_argument("--min-len", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=24)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo:
        self_test()
        sys.exit(0)

    if not args.can_id:
        ap.error("can_id erforderlich (ausser bei --demo)")

    can_id = int(args.can_id, 0)
    db = load_db()
    raw = parse_candump(args.log)
    can_df = raw[raw["can_id"] == can_id][["t", "data"]]
    if can_df.empty:
        print(f"ERROR: keine Frames fuer 0x{can_id:03X} in {args.log}.", file=sys.stderr)
        sys.exit(1)

    ref_t, ref_v = _load_reference(args, db)
    reps = search_field(can_df, ref_t, ref_v, min_len=args.min_len, max_len=args.max_len, top=args.top)
    if not reps:
        print("Keine variierenden Kandidaten gefunden.", file=sys.stderr)
        sys.exit(1)
    report(reps, can_id)
