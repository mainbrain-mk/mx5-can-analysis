"""Minimaler Selbsttest fuer gunzip_or_recover() aus run_daily_pipeline.py -
deckt genau den Fall ab, der am 2026-09-18 zwei CAN-Logs verschluckt hat
(truncated .gz durch Stromverlust auf dem Pi). Kein Framework noetig:
`python scripts/test_run_daily_pipeline.py`.
"""
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime

import numpy as np

import run_daily_pipeline as rdp


def test_clean_gz_decompresses_normally():
    tmp = tempfile.mkdtemp()
    try:
        gz_path = os.path.join(tmp, "ok.log.gz")
        with gzip.open(gz_path, "wb") as f:
            f.write(b"(1789459376.467328) can0 076#0102030405060708\n")
        out_path = os.path.join(tmp, "ok.log")
        errors = []
        assert rdp.gunzip_or_recover(gz_path, out_path, errors) is True
        assert errors == []
        with open(out_path, "rb") as f:
            assert f.read().startswith(b"(1789459376")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_truncated_gz_recovers_partial_data():
    tmp = tempfile.mkdtemp()
    try:
        full_path = os.path.join(tmp, "full.log.gz")
        payload = b"(1789459376.467328) can0 076#0102030405060708\n" * 1000
        with gzip.open(full_path, "wb") as f:
            f.write(payload)
        with open(full_path, "rb") as f:
            truncated = f.read()[:-20]  # kappt vor dem gzip-Trailer
        gz_path = os.path.join(tmp, "truncated.log.gz")
        with open(gz_path, "wb") as f:
            f.write(truncated)

        out_path = os.path.join(tmp, "truncated.log")
        errors = []
        assert rdp.gunzip_or_recover(gz_path, out_path, errors) is True
        assert len(errors) == 1 and "partial recovery" in errors[0][1]
        with open(out_path, "rb") as f:
            recovered = f.read()
        assert len(recovered) > len(payload) * 0.9  # fast alles erhalten
        assert recovered == payload[: len(recovered)]  # kein Datenmuell
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_gz_reports_failure_and_leaves_no_file():
    tmp = tempfile.mkdtemp()
    try:
        gz_path = os.path.join(tmp, "empty.log.gz")
        open(gz_path, "wb").close()
        out_path = os.path.join(tmp, "empty.log")
        errors = []
        assert rdp.gunzip_or_recover(gz_path, out_path, errors) is False
        assert not os.path.exists(out_path)
        assert len(errors) == 1 and "0 Bytes" in errors[0][1]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_clockstate_warning_none_when_ntp_confirmed():
    tmp = tempfile.mkdtemp()
    orig_can_dir = rdp.CAN_DIR
    rdp.CAN_DIR = tmp
    try:
        with open(os.path.join(tmp, "clockstate-20260919-163755.txt"), "w") as f:
            f.write("ntp\nUhr per NTP synchronisiert (2026-09-19 16:37:55)")
        assert rdp._clockstate_warning("candump-2026-09-19_163755.log") is None
    finally:
        rdp.CAN_DIR = orig_can_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_clockstate_warning_flags_uncorrected_clock():
    """Echtes Beispiel vom 2026-09-19er No-RTC-Vorfall (siehe docs/logs/can-bus-status.md,
    "Viertes No-RTC-Vorkommnis") - genau dieser Marker haette das Log schon beim Download
    als zeitlich unsicher gekennzeichnet, statt es erst per Zufall beim Auswerten zu merken."""
    tmp = tempfile.mkdtemp()
    orig_can_dir = rdp.CAN_DIR
    rdp.CAN_DIR = tmp
    try:
        with open(os.path.join(tmp, "clockstate-20260919-165600.txt"), "w") as f:
            f.write("korrigiert\nUhr von 2026-09-13 13:54:01 auf gespeicherte "
                     "2026-09-19 16:55:36 vorgestellt (kein NTP). ACHTUNG: der Anker "
                     "stammt vom Ende der letzten Fahrt.")
        warning = rdp._clockstate_warning("candump-2026-09-19_165600.log")
        assert warning is not None
        assert "korrigiert" in warning
        assert "ACHTUNG" in warning
    finally:
        rdp.CAN_DIR = orig_can_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_clockstate_warning_none_when_marker_missing():
    """Aeltere Logs vor 2026-09-15 haben keinen Marker - kein falscher Alarm."""
    tmp = tempfile.mkdtemp()
    orig_can_dir = rdp.CAN_DIR
    rdp.CAN_DIR = tmp
    try:
        assert rdp._clockstate_warning("candump-2026-09-11_180456.log") is None
    finally:
        rdp.CAN_DIR = orig_can_dir
        shutil.rmtree(tmp, ignore_errors=True)


def test_fix_can_log_clocks_renames_whole_boot_group():
    """Nachbau des Fahrtags 26.09. (docs/logs/can-bus-status.md, "Automatische Uhrkorrektur"):
    zwei Logs desselben Pi-Boots (gleicher Anker), beide um die Standzeit zu frueh. Das erste
    faehrt und liegt unter einer dlg-GPS-Spur, das zweite steht nur - es muss den Offset des
    ersten uebernehmen. Pi-Aufrufe werden abgefangen, nichts verlaesst den Rechner."""
    tmp = tempfile.mkdtemp()
    orig = (rdp.CAN_DIR, rdp.RAW_DIR, rdp.CAN_GPS_PAIRS_OVERRIDE_PATH, rdp.subprocess.run)
    rdp.CAN_DIR, rdp.RAW_DIR = tmp, tmp
    rdp.CAN_GPS_PAIRS_OVERRIDE_PATH = os.path.join(tmp, "pairs.json")
    ssh_cmds = []
    rdp.subprocess.run = lambda args, **kw: ssh_cmds.append(args[-1]) or subprocess.CompletedProcess(args, 0, "", "")
    try:
        offset = 6 * 86400 + 12 * 3600 + 8 * 60 + 26
        rng = np.random.default_rng(0)
        speed = np.clip(np.cumsum(rng.normal(0, 3, 900)) + 60, 0, 160)  # km/h, 1 Hz
        logs = {"candump-2026-09-19_235409": speed, "candump-2026-09-20_001000": np.zeros(120)}
        for log_id, v in logs.items():
            start = datetime.strptime(log_id, "candump-%Y-%m-%d_%H%M%S").replace(tzinfo=rdp.LOCAL_TZ).timestamp()
            with open(os.path.join(tmp, f"{log_id}.log"), "w") as f:
                for i in range(len(v) * 10):  # 10 Hz, linear zwischen den Sekundenwerten
                    kmh = np.interp(i / 10, np.arange(len(v)), v)
                    f.write(f"({start + i / 10:.6f}) can0 202#0000{round(kmh * 100):04X}00000000\n")
            with open(os.path.join(tmp, rdp._clockstate_name(log_id)), "w") as f:
                f.write("korrigiert\nUhr von 2026-09-13 13:54:17 auf gespeicherte 2026-09-19 23:51:49 "
                        "vorgestellt (kein NTP). ACHTUNG: der Anker stammt vom Ende der letzten Fahrt.")
        with open(rdp.CAN_GPS_PAIRS_OVERRIDE_PATH, "w") as f:
            json.dump({f"{i}.log": "" for i in logs}, f)

        # dlg: GPS-Speed zur wahren Zeit, um GPS_SPEED_LAG_S verspaetet, ohne CAN-Voreilung
        true_start = datetime.strptime("2026-09-19 235409", "%Y-%m-%d %H%M%S").replace(tzinfo=rdp.LOCAL_TZ).timestamp() + offset
        dlg_name = datetime.fromtimestamp(true_start - 60, rdp.LOCAL_TZ).strftime("%Y-%m-%d %H%M%S")
        con = sqlite3.connect(os.path.join(tmp, f"{dlg_name}.dlg"))
        con.execute("CREATE TABLE PidMetadataEntry (UniqueId varchar, PidName varchar)")
        con.execute("CREATE TABLE PidDataEntry (UniqueId varchar, Time bigint, Value float)")
        con.execute("INSERT INTO PidMetadataEntry VALUES ('g', 'GPS-Geschwindigkeit')")
        con.executemany("INSERT INTO PidDataEntry VALUES ('g', ?, ?)", [
            (int((true_start + i + rdp.GPS_SPEED_LAG_S + 0.3) * 10**7) + rdp.DOTNET_EPOCH_TICKS,
             float(v * rdp.CAN_TO_GPS_SPEED)) for i, v in enumerate(speed)])
        con.commit()
        con.close()

        errors = []
        new_ids, messages = rdp.fix_can_log_clocks(list(logs), errors)
        assert errors == [], errors
        assert new_ids == ["candump-2026-09-26_120235", "candump-2026-09-26_121826"], new_ids
        assert "vom selben Boot uebernommen" in messages[1]
        for new_id in new_ids:
            assert os.path.exists(os.path.join(tmp, f"{new_id}.log"))
            assert os.path.exists(os.path.join(tmp, rdp._clockstate_name(new_id)))
        assert not os.path.exists(os.path.join(tmp, "candump-2026-09-19_235409.log"))
        assert len(ssh_cmds) == 2 and "sudo mv candump-2026-09-19_235409.log.gz candump-2026-09-26_120235.log.gz" in ssh_cmds[0]
        with open(rdp.CAN_GPS_PAIRS_OVERRIDE_PATH) as f:
            assert sorted(json.load(f)) == [f"{i}.log" for i in new_ids]
    finally:
        rdp.CAN_DIR, rdp.RAW_DIR, rdp.CAN_GPS_PAIRS_OVERRIDE_PATH, rdp.subprocess.run = orig
        shutil.rmtree(tmp, ignore_errors=True)


def test_fix_clock_jump_shifts_part_before_ntp_and_renames():
    """NTP-Sync mitten in der Fahrt (Nachbau candump-2026-09-11_201950, Sprung +67588,875 s):
    Teil vor dem Sprung + Name werden nachgezogen, Frames danach bleiben, Marker gilt als ok.
    Ein Log mit zwei Spruengen wird nur gemeldet, nicht angefasst."""
    tmp = tempfile.mkdtemp()
    orig = (rdp.CAN_DIR, rdp.subprocess.run)
    rdp.CAN_DIR = tmp
    ssh_cmds = []
    rdp.subprocess.run = lambda args, **kw: ssh_cmds.append(args[-1]) or subprocess.CompletedProcess(args, 0, "", "")
    try:
        old_id = "candump-2026-09-11_201950"
        start_us = int(datetime(2026, 9, 11, 20, 19, 50, tzinfo=rdp.LOCAL_TZ).timestamp()) * 10**6 + 920951
        jump_us = 67588_875_000
        ts = [start_us + i * 5000 for i in range(100)]            # 20 s vor dem Sync, 200 Hz
        ts += [ts[-1] + jump_us + (i + 1) * 5000 for i in range(50)]
        with open(os.path.join(tmp, f"{old_id}.log"), "w") as f:
            f.writelines(f"({t // 10**6:010d}.{t % 10**6:06d}) can0 202#00001F4000000000\n" for t in ts)
        with open(os.path.join(tmp, rdp._clockstate_name(old_id)), "w") as f:
            f.write("plausibel\nkein NTP, Systemzeit >= gespeicherte Zeit - vermutlich ok (2026-09-11 20:19:50)")

        errors = []
        new_id = rdp.fix_clock_jump(old_id, errors)
        assert errors == [], errors
        assert new_id == "candump-2026-09-12_150619", new_id
        assert "sudo mv candump-2026-09-11_201950.log.gz candump-2026-09-12_150619.log.gz" in ssh_cmds[0]
        with open(os.path.join(tmp, f"{new_id}.log")) as f:
            got = [rdp._candump_ts_us(l) for l in f]
        assert got[:100] == [t + jump_us for t in ts[:100]] and got[100:] == ts[100:]
        assert max(b - a for a, b in zip(got, got[1:])) == 5000  # lueckenlos
        assert rdp._clockstate_warning(f"{new_id}.log") is None
        assert rdp.find_clock_jump(os.path.join(tmp, f"{new_id}.log")) is None

        two_id = "candump-2026-09-20_023215"
        two = ts[:10] + [ts[9] + 10**7] + [ts[9] + 2 * 10**7]
        with open(os.path.join(tmp, f"{two_id}.log"), "w") as f:
            f.writelines(f"({t // 10**6:010d}.{t % 10**6:06d}) can0 202#00\n" for t in two)
        assert rdp.fix_clock_jump(two_id, errors) == two_id
        assert len(errors) == 1 and "nicht korrigiert" in errors[0][1]
        assert len(ssh_cmds) == 1
    finally:
        rdp.CAN_DIR, rdp.subprocess.run = orig
        shutil.rmtree(tmp, ignore_errors=True)


def test_find_matching_gpx_by_overlap():
    """27.09.: ein GPX (Start 11:56:42, Ende 14:43:03) deckt zwei CAN-Logs ab, das erste startet
    8,5 min VOR dem Track - Zuordnung ueber Zeitueberlappung aus dem GPX-Inhalt, nicht den Namen."""
    tmp = tempfile.mkdtemp()
    orig = rdp.CAN_DIR
    rdp.CAN_DIR = tmp
    try:
        def write_can(log_id, dur_s):
            t0 = int(datetime.strptime(log_id, "candump-%Y-%m-%d_%H%M%S").replace(tzinfo=rdp.LOCAL_TZ).timestamp())
            with open(os.path.join(tmp, f"{log_id}.log"), "w") as f:
                f.write(f"({t0}.000000) can0 202#00\n({t0 + dur_s}.000000) can0 202#00\n")

        def write_gpx(name, t_from, t_to):
            with open(os.path.join(tmp, name), "w") as f:
                f.writelines(f"<trkpt><time>{t}</time><speed>10.0</speed></trkpt>\n" for t in (t_from, t_to))

        write_gpx("20260927-115642.gpx", "2026-09-27T09:56:42Z", "2026-09-27T12:43:03Z")
        write_gpx("20260927-163740.gpx", "2026-09-27T14:37:40Z", "2026-09-27T16:41:18Z")
        write_can("candump-2026-09-27_114812", 3780)
        write_can("candump-2026-09-27_125452", 6440)
        write_can("candump-2026-09-27_112539", 500)   # vor jedem Track
        assert rdp._find_matching_gpx("candump-2026-09-27_114812") == "20260927-115642.gpx"
        assert rdp._find_matching_gpx("candump-2026-09-27_125452") == "20260927-115642.gpx"
        assert rdp._find_matching_gpx("candump-2026-09-27_112539") is None
    finally:
        rdp.CAN_DIR = orig
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
