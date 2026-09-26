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
import zoneinfo
from datetime import date, datetime

import numpy as np
import pandas as pd

import can_log_parser
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
LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Berlin")

DRIVER_MASS_KG = 86.0
EMPTY_MASS_KG = 1073.0
# Tankinhalt aus FLI (%): Kennlinie aus dem Tank 16.->26.09. (Voll-bis-Voll + CAN-Kraftstoffzaehler,
# docs/logs/can-bus-status.md "Kraftstoff absolut"): Liter = 4,0 + 1,10 * Fuel_Tank_roh, und
# FLI % = 2,486 * roh. Vorher FLI % * 45 l - gleiche Steigung, aber ohne die ~4 l unter "0 %".
FUEL_L_AT_FLI0 = 4.0
FUEL_L_PER_FLI_PCT = 1.10 / 2.486
FUEL_DENSITY_KG_L = 0.745
# Beifahrer-Zusatzmasse (2026-09-20, siehe DBC-Kommentar bei BO_832
# PassengerSeatOccupied_maybe / docs/logs/can-bus-status.md): wird anteilig zum
# belegten Zeitanteil der Fahrt aufaddiert, nicht binaer - siehe compute_mass().
# 75 kg sind gewogen, nicht geschaetzt (Nutzerangabe 2026-09-26).
PASSENGER_MASS_KG = 75.0


def run_script(args, errors, timeout=1800):
    """Fuehrt ein Analyseskript per subprocess aus. Nicht-Null-Exitcode
    oder Exception wird in errors gesammelt, der Lauf geht weiter (siehe
    Docstring oben). Gibt CompletedProcess oder None (bei Fehler) zurueck.

    Timeout grosszuegig (30min): das hier ist ein naechtlicher Batchlauf,
    ein zu knappes Limit kostet mehr als ein zu weites. Die fuenf
    langsamsten Schritte standen frueher auf 300s - also UNTER dem
    damaligen Default von 600s - und `build_datalake.py` lief am
    2026-09-15 genau deshalb in den Timeout. Schlimm daran war nicht der
    fehlende Datalake-Bau selbst, sondern dass jeder nachfolgende Schritt
    danach still auf dem TAGE ALTEN Datalake weiterrechnete (run_script
    sammelt den Fehler und macht weiter) - die Kurven-/Schaltergebnisse
    dieses Laufs waren dadurch unbemerkt veraltet. Gemessen 2026-09-15:
    build_datalake 380s, drivetrain_model_validation 425s,
    top_speed_validation 422s, coastdown_analysis 59s - und alle drei
    grossen wachsen mit jedem neuen Log weiter. 600s waeren schon in
    wenigen Wochen wieder zu knapp gewesen."""
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
    passwortlos per Key, siehe docs/logs/can-bus-status.md). False bei jedem
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


def _find_matching_can_log(dlg_log_id):
    """Sucht data/can/candump-*.log mit gleichem Datum wie dlg_log_id (Format
    'YYYY-MM-DD HHMMSS') und Startzeit innerhalb GPX_PAIR_TOLERANCE_S (dieselbe
    Toleranz wie bei _find_matching_gpx - der Pi startet candump typischerweise
    einige Sekunden vor der OBD-Fusion-App, siehe z.B. candump-2026-09-18_090404
    vs. dlg '2026-09-18 090449', 45s Abstand). None, falls kein Treffer - dann
    bleibt compute_mass() bei der reinen SOLO-Annahme."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2})(\d{2})(\d{2})", dlg_log_id)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    date_prefix = f"{y}-{mo}-{d}"
    dlg_s = int(h) * 3600 + int(mi) * 60 + int(s)
    best, best_diff = None, None
    for can_path in glob.glob(f"{CAN_DIR}/candump-{date_prefix}_*.log"):
        cm = re.match(rf"candump-{date_prefix}_(\d{{2}})(\d{{2}})(\d{{2}})\.log$", os.path.basename(can_path))
        if not cm:
            continue
        ch, cmi, cs = cm.groups()
        diff = abs((int(ch) * 3600 + int(cmi) * 60 + int(cs)) - dlg_s)
        if diff <= GPX_PAIR_TOLERANCE_S and (best_diff is None or diff < best_diff):
            best, best_diff = can_path, diff
    return best


def gunzip_or_recover(gz_path, out_path, errors):
    """Entpackt gz_path nach out_path. `gzip -dk` bricht bei einem
    truncated .gz (haerter Stromverlust auf dem Pi waehrend des Schreibens,
    siehe docs/logs/can-bus-status.md) mit "unexpected end of file" ab UND
    schreibt dabei gar keine Ausgabedatei - der gesamte Log ging damit
    bisher verloren, obwohl `gzip -dc` bis zum Bruchpunkt brauchbare Frames
    liefert (candump-2026-09-16/17 verifiziert: >99% der Bytes erhalten).
    Faellt bei einem echten Fehler auf Streaming-Dekompression zurueck;
    parse_candump() ueberspringt die dadurch abgeschnittene letzte Zeile
    ohnehin schon per try/except. Gibt True zurueck, wenn out_path danach
    Daten enthaelt."""
    try:
        subprocess.run(["gzip", "-dk", "-f", gz_path], check=True,
                        capture_output=True, timeout=60)
        return True
    except Exception:
        pass
    try:
        with open(out_path, "wb") as out:
            res = subprocess.run(["gzip", "-dc", gz_path], stdout=out,
                                  stderr=subprocess.PIPE, timeout=60)
    except Exception as e:
        errors.append((f"gunzip {gz_path}", f"Exception: {e}"))
        return False
    if os.path.getsize(out_path) == 0:
        os.remove(out_path)
        errors.append((f"gunzip {gz_path}", "leer/nicht rekonstruierbar (0 Bytes): "
                        + res.stderr.decode(errors="replace").strip()))
        return False
    errors.append((f"gunzip {gz_path}", "partial recovery: "
                    + res.stderr.decode(errors="replace").strip()))
    return True


CLOCKSTATE_RE = re.compile(r"^candump-(\d{4})-(\d{2})-(\d{2})_(\d{6})\.log$")


def _clockstate_warning(log_name):
    """Liest den zu einem CAN-Log gehoerigen Uhr-Marker (siehe session_logger.py
    write_clock_marker/restore_clock) und gibt eine Warnmeldung zurueck, falls die
    Uhr beim Start dieses Logs NICHT per NTP bestaetigt war - None, wenn der Marker
    "ntp" sagt oder gar nicht existiert (aeltere Logs vor 2026-09-15 haben keinen).
    Der Dateiname allein beweist nie, dass die Zeitstempel stimmen (siehe
    docs/logs/can-bus-status.md, "Viertes No-RTC-Vorkommnis") - dieser Marker ist
    die einzige Quelle, die das schon beim Schreiben auf dem Pi selbst festhaelt."""
    m = CLOCKSTATE_RE.match(log_name)
    if not m:
        return None
    y, mo, d, hms = m.groups()
    clockstate_path = os.path.join(CAN_DIR, f"clockstate-{y}{mo}{d}-{hms}.txt")
    if not os.path.exists(clockstate_path):
        return None
    with open(clockstate_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    state = lines[0] if lines else ""
    note = lines[1] if len(lines) > 1 else ""
    if state == "ntp":
        return None
    return f"Uhr beim Start NICHT per NTP bestaetigt ({state}): {note} Zeitstempel dieses Logs pruefen/gegen ein dlg synchronisieren, bevor sie als Fakt behandelt werden."


# Automatische Uhrkorrektur (2026-09-26, siehe docs/logs/can-bus-status.md "Automatische
# Uhrkorrektur"): Offset per VehicleSpeed (CAN) gegen GPS-Geschwindigkeit der dlg-Dateien.
CLOCK_ANCHOR_RE = re.compile(r"auf gespeicherte (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
CLOCK_FIX_MAX_RMSE_KMH = 5.0     # echte Treffer 26.09.: 3,3-3,4 km/h, Fehltreffer >= 9 km/h
CLOCK_FIX_MIN_POINTS = 300       # Mindestueberlappung in GPS-Punkten ...
CLOCK_FIX_MIN_FRACTION = 0.5     # ... oder Anteil der CAN-Log-Sekunden, was kleiner ist,
CLOCK_FIX_MIN_POINTS_FLOOR = 60  # aber nie weniger (kurze Logs passen sonst ueberall)
CLOCK_FIX_MIN_SPEED_STD = 5.0    # Stillstand passt auf jeden Offset -> Bewegung verlangen
CLOCK_FIX_SEPARATION_S = 30      # zweiter Treffer weiter weg als das -> mehrdeutig
CLOCK_FIX_GROUP_TOL_S = 5        # Logs eines Boots muessen sich so genau einig sein
CAN_TO_GPS_SPEED = 0.968         # VehicleSpeed eilt GPS ~3 % vor (Fahrtag 26.09.)
GPS_SPEED_LAG_S = 1              # Handy-GPS-Speed haengt 0,8-2,7 s nach (gegen KnockRetard, 26.09.)
DOTNET_EPOCH_TICKS = 621355968000000000


def _can_speed_1hz(log_path):
    """VehicleSpeed (0x202 HS_PCM, Byte2-3 BE * 0,01) als Serie {Pi-Epoch-Sekunde: km/h}."""
    df = can_log_parser.parse_candump(log_path)
    sub = df[df["can_id"] == 0x202]
    v = sub["data"].map(lambda d: int.from_bytes(d[2:4], "big") * 0.01 * CAN_TO_GPS_SPEED)
    return v.groupby(sub["t"].astype(int)).mean()


def _dlg_gps_speed_1hz(dlg_path):
    """GPS-Geschwindigkeit aus der dlg als {UTC-Epoch-Sekunde: km/h} (Time = .NET-Ticks UTC)."""
    con = sqlite3.connect(dlg_path)
    try:
        rows = con.execute("""SELECT pde.Time, pde.Value FROM PidDataEntry pde
            JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
            WHERE pme.PidName = 'GPS-Geschwindigkeit'""").fetchall()
    finally:
        con.close()
    s = pd.Series([r[1] for r in rows], index=[(r[0] - DOTNET_EPOCH_TICKS) // 10**7 for r in rows], dtype=float)
    return s.groupby(level=0).mean()


def _offset_candidates(can_s, gps_s):
    """Alle Offsets L (wahre Zeit = Pi-Zeit + L, 1-s-Raster, L >= -120 s, weil die Standzeit
    nur nach vorn verschiebt), die Mindestueberlappung und Bewegung erfuellen: [(rmse, L, n)].
    ponytail: Brute Force ueber alle Lags, O(len_can * len_gps); reicht fuer einige dlg pro Lauf."""
    if can_s.empty or gps_s.empty:
        return []
    c0, g0 = int(can_s.index.min()), int(gps_s.index.min())
    c = can_s.reindex(range(c0, int(can_s.index.max()) + 1)).to_numpy()
    g = gps_s.reindex(range(g0, int(gps_s.index.max()) + 1)).to_numpy()
    need = max(CLOCK_FIX_MIN_POINTS_FLOOR, min(CLOCK_FIX_MIN_POINTS, CLOCK_FIX_MIN_FRACTION * can_s.size))
    out = []
    for k in range(max(1 - len(c), c0 - g0 - 120), len(g)):  # k: gps-Index minus can-Index
        i0, i1 = max(0, -k), min(len(c), len(g) - k)
        cc, gg = c[i0:i1], g[i0 + k:i1 + k]
        ok = ~(np.isnan(cc) | np.isnan(gg))
        n = int(ok.sum())
        if n < need or gg[ok].std() < CLOCK_FIX_MIN_SPEED_STD:
            continue
        out.append((float(np.sqrt(np.mean((cc[ok] - gg[ok]) ** 2))), g0 + k - c0 - GPS_SPEED_LAG_S, n))
    return out


def _find_clock_offset(log_id, gps_cache):
    """Eindeutiger Treffer (rmse, L, n, dlg) oder None: bester Lag unter der RMSE-Schwelle und
    kein weiterer darunter, der mehr als CLOCK_FIX_SEPARATION_S davon entfernt liegt."""
    can_s = _can_speed_1hz(os.path.join(CAN_DIR, f"{log_id}.log"))
    claimed = datetime.strptime(log_id, "candump-%Y-%m-%d_%H%M%S").replace(tzinfo=LOCAL_TZ).timestamp()
    cands = []
    for dlg_path in sorted(glob.glob(f"{RAW_DIR}/*.dlg")):
        try:
            dlg_start = datetime.strptime(os.path.basename(dlg_path)[:-4], "%Y-%m-%d %H%M%S")
        except ValueError:
            continue
        if dlg_start.replace(tzinfo=LOCAL_TZ).timestamp() < claimed - 86400:
            continue  # Standzeit verschiebt nur nach vorn
        if dlg_path not in gps_cache:
            gps_cache[dlg_path] = _dlg_gps_speed_1hz(dlg_path)
        cands += [(*c, os.path.basename(dlg_path)) for c in _offset_candidates(can_s, gps_cache[dlg_path])]
    good = [c for c in cands if c[0] <= CLOCK_FIX_MAX_RMSE_KMH]
    if not good:
        return None
    best = min(good)
    if any(abs(c[1] - best[1]) > CLOCK_FIX_SEPARATION_S for c in good):
        return None
    return best


def _clockstate_name(log_id):
    y, mo, d, hms = CLOCKSTATE_RE.match(f"{log_id}.log").groups()
    return f"clockstate-{y}{mo}{d}-{hms}.txt"


def _rename_can_log(old_id, new_id, errors):
    """Benennt .log/.log.gz/clockstate auf dem Pi (root-owned -> sudo) und lokal zusaetzlich
    _decoded.csv um. Pi zuerst: behielte er den alten Namen, holte der naechste Sync das Log
    erneut als "neu". Bricht ab, wenn ein Zielname schon existiert."""
    names = [(f"{old_id}.log", f"{new_id}.log"), (f"{old_id}.log.gz", f"{new_id}.log.gz"),
             (_clockstate_name(old_id), _clockstate_name(new_id))]
    local = names + [(f"{old_id}_decoded.csv", f"{new_id}_decoded.csv")]
    if any(os.path.exists(os.path.join(CAN_DIR, b)) for _, b in local):
        errors.append((f"{old_id}.log", f"Uhrkorrektur: Ziel {new_id} existiert lokal schon, nichts umbenannt."))
        return False
    q = shlex.quote
    remote_cmd = f"cd {q(PI_CANLOGS_DIR)} && " + " && ".join(
        f"if [ -e {q(a)} ]; then [ ! -e {q(b)} ] && sudo mv {q(a)} {q(b)}; fi" for a, b in names)
    try:
        res = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", PI_HOST, remote_cmd],
                             capture_output=True, text=True, timeout=30)
    except Exception as e:
        res = subprocess.CompletedProcess([], 1, "", str(e))
    if res.returncode != 0:
        errors.append((f"{old_id}.log", f"Uhrkorrektur -> {new_id}: Umbenennen auf dem Pi fehlgeschlagen "
                       f"({res.stderr[-300:]}), lokal nichts geaendert."))
        return False
    for a, b in local:
        if os.path.exists(os.path.join(CAN_DIR, a)):
            os.rename(os.path.join(CAN_DIR, a), os.path.join(CAN_DIR, b))
    return True


def fix_can_log_clocks(log_ids, errors):
    """Fuer neue CAN-Logs mit Uhr-Marker "korrigiert" (Pi ohne NTP, Uhr auf den letzten Anker
    gestellt -> Name UND Frames um die Standzeit zu frueh): Offset gegen die dlg bestimmen, Logs
    desselben Boots (gleicher Anker) gemeinsam. Eindeutig -> umbenennen (Pi + lokal +
    can_gps_pairs.json), sonst die bisherige clockstate-Warnung. Gibt (log_ids mit neuen
    Namen, Info-Meldungen) zurueck."""
    groups, warnings = {}, {}
    for log_id in log_ids:
        warning = _clockstate_warning(f"{log_id}.log")
        if not warning:
            continue
        warnings[log_id] = warning
        anchor = CLOCK_ANCHOR_RE.search(warning)
        if "(korrigiert)" in warning and anchor:
            groups.setdefault(anchor.group(1), []).append(log_id)
        else:
            errors.append((f"{log_id}.log", warning))

    gps_cache, renamed, messages = {}, {}, []
    for anchor, members in groups.items():
        hits = {m: h for m in members if (h := _find_clock_offset(m, gps_cache))}
        offsets = [h[1] for h in hits.values()]
        if not hits or max(offsets) - min(offsets) > CLOCK_FIX_GROUP_TOL_S:
            reason = ("kein eindeutiger dlg-Treffer" if not hits
                      else f"Logs desselben Boots uneinig (Offsets {sorted(offsets)} s)")
            errors += [(f"{m}.log", f"{warnings[m]} Automatische Korrektur: {reason}.") for m in members]
            continue
        rmse, offset, n, dlg = min(hits.values())
        for m in members:
            claimed = datetime.strptime(m, "candump-%Y-%m-%d_%H%M%S").replace(tzinfo=LOCAL_TZ).timestamp()
            new_id = datetime.fromtimestamp(claimed + offset, LOCAL_TZ).strftime("candump-%Y-%m-%d_%H%M%S")
            if _rename_can_log(m, new_id, errors):
                renamed[m] = new_id
                messages.append(f"Pi-Uhr ohne NTP korrigiert: {m} -> {new_id} (Offset {offset:+d} s, "
                                f"Boot-Anker {anchor}, Beleg dlg '{dlg}' mit {n} GPS-Punkten, "
                                f"Speed-RMSE {rmse:.1f} km/h{'' if m in hits else ', Offset vom selben Boot uebernommen'})")

    if renamed and os.path.exists(CAN_GPS_PAIRS_OVERRIDE_PATH):
        with open(CAN_GPS_PAIRS_OVERRIDE_PATH, encoding="utf-8") as f:
            pairs = json.load(f)
        for old, new in renamed.items():
            pairs.pop(f"{old}.log", None)
            pairs[f"{new}.log"] = _find_matching_gpx(new) or ""
        with open(CAN_GPS_PAIRS_OVERRIDE_PATH, "w", encoding="utf-8") as f:
            json.dump(pairs, f, indent=2, ensure_ascii=False, sort_keys=True)
    return [renamed.get(i, i) for i in log_ids], messages


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
    # uebergeben, dann fasst ssh nichts mehr zusammen. Holt zusaetzlich die
    # clockstate-*.txt-Marker (session_logger.py schreibt einen pro Log,
    # siehe write_clock_marker) - klein, immer alle neuen mitnehmen statt
    # gezielt zu matchen.
    remote_cmd = (f"find {shlex.quote(PI_CANLOGS_DIR)} -maxdepth 1 "
                  f"\\( -name 'candump-*.log*' -mmin +5 -o -name 'clockstate-*.txt' \\) "
                  f"-printf '%f\\n'")
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
    local_clockstates = {os.path.basename(p) for p in glob.glob(f"{CAN_DIR}/clockstate-*.txt")}

    def _is_new(fname):
        if fname.endswith(".txt"):
            return fname not in local_clockstates
        return fname.removesuffix(".gz") not in local_logs

    new_remote = sorted(f for f in remote_files if _is_new(f))
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
        if fname.endswith(".txt"):
            continue  # clockstate-Marker - nur mitkopiert, unten separat ausgewertet
        local_path = os.path.join(CAN_DIR, fname)
        if fname.endswith(".gz"):
            log_name = fname[:-3]
            if not gunzip_or_recover(local_path, os.path.join(CAN_DIR, log_name), errors):
                continue
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
    docs/logs/projekt-stand.md "Fahrzeuggewicht"). None, falls kein FLI-Kanal."""
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
    fuel_kg = (FUEL_L_AT_FLI0 + FUEL_L_PER_FLI_PCT * level_pct) * FUEL_DENSITY_KG_L
    mass_kg = EMPTY_MASS_KG + DRIVER_MASS_KG + fuel_kg
    note = f"FLI ~{start_pct:.1f}%->~{end_pct:.1f}%"

    can_path = _find_matching_can_log(log_id)
    if can_path is not None:
        occupied_s, total_s = can_log_parser.passenger_occupied_seconds(can_path)
        if total_s > 0:
            occupied_frac = occupied_s / total_s
            mass_kg += PASSENGER_MASS_KG * occupied_frac
            note += (f", Beifahrer {occupied_frac:.0%} der Fahrt "
                     f"({os.path.basename(can_path)}, PassengerSeatOccupied_maybe)")
        else:
            note += ", CAN-Log ohne 0x340-Belegungsdaten (SOLO-Annahme)"
    else:
        note += ", kein CAN-Log gefunden (SOLO-Annahme)"

    return round(mass_kg, 1), note


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
    new_can_logs, clock_fixes = fix_can_log_clocks(new_can_logs, errors)

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
        run_script([PYTHON, "scripts/build_datalake.py"], errors)

    previous_shift_best = load_json("shift_time_best.json") or {}
    if new_can_logs:
        run_script([PYTHON, "scripts/shift_time_analysis.py"], errors)
        # beide haengen an shift_time_analysis.py's Output (results/shift_time_
        # analysis_summary.json), deshalb direkt danach in derselben Bedingung
        run_script([PYTHON, "scripts/shift_traction_gap_analysis.py"], errors)
        run_script([PYTHON, "scripts/clutch_ride_detection.py"], errors)
        # zieht sich selbst ALLE Logs mit LongitudinalAcc_CAN aus dem (gerade
        # aktualisierten) Datalake, nicht nur new_can_logs - Plot/Summary
        # sind damit immer auf dem vollen, aktuellen CAN-Datenstand
        run_script([PYTHON, "scripts/can_traction_circle.py"], errors)
    current_shift_best = load_json("shift_time_best.json") or {}

    previous_corner_peak_best = load_json("corner_peak_best.json") or {}
    if new_can_logs:
        for log_id in new_can_logs:
            run_script([PYTHON, "scripts/can_corner_event_analysis.py", log_id], errors)
        run_script([PYTHON, "scripts/corner_peak_tracker.py"], errors)
    current_corner_peak_best = load_json("corner_peak_best.json") or {}

    if new_logs:
        run_script([PYTHON, "scripts/partial_load_model.py"], errors)
        run_script([PYTHON, "scripts/drivetrain_model_validation.py"], errors)
        run_script([PYTHON, "scripts/top_speed_validation.py"], errors)
        run_script([PYTHON, "scripts/check_dgm_coverage_gaps.py"], errors)

    previous_coastdown = load_json("coastdown_analysis_summary.json") or []
    if new_logs:
        run_script([PYTHON, "scripts/coastdown_analysis.py", *new_logs], errors)
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
                run_script([PYTHON, "scripts/steering_zero_offset.py"], errors)
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
    findings += [pipeline_checks._finding("info", "can_clock_fixed", m) for m in clock_fixes]

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
