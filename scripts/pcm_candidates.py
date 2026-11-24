"""
PCM-Kampagne, Kandidatensuche: offene Felder der PCM-Botschaften gegen die Diagnosewerte, die im
selben candump stehen (docs/plans/pcm-bits-plan.md, Beweisregeln 2-4). Liefert nur *Kandidaten*;
Hypothese/bestaetigt entscheidet die Pruefung in der Aufgabe process-new-mx5-logs.

Anker: jede positive Diagnoseantwort im Log (Mode 22 und Mode 01, Kampagne UND Poller), zerlegt in
Teilwerte: jedes Byte (B<k>), jedes Big-Endian-Wort (W<k>), bei 3-4 Byte der Gesamtwert (ALL) und
bei Antworten bis 8 Byte jedes variierende Einzelbit (b<k>.<p>, nur gegen 1-2-Bit-Felder).
Genutzt werden Teilwerte mit >= MIN_N Abtastungen und mindestens zwei Werten.

Felder: je PCM-Botschaft (DBC-Absender HS_PCM, Register H-A1) die offenen Bits laut Feldinventur
(results/can_open_fields.csv, in >= 3 Logs variierend) plus die Bits unbestaetigter Signale
(_maybe/_related/_raw), ohne bestaetigte DBC-Signale; fest ueber alle Logs, als zusammenhaengende
Laeufe in Motorola-Reihenfolge; daraus jedes Teilfeld (Startbit x Laenge 1..16).

Vergleich je (Teilfeld, Teilwert), Feldwert = letzter Frame vor Antwortzeit + Lag (max. 1 s alt):
  r_pegel  Pearson auf den Werten
  r_diff   Pearson auf den Aenderungen von Abtastung zu Abtastung (Regel 3: zwei monotone Groessen
           haben hohes r_pegel, aber r_diff ~ 0)
  exakt    Anteil Feld == (Teilwert >> s) & Maske, bestes s (Regel 2, auch abgeschnittene Kopien);
           Frame davor/danach zaehlt mit (Rechentakt rutscht gegen Sendeplan, H-A2-08)
Treffer je Log: Feld wechselt >= MIN_CHANGES-mal, und exakt >= EXACT_MIN, oder |r_pegel| >= R_LEVEL_MIN und |r_diff| >= R_DIFF_MIN und
Breite passt (Regel 4: das Feld nutzt mindestens 1/4 der Aufloesung, die Anker und Bitbreite
zulassen). Je Log: results/pcm_campaign/candidates/<log>.csv.gz (Paare mit |r_pegel| >= KEEP_R)
und <log>.json (welche offenen Bits/Teilwerte in diesem Log variierten = "pruefbar").

Ueber alle Logs (aggregate): Kandidat, wenn das Paar in JEDEM Log trifft, in dem es pruefbar war
(Regel 3), in >= 2 Logs, und der Lag ueber die Logs um <= 0,3 s streut. Verschachtelte Treffer je
(Botschaft, Teilwert): s. _parsimony (laengstes exaktes Abbild und bestes r, je ohne Ueberlappung). Dazu je DID die Variation ueber
alle Logs: ueberall konstant -> Vorschlag zum Ausmustern aus der Rotation (rotation_exclude).
Ergebnis: results/pcm_campaign/candidates_<datum>.json + candidates_latest.json.

Aufruf: .venv/bin/python scripts/pcm_candidates.py [--all | <log_id> ...] | --aggregate | --self-test
"""
import datetime
import glob
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_DIR = "results/pcm_campaign"
CAND_DIR = os.path.join(OUT_DIR, "candidates")
MIN_N = 50              # Abtastungen je Teilwert und Log
MAX_N = 4000            # mehr wird gleichmaessig ausgeduennt (Poller-Lambda hat ~120 000 je Fahrt)
MAX_LEN = 16
LAGS = (-0.5, -0.2, -0.1, 0.0, 0.1, 0.2, 0.5)
MAX_AGE_S = 1.0
KEEP_R = 0.7
EXACT_MIN = 0.95
EXACT_RANK_MIN = 0.9    # Parsimonie: fast exakte Treffer zaehlen als exakt (kurze/lueckige Logs druecken
                        # die Quote eines echten 8-Bit-Abbilds unter 0,95, das 4-Bit-Teilfeld bleibt drueber)
R_LEVEL_MIN = 0.9
R_DIFF_MIN = 0.5
MIN_CHANGES = 5         # Wertwechsel des Feldes an den Abtastungen; ein Flag mit einem Wechsel passt
                        # zu jedem monotonen Anker, der irgendwann eine Schwelle kreuzt (Oeltemp., 24.11.)
LAG_SPREAD_MAX = 0.3
UNCONFIRMED = ("_maybe", "_related", "_raw")
COLS = ["can_id", "start", "length", "anker", "teil", "breite", "lag", "n", "r_pegel", "r_diff",
        "exakt", "shift", "n_feld", "n_anker", "wechsel", "treffer"]


# --------------------------------------------------------------------------------------------
# Anker
# --------------------------------------------------------------------------------------------
def anchors(ev):
    """{anker: {'t': [n], 'teile': {teil: (werte int64[n], breite)}}} aus diag_events-Tupeln."""
    by = {}
    for t, rich, mode, ident, erg, hexp in ev:
        if rich == "resp" and erg == "pos" and ident >= 0 and hexp:
            key = f"{mode}:{ident:04X}" if mode == "22" else f"{mode}:{ident:02X}"
            by.setdefault(key, []).append((t, bytes.fromhex(hexp)))
    out = {}
    for key, rows in by.items():
        lens = [len(p) for _, p in rows]
        L = max(set(lens), key=lens.count)
        rows = [r for r in rows if len(r[1]) == L]
        if len(rows) < MIN_N or L == 0:
            continue
        if len(rows) > MAX_N:
            rows = [rows[i] for i in np.linspace(0, len(rows) - 1, MAX_N).astype(int)]
        t = np.array([r[0] for r in rows])
        P = np.array([list(r[1]) for r in rows], dtype=np.int64)
        teile = {}
        for k in range(L):
            teile[f"B{k}"] = (P[:, k], 8)
            if k + 1 < L:
                teile[f"W{k}"] = (P[:, k] * 256 + P[:, k + 1], 16)
        if L in (3, 4):
            v = np.zeros(len(P), np.int64)
            for k in range(L):
                v = v * 256 + P[:, k]
            teile["ALL"] = (v, 8 * L)
        if L <= 8:
            for k in range(L):
                for p in range(7, -1, -1):
                    teile[f"b{k}.{p}"] = ((P[:, k] >> p) & 1, 1)
        teile = {n: tv for n, tv in teile.items() if len(np.unique(tv[0])) >= 2}
        if teile:
            out[key] = {"t": t, "teile": teile}
    return out


# --------------------------------------------------------------------------------------------
# Felder
# --------------------------------------------------------------------------------------------
def pcm_open_bits(inventory="results/can_open_fields.csv", min_logs=3):
    """{can_id: set(dbc-Bits)} der offenen PCM-Bits, fest ueber alle Logs: was die Feldinventur
    (can_open_fields.py) in >= min_logs Logs variieren sah, plus die Bits unbestaetigter Signale
    (_maybe/_related/_raw), minus die Bits bestaetigter DBC-Signale (die DBC kann neuer sein).
    Fest statt je Log, damit ein Feld vergleichbar bleibt, auch wenn sein oberstes Bit in einem Log
    konstant ist."""
    import can_offline_lab as lab
    import pandas as pd
    ids, conf, unconf = [], {}, {}
    for m in lab.db().messages:
        if "HS_PCM" in (m.senders or []):
            ids.append(m.frame_id)
        for s in m.signals:
            tgt = unconf if any(u in s.name for u in UNCONFIRMED) else conf
            tgt.setdefault(m.frame_id, set()).update(
                lab.bit_order(s.start, s.length, s.byte_order == "big_endian"))
    inv = pd.read_csv(inventory)
    out = {}
    for cid in ids:
        bits = set(unconf.get(cid, set()))
        for r in inv[(inv.can_id == f"0x{cid:03X}") & (inv.logs >= min_logs)].itertuples():
            bits.update(lab.bit_order(int(r.start), int(r.length), True))
        bits -= conf.get(cid, set())
        if bits:
            out[cid] = bits
    return out


def open_runs(fr, open_bits):
    """({can_id: [[dbc-Bits MSB->LSB], ...]}, {can_id: [im Log variierende offene Bits]})."""
    import can_offline_lab as lab
    from can_open_fields import segments
    from can_rare_bits import dbc_bits
    runs, var = {}, {}
    for cid, bits in open_bits.items():
        if cid not in fr or len(fr[cid][0]) < 100:
            continue
        m = dbc_bits(fr[cid][1]).mean(axis=0)
        v = sorted(b for b in bits if 0 < m[b] < 1)
        if v:
            runs[cid] = [lab.bit_order(st, ln, True) for st, ln in segments(bits)]
            var[cid] = v
    return runs, var


def _subfields(Bs, runs):
    """[(start, laenge, werte[n])] aller Teilfelder aus der Bitmatrix an den Abtastzeitpunkten."""
    out = []
    for run in runs:
        for i in range(len(run)):
            v = np.zeros(len(Bs), np.int64)
            for ln in range(1, min(MAX_LEN, len(run) - i) + 1):
                v = v * 2 + Bs[:, run[i + ln - 1]]
                out.append((run[i], ln, v.copy()))
    return out


# --------------------------------------------------------------------------------------------
# Vergleich
# --------------------------------------------------------------------------------------------
def _corr_rows(X, Y):
    """Pearson-Matrix zwischen den Zeilen von X (a x n) und Y (b x n); konstante Zeilen -> 0."""
    def z(M):
        M = M - M.mean(axis=1, keepdims=True)
        s = np.sqrt((M * M).sum(axis=1, keepdims=True))
        return np.divide(M, s, out=np.zeros_like(M), where=s > 0)
    return z(X) @ z(Y).T


def _exact(f, a, width, ln, neighbours=()):
    """bester Anteil f == (a >> s) & Maske ueber alle Verschiebungen s. Mit neighbours (Feldwerte des
    Frames davor und danach) zaehlt auch der Nachbarframe: der Rechentakt des PCM liegt nicht fest zu
    Sendeplan und Diagnoseantwort, eine sich schnell aendernde Groesse waere sonst nie bitgleich."""
    mask = (1 << ln) - 1
    best, bs = 0.0, 0
    for s in range(0, max(0, width - ln) + 1):
        q_ = (a >> s) & mask
        m = f == q_
        for g in neighbours:
            m |= g == q_
        q = float(np.mean(m))
        if q > best:
            best, bs = q, s
    return best, bs


def compare(fr, runs, anc):
    """Zeilen (dict je COLS) fuer alle Paare mit |r_pegel| >= KEEP_R."""
    from can_rare_bits import dbc_bits
    rows = []
    for cid, cruns in runs.items():
        tf, d = fr[cid]
        B = dbc_bits(d).astype(np.int64)
        for key, a in anc.items():
            names = list(a["teile"])
            widths = np.array([a["teile"][n][1] for n in names])
            best = {}                                   # (sf_index, teil_index) -> (|r|, lag, r, rd)
            sf_meta = None
            for lag in LAGS:
                tq = a["t"] + lag
                idx = np.searchsorted(tf, tq, side="right") - 1
                ok = (idx >= 0) & (tq - tf[np.clip(idx, 0, None)] <= MAX_AGE_S)
                if ok.sum() < MIN_N:
                    continue
                sfs = _subfields(B[idx[ok]], cruns)
                if sf_meta is None:
                    sf_meta = [(s, ln) for s, ln, _ in sfs]
                F = np.array([v for _, _, v in sfs], dtype=float)
                A = np.array([a["teile"][n][0][ok] for n in names], dtype=float)
                R = _corr_rows(F, A)
                Rd = _corr_rows(np.diff(F, axis=1), np.diff(A, axis=1))
                lens = np.array([ln for _, ln in sf_meta])
                # Einzelbits des Ankers nur gegen 1-2-Bit-Felder
                R[np.ix_(lens > 2, widths == 1)] = 0
                for i, j in zip(*np.nonzero(np.abs(R) >= KEEP_R)):
                    if (i, j) not in best or abs(R[i, j]) > best[(i, j)][0]:
                        best[(i, j)] = (abs(R[i, j]), lag, R[i, j], Rd[i, j])
            for (i, j), (_, lag, r, rd) in best.items():
                st, ln = sf_meta[i]
                tq = a["t"] + lag
                idx = np.searchsorted(tf, tq, side="right") - 1
                ok = (idx >= 0) & (tq - tf[np.clip(idx, 0, None)] <= MAX_AGE_S)
                wts = 1 << np.arange(ln - 1, -1, -1)
                bits = lab_bits(cruns, st, ln)
                f = B[idx[ok]][:, bits] @ wts
                nb = [B[np.clip(idx[ok] + k, 0, len(B) - 1)][:, bits] @ wts for k in (-1, 1)]
                av, w = a["teile"][names[j]]
                av = av[ok]
                ex, sh = _exact(f, av, w, ln, nb)
                nf, na = len(np.unique(f)), len(np.unique(av))
                breite_ok = nf >= 0.25 * min(na, 1 << ln)
                wechsel = int(np.count_nonzero(np.diff(f)))
                hit = wechsel >= MIN_CHANGES and (
                    ex >= EXACT_MIN or (abs(r) >= R_LEVEL_MIN and abs(rd) >= R_DIFF_MIN and breite_ok))
                rows.append(dict(can_id=f"0x{cid:03X}", start=st, length=ln, anker=key, teil=names[j],
                                 breite=int(w), lag=lag, n=int(ok.sum()), r_pegel=round(float(r), 4),
                                 r_diff=round(float(rd), 4), exakt=round(ex, 4), shift=sh, n_feld=nf,
                                 n_anker=na, wechsel=wechsel, treffer=bool(hit)))
    return rows


def lab_bits(runs, start, ln):
    for run in runs:
        if start in run:
            i = run.index(start)
            return run[i:i + ln]
    raise KeyError(start)


# --------------------------------------------------------------------------------------------
# je Log
# --------------------------------------------------------------------------------------------
def analyse(fr, ev, log_id, open_bits=None, out_dir=CAND_DIR):
    """Kandidatensuche fuer ein Log; schreibt <log>.csv.gz und <log>.json, gibt Trefferzahl zurueck."""
    anc = anchors(ev)
    runs, var = open_runs(fr, open_bits if open_bits is not None else pcm_open_bits())
    rows = compare(fr, runs, anc) if anc and runs else []
    os.makedirs(out_dir, exist_ok=True)
    with gzip.open(os.path.join(out_dir, f"{log_id}.csv.gz"), "wt") as fh:
        fh.write(",".join(COLS) + "\n")
        for r in rows:
            fh.write(",".join(str(r[c]) for c in COLS) + "\n")
    meta = {
        "variiert": {f"0x{c:03X}": v for c, v in var.items()},
        "anker": {k: {"n": len(a["t"]), "dauer_s": round(float(a["t"][-1] - a["t"][0]), 1),
                      "teile": {n: int(len(np.unique(v))) for n, (v, _) in a["teile"].items()}}
                  for k, a in anc.items()},
    }
    # Variation je DID auch fuer Anker unter MIN_N bzw. ohne Variation (Ausmusterung)
    seen = {}
    for t, rich, mode, ident, erg, hexp in ev:
        if rich == "resp" and erg == "pos" and ident >= 0:
            key = f"{mode}:{ident:04X}" if mode == "22" else f"{mode}:{ident:02X}"
            seen.setdefault(key, []).append(hexp)
    meta["varianz"] = {k: [len(v), len(set(v))] for k, v in seen.items()}     # [Antworten, Werte]
    with open(os.path.join(out_dir, f"{log_id}.json"), "w") as fh:
        json.dump(meta, fh)
    return sum(r["treffer"] for r in rows)


# --------------------------------------------------------------------------------------------
# ueber alle Logs
# --------------------------------------------------------------------------------------------
def _testable(meta, can_id, start, ln, anker, teil):
    """pruefbar = Teilwert lag im Log vor und mindestens ein Bit des Teilfelds variierte."""
    import can_offline_lab as lab
    if teil not in meta["anker"].get(anker, {}).get("teile", {}):
        return False
    return bool(set(lab.bit_order(start, ln, True)) & set(meta["variiert"].get(can_id, [])))


def aggregate(cand_dir=CAND_DIR, out_dir=OUT_DIR, today=None):
    import pandas as pd
    metas, frames = {}, []
    for p in sorted(glob.glob(os.path.join(cand_dir, "*.json"))):
        log = os.path.basename(p)[:-5]
        with open(p) as fh:
            metas[log] = json.load(fh)
        cp = os.path.join(cand_dir, f"{log}.csv.gz")
        if os.path.exists(cp):
            df = pd.read_csv(cp)
            if len(df):
                df["log"] = log
                frames.append(df)
    keys = ["can_id", "start", "length", "anker", "teil"]
    cands = []
    if frames:
        df = pd.concat(frames, ignore_index=True)
        for k, g in df[df.treffer].groupby(keys):
            can_id, start, ln, anker, teil = k
            testable = [l for l, m in metas.items() if _testable(m, can_id, int(start), int(ln), anker, teil)]
            hits = g.set_index("log")
            if len(testable) < 2 or not set(testable) <= set(hits.index):
                continue
            if hits.lag.max() - hits.lag.min() > LAG_SPREAD_MAX:
                continue
            cands.append(dict(can_id=can_id, start=int(start), length=int(ln), anker=anker, teil=teil,
                              breite=int(g.breite.iloc[0]), logs=len(testable), erster_log=min(testable),
                              lag_median=float(hits.lag.median()),
                              exakt_min=float(hits.exakt.min()), r_pegel_min=float(hits.r_pegel.abs().min()),
                              r_diff_min=float(hits.r_diff.abs().min()), shift=int(hits["shift"].mode().iloc[0]),
                              n_feld_max=int(hits.n_feld.max()), n_anker_max=int(hits.n_anker.max())))
    cands = _parsimony(cands)
    # Ausmusterung: DIDs, die in allen Logs mit genug Antworten nur einen Wert hatten
    # (nur Logs mit >= MIN_N Antworten zaehlen; ein Sweep liest jede DID nur einmal)
    var = {}
    for m in metas.values():
        for k, nv in m.get("varianz", {}).items():
            if isinstance(nv, list) and nv[0] >= MIN_N:
                var.setdefault(k, []).append(nv[1])
    konstant = sorted(k for k, v in var.items() if k.startswith("22:") and max(v) == 1)
    today = today or datetime.date.today().isoformat()
    out = {"datum": today, "logs": sorted(metas), "kandidaten": cands,
           "konstante_dids": konstant,
           "regeln": dict(EXACT_MIN=EXACT_MIN, R_LEVEL_MIN=R_LEVEL_MIN, R_DIFF_MIN=R_DIFF_MIN,
                          LAG_SPREAD_MAX=LAG_SPREAD_MAX, MIN_N=MIN_N)}
    os.makedirs(out_dir, exist_ok=True)
    for name in (f"candidates_{today}.json", "candidates_latest.json"):
        with open(os.path.join(out_dir, name), "w") as fh:
            json.dump(out, fh, indent=1)
    return out


def _parsimony(cands):
    """je (Botschaft, Anker, Teil) zwei Ranglisten, jede ohne Bitueberlappung, je hoechstens 2:
    (a) (fast) exakte Abbilder (exakt_min >= EXACT_RANK_MIN), davon die laengsten;
    (b) Korrelation: bestes r_pegel (4 Stellen; zusaetzliche niederwertige Bits einer echten Kopie
        heben r leicht, fremde Bits senken es), bei Gleichstand das kuerzeste.
    Beide Sichten bleiben stehen (ein skaliertes Abbild wie 0..100 % ist nie bitgleich, eine
    abgeschnittene 2-Bit-Lesung oft schon); die Entscheidung faellt in der Pruefung von Hand."""
    import can_offline_lab as lab
    groups = {}
    for c in cands:
        groups.setdefault((c["can_id"], c["anker"], c["teil"]), []).append(c)
    out = []
    for g in groups.values():
        tiers = (
            sorted((c for c in g if c["exakt_min"] >= EXACT_RANK_MIN),
                   key=lambda c: (c["length"], c["exakt_min"]), reverse=True),
            sorted(g, key=lambda c: (round(c["r_pegel_min"], 4), round(c["r_diff_min"], 3), -c["length"]),
                   reverse=True),
        )
        picked = []
        for tier in tiers:
            used, n = set(), 0
            for c in tier:
                bits = set(lab.bit_order(c["start"], c["length"], True))
                if bits & used:
                    continue
                used |= bits
                n += 1
                if c not in picked:
                    picked.append(c)
                if n == 2:
                    break
        out += picked
    return sorted(out, key=lambda c: (c["can_id"], c["start"], c["anker"], c["teil"]))


def summary_line(out):
    c = out["kandidaten"]
    ex = sum(1 for x in c if x["exakt_min"] >= EXACT_MIN)
    return (f"Kandidatensuche: {len(c)} Kandidaten ({ex} exakt) ueber {len(out['logs'])} Logs, "
            f"{len(out['konstante_dids'])} DIDs bisher ueberall konstant")


# --------------------------------------------------------------------------------------------
def run_logs(log_ids):
    import can_offline_lab as lab
    from pcm_campaign_eval import diag_events
    ob = pcm_open_bits()
    for log in log_ids:
        fr = lab.frames(f"data/can/{log}.log")
        n = analyse(fr, diag_events(fr), log, ob)
        print(f"{log}: {n} Treffer", flush=True)


def _one(log):
    try:
        run_logs([log])
    except Exception as exc:
        print(f"{log}: Fehler {exc!r}"[:300], flush=True)


def _make_test_log(seed):
    """Kuenstliches Log fuer self_test (siehe dort)."""
    rng = np.random.default_rng(seed)
    T = 600.0
    tf = np.arange(0, T, 0.02)                          # 50-Hz-Botschaft 0x123
    n = len(tf)
    x = np.clip(2048 + 1800 * np.sin(tf / 40.0) + rng.normal(0, 15, n), 0, 4095).astype(np.int64)  # 12 Bit
    y = np.clip(30000 + np.cumsum(rng.normal(0, 80, n)), 0, 65535).astype(np.int64)  # 16-Bit-Groesse
    flag = (np.sin(tf / 7.0) > 0.3).astype(np.int64)
    mono_f = (tf / T * 200).astype(np.int64)            # steigt monoton (z. B. Fahrzeit)
    d = np.zeros((n, 8), np.uint8)
    d[:, 0] = x >> 4                                    # Bit 7..0 Byte0 + Byte1 High-Nibble = x (12 Bit)
    d[:, 1] = ((x & 0xF) << 4) | (flag << 3)
    d[:, 2] = y >> 8                                    # nur oberes Byte von y
    d[:, 3] = mono_f
    d[:, 4] = rng.integers(0, 256, n)                   # Rauschbyte
    fr = {0x123: (tf, d)}
    ev = []
    ta = np.arange(1.0, T, 0.7)
    for t in ta:
        i = np.searchsorted(tf, t, side="right") - 1
        ev.append((t + 0.005, "resp", "22", 0xAA01, "pos", f"{int(x[i]):04x}"))
        ev.append((t + 0.010, "resp", "22", 0xAA02, "pos", f"{int(y[i]):04x}{int(flag[i]):02x}"))
        mono_a = int(50 + t / T * 100 + rng.normal(0, 2))   # anderer monotoner Verlauf, eigenes Rauschen
        ev.append((t + 0.015, "resp", "22", 0xAA03, "pos", f"{mono_a:02x}"))
        ev.append((t + 0.020, "resp", "22", 0xAA04, "pos", "07"))   # konstant
    return fr, ev



def self_test():
    """Kuenstliches Log: zwei eingebaute Kopien (exakt 12 Bit, abgeschnittenes 8-Bit-Feld), ein Flag,
    und eine Scheinkorrelation (zwei unabhaengige monotone Groessen), die NICHT Kandidat werden darf."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ob = {0x123: set(range(40))}
        for k in range(3):
            fr, ev = _make_test_log(10 + k)
            analyse(fr, ev, f"candump-2026-01-0{k + 1}_000000", ob, out_dir=tmp)
        out = aggregate(tmp, os.path.join(tmp, "out"), today="2026-01-04")
        c = {(x["can_id"], x["start"], x["length"], x["anker"], x["teil"]): x for x in out["kandidaten"]}
        assert ("0x123", 7, 12, "22:AA01", "W0") in c, sorted(c)
        assert c[("0x123", 7, 12, "22:AA01", "W0")]["exakt_min"] == 1.0
        assert ("0x123", 23, 8, "22:AA02", "W0") in c, sorted(c)
        assert c[("0x123", 23, 8, "22:AA02", "W0")]["shift"] == 8
        assert ("0x123", 11, 1, "22:AA02", "b2.0") in c or ("0x123", 11, 1, "22:AA02", "B2") in c, sorted(c)
        assert not any(k[3] == "22:AA03" for k in c), [k for k in c if k[3] == "22:AA03"]
        assert not any(k[1] in range(32, 40) for k in c), "Rauschbyte als Kandidat"
        assert out["konstante_dids"] == ["22:AA04"], out["konstante_dids"]
        # nur ein Log -> kein Kandidat (Regel 3: >= 2 Logs)
        for p in glob.glob(os.path.join(tmp, "candump-2026-01-0[23]*")):
            os.remove(p)
        assert aggregate(tmp, os.path.join(tmp, "out"), today="2026-01-05")["kandidaten"] == []
    print("self-test ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--self-test"]:
        self_test()
    elif a == ["--aggregate"]:
        print(summary_line(aggregate()))
    else:
        if a == ["--all"]:
            import can_offline_lab as lab
            a = [os.path.basename(p)[:-4] for p in lab.logs()]
        from memguard import bounded_map
        list(bounded_map(_one, a))
        print(summary_line(aggregate()))
