"""Orchestrator fuer die taegliche MX-5-Log-Pipeline - ersetzt die
bisherige, Claude-gesteuerte 14-Schritte-Routine durch EIN
deterministisches Python-Skript ohne LLM-Beteiligung.

Ablauf: Google-Drive-Download (rclone) -> neue Logs identifizieren ->
Masse pro Log berechnen -> Analyseskripte per subprocess ausfuehren ->
Schwellwert-Befunde (pipeline_checks.py) -> Report (render_report.py) ->
Desktop-Notification bei Befund. Jeder Analyseskript-Fehler wird
gesammelt und gemeldet, bricht den Lauf aber nicht ab (siehe SKILL.md
"WICHTIG").

Aufruf: .venv/bin/python scripts/run_daily_pipeline.py
Siehe /home/manuel/.claude/plans/recursive-conjuring-minsky.md fuer den
Architektur-Hintergrund.
"""
import glob
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import sys
from datetime import date

import pipeline_checks
import render_report

RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"
CAN_DIR = "data/can"
RESULTS_DIR = "results"
MASS_OVERRIDE_PATH = "data/log_mass_overrides.json"
CAN_GPS_PAIRS_OVERRIDE_PATH = "data/can_gps_pairs.json"
PI_HOST = "pi@192.168.0.247"
PI_CANLOGS_DIR = "/home/pi/canlogs"
GPX_PAIR_TOLERANCE_S = 180
PYTHON = ".venv/bin/python"

DRIVER_MASS_KG = 86.0
EMPTY_MASS_KG = 1073.0
TANK_LITERS = 45.0
FUEL_DENSITY_KG_L = 0.745


def run_script(args, errors, timeout=600):
    """Fuehrt ein Analyseskript per subprocess aus. Nicht-Null-Exitcode
    oder Exception wird in errors gesammelt, der Lauf geht weiter (siehe
    Docstring oben). Gibt CompletedProcess oder None (bei Fehler) zurueck."""
    label = " ".join(args)
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        errors.append((label, f"Exception: {e}"))
        return None
    if res.returncode != 0:
        errors.append((label, f"exit code {res.returncode}: {res.stderr[-500:]}"))
        return None
    return res


def rclone_sync_new_logs(errors):
    """Schritt 0: neue .dlg-Dateien aus dem Drive-Ordner "Loggs" nach
    data/raw/ laden (Remote per root_folder_id fest darauf gesetzt, siehe
    ~/.config/rclone/rclone.conf). Gibt die Liste der heruntergeladenen
    Dateinamen (ohne .dlg) zurueck, oder [] bei Fehler/nichts Neuem."""
    local = {os.path.basename(p)[:-4] for p in glob.glob(f"{RAW_DIR}/*.dlg")}
    try:
        res = subprocess.run(["rclone", "lsf", "gdrive:", "--files-only"],
                              capture_output=True, text=True, timeout=60)
    except Exception as e:
        errors.append(("rclone lsf", f"Exception: {e}"))
        return []
    if res.returncode != 0:
        errors.append(("rclone lsf", f"exit code {res.returncode}: {res.stderr[-300:]}"))
        return []

    drive_dlgs = {line[:-4] for line in res.stdout.splitlines() if line.endswith(".dlg")}
    missing = sorted(drive_dlgs - local)
    if not missing:
        return []

    cmd = ["rclone", "copy", "gdrive:", RAW_DIR]
    for log_id in missing:
        cmd += ["--include", f"{log_id}.dlg"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:
        errors.append(("rclone copy", f"Exception: {e}"))
        return []
    if res.returncode != 0:
        errors.append(("rclone copy", f"exit code {res.returncode}: {res.stderr[-300:]}"))
        return []
    return missing


def pi_reachable():
    """Kurzer SSH-Erreichbarkeitscheck fuer den CAN-Logger-Pi ("car",
    passwortlos per Key, siehe mx5_can_bus_status.md). False bei jedem
    Fehler (Pi aus/nicht im Netz) - kein Grund, den Lauf abzubrechen."""
    try:
        res = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=accept-new", PI_HOST, "true"],
            capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    return res.returncode == 0


def _find_matching_gpx(log_id):
    """Sucht data/can/*.gpx mit gleichem Datum wie log_id (candump-YYYY-MM-DD_HHMMSS)
    und Startzeit innerhalb GPX_PAIR_TOLERANCE_S (die bestehenden, handkuratierten
    CAN_GPS_PAIRS-Paare in build_datalake.py liegen 9-17s auseinander). None, falls
    kein Treffer - der CAN-Log wird dann ohne GPS-Track (CAN-only) registriert."""
    m = re.match(r"candump-(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})", log_id)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    date_prefix = f"{y}{mo}{d}"
    log_s = int(h) * 3600 + int(mi) * 60 + int(s)
    best, best_diff = None, None
    for gpx_path in glob.glob(f"{CAN_DIR}/{date_prefix}-*.gpx"):
        gm = re.match(rf"{date_prefix}-(\d{{2}})(\d{{2}})(\d{{2}})\.gpx$", os.path.basename(gpx_path))
        if not gm:
            continue
        gh, gmi, gs = gm.groups()
        diff = abs((int(gh) * 3600 + int(gmi) * 60 + int(gs)) - log_s)
        if diff <= GPX_PAIR_TOLERANCE_S and (best_diff is None or diff < best_diff):
            best, best_diff = os.path.basename(gpx_path), diff
    return best


def sync_can_logs_from_pi(errors):
    """Neuer Schritt (vor der eigentlichen Auswertung): prueft, ob der
    Raspberry Pi erreichbar ist und abgeschlossene CAN-Logs hat, die
    lokal noch fehlen (>5min unveraendert - "-mmin +5" via find, damit
    eine noch laufende Aufzeichnung nicht mitten im Schreiben kopiert
    wird). Falls ja: nach data/can/ kopieren, entpacken, mit
    can_log_parser.py dekodieren und per Zeitstempel-Naeherung optional
    mit einem schon vorhandenen GPS-Track paaren. Die Paarung landet in
    data/can_gps_pairs.json, das build_datalake.py zusaetzlich zur fest
    kuratierten CAN_GPS_PAIRS-Liste einliest - die kuratierte Liste
    bleibt dadurch unangetastet. Gibt die Liste neuer log_ids zurueck
    (leer, falls Pi nicht erreichbar oder nichts Neues)."""
    if not pi_reachable():
        return []
    # ssh joins mehrere Argumente OHNE Escaping zu einem Remote-Kommando
    # zusammen (dokumentiertes ssh-Verhalten) - "-printf %f\n" als
    # getrenntes Argument verliert dabei den Backslash (Remote-Shell
    # frisst ihn beim unquoted-Wort), "\n" wird zu litereal "n". Fix:
    # das komplette Remote-Kommando selbst quoten und als EIN Argument
    # uebergeben, dann fasst ssh nichts mehr zusammen.
    remote_cmd = (f"find {shlex.quote(PI_CANLOGS_DIR)} -maxdepth 1 "
                  f"-name 'candump-*.log*' -mmin +5 -printf '%f\\n'")
    try:
        res = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", PI_HOST, remote_cmd],
            capture_output=True, text=True, timeout=20)
    except Exception as e:
        errors.append(("ssh find (Pi CAN-Logs)", f"Exception: {e}"))
        return []
    if res.returncode != 0:
        errors.append(("ssh find (Pi CAN-Logs)", f"exit code {res.returncode}: {res.stderr[-300:]}"))
        return []

    remote_files = [l for l in res.stdout.splitlines() if l.strip()]
    local_logs = {os.path.basename(p) for p in glob.glob(f"{CAN_DIR}/candump-*.log")}
    new_remote = sorted(f for f in remote_files if f.removesuffix(".gz") not in local_logs)
    if not new_remote:
        return []

    fetched = []
    for fname in new_remote:
        try:
            res = subprocess.run(
                ["scp", "-o", "ConnectTimeout=6", f"{PI_HOST}:{PI_CANLOGS_DIR}/{fname}", f"{CAN_DIR}/"],
                capture_output=True, text=True, timeout=180)
        except Exception as e:
            errors.append((f"scp {fname}", f"Exception: {e}"))
            continue
        if res.returncode != 0:
            errors.append((f"scp {fname}", f"exit code {res.returncode}: {res.stderr[-300:]}"))
            continue
        fetched.append(fname)

    pairs_override = {}
    if os.path.exists(CAN_GPS_PAIRS_OVERRIDE_PATH):
        with open(CAN_GPS_PAIRS_OVERRIDE_PATH, encoding="utf-8") as f:
            pairs_override = json.load(f)

    new_log_ids = []
    for fname in fetched:
        local_path = os.path.join(CAN_DIR, fname)
        if fname.endswith(".gz"):
            try:
                subprocess.run(["gzip", "-dk", "-f", local_path], check=True,
                                capture_output=True, timeout=60)
            except Exception as e:
                errors.append((f"gunzip {fname}", str(e)))
                continue
            log_name = fname[:-3]
        else:
            log_name = fname
        log_id = os.path.splitext(log_name)[0]

        res = run_script([PYTHON, "scripts/can_log_parser.py", os.path.join(CAN_DIR, log_name)], errors)
        if res is None:
            continue

        pairs_override[log_name] = _find_matching_gpx(log_id) or ""
        new_log_ids.append(log_id)

    with open(CAN_GPS_PAIRS_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        json.dump(pairs_override, f, indent=2, ensure_ascii=False, sort_keys=True)

    return new_log_ids


def find_new_logs():
    """Schritt 1: Logs in data/raw/, die noch keine
    data/derived/<name>_vibration_summary.json haben."""
    processed = {
        os.path.basename(p)[:-len("_vibration_summary.json")]
        for p in glob.glob(f"{DERIVED_DIR}/*_vibration_summary.json")
    }
    raw = {os.path.basename(p)[:-4] for p in glob.glob(f"{RAW_DIR}/*.dlg")}
    return sorted(raw - processed)


def compute_mass(log_id):
    """Schritt 2: Masse aus dem FLI-Kanal der .dlg-Datei berechnen
    (Median erste/letzte 10 Werte, SOLO-Annahme - siehe
    PROJEKT_STAND.md "Fahrzeuggewicht"). None, falls kein FLI-Kanal."""
    path = os.path.join(RAW_DIR, f"{log_id}.dlg")
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        query = """
            SELECT pde.Value FROM PidDataEntry pde
            JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
            WHERE pme.PidName = 'FLI' ORDER BY pde.Time {} LIMIT 10
        """
        cur.execute(query.format("ASC"))
        first10 = [r[0] for r in cur.fetchall()]
        cur.execute(query.format("DESC"))
        last10 = [r[0] for r in cur.fetchall()]
    finally:
        con.close()
    if not first10 or not last10:
        return None
    start_pct, end_pct = statistics.median(first10), statistics.median(last10)
    level_pct = (start_pct + end_pct) / 2
    fuel_kg = (level_pct / 100 * TANK_LITERS) * FUEL_DENSITY_KG_L
    mass_kg = round(EMPTY_MASS_KG + DRIVER_MASS_KG + fuel_kg, 1)
    note = f"FLI ~{start_pct:.1f}%->~{end_pct:.1f}%, automatisch berechnet (SOLO-Annahme)"
    return mass_kg, note


def update_mass_overrides(new_logs):
    overrides = {}
    if os.path.exists(MASS_OVERRIDE_PATH):
        with open(MASS_OVERRIDE_PATH, encoding="utf-8") as f:
            overrides = json.load(f)
    computed = {}
    for log_id in new_logs:
        result = compute_mass(log_id)
        if result is None:
            continue
        mass_kg, note = result
        overrides[log_id] = {"mass_kg": mass_kg, "note": note}
        computed[log_id] = {"mass_kg": mass_kg, "note": note}
    with open(MASS_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False, sort_keys=True)
    return computed


def load_json(name):
    path = os.path.join(RESULTS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def notify(message):
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(["notify-send", "MX-5 Pipeline", message], timeout=10)
    except Exception:
        pass


def main():
    errors = []

    downloaded = rclone_sync_new_logs(errors)
    new_logs = find_new_logs()
    new_can_logs = sync_can_logs_from_pi(errors)

    if not new_logs and not new_can_logs:
        print("Keine neuen Logs gefunden.")
        return 0

    if new_logs:
        print(f"{len(new_logs)} neue(s) Log(s): {new_logs}")
        if downloaded:
            print(f"  davon aus Google Drive geladen: {downloaded}")
    if new_can_logs:
        print(f"{len(new_can_logs)} neue(s) CAN-Log(s) vom Raspberry Pi geladen: {new_can_logs}")

    masses = update_mass_overrides(new_logs)

    for log_id in new_logs:
        run_script([PYTHON, "scripts/vibration_analysis.py", f"{log_id}.dlg"], errors)

    if new_logs:
        run_script([PYTHON, "scripts/brake_event_analysis.py", *new_logs], errors)
        run_script([PYTHON, "scripts/corner_event_analysis.py", *new_logs], errors)

    if new_logs or new_can_logs:
        run_script([PYTHON, "scripts/build_datalake.py"], errors, timeout=300)

    previous_shift_best = load_json("shift_time_best.json") or {}
    if new_can_logs:
        run_script([PYTHON, "scripts/shift_time_analysis.py"], errors)
    current_shift_best = load_json("shift_time_best.json") or {}

    previous_corner_peak_best = load_json("corner_peak_best.json") or {}
    if new_can_logs:
        for log_id in new_can_logs:
            run_script([PYTHON, "scripts/can_corner_event_analysis.py", log_id], errors)
        run_script([PYTHON, "scripts/corner_peak_tracker.py"], errors)
    current_corner_peak_best = load_json("corner_peak_best.json") or {}

    if new_logs:
        run_script([PYTHON, "scripts/partial_load_model.py"], errors)
        run_script([PYTHON, "scripts/drivetrain_model_validation.py"], errors, timeout=300)
        run_script([PYTHON, "scripts/top_speed_validation.py"], errors, timeout=300)
        run_script([PYTHON, "scripts/check_dgm_coverage_gaps.py"], errors)

    previous_coastdown = load_json("coastdown_analysis_summary.json") or []
    if new_logs:
        run_script([PYTHON, "scripts/coastdown_analysis.py", *new_logs], errors, timeout=300)
    current_coastdown = load_json("coastdown_analysis_summary.json") or []

    steering_ran = False
    if new_logs:
        res = run_script([PYTHON, "scripts/check_steering_channel.py"], errors)
        if res is not None:
            try:
                n_steering_logs = int(res.stdout.strip())
            except ValueError:
                n_steering_logs = 0
            if n_steering_logs > 0:
                run_script([PYTHON, "scripts/steering_zero_offset.py"], errors, timeout=300)
                run_script([PYTHON, "scripts/steering_lateral_model.py", *new_logs], errors)
                steering_ran = True

    # --- Kernzahlen fuer den Report zusammenstellen ---
    vibration = {}
    for log_id in new_logs:
        p = os.path.join(DERIVED_DIR, f"{log_id}_vibration_summary.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                vibration[log_id] = json.load(f)

    brake_data = load_json("brake_event_summary.json") or []
    brake = {r["file"][:-4]: r for r in brake_data if "error" not in r and r["file"][:-4] in new_logs}

    corner_data = load_json("corner_event_summary.json") or []
    corner = {}
    for r in corner_data:
        log_id = r["file"][:-4]
        if log_id in new_logs and "error" not in r:
            n_right = sum(1 for e in r["events"] if e["direction"] == "rechts")
            corner[log_id] = {"n_events": r["n_events"], "n_right": n_right, "n_left": r["n_events"] - n_right}

    dt_data = load_json("drivetrain_model_validation_summary.json") or []
    dt_ratios = [e["a_measured_ms2"] / e["a_model_ms2"] for e in dt_data
                 if e.get("file", "")[:-4] in new_logs and e.get("a_model_ms2")]
    drivetrain = {"median_ratio": statistics.median(dt_ratios) if dt_ratios else None, "n": len(dt_ratios)}

    ts_data = load_json("top_speed_validation_summary.json") or []
    top_speed = {"n_new_segments": sum(1 for e in ts_data if e.get("log_id") in new_logs)}

    pl_data = load_json("partial_load_model_summary.json") or {}
    cv_results = pl_data.get("cross_validation") or []
    overall_rmse = (
        math.sqrt(statistics.mean(r["rmse_pct"] ** 2 for r in cv_results))
        if cv_results else None
    )
    partial_load = {"overall_rmse": overall_rmse}

    coastdown = {
        "n_new_events": len(current_coastdown) - len(previous_coastdown),
        "n_total": len(current_coastdown),
    }

    steering_summary = load_json("steering_lateral_model_summary.json") if steering_ran else None
    steering = None
    if steering_summary:
        k = steering_summary.get("k")
        steering = {
            "ran": True,
            "k1": k[0] if isinstance(k, list) else k,
            "r2": steering_summary.get("r2"),
            "n": len(steering_summary.get("calibration_points", [])),
        }

    dgm_data = load_json("dgm_coverage_gaps.json") or []
    dgm_gap_new_logs = sorted({g["log_id"] for g in dgm_data if g.get("log_id") in new_logs})

    findings = pipeline_checks.run_all(
        new_logs,
        previous_coastdown_count=len(previous_coastdown),
        current_coastdown_summary=current_coastdown,
        steering_ran=steering_ran,
        previous_shift_best=previous_shift_best,
        current_shift_best=current_shift_best,
        previous_corner_peak_best=previous_corner_peak_best,
        current_corner_peak_best=current_corner_peak_best,
        script_errors=errors,
    )

    run_report = {
        "date": date.today().isoformat(),
        "new_logs": new_logs,
        "new_can_logs": new_can_logs,
        "downloaded_from_drive": downloaded,
        "masses": masses,
        "vibration": vibration,
        "brake": brake,
        "corner": corner,
        "drivetrain": drivetrain,
        "top_speed": top_speed,
        "partial_load": partial_load,
        "coastdown": coastdown,
        "steering": steering,
        "dgm_gap_new_logs": dgm_gap_new_logs,
        "findings": findings,
    }

    report_path = render_report.write_run(run_report)

    n_warn = sum(1 for f in findings if f["severity"] == "warn")
    if n_warn:
        notify(f"{n_warn} Befund(e) im Lauf vom {run_report['date']} - siehe {report_path}")

    print(f"\nReport: {report_path}")
    if report_path:
        with open(report_path, encoding="utf-8") as f:
            print(f.read())

    return 0


if __name__ == "__main__":
    sys.exit(main())
