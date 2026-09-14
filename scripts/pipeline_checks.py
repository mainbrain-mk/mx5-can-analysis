"""Schwellwert-basierte Befund-Erkennung fuer die taegliche MX-5-Pipeline.

Ersetzt die bisherige LLM-Interpretation aus Schritt 14 der
process-new-mx5-logs-Routine durch feste, deterministische Regeln. Jede
check_*-Funktion liest die passende results/*.json, wertet sie NUR fuer
die in diesem Lauf neu hinzugekommenen Logs aus und gibt eine Liste von
Findings zurueck: {"severity": "info"|"warn", "category": str,
"message": str, "data": dict}.

Referenzwerte (theta-Bereich, Vollast-Verhaeltnis, k/R²-Schwelle) sind aus
PROJEKT_STAND.md/SKILL.md uebernommen bzw. - wo dort nur "deutliche
Abweichung" ohne Zahl stand (k/R²-Sprung) - als konkrete Schwelle NEU
festgelegt (siehe check_steering_model_shift-Docstring). Bei Bedarf hier
anpassen, nicht im Orchestrator.

Aufruf: als Bibliothek aus run_daily_pipeline.py, siehe run_all().
"""
import json
import os

RESULTS_DIR = "results"
DERIVED_DIR = "data/derived"
STEERING_REFERENCE_PATH = "data/steering_model_reference.json"

THETA_RANGE_DEG = (40.8, 83.1)
THETA_RANGE_AXES = frozenset({"X", "Z"})
R_MIN = 0.7
A_LAT_RATIO_MAX = 2.0
VIBRATION_RESONANCE_RANGE_HZ = (18.0, 23.0)
DRIVETRAIN_RATIO_REFERENCE = 0.94
DRIVETRAIN_RATIO_TOLERANCE = 0.05
STEERING_R2_DELTA_WARN = 0.01
STEERING_K1_REL_DELTA_WARN = 0.05


def _load_json(name):
    path = os.path.join(RESULTS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _finding(severity, category, message, **data):
    return {"severity": severity, "category": category, "message": message, "data": data}


def check_vibration(new_logs):
    """IMU-Resonanz je Achse ausserhalb 18-23 Hz (Schritt 3)."""
    findings = []
    for log_id in new_logs:
        path = os.path.join(DERIVED_DIR, f"{log_id}_vibration_summary.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            summary = json.load(f)
        for axis, info in summary.get("axes", {}).items():
            peak = info.get("resonance_peak_hz")
            if peak is None:
                continue
            lo, hi = VIBRATION_RESONANCE_RANGE_HZ
            if not (lo <= peak <= hi):
                findings.append(_finding(
                    "warn", "vibration",
                    f"{log_id}: Resonanz auf {axis} bei {peak:.1f} Hz, "
                    f"ausserhalb des erwarteten Bereichs {lo:.0f}-{hi:.0f} Hz.",
                    log_id=log_id, axis=axis, resonance_peak_hz=peak,
                ))
    return findings


def check_brake_axis(new_logs):
    """R<0.7, theta ausserhalb Referenzbereich (nur Achsenpaar X/Z),
    vertical_axis_defaulted, und Halterungswechsel ggue. dem jeweils
    vorherigen Log im Datensatz (Schritt 4)."""
    data = _load_json("brake_event_summary.json")
    if not data:
        return []
    by_log = {r["file"].removesuffix(".dlg"): r for r in data if "error" not in r}
    ordered_ids = sorted(by_log)
    findings = []
    for log_id in new_logs:
        r = by_log.get(log_id)
        if r is None:
            continue
        axes = frozenset(r.get("horizontal_axes") or [])
        if r.get("vertical_axis_defaulted"):
            findings.append(_finding(
                "warn", "brake_axis",
                f"{log_id}: Vertikalachse konnte nicht erkannt werden, Fallback auf Y.",
                log_id=log_id,
            ))
        theta_r = r.get("theta_r")
        if theta_r is not None and theta_r < R_MIN:
            findings.append(_finding(
                "warn", "brake_axis",
                f"{log_id}: Konzentrationsmass R={theta_r:.2f} < {R_MIN} bei der Achsenrotation.",
                log_id=log_id, theta_r=theta_r,
            ))
        theta = r.get("forward_direction_deg")
        if theta is not None and axes == THETA_RANGE_AXES:
            lo, hi = THETA_RANGE_DEG
            if not (lo <= theta <= hi):
                findings.append(_finding(
                    "warn", "brake_axis",
                    f"{log_id}: Fahrzeug-vorwaerts-Winkel {theta:.1f}° ausserhalb "
                    f"des Referenzbereichs {lo}-{hi}° (gleiches Achsenpaar X/Z wie bisher).",
                    log_id=log_id, forward_direction_deg=theta,
                ))
        idx = ordered_ids.index(log_id)
        if idx > 0:
            prev = by_log[ordered_ids[idx - 1]]
            prev_axes = frozenset(prev.get("horizontal_axes") or [])
            if prev_axes and axes and prev_axes != axes:
                findings.append(_finding(
                    "info", "brake_axis",
                    f"{log_id}: Halterung geaendert - horizontal_axes "
                    f"{sorted(prev_axes)} -> {sorted(axes)} ggue. vorigem Log "
                    f"{ordered_ids[idx - 1]}.",
                    log_id=log_id, previous_log=ordered_ids[idx - 1],
                ))
    return findings


def check_corner_events(new_logs):
    """a_lat_peak/a_lat_mean > 2 bei einer bestaetigten Kurve (Schritt 5)."""
    data = _load_json("corner_event_summary.json")
    if not data:
        return []
    by_log = {r["file"].removesuffix(".dlg"): r for r in data if "error" not in r}
    findings = []
    for log_id in new_logs:
        r = by_log.get(log_id)
        if r is None:
            continue
        for ev in r.get("events", []):
            mean_g, peak_g = ev.get("a_lat_mean_g"), ev.get("a_lat_peak_g")
            if not mean_g:
                continue
            ratio = abs(peak_g / mean_g)
            if ratio > A_LAT_RATIO_MAX:
                findings.append(_finding(
                    "info", "corner_event",
                    f"{log_id} t={ev['t_start']:.0f}-{ev['t_end']:.0f}s: "
                    f"a_lat_peak/mean-Verhaeltnis {ratio:.1f} > {A_LAT_RATIO_MAX} "
                    f"(peak={peak_g:+.2f}g, mean={mean_g:+.2f}g) - moegliches Schleudern/Uebersteuern.",
                    log_id=log_id, t_start=ev["t_start"], ratio=ratio,
                ))
    return findings


def check_unmapped_channels(new_logs):
    """UNMAPPED-Kanaele insgesamt sowie speziell bei neuen Logs (Schritt 6+7 -
    dasselbe Signal, ein neuer Kanaltyp IST eine UNMAPPED-Spalte)."""
    data = _load_json("datalake_build_summary.json")
    if not data:
        return []
    findings = []
    if data.get("n_unmapped_channels"):
        findings.append(_finding(
            "warn", "unmapped_channels",
            f"{data['n_unmapped_channels']} nicht zugeordnete Messwerte insgesamt, "
            f"unbekannte Original-Spalten: {data.get('unmapped_channels_overall')}.",
            n_unmapped=data["n_unmapped_channels"],
            channels=data.get("unmapped_channels_overall"),
        ))
    by_log = data.get("unmapped_channels_by_log", {})
    for log_id in new_logs:
        cols = by_log.get(log_id)
        if cols:
            findings.append(_finding(
                "warn", "unmapped_channels",
                f"{log_id}: neue/unbekannte Kanaele {cols}.",
                log_id=log_id, channels=cols,
            ))
    return findings


def check_drivetrain_ratio(new_logs):
    """Vollast-Verhaeltnis gemessen/Modell der neuen Logs deutlich (>5
    Prozentpunkte) vom Referenzwert 0.94 abweichend (Schritt 9)."""
    data = _load_json("drivetrain_model_validation_summary.json")
    if not data:
        return []
    ratios = [
        e["a_measured_ms2"] / e["a_model_ms2"]
        for e in data
        if e.get("file", "").removesuffix(".dlg") in new_logs and e.get("a_model_ms2")
    ]
    if not ratios:
        return []
    ratios.sort()
    median = ratios[len(ratios) // 2]
    if abs(median - DRIVETRAIN_RATIO_REFERENCE) > DRIVETRAIN_RATIO_TOLERANCE:
        return [_finding(
            "warn", "drivetrain_ratio",
            f"Median-Verhaeltnis gemessen/Modell der neuen Logs {median:.2f} weicht "
            f"um mehr als {DRIVETRAIN_RATIO_TOLERANCE} vom Referenzwert "
            f"{DRIVETRAIN_RATIO_REFERENCE} ab (n={len(ratios)}).",
            median_ratio=median, n=len(ratios),
        )]
    return []


def check_dgm_gaps(new_logs):
    """Eines der neuen Logs traegt zu einer DGM-Luecke bei (Schritt 10)."""
    data = _load_json("dgm_coverage_gaps.json")
    if not data:
        return []
    findings = []
    for gap in data:
        if gap.get("log_id") in new_logs:
            findings.append(_finding(
                "info", "dgm_gap",
                f"{gap['log_id']} t={gap['t_start']:.0f}-{gap['t_end']:.0f}s: "
                f"Segment ohne Steigungskorrektur (fehlende Hoehendaten-Kachel).",
                log_id=gap["log_id"], t_start=gap["t_start"], t_end=gap["t_end"],
            ))
    return findings


def check_new_coastdown_events(previous_event_count, current_summary):
    """Jedes neue Ausrollereignis ist ein besonderer Fund (Schritt 11).
    previous_event_count: Anzahl Eintraege VOR diesem Lauf (vom Orchestrator
    vor dem coastdown_analysis.py-Aufruf gezaehlt)."""
    if current_summary is None:
        return []
    new_events = current_summary[previous_event_count:]
    return [
        _finding(
            "warn", "coastdown",
            f"Neues Ausrollereignis: {ev['log_id']} t={ev['t_start']:.0f}-{ev['t_end']:.0f}s "
            f"({ev['duration_s']:.1f}s), CdA={ev.get('cda_fit_m2')}, Crr={ev.get('crr_fit')}.",
            log_id=ev["log_id"], t_start=ev["t_start"],
        )
        for ev in new_events
    ]


def check_new_shift_records(previous_best, current_best):
    """Neue Bestzeit in einem Gangwechsel (Richtung getrennt, z.B. 2->3 vs.
    3->2) ggue. dem Stand vor diesem Lauf - IMMER meldenswert, aber als
    "info" (kein Problem, sondern ein positiver Fund) statt "warn"."""
    findings = []
    for key, cur in (current_best or {}).items():
        prev = (previous_best or {}).get(key)
        if prev is None or cur["best_s"] < prev["best_s"]:
            arrow = key.replace("-", " -> ")
            findings.append(_finding(
                "info", "shift_record",
                f"Neue Bestzeit {arrow} ({cur['kind']}): {cur['best_s']:.2f}s"
                + (f" (vorher {prev['best_s']:.2f}s)" if prev else " (erste Messung)")
                + f", {cur['log_id']} @ t={cur['t_start']:.1f}s.",
                gear_change=key, kind=cur["kind"], best_s=cur["best_s"],
                previous_best_s=prev["best_s"] if prev else None,
                log_id=cur["log_id"], t_start=cur["t_start"],
            ))
    return findings


def check_new_corner_peak_record(previous_best, current_best):
    """Neuer Spitzenwert |a_lat_peak_g| einer per CAN erkannten Kurve, je
    Richtung (rechts/links) getrennt - analog zu check_new_shift_records.
    KEIN verifizierter Befund (siehe corner_peak_tracker.py-Docstring: liest
    alle can_corner_event_summary_*.json ungeprueft, nicht nur die
    nutzerbestaetigte 43er-Basis aus corner_speed_model.py) - deshalb "info",
    als Hinweis fuer manuelle Durchsicht, nicht als bestaetigte Tatsache."""
    findings = []
    for direction, cur in (current_best or {}).items():
        prev = (previous_best or {}).get(direction)
        if prev is None or cur["a_lat_peak_g"] > prev["a_lat_peak_g"]:
            findings.append(_finding(
                "info", "corner_peak",
                f"Neuer Spitzenwert Querbeschleunigung ({direction}): {cur['a_lat_peak_g']:.2f}g"
                + (f" (vorher {prev['a_lat_peak_g']:.2f}g)" if prev else " (erste Messung)")
                + f", {cur['log_id']} @ t={cur['t_start']:.1f}s, "
                  f"v={cur.get('speed_at_peak_kmh', 0):.0f}km/h - unverifizierter Kandidat, manuell pruefen.",
                direction=direction, a_lat_peak_g=cur["a_lat_peak_g"],
                previous_a_lat_peak_g=prev["a_lat_peak_g"] if prev else None,
                log_id=cur["log_id"], t_start=cur["t_start"],
            ))
    return findings


def check_steering_model_shift():
    """k/R²-Sprung ggue. dem zuletzt gespeicherten Referenzwert (Schritt 12).
    SKILL.md nennt keine feste Zahl ("deutliche Verschiebung") - hier NEU
    festgelegt: |Delta R²| >= 0.01 ODER relative |Delta k1| >= 5%. Bei Bedarf
    in den Konstanten oben anpassen. Aktualisiert die Referenzdatei danach
    immer auf den aktuellen Stand."""
    summary = _load_json("steering_lateral_model_summary.json")
    if summary is None:
        return []
    k = summary.get("k")
    k1 = k[0] if isinstance(k, list) else k
    r2 = summary.get("r2")
    n = len(summary.get("calibration_points", []))

    findings = []
    if os.path.exists(STEERING_REFERENCE_PATH):
        with open(STEERING_REFERENCE_PATH, encoding="utf-8") as f:
            ref = json.load(f)
        if ref.get("r2") is not None and r2 is not None:
            if abs(r2 - ref["r2"]) >= STEERING_R2_DELTA_WARN:
                findings.append(_finding(
                    "warn", "steering_model",
                    f"R² verschoben: {ref['r2']:.3f} -> {r2:.3f} "
                    f"(n={ref.get('n')} -> {n}).",
                    r2_before=ref["r2"], r2_after=r2, n_before=ref.get("n"), n_after=n,
                ))
        if ref.get("k1") and k1 is not None and abs(k1 - ref["k1"]) / abs(ref["k1"]) >= STEERING_K1_REL_DELTA_WARN:
            findings.append(_finding(
                "warn", "steering_model",
                f"k1 verschoben: {ref['k1']:.6f} -> {k1:.6f}.",
                k1_before=ref["k1"], k1_after=k1,
            ))

    os.makedirs(os.path.dirname(STEERING_REFERENCE_PATH), exist_ok=True)
    with open(STEERING_REFERENCE_PATH, "w", encoding="utf-8") as f:
        json.dump({"k1": k1, "r2": r2, "n": n}, f, indent=2)
    return findings


def check_script_errors(errors):
    """errors: Liste von (script_name, returncode/exception) - vom
    Orchestrator gesammelt. Jeder Fehler ist immer meldenswert."""
    return [
        _finding("warn", "script_error", f"{name}: {detail}", script=name)
        for name, detail in errors
    ]


def run_all(new_logs, previous_coastdown_count=None, current_coastdown_summary=None,
            steering_ran=False, script_errors=None,
            previous_shift_best=None, current_shift_best=None,
            previous_corner_peak_best=None, current_corner_peak_best=None):
    """Fuehrt alle Checks aus und gibt eine flache Findings-Liste zurueck.
    new_logs: Liste von log_ids (ohne .dlg) der in diesem Lauf neu
    verarbeiteten Logs."""
    findings = []
    findings += check_vibration(new_logs)
    findings += check_brake_axis(new_logs)
    findings += check_corner_events(new_logs)
    findings += check_unmapped_channels(new_logs)
    findings += check_drivetrain_ratio(new_logs)
    findings += check_dgm_gaps(new_logs)
    if previous_coastdown_count is not None:
        findings += check_new_coastdown_events(previous_coastdown_count, current_coastdown_summary)
    if steering_ran:
        findings += check_steering_model_shift()
    if current_shift_best is not None:
        findings += check_new_shift_records(previous_shift_best, current_shift_best)
    if current_corner_peak_best is not None:
        findings += check_new_corner_peak_record(previous_corner_peak_best, current_corner_peak_best)
    findings += check_script_errors(script_errors or [])
    return findings
