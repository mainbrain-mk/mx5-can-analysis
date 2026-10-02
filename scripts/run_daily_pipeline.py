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
from datetime import date, datetime, timezone

import duckdb
import numpy as np
import pandas as pd

import can_log_parser
from build_datalake import KNOWN_CAN_LOG_DUPLICATES
import pipeline_checks
import render_report

RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"
CAN_DIR = "data/can"
RESULTS_DIR = "results"
MASS_OVERRIDE_PATH = "data/log_mass_overrides.json"
CAN_GPS_PAIRS_OVERRIDE_PATH = "data/can_gps_pairs.json"
# CAN-Logs gelten nur im Lauf ihres Downloads als neu - bis zum Lauf-Ende hier vorgemerkt,
# damit ein Absturz sie nicht still verliert (02.10.: 3 Logs nach Crash nie ausgewertet)
PENDING_CAN_LOGS_PATH = "data/pending_can_logs.json"
PI_HOST = "pi@192.168.0.247"
PI_CANLOGS_DIR = "/home/pi/canlogs"
GPX_PAIR_TOLERANCE_S = 180
GPX_TIME_SPEED_RE = re.compile(r"<time>([^<]+)</time><speed>([^<]+)</speed>")
PYTHON = ".venv/bin/python"
LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Berlin")

DRIVER_MASS_KG = 86.0
EMPTY_MASS_KG = 1073.0
# Tankinhalt aus CAN Fuel_Tank (0x09E): Kennlinie aus dem Tank 16.->26.09. (Voll-bis-Voll +
# CAN-Kraftstoffzaehler, docs/logs/can-bus-status.md "Kraftstoff absolut"): Liter = 3,0 + 1,10 * roh
# (voll = 45 l laut Mazda). Bis 02.10. kam der Tank aus dem FLI-Kanal der Handy-.dlg - den fragt das
# Handy seit 26.09. nicht mehr ab, CAN-only-Fahrten bekamen gar keine Masse.
FUEL_L_AT_RAW0 = 3.0
FUEL_L_PER_RAW = 1.10
DATALAKE_PATH = "data/datalake.duckdb"
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


def rclone_sync_new_logs(errors, ext=".dlg", dest=RAW_DIR):
    """Schritt 0: neue <ext>-Dateien aus dem Drive-Ordner "Loggs" nach
    dest laden (Remote per root_folder_id fest darauf gesetzt, siehe
    ~/.config/rclone/rclone.conf): .dlg nach data/raw/, seit 01.10. auch die
    BasicAirData-.gpx nach data/can/. Gibt die Liste der heruntergeladenen
    Dateinamen (ohne Endung) zurueck, oder [] bei Fehler/nichts Neuem."""
    local = {os.path.basename(p)[:-len(ext)] for p in glob.glob(f"{dest}/*{ext}")}
    try:
        res = subprocess.run(["rclone", "lsf", "gdrive:", "--files-only"],
                              capture_output=True, text=True, timeout=60)
    except Exception as e:
        errors.append(("rclone lsf", f"Exception: {e}"))
        return []
    if res.returncode != 0:
        errors.append(("rclone lsf", f"exit code {res.returncode}: {res.stderr[-300:]}"))
        return []

    on_drive = {line[:-len(ext)] for line in res.stdout.splitlines() if line.endswith(ext)}
    missing = sorted(on_drive - local)
    if not missing:
        return []

    cmd = ["rclone", "copy", "gdrive:", dest]
    for log_id in missing:
        cmd += ["--include", f"{log_id}{ext}"]
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


def _gpx_speed_1hz(gpx_path):
    """GPS-Geschwindigkeit aus einer BasicAirData-GPX als {UTC-Epoch-Sekunde: km/h}."""
    with open(gpx_path, encoding="utf-8") as f:
        rows = GPX_TIME_SPEED_RE.findall(f.read())
    idx = [int(datetime.fromisoformat(t).timestamp()) for t, _ in rows]
    return pd.Series([float(v) * 3.6 for _, v in rows], index=idx, dtype=float).groupby(level=0).mean()


def _can_log_span(log_id):
    """(Start, Ende) als Epoch: Start aus dem (ggf. korrigierten) Namen, Dauer aus erster/letzter
    candump-Zeile - der Name ist nach einer Uhrkorrektur verlaesslicher als die Frames."""
    start = datetime.strptime(log_id, "candump-%Y-%m-%d_%H%M%S").replace(tzinfo=LOCAL_TZ).timestamp()
    stamps = []
    with open(os.path.join(CAN_DIR, f"{log_id}.log"), "rb") as f:
        head = f.readline()
        f.seek(max(0, os.path.getsize(f.name) - 4096))
        for line in [head, *f.read().splitlines()]:
            try:
                stamps.append(_candump_ts_us(line.decode()) / 1e6)
            except (ValueError, UnicodeDecodeError):
                pass  # abgeschnittene Zeile
    return start, start + (max(stamps) - stamps[0] if stamps else 0)


def _find_matching_gpx(log_id):
    """GPX in data/can/, die sich zeitlich am laengsten mit dem CAN-Log ueberlappt (ein Track
    kann mehrere CAN-Logs abdecken und spaeter als candump starten, 27.09.: 115642.gpx deckt
    _114812 und _125452 ab). Zeiten aus dem GPX-Inhalt (UTC), nicht aus dem Dateinamen.
    None, falls keine Ueberlappung - der CAN-Log wird dann ohne GPS-Track (CAN-only) registriert."""
    if not re.match(r"candump-\d{4}-\d{2}-\d{2}_\d{6}$", log_id):
        return None
    c0, c1 = _can_log_span(log_id)
    best, best_overlap = None, 0
    for gpx_path in glob.glob(f"{CAN_DIR}/*.gpx"):
        g = _gpx_speed_1hz(gpx_path)
        if g.empty:
            continue
        overlap = min(c1, g.index.max()) - max(c0, g.index.min())
        if overlap > best_overlap:
            best, best_overlap = os.path.basename(gpx_path), overlap
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
    "ntp"/"rtc" sagt oder gar nicht existiert (aeltere Logs vor 2026-09-15 haben keinen).
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
    # "rtc": DS3231 seit 28.09. aktiv und vom Nutzer als gueltige Uhrquelle bestaetigt
    if state in ("ntp", "rtc", JUMP_FIXED_STATE):
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
    con = sqlite3.connect(f"file:{dlg_path}?mode=ro", uri=True)
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
    for gpx_path in sorted(glob.glob(f"{CAN_DIR}/*.gpx")):  # BasicAirData-Track, 27.09.: einzige Quelle nachmittags
        if gpx_path not in gps_cache:
            gps_cache[gpx_path] = _gpx_speed_1hz(gpx_path)
        if gps_cache[gpx_path].empty or gps_cache[gpx_path].index.max() < claimed:
            continue  # Standzeit verschiebt nur nach vorn
        cands += [(*c, os.path.basename(gpx_path)) for c in _offset_candidates(can_s, gps_cache[gpx_path])]
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
            if new_id == m:  # Uhr lief schon richtig (NTP kam vor diesem Log, Marker ist vom Logger-Start)
                messages.append(f"Pi-Uhr bestaetigt: {m} (Offset {offset:+d} s, dlg '{dlg}')")
                continue
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


# Uhrsprung mitten im Log (2026-09-27, siehe docs/logs/can-bus-status.md "Sprungkorrektur"): kommt der
# NTP-Sync erst waehrend der Fahrt, springen die candump-Zeitstempel um die Uhrabweichung nach vorn.
# Bei laufendem Bus liegen Frames im ms-Takt (groesste normale Luecke im Bestand 1,27 s), eine Luecke
# > 5 s ist also der Sprung. Der Teil danach ist NTP-richtig -> Teil davor + Dateiname um den Sprung
# nachziehen. Im Bestand genau ein Fall (candump-2026-09-11_201950, +67588,9 s = Duplikat von _150619).
CLOCK_JUMP_MIN_US = 5 * 10**6
CLOCK_JUMP_BACK_US = -1 * 10**6
JUMP_FIXED_STATE = "ntp_sprung_korrigiert"


def _candump_ts_us(line):
    """'(1789150790.920951) can0 ...' -> 1789150790920951 (ganzzahlig, float verliert hier die us)."""
    sec, usec = line[1:line.index(")")].split(".")
    return int(sec) * 10**6 + int(usec)


def find_clock_jump(log_path):
    """-> (Zeilennummer des ersten Frames nach dem Sprung, Sprung in us) oder None. ValueError bei
    mehreren Spruengen oder einem Rueckwaertssprung - dann lieber nichts anfassen und melden."""
    jumps, prev, last_dt = [], None, 0
    with open(log_path) as f:
        for i, line in enumerate(f):
            try:
                ts = _candump_ts_us(line)
            except ValueError:
                continue  # abgeschnittene letzte Zeile (Stromverlust), wie parse_candump()
            if prev is not None:
                dt = ts - prev
                if CLOCK_JUMP_BACK_US <= dt <= CLOCK_JUMP_MIN_US:
                    last_dt = dt
                else:  # Luecke minus normaler Frame-Abstand davor = Uhrsprung (auf ~1 ms genau)
                    jumps.append((i, dt - last_dt))
            prev = ts
    if not jumps:
        return None
    if len(jumps) > 1 or jumps[0][1] < 0:
        raise ValueError(", ".join(f"Zeile {i}: {d / 1e6:+.3f} s" for i, d in jumps))
    return jumps[0]


# Seit der RTC (2026-09-28) springt die Uhr beim NTP-Sync nur noch um Sekunden, auch rueckwaerts -
# das faellt als Luecke nicht auf. session_logger.py protokolliert den Sprung deshalb selbst
# (clockjump-*.txt: Sprung in us aus Wand- minus Monotonuhr, Wanduhr davor, laufendes Log).
RECORDED_JUMP_WINDOW_US = 2 * 10**6   # Frame-Zeit vs. Protokoll-Zeitpunkt
RECORDED_JUMP_TOL_US = 50_000         # Luecke am Sprung = Sprung + normaler Frame-Abstand (~1 ms)


def _clock_jump_record(log_id):
    # ponytail: nur der erste Sprung je Log; offline gebootet gibt es genau einen (RTC -> NTP).
    # Mehrere Protokolle fuer ein Log erst behandeln, wenn das real vorkommt.
    for p in sorted(glob.glob(f"{CAN_DIR}/clockjump-*.txt")):
        with open(p, encoding="utf-8") as f:
            rec = dict(l.split("=", 1) for l in f.read().split())
        if rec.get("log") == f"{log_id}.log":
            return os.path.basename(p), int(rec["jump_us"]), round(float(rec["wall_before"]) * 1e6)
    return None


def find_recorded_jump(log_path, jump_us, wall_before_us):
    """-> (Zeilennummer des ersten Frames nach dem Sprung, jump_us) oder None: die Stelle nahe
    wall_before, an der der Frame-Abstand dem protokollierten Sprung am naechsten kommt."""
    best, prev = None, None
    with open(log_path) as f:
        for i, line in enumerate(f):
            try:
                ts = _candump_ts_us(line)
            except ValueError:
                continue
            if prev is not None and abs(prev - wall_before_us) <= RECORDED_JUMP_WINDOW_US:
                err = abs(ts - prev - jump_us)
                if best is None or err < best[0]:
                    best = (err, i)
            prev = ts
    if best is None or best[0] > RECORDED_JUMP_TOL_US:
        return None
    return best[1], jump_us


def fix_clock_jump(log_id, errors):
    """Korrigiert einen Uhrsprung im frisch geholten Log: erst umbenennen (Pi + lokal, wie
    fix_can_log_clocks), dann die Zeitstempel vor dem Sprung verschieben und den Marker auf
    JUMP_FIXED_STATE setzen. Das Original bleibt als .log.gz lokal und auf dem Pi erhalten.
    Gibt die (ggf. neue) log_id zurueck."""
    path = os.path.join(CAN_DIR, f"{log_id}.log")
    record = _clock_jump_record(log_id)
    if record:
        rec_name, rec_jump_us, wall_before_us = record
        jump = find_recorded_jump(path, rec_jump_us, wall_before_us)
        if jump is None:
            errors.append((f"{log_id}.log", f"{rec_name}: Sprung {rec_jump_us / 1e6:+.6f} s im Log nicht "
                           f"wiedergefunden, nicht korrigiert."))
            return log_id
        source = f"protokolliert in {rec_name}, auf 1 us"
    else:
        try:
            jump = find_clock_jump(path)
        except ValueError as e:
            errors.append((f"{log_id}.log", f"Mehrere/rueckwaerts gerichtete Uhrspruenge ({e}), nicht korrigiert."))
            return log_id
        if jump is None:
            return log_id
        source = "aus der Luecke geschaetzt, auf ~1 ms"
    line_no, jump_us = jump
    claimed = datetime.strptime(log_id, "candump-%Y-%m-%d_%H%M%S").replace(tzinfo=LOCAL_TZ).timestamp()
    new_id = datetime.fromtimestamp(claimed + round(jump_us / 1e6), LOCAL_TZ).strftime("candump-%Y-%m-%d_%H%M%S")
    marker = os.path.join(CAN_DIR, _clockstate_name(log_id))
    old_marker = "kein Marker"
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            old_marker = f.read().strip().replace("\n", " | ")
    if new_id != log_id and not _rename_can_log(log_id, new_id, errors):
        return log_id
    path = os.path.join(CAN_DIR, f"{new_id}.log")
    with open(path) as src, open(path + ".tmp", "w") as dst:
        for i, line in enumerate(src):
            if i < line_no:
                try:
                    t = _candump_ts_us(line) + jump_us
                    line = f"({t // 10**6:010d}.{t % 10**6:06d}){line[line.index(')') + 1:]}"
                except ValueError:
                    pass
            dst.write(line)
    os.replace(path + ".tmp", path)
    with open(os.path.join(CAN_DIR, _clockstate_name(new_id)), "w", encoding="utf-8") as f:
        f.write(f"{JUMP_FIXED_STATE}\nUhrsprung {jump_us / 1e6:+.6f} s ({source}) vor Frame-Zeile {line_no} (NTP-Sync waehrend "
                f"der Fahrt); Zeitstempel davor und Name nachtraeglich korrigiert, vorher {log_id} "
                f"[{old_marker}]. Unveraenderte Zeitstempel: {new_id}.log.gz.\n")
    print(f"Uhrsprung korrigiert: {log_id} -> {new_id} ({jump_us / 1e6:+.3f} s ab Zeile {line_no})")
    return new_id


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
                  f"\\( -name 'candump-*.log*' -mmin +5 -o -name 'clockstate-*.txt' -o -name 'clockjump-*.txt' \\) "
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

    # nur fertige Dateien: .log.gz.tmp (Kompression von session_logger.py lief bzw.
    # wurde per Stromausfall abgebrochen, 27.09.) wuerde sonst als Log behandelt
    remote_files = [l for l in res.stdout.splitlines() if l.endswith((".log", ".log.gz", ".txt"))]
    local_logs = {os.path.basename(p) for p in glob.glob(f"{CAN_DIR}/candump-*.log")}
    local_clockstates = {os.path.basename(p) for p in glob.glob(f"{CAN_DIR}/clock*-*.txt")}

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
            continue  # clockstate-/clockjump-Dateien - nur mitkopiert, separat ausgewertet
        local_path = os.path.join(CAN_DIR, fname)
        if fname.endswith(".gz"):
            log_name = fname[:-3]
            if not gunzip_or_recover(local_path, os.path.join(CAN_DIR, log_name), errors):
                continue
        else:
            log_name = fname
        log_id = fix_clock_jump(os.path.splitext(log_name)[0], errors)  # vor dem Dekodieren
        log_name = f"{log_id}.log"

        res = run_script([PYTHON, "scripts/can_log_parser.py", os.path.join(CAN_DIR, log_name)], errors)
        if res is None:
            continue

        pairs_override[log_name] = _find_matching_gpx(log_id) or ""
        new_log_ids.append(log_id)

    with open(CAN_GPS_PAIRS_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        json.dump(pairs_override, f, indent=2, ensure_ascii=False, sort_keys=True)

    return new_log_ids


def pair_late_gpx():
    """CAN-Logs mit leerer GPX-Zuordnung erneut zuordnen - fuer GPX, die erst nach ihrem CAN-Log
    im Drive landen (01.10.: 20261001-135802.gpx). Nur bestehende leere Eintraege; die Uhrzeit-
    pruefung (fix_can_log_clocks) laeuft fuer diese alten Logs nicht nochmal. Gibt die neu
    zugeordneten Logs als {log_name: gpx} zurueck."""
    if not os.path.exists(CAN_GPS_PAIRS_OVERRIDE_PATH):
        return {}
    with open(CAN_GPS_PAIRS_OVERRIDE_PATH, encoding="utf-8") as f:
        pairs = json.load(f)
    paired = {}
    for log_name, gpx in pairs.items():
        if gpx or log_name in KNOWN_CAN_LOG_DUPLICATES or not os.path.exists(os.path.join(CAN_DIR, log_name)):
            continue
        gpx = _find_matching_gpx(log_name.removesuffix(".log"))
        if gpx:
            pairs[log_name] = paired[log_name] = gpx
    if paired:
        with open(CAN_GPS_PAIRS_OVERRIDE_PATH, "w", encoding="utf-8") as f:
            json.dump(pairs, f, indent=2, ensure_ascii=False, sort_keys=True)
    return paired


def _merge_pending_can_logs(new_can_logs):
    """Vom letzten (abgestuerzten) Lauf liegengebliebene CAN-Logs dazunehmen und die Gesamtliste
    vormerken; main() loescht die Vormerkung erst nach dem Report. Nach Uhrkorrektur
    umbenannte/geloeschte Logs fallen raus."""
    pending = []
    if os.path.exists(PENDING_CAN_LOGS_PATH):
        with open(PENDING_CAN_LOGS_PATH, encoding="utf-8") as f:
            pending = json.load(f)
    merged = sorted(set(new_can_logs) | {l for l in pending if os.path.exists(os.path.join(CAN_DIR, f"{l}.log"))})
    with open(PENDING_CAN_LOGS_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f)
    return merged


def find_new_logs():
    """Schritt 1: Logs in data/raw/, die noch keine
    data/derived/<name>_vibration_summary.json haben. Leere .dlg ueberspringen: sqlite3.connect()
    auf einen falschen Pfad legt die Datei leer an (02.10.: candump-...dlg aus einem Handlauf)."""
    processed = {
        os.path.basename(p)[:-len("_vibration_summary.json")]
        for p in glob.glob(f"{DERIVED_DIR}/*_vibration_summary.json")
    }
    raw = {os.path.basename(p)[:-4] for p in glob.glob(f"{RAW_DIR}/*.dlg") if os.path.getsize(p) > 0}
    return sorted(raw - processed)


def compute_mass(can_log_id):
    """Masse einer CAN-Fahrt: Leergewicht + Fahrer + Tank (Median von FuelTank_CAN_raw im Datalake,
    ohne die 0-Initwerte - der Geber schwappt um +-3 roh) + Beifahrer anteilig nach Belegungszeit
    (0x340). None, falls das Log keinen Tankwert hat. Braucht den frisch gebauten Datalake."""
    con = duckdb.connect(DATALAKE_PATH, read_only=True)
    try:
        raw = con.execute("SELECT median(value) FROM measurements WHERE log_id = ? "
                          "AND channel = 'FuelTank_CAN_raw' AND value > 0", [can_log_id]).fetchone()[0]
    finally:
        con.close()
    if raw is None:
        return None
    fuel_l = FUEL_L_AT_RAW0 + FUEL_L_PER_RAW * raw
    mass_kg = EMPTY_MASS_KG + DRIVER_MASS_KG + fuel_l * FUEL_DENSITY_KG_L
    note = f"Tank ~{fuel_l:.1f} l (CAN Fuel_Tank Median {raw:.1f} roh)"

    occupied_s, total_s = can_log_parser.passenger_occupied_seconds(os.path.join(CAN_DIR, f"{can_log_id}.log"))
    if total_s > 0:
        occupied_frac = occupied_s / total_s
        mass_kg += PASSENGER_MASS_KG * occupied_frac
        note += f", Beifahrer {occupied_frac:.0%} der Fahrt (0x340)"
    else:
        note += ", ohne 0x340-Belegungsdaten (SOLO-Annahme)"

    return round(mass_kg, 1), note


def update_mass_overrides(new_logs, errors):
    overrides = {}
    if os.path.exists(MASS_OVERRIDE_PATH):
        with open(MASS_OVERRIDE_PATH, encoding="utf-8") as f:
            overrides = json.load(f)
    computed = {}
    for log_id in new_logs:
        try:
            result = compute_mass(log_id)
        except Exception as e:  # ein kaputtes Log darf nicht den ganzen Lauf abbrechen (02.10.)
            errors.append((f"Masse {log_id}", f"Exception: {e}"))
            continue
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
    new_gpx = rclone_sync_new_logs(errors, ".gpx", CAN_DIR)
    late_pairs = pair_late_gpx() if new_gpx else {}  # vor dem Pi-Sync: neue CAN-Logs paart der selbst
    new_logs = find_new_logs()
    new_can_logs = sync_can_logs_from_pi(errors)
    new_can_logs, clock_fixes = fix_can_log_clocks(new_can_logs, errors)
    new_can_logs = _merge_pending_can_logs(new_can_logs)

    if new_gpx:
        print(f"{len(new_gpx)} neue GPX aus Google Drive geladen: {new_gpx}")
    if late_pairs:
        print(f"GPX nachtraeglich zugeordnet: {late_pairs}")

    if not new_logs and not new_can_logs:
        if late_pairs and run_script([PYTHON, "scripts/build_datalake.py"], errors) is None:
            print(f"FEHLER build_datalake: {errors[-1][1]}")
            return 1
        print("Keine neuen Logs gefunden.")
        return 0

    if new_logs:
        print(f"{len(new_logs)} neue(s) Log(s): {new_logs}")
        if downloaded:
            print(f"  davon aus Google Drive geladen: {downloaded}")
    if new_can_logs:
        print(f"{len(new_can_logs)} neue(s) CAN-Log(s) vom Raspberry Pi geladen: {new_can_logs}")


    for log_id in new_logs:
        run_script([PYTHON, "scripts/vibration_analysis.py", f"{log_id}.dlg"], errors)

    if new_logs:
        run_script([PYTHON, "scripts/brake_event_analysis.py", *new_logs], errors)
        run_script([PYTHON, "scripts/corner_event_analysis.py", *new_logs], errors)

    if new_logs or new_can_logs:
        run_script([PYTHON, "scripts/build_datalake.py"], errors)
    masses = update_mass_overrides(new_can_logs, errors)

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
            # CAN-Gegenstuecke zu brake_event_analysis.py / vibration_analysis.py (02.10.)
            run_script([PYTHON, "scripts/can_brake_event_analysis.py", log_id], errors)
            run_script([PYTHON, "scripts/can_vibration_analysis.py", log_id], errors)
        run_script([PYTHON, "scripts/corner_peak_tracker.py"], errors)
    current_corner_peak_best = load_json("corner_peak_best.json") or {}

    # Modellpruefungen laufen fuer CAN- UND Handy-Logs (02.10.: vorher nur bei neuer .dlg - seit 27.09.
    # kommt keine mehr, CAN-Fahrten blieben ungeprueft). Kanaele loest datalake_channels.py auf.
    analysis_logs = sorted(new_logs + new_can_logs)
    # Die Skripte cachen ihr Ergebnis je Log (per_log_cache.py) - neu gerechnet werden nur neue/
    # geaenderte Logs, die Zusammenfassung ueber alle Logs ist billig (02.10.: vorher 43 min je Lauf).
    if analysis_logs:
        run_script([PYTHON, "scripts/partial_load_model.py"], errors)
        run_script([PYTHON, "scripts/drivetrain_model_validation.py"], errors)
        run_script([PYTHON, "scripts/top_speed_validation.py"], errors)
        run_script([PYTHON, "scripts/check_dgm_coverage_gaps.py"], errors)

    previous_coastdown = load_json("coastdown_analysis_summary.json") or []
    if analysis_logs:
        run_script([PYTHON, "scripts/coastdown_analysis.py", *analysis_logs], errors)
    current_coastdown = load_json("coastdown_analysis_summary.json") or []

    steering_ran = False
    if analysis_logs:
        res = run_script([PYTHON, "scripts/check_steering_channel.py"], errors)
        if res is not None:
            try:
                n_steering_logs = int(res.stdout.strip())
            except ValueError:
                n_steering_logs = 0
            if n_steering_logs > 0:
                run_script([PYTHON, "scripts/steering_zero_offset.py"], errors)
                run_script([PYTHON, "scripts/steering_lateral_model.py", *analysis_logs], errors)
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
    for log_id in new_can_logs:
        r = load_json(f"can_corner_event_summary_{log_id}.json")
        if r:
            n_right = sum(1 for e in r["events"] if e["direction"] == "rechts")
            corner[log_id] = {"n_events": len(r["events"]), "n_right": n_right, "n_left": len(r["events"]) - n_right}

    can_brake, can_vibration = {}, {}
    for log_id in new_can_logs:
        r = load_json(f"can_brake_event_summary_{log_id}.json")
        if r:
            ev = r["events"]
            can_brake[log_id] = {"n_events": len(ev), "n_abs": sum(e["abs_active"] for e in ev),
                                 "decel_peak_g": max((e["decel_peak_g"] for e in ev), default=None)}
        r = load_json(f"can_vibration_summary_{log_id}.json")
        if r and r.get("n_windows"):
            can_vibration[log_id] = r

    dt_data = load_json("drivetrain_model_validation_summary.json") or []
    dt_ratios = [e["a_measured_ms2"] / e["a_model_ms2"] for e in dt_data
                 if e.get("file", "").removesuffix(".dlg") in analysis_logs and e.get("a_model_ms2")]
    drivetrain = {"median_ratio": statistics.median(dt_ratios) if dt_ratios else None, "n": len(dt_ratios)}

    ts_data = load_json("top_speed_validation_summary.json") or []
    top_speed = {"n_new_segments": sum(1 for e in ts_data if e.get("log_id") in analysis_logs)}

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
    dgm_gap_new_logs = sorted({g["log_id"] for g in dgm_data if g.get("log_id") in analysis_logs})

    findings = pipeline_checks.run_all(
        analysis_logs,
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
    findings += [pipeline_checks._finding("info", "gpx_late_paired", f"{log} nachtraeglich mit {gpx} gepaart.")
                 for log, gpx in late_pairs.items()]

    run_report = {
        "date": date.today().isoformat(),
        "new_logs": new_logs,
        "new_can_logs": new_can_logs,
        "downloaded_from_drive": downloaded,
        "masses": masses,
        "vibration": vibration,
        "brake": brake,
        "can_brake": can_brake,
        "can_vibration": can_vibration,
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

    if os.path.exists(PENDING_CAN_LOGS_PATH):
        os.remove(PENDING_CAN_LOGS_PATH)

    print(f"\nReport: {report_path}")
    if report_path:
        with open(report_path, encoding="utf-8") as f:
            print(f.read())

    return 0


if __name__ == "__main__":
    sys.exit(main())
