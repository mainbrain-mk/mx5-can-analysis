"""Minimaler Selbsttest fuer build_datalake.py - prueft (1) dass ingest_csv()
bei zwei auf denselben Kanal gemappten Rohspalten die informativere behaelt
statt beide blind zusammenzufuehren (siehe Docstring TM_GEST/AFR_MZ/etc.),
und (2) die Reconciliation-Logik von run_build() (inkrementeller Bau seit
2026-09-20): unveraendertes Log wird uebersprungen, geaenderte Quelldatei
und SCHEMA_VERSION-Bump loesen Re-Ingest aus, aus dem Ziel-Scan verschwundene
Logs werden aus der DB entfernt (das ist der strukturelle Fix fuer den
dokumentierten Pi-Uhr-Umbenennungs-Bug, siehe docs/logs/can-bus-status.md).
Kein Framework, kein pytest noetig: `python scripts/test_build_datalake.py`.
"""
import os
import shutil
import tempfile

import duckdb

import build_datalake as bd

HEADER = ("Time (sec),Motordrehzahl (RPM),Engine Revolutions Per Minute (RPM),"
          "Tatsächlicher Gangstatus des Getriebes,"
          "Unterstützter tatsächlicher Gangstatus des Getriebes\n")


def write_csv(rows):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
    tmp.write("# StartTime = 08.25.2026 08:15:38.0000 AM\n")
    tmp.write(HEADER)
    for r in rows:
        tmp.write(",".join(str(v) for v in r) + "\n")
    tmp.close()
    return tmp.name


def test_dedup_keeps_informative_column():
    # Motordrehzahl variiert real, "Engine Revolutions..." haengt bei ~1000 fest
    # (defekter PID-Slot) - Tatsaechlicher Gangstatus variiert real 2..5,
    # Unterstuetzter ist die "PID unterstuetzt"-Flagge (0 dann konstant 3).
    rows = [
        (0.0, 1000, 1000, 2, 0),
        (0.1, 2500, 1002, 3, 3),
        (0.2, 4000, 998, 4, 3),
        (0.3, 6500, 1001, 5, 3),
    ]
    path = write_csv(rows)
    try:
        out = bd.ingest_csv(path)
    finally:
        os.unlink(path)

    rpm = out[out["channel"] == "EngineRPM"]
    assert set(rpm["channel_original"]) == {"Motordrehzahl (RPM)"}, rpm["channel_original"].unique()
    assert sorted(rpm["value"]) == [1000, 2500, 4000, 6500]

    gear = out[out["channel"] == "TM_GEST"]
    assert set(gear["channel_original"]) == {"Tatsächlicher Gangstatus des Getriebes"}, gear["channel_original"].unique()
    assert sorted(gear["value"]) == [2, 3, 4, 5]

    # verworfene Spalten bleiben sichtbar unter UNMAPPED statt zu verschwinden
    unmapped_channels = set(out.loc[out["channel"].str.startswith("UNMAPPED:"), "channel"])
    assert "UNMAPPED:Engine Revolutions Per Minute (RPM)" in unmapped_channels
    assert "UNMAPPED:Unterstützter tatsächlicher Gangstatus des Getriebes" in unmapped_channels


def _scratch_env():
    """Isoliertes Scratch-Projektverzeichnis fuer run_build()-Tests: patcht
    die Modul-Konstanten von build_datalake, damit nur dort gelesen/
    geschrieben wird statt im echten data/-Verzeichnis. Gibt (raw_csv_dir,
    restore) zurueck - restore() MUSS im finally-Block aufgerufen werden."""
    tmpdir = tempfile.mkdtemp()
    raw_dlg, raw_csv, can_dir, results_dir = (os.path.join(tmpdir, d) for d in
                                               ("raw", "raw_csv", "can", "results"))
    for d in (raw_dlg, raw_csv, can_dir, results_dir):
        os.makedirs(d)

    originals = {name: getattr(bd, name) for name in (
        "RAW_DLG_DIR", "RAW_CSV_DIR", "CAN_DIR", "DB_PATH", "RESULTS_DIR",
        "CAN_GPS_PAIRS_OVERRIDE_PATH", "CAN_GPS_PAIRS")}
    bd.RAW_DLG_DIR, bd.RAW_CSV_DIR, bd.CAN_DIR = raw_dlg, raw_csv, can_dir
    bd.DB_PATH = os.path.join(tmpdir, "test.duckdb")
    bd.RESULTS_DIR = results_dir
    bd.CAN_GPS_PAIRS_OVERRIDE_PATH = os.path.join(tmpdir, "can_gps_pairs.json")
    bd.CAN_GPS_PAIRS = []  # hartkodierte echte Fahrten sollen im Scratch-Test nicht anschlagen

    def restore():
        for name, value in originals.items():
            setattr(bd, name, value)
        shutil.rmtree(tmpdir, ignore_errors=True)

    return raw_csv, restore


def _write_csv(dir_, name, rows):
    path = os.path.join(dir_, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# StartTime = 08.25.2026 08:15:38.0000 AM\n")
        f.write("Time (sec),Motordrehzahl (RPM)\n")
        for r in rows:
            f.write(",".join(str(v) for v in r) + "\n")
    return path


def _counting_ingest_csv():
    """Wrapper um bd.ingest_csv, der jeden echten Aufruf zaehlt - so laesst
    sich pruefen, ob run_build() ein Log tatsaechlich neu eingelesen oder
    (korrekt) uebersprungen hat, ohne DB-Interna anzufassen."""
    calls = []
    original = bd.ingest_csv

    def wrapped(path):
        calls.append(path)
        return original(path)

    bd.ingest_csv = wrapped
    return calls, original


def test_unchanged_log_is_skipped():
    raw_csv, restore = _scratch_env()
    calls, original_ingest_csv = _counting_ingest_csv()
    try:
        _write_csv(raw_csv, "log1.csv", [(0.0, 1000), (0.1, 2000)])
        bd.run_build()
        assert len(calls) == 1
        bd.run_build()
        assert len(calls) == 1, "unveraendertes Log wurde erneut eingelesen"

        con = duckdb.connect(bd.DB_PATH)
        n_logs, n_meas = con.execute(
            "SELECT (SELECT COUNT(*) FROM logs), (SELECT COUNT(*) FROM measurements)").fetchone()
        con.close()
        assert (n_logs, n_meas) == (1, 2)
    finally:
        bd.ingest_csv = original_ingest_csv
        restore()


def test_changed_source_triggers_reingest():
    raw_csv, restore = _scratch_env()
    calls, original_ingest_csv = _counting_ingest_csv()
    try:
        path = _write_csv(raw_csv, "log1.csv", [(0.0, 1000)])
        bd.run_build()
        assert len(calls) == 1

        # Inhalt UND mtime aendern (manche Dateisysteme haben grobe
        # mtime-Aufloesung, deshalb explizit vorwaerts setzen statt auf die
        # Systemuhr zu vertrauen).
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        with open(path, "a", encoding="utf-8") as f:
            f.write("0.1,2000\n")
        bd.run_build()
        assert len(calls) == 2, "geaenderte Quelldatei wurde nicht neu eingelesen"

        con = duckdb.connect(bd.DB_PATH)
        n_meas = con.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        con.close()
        assert n_meas == 2
    finally:
        bd.ingest_csv = original_ingest_csv
        restore()


def test_removed_source_purges_log():
    # Simuliert den dokumentierten Pi-Uhr-Umbenennungs-Bug (siehe
    # docs/logs/can-bus-status.md): eine log_id verschwindet aus dem
    # Ziel-Scan (Datei umbenannt/geloescht) - der naechste run_build() MUSS
    # sie aus der DB entfernen, sonst steht sie dauerhaft mit falschen
    # Daten drin.
    raw_csv, restore = _scratch_env()
    try:
        path = _write_csv(raw_csv, "log1.csv", [(0.0, 1000)])
        bd.run_build()
        con = duckdb.connect(bd.DB_PATH)
        assert con.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 1
        con.close()

        os.remove(path)
        bd.run_build()
        con = duckdb.connect(bd.DB_PATH)
        n_logs, n_meas = con.execute(
            "SELECT (SELECT COUNT(*) FROM logs), (SELECT COUNT(*) FROM measurements)").fetchone()
        con.close()
        assert (n_logs, n_meas) == (0, 0), "verschwundenes Log wurde nicht aus der DB entfernt"
    finally:
        restore()


def test_schema_version_bump_forces_reingest():
    raw_csv, restore = _scratch_env()
    calls, original_ingest_csv = _counting_ingest_csv()
    original_schema_version = bd.SCHEMA_VERSION
    try:
        _write_csv(raw_csv, "log1.csv", [(0.0, 1000)])
        bd.run_build()
        assert len(calls) == 1
        bd.run_build()
        assert len(calls) == 1  # unveraendert -> uebersprungen

        bd.SCHEMA_VERSION = original_schema_version + "-test"
        bd.run_build()
        assert len(calls) == 2, "SCHEMA_VERSION-Aenderung hat keinen Re-Ingest ausgeloest"
    finally:
        bd.ingest_csv = original_ingest_csv
        bd.SCHEMA_VERSION = original_schema_version
        restore()


def test_force_full_reingests_unchanged_log():
    raw_csv, restore = _scratch_env()
    calls, original_ingest_csv = _counting_ingest_csv()
    try:
        _write_csv(raw_csv, "log1.csv", [(0.0, 1000)])
        bd.run_build()
        assert len(calls) == 1
        bd.run_build(force_full=True)
        assert len(calls) == 2, "--full hat unveraendertes Log nicht erneut eingelesen"
    finally:
        bd.ingest_csv = original_ingest_csv
        restore()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
