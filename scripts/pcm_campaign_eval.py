"""
PCM-Kampagne: deterministische Auswertung je neuem CAN-Log fuer die Tagespipeline
(docs/plans/pcm-bits-plan.md, Abschnitt "Auswertung im Prozess process-new-mx5-logs").

Je Log (parallel, ein Prozess je Log):
  1. Gesundheit: Rate der schnellen Poller-Gruppe (Lambda 0x44, KnockRetard 0x03EC) im
     90. Perzentil der 10-s-Fenster bei laufendem Motor (= ungedrosselte Rate; das Handy
     drosselt die Gruppe zeitweise), Oeltemperatur-Antworten, Antwortquote der bekannten
     Poller-Anfragen, Anfragen der Kampagne, Handy-Anwesenheit (funktionale 0x7DF-Anfragen).
  2. Hypothesenpruefung: alle maschinenpruefbaren Eintraege des Registers
     (docs/plans/pcm-bits-hypotheses.json) gegen dieses Log.
  3. Kampagnen-Antworten aus dem candump (Grundwahrheit, nicht state.json):
     results/pcm_campaign/responses/<log>.csv.gz (t, mode, id, ergebnis, payload).
Danach ueber alle neuen Logs: Befunde (warn/info) und Fortschritt aus dem vom Pi geholten
state.json -> results/pcm_campaign/eval_latest.json, das run_daily_pipeline.py einliest.

Grenzen: Kampagnen- und Handy-Anfragen sind auf 0x7E0 nicht unterscheidbar. Als Kampagne
zaehlt jede Mode-22-Anfrage ausserhalb der Poller-DIDs und jede Mode-01-Anfrage ausserhalb
der Poller-PIDs; fragt das Handy dieselben Dienste, zaehlt es mit. Die Raten-Pruefung haengt
daran nicht (sie misst nur die bekannten Poller-Antworten).

Aufruf: .venv/bin/python scripts/pcm_campaign_eval.py <log_id> [...]   bzw. --self-test
"""
import argparse
import glob
import gzip
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_DIR = "results/pcm_campaign"
HEALTH_CSV = os.path.join(OUT_DIR, "health.csv")
CHECKS_JSONL = os.path.join(OUT_DIR, "hypothesis_checks.jsonl")
REGISTER = "docs/plans/pcm-bits-hypotheses.json"
STATE_DIR = "data/can/pcm_campaign"
POLLER_MODE01 = {0x44, 0x11, 0x10, 0x3C, 0x34}
POLLER_DIDS = {0x03EC, 0x1310}
MIN_RATE_SHARE = 0.85
BASELINE_N = 10
HEALTH_COLS = ["log", "engine_s", "lambda_hz_p90", "knock_hz_p90", "oil_n", "poller_req",
               "poller_answered", "campaign_req", "campaign_pos", "campaign_nrc", "func_req_7df"]


# --------------------------------------------------------------------------------------------
def diag_events(fr):
    """[(t, richtung, mode, ident, ergebnis, payload_hex)] fuer 0x7E0/0x7DF/0x7E8, Mehrrahmen
    zusammengesetzt. richtung: req/resp; ergebnis bei resp: pos/nrc."""
    import pandas as pd
    from obd_from_can import _reassemble
    parts = []
    for cid in (0x7E0, 0x7DF, 0x7E8):
        if cid in fr:
            t, d = fr[cid]
            parts.append(pd.DataFrame({"t": t, "can_id": cid, "data": [bytes(r) for r in d]}))
    if not parts:
        return []
    df = pd.concat(parts).sort_values("t", kind="stable")
    out = []
    for t, cid, p in _reassemble(df):
        if not p:
            continue
        sid = p[0]
        if cid in (0x7E0, 0x7DF):
            if sid == 0x22 and len(p) >= 3:
                out.append((t, "req" if cid == 0x7E0 else "req7df", "22", (p[1] << 8) | p[2], "", ""))
            elif sid == 0x01 and len(p) >= 2:
                out.append((t, "req" if cid == 0x7E0 else "req7df", "01", p[1], "", ""))
        else:
            if sid == 0x62 and len(p) >= 3:
                out.append((t, "resp", "22", (p[1] << 8) | p[2], "pos", p[3:].hex()))
            elif sid == 0x41 and len(p) >= 2:
                out.append((t, "resp", "01", p[1], "pos", p[2:].hex()))
            elif sid == 0x7F and len(p) >= 3:
                out.append((t, "resp", "22" if p[1] == 0x22 else f"{p[1]:02X}", -1, "nrc", f"{p[2]:02X}"))
    return out


def engine_mask_fn(fr):
    """Funktion t -> bool 'Motor laeuft' aus 0x202 (Drehzahl >= 400/min)."""
    if 0x202 not in fr:
        return lambda t: np.zeros(len(np.atleast_1d(t)), bool)
    t, d = fr[0x202]
    rpm = (d[:, 0].astype(np.int64) * 256 + d[:, 1]) * 0.25
    run = rpm >= 400

    def f(tq):
        i = np.clip(np.searchsorted(t, tq, side="right") - 1, 0, len(t) - 1)
        return run[i] & (np.abs(t[i] - tq) < 1.0)
    return f


def _rate_p90(ts, engine, win=10.0):
    if not len(ts):
        return 0.0
    t0, t1 = ts.min(), ts.max()
    edges = np.arange(t0, t1 + win, win)
    n, _ = np.histogram(ts, edges)
    mids = edges[:-1] + win / 2
    m = engine(mids)
    return float(np.percentile(n[m] / win, 90)) if m.any() else 0.0


def health(fr, ev):
    engine = engine_mask_fn(fr)
    eng_s = 0.0
    if 0x202 in fr:
        t = fr[0x202][0]
        grid = np.arange(t[0], t[-1], 1.0)
        eng_s = float(engine(grid).sum())
    resp = [e for e in ev if e[1] == "resp" and e[4] == "pos"]
    lam = np.array([e[0] for e in resp if e[2] == "01" and e[3] == 0x44])
    kno = np.array([e[0] for e in resp if e[2] == "22" and e[3] == 0x03EC])
    oil = sum(1 for e in resp if e[2] == "22" and e[3] == 0x1310)
    reqs = [e for e in ev if e[1] == "req"]
    is_poller = [(e[2] == "01" and e[3] in POLLER_MODE01) or (e[2] == "22" and e[3] in POLLER_DIDS) for e in reqs]
    poller = [e for e, p in zip(reqs, is_poller) if p]
    camp = [e for e, p in zip(reqs, is_poller) if not p]
    # Antwortquote der bekannten Poller-Anfragen: passende positive Antwort binnen 60 ms
    by_key = {}
    for e in resp:
        by_key.setdefault((e[2], e[3]), []).append(e[0])
    by_key = {k: np.array(v) for k, v in by_key.items()}
    answered = 0
    for e in poller:
        ts = by_key.get((e[2], e[3]))
        if ts is not None:
            i = np.searchsorted(ts, e[0])
            if i < len(ts) and ts[i] - e[0] < 0.06:
                answered += 1
    camp_keys = {(e[2], e[3]) for e in camp}
    return {
        "engine_s": round(eng_s, 1),
        "lambda_hz_p90": round(_rate_p90(lam, engine), 2),
        "knock_hz_p90": round(_rate_p90(kno, engine), 2),
        "oil_n": oil,
        "poller_req": len(poller),
        "poller_answered": answered,
        "campaign_req": len(camp),
        "campaign_pos": sum(1 for e in resp if (e[2], e[3]) in camp_keys),
        "campaign_nrc": sum(1 for e in ev if e[1] == "resp" and e[4] == "nrc"),
        "func_req_7df": sum(1 for e in ev if e[1] == "req7df"),
    }


def load_register(path=REGISTER):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"hypothesen": []}


def check_hypotheses(fr, register):
    """{hyp_id: {frames, bad, ...}} fuer alle Eintraege mit maschinenpruefbarer Pruefung."""
    from pcm_counter_checksum import verify_log
    from can_sender_clock import period_windows, ratio_ppm, WIN_S
    res = {}
    vl = None
    ref = None
    for h in register.get("hypothesen", []):
        pr = h.get("pruefung") or {}
        typ = pr.get("typ")
        if typ in ("pruefsumme", "zaehler", "flag"):
            if vl is None:
                vl = verify_log(fr)
            if pr["name"] in vl:
                res[h["id"]] = vl[pr["name"]]
        elif typ == "absender":
            cid, refid = int(pr["can_id"], 16), int(pr["ref_id"], 16)
            if cid not in fr or refid not in fr:
                continue
            # Fenster ueber die Fensternummer zuordnen (Mitten liegen je ID verschieden)
            if ref is None:
                ref = {int(tm // WIN_S): p for tm, p in period_windows(fr[refid][0])}
            mine = {int(tm // WIN_S): p for tm, p in period_windows(fr[cid][0])}
            common = sorted(set(mine) & set(ref))
            if len(common) < 5:
                continue
            dev, nom = ratio_ppm([mine[k] for k in common], [ref[k] for k in common])
            res[h["id"]] = {"dev_ppm": round(float(np.median(dev)), 2), "ratio": nom, "n_win": len(common)}
    return res


def analyse_log(log_id):
    """Fehler je Log abfangen: ein leeres/kaputtes Log darf den Lauf nie stoppen."""
    try:
        return _analyse_log(log_id)
    except Exception as exc:
        return log_id, None, {"_fehler": repr(exc)[:300]}


def _analyse_log(log_id):
    import can_offline_lab as lab
    path = f"data/can/{log_id}.log"
    fr = lab.frames(path)
    ev = diag_events(fr)
    h = health(fr, ev)
    checks = check_hypotheses(fr, load_register())
    os.makedirs(os.path.join(OUT_DIR, "responses"), exist_ok=True)
    camp = [e for e in ev if e[1] == "resp" and not (
        (e[2] == "01" and e[3] in POLLER_MODE01) or (e[2] == "22" and e[3] in POLLER_DIDS))]
    with gzip.open(os.path.join(OUT_DIR, "responses", f"{log_id}.csv.gz"), "wt") as fh:
        fh.write("t,mode,id,ergebnis,payload\n")
        for e in camp:
            fh.write(f"{e[0]:.4f},{e[2]},{e[3] if e[3] < 0 else format(e[3], '04X')},{e[4]},{e[5]}\n")
    return log_id, h, checks


# --------------------------------------------------------------------------------------------
def _read_health():
    rows = {}
    if os.path.exists(HEALTH_CSV):
        with open(HEALTH_CSV) as fh:
            head = fh.readline().strip().split(",")
            for line in fh:
                v = line.strip().split(",")
                r = dict(zip(head, v))
                rows[r["log"]] = {k: (float(x) if k != "log" else x) for k, x in r.items()}
    return rows


def _write_health(rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(HEALTH_CSV, "w") as fh:
        fh.write(",".join(HEALTH_COLS) + "\n")
        for log in sorted(rows):
            fh.write(",".join(str(rows[log].get(c, "")) for c in HEALTH_COLS) + "\n")


def baseline(rows, log):
    """Median der letzten BASELINE_N Logs VOR `log` ohne Kampagnen-Anfragen und mit Motorlauf."""
    prev = [r for k, r in sorted(rows.items()) if k < log and r["campaign_req"] == 0
            and r["engine_s"] > 300 and r["lambda_hz_p90"] > 0][-BASELINE_N:]
    if len(prev) < 3:
        return None
    return {k: float(np.median([r[k] for r in prev])) for k in ("lambda_hz_p90", "knock_hz_p90")}


def findings_for(log, h, base, checks, register):
    out = []
    hyp = {x["id"]: x for x in register.get("hypothesen", [])}
    if base and h["campaign_req"] > 0 and h["engine_s"] > 300:
        for k, name in (("lambda_hz_p90", "Lambda"), ("knock_hz_p90", "KnockRetard")):
            if base[k] > 0 and h[k] < MIN_RATE_SHARE * base[k]:
                out.append(("warn", "pcm_campaign_rate",
                            f"{log}: {name}-Rate {h[k]:.1f}/s < {MIN_RATE_SHARE:.0%} des Bezugs "
                            f"({base[k]:.1f}/s) bei {h['campaign_req']} Kampagnen-Anfragen"))
    if h["poller_req"] > 100 and h["poller_answered"] / h["poller_req"] < 0.95 and h["campaign_req"] > 0:
        out.append(("warn", "pcm_campaign_answers",
                    f"{log}: nur {h['poller_answered'] / h['poller_req']:.1%} der Poller-Anfragen "
                    f"binnen 60 ms beantwortet (Kampagne aktiv)"))
    for hid, r in checks.items():
        if hid.startswith("_"):
            continue
        if r.get("bad"):
            out.append(("warn", "pcm_hypothesis_violated",
                        f"{log}: {hid} ({hyp.get(hid, {}).get('aussage', '')}) - {r['bad']} Verstoesse "
                        f"bei {r['frames']} Frames"))
        if "dev_ppm" in r and abs(r["dev_ppm"]) > 25:
            out.append(("info", "pcm_sender_deviation",
                        f"{log}: {hid} Taktabweichung {r['dev_ppm']:+.1f} ppm (Einzellog, Rauschen bis "
                        "~20 ppm bei 10-Hz-Botschaften normal)"))
    return out


def progress_from_state(state_dir=STATE_DIR):
    """Kurzfassung des vom Pi geholten state.json + Warnungen aus events.log."""
    out, fnd = {}, []
    try:
        with open(os.path.join(state_dir, "state.json")) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return None, []
    sw = st["sweep"]
    out = {
        "mode01_done": st["mode01"]["done"],
        "mode01_supported": len(st["mode01"]["supported"]),
        "sweep_blocks_done": len(sw["blocks_done"]),
        "sweep_found": len(sw["found"]),
        "sweep_special": sw["special"],
        "sweep_silent": sum(sw["silent"].values()),
        "sweep_nrc": sum(sum(v.values()) for v in sw["nrc"].values()),
        "rotation_group": st["rotation"]["group"],
        "sessions": st["sessions"][-5:],
    }
    try:
        with open(os.path.join(state_dir, "events.log")) as fh:
            ev = fh.read().splitlines()[-200:]
    except OSError:
        ev = []
    for line in ev:
        if "Fehler" in line or "STOP" in line or "zu viele" in line:
            fnd.append(("warn", "pcm_campaign_event", "Pi: " + line))
    return out, fnd[-10:]


def main(log_ids, workers=None):
    from concurrent.futures import ProcessPoolExecutor
    register = load_register()
    rows = _read_health()
    # Bezug: fehlen Gesundheitswerte fuer die Logs davor, die letzten BASELINE_N nachrechnen
    all_logs = sorted(os.path.basename(p)[:-4] for p in glob.glob("data/can/candump-*.log")
                      if os.path.getsize(p) > 20_000_000)
    need = set(log_ids)
    if log_ids:
        first = min(log_ids)
        prior = [l for l in all_logs if l < first][-BASELINE_N:]
        need |= {l for l in prior if l not in rows}
    results = {}
    with ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 2)) as ex:
        for log, h, checks in ex.map(analyse_log, sorted(need)):
            if h is not None:
                rows[log] = dict(h, log=log)
            results[log] = checks
    _write_health(rows)
    findings = []
    with open(CHECKS_JSONL, "a") as fh:
        for log in sorted(log_ids):
            fh.write(json.dumps({"log": log, "checks": results.get(log, {})}) + "\n")
            if log not in rows:
                findings.append(("info", "pcm_eval_skipped",
                                 f"{log}: nicht auswertbar ({results.get(log, {}).get('_fehler', '?')})"))
                continue
            findings += findings_for(log, rows[log], baseline(rows, log), results.get(log, {}), register)
    prog, ev_findings = progress_from_state()
    findings += ev_findings
    summary = {"logs": sorted(log_ids), "health": {l: rows[l] for l in sorted(log_ids) if l in rows},
               "progress": prog, "findings": findings,
               "hypothesen_geprueft": sorted({k for l in log_ids for k in results.get(l, {}) if not k.startswith("_")})}
    with open(os.path.join(OUT_DIR, "eval_latest.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=float)
    print(json.dumps({"logs": len(log_ids), "findings": len(findings)}))


# --------------------------------------------------------------------------------------------
def self_test():
    import tempfile
    # diag_events: Mehrrahmen-Antwort, NRC, Poller-/Kampagnen-Trennung, Antwortquote
    def fr_from(frames):
        out = {}
        for cid in sorted({c for _, c, _ in frames}):
            sel = [(t, d) for t, c, d in frames if c == cid]
            out[cid] = (np.array([t for t, _ in sel]), np.array([list(d) + [0] * (8 - len(d)) for _, d in sel], np.uint8))
        return out
    frames = []
    for i in range(2000):                                    # 0x202: 800/min, 100 Hz, 20 s
        frames.append((i * 0.01, 0x202, [0x0C, 0x80, 0, 0, 0, 0, 0x19, 0]))
    for i in range(400):                                     # Poller: Lambda 20/s, beantwortet
        t = i * 0.05
        frames.append((t, 0x7E0, [2, 1, 0x44]))
        frames.append((t + 0.004, 0x7E8, [4, 0x41, 0x44, 0x80, 0x00]))
    frames += [(5.0, 0x7E0, [3, 0x22, 0x13, 0x01]),          # Kampagne: Mehrrahmen
               (5.003, 0x7E8, [0x10, 0x0A, 0x62, 0x13, 0x01, 1, 2, 3]),
               (5.004, 0x7E0, [0x30, 0, 0]),
               (5.006, 0x7E8, [0x21, 4, 5, 6, 7]),
               (6.0, 0x7E0, [3, 0x22, 0x13, 0x02]),
               (6.003, 0x7E8, [3, 0x7F, 0x22, 0x31])]
    fr = fr_from(sorted(frames, key=lambda x: x[0]))
    ev = diag_events(fr)
    assert (5.003, "resp", "22", 0x1301, "pos", "01020304050607") in ev, [e for e in ev if e[3] == 0x1301]
    h = health(fr, ev)
    assert h["campaign_req"] == 2 and h["campaign_pos"] == 1 and h["campaign_nrc"] == 1, h
    assert h["poller_req"] == 400 and h["poller_answered"] == 400, h
    assert 19 <= h["lambda_hz_p90"] <= 21, h
    assert 19 <= h["engine_s"] <= 20, h
    # Baseline/Befunde: Rate 60 % des Bezugs mit Kampagne -> warn; ohne Kampagne -> nichts
    rows = {f"candump-2026-11-0{i}_000000": {"log": "", "engine_s": 1000, "lambda_hz_p90": 30.0,
                                              "knock_hz_p90": 30.0, "campaign_req": 0} for i in range(1, 6)}
    base = baseline(rows, "candump-2026-11-10_000000")
    assert base == {"lambda_hz_p90": 30.0, "knock_hz_p90": 30.0}
    bad = dict(h, lambda_hz_p90=18.0, knock_hz_p90=29.0, engine_s=1000)
    f = findings_for("x", bad, base, {}, {})
    assert [x[1] for x in f] == ["pcm_campaign_rate"], f
    assert findings_for("x", dict(bad, campaign_req=0), base, {}, {}) == []
    f = findings_for("x", h, None, {"H1": {"frames": 10, "bad": 2}}, {"hypothesen": [{"id": "H1", "aussage": "a"}]})
    assert f and f[0][1] == "pcm_hypothesis_violated"
    # Fortschritt aus state.json + Ereignis-Warnungen
    with tempfile.TemporaryDirectory() as tmp:
        from pcm_campaign import new_state
        st = new_state()
        st["sweep"]["found"] = {"1310": {"n": 2, "v": "1f40"}}
        with open(os.path.join(tmp, "state.json"), "w") as fh:
            json.dump(st, fh)
        with open(os.path.join(tmp, "events.log"), "w") as fh:
            fh.write("2026-11-23 08:00:00 start stage=0 job=J0\n2026-11-23 08:01:00 Fehler 1: OSError()\n")
        prog, fnd = progress_from_state(tmp)
        assert prog["sweep_found"] == 1 and len(fnd) == 1, (prog, fnd)
    print("self-test ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="*")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
    else:
        main(a.logs)
