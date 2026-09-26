"""Minimaler Selbsttest fuer build_datalake.py - prueft (1) dass ingest_csv()
bei zwei auf denselben Kanal gemappten Rohspalten die informativere behaelt
statt beide blind zusammenzufuehren (siehe Docstring TM_GEST/AFR_MZ/etc.),
und (2) die Reconciliation-Logik von run_build() (inkrementeller Bau seit
2026-09-20): unveraendertes Log wird uebersprungen, geaenderte Quelldatei
und SCHEMA_VERSIONS-Bump loesen Re-Ingest aus (nur fuer das betroffene
Quellformat), aus dem Ziel-Scan verschwundene Logs werden aus der DB entfernt
(das ist der strukturelle Fix fuer den dokumentierten Pi-Uhr-Umbenennungs-Bug,
siehe docs/logs/can-bus-status.md), und (3) die Laufzeitoptimierung vom
2026-09-26: dlg-Sortierung, Gang-Ableitung, CAN-Sonderfaelle, reihenfolgetreuer
INSERT und --verify.
Kein Framework, kein pytest noetig: `python scripts/test_build_datalake.py`.
"""
import os
import shutil
import sqlite3
import tempfile

import duckdb
import numpy as np
import pandas as pd

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
    original_versions = dict(bd.SCHEMA_VERSIONS)
    try:
        _write_csv(raw_csv, "log1.csv", [(0.0, 1000)])
        bd.run_build()
        assert len(calls) == 1
        bd.run_build()
        assert len(calls) == 1  # unveraendert -> uebersprungen

        bd.SCHEMA_VERSIONS["csv"] += "-test"
        bd.run_build()
        assert len(calls) == 2, "SCHEMA_VERSIONS-Aenderung hat keinen Re-Ingest ausgeloest"
    finally:
        bd.ingest_csv = original_ingest_csv
        bd.SCHEMA_VERSIONS.clear()
        bd.SCHEMA_VERSIONS.update(original_versions)
        restore()


def _write_dlg(dir_, name, rows, names):
    """Minimale .dlg (SQLite wie von OBD Fusion): rows = [(ticks, uid, value)]."""
    path = os.path.join(dir_, name)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE PidMetadataEntry (UniqueId INTEGER, PidName TEXT)")
    con.execute("CREATE TABLE PidDataEntry (Time INTEGER, UniqueId INTEGER, Value REAL)")
    con.executemany("INSERT INTO PidMetadataEntry VALUES (?, ?)", list(names.items()))
    con.executemany("INSERT INTO PidDataEntry VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def test_schema_bump_only_reingests_its_format():
    # Seit 2026-09-26 gibt es SCHEMA_VERSIONS pro Quellformat: eine CAN-Mapping-
    # Aenderung darf nicht mehr alle .dlg/CSV-Logs neu einlesen lassen.
    raw_csv, restore = _scratch_env()
    csv_calls, original_ingest_csv = _counting_ingest_csv()
    dlg_calls, original_ingest_dlg = [], bd.ingest_dlg
    bd.ingest_dlg = lambda path: dlg_calls.append(path) or original_ingest_dlg(path)
    original_versions = dict(bd.SCHEMA_VERSIONS)
    try:
        _write_csv(raw_csv, "log1.csv", [(0.0, 1000)])
        _write_dlg(bd.RAW_DLG_DIR, "log2.dlg", [(TICKS0, 1, 2000.0)], {1: "EngineRPM"})
        bd.run_build()
        assert (len(csv_calls), len(dlg_calls)) == (1, 1)

        bd.SCHEMA_VERSIONS["can"] += "-test"
        bd.run_build()
        assert (len(csv_calls), len(dlg_calls)) == (1, 1), "CAN-Bump hat dlg/csv neu eingelesen"

        bd.SCHEMA_VERSIONS["dlg"] += "-test"
        bd.run_build()
        assert (len(csv_calls), len(dlg_calls)) == (1, 2), "dlg-Bump hat nicht genau das dlg-Log neu eingelesen"
    finally:
        bd.ingest_csv = original_ingest_csv
        bd.ingest_dlg = original_ingest_dlg
        bd.SCHEMA_VERSIONS.clear()
        bd.SCHEMA_VERSIONS.update(original_versions)
        restore()


TICKS0 = 639_000_000_000_000_000  # beliebiger .NET-Ticks-Zeitpunkt (2025)


def test_dlg_ingest_sorts_by_time_and_maps_names():
    # frueher hat SQLite per JOIN + ORDER BY Time geliefert; jetzt stabiles argsort
    # in pandas - gleiche Zeitstempel muessen in Datei-Reihenfolge bleiben, eine
    # UniqueId ohne Metadaten ergibt channel NULL (wie der alte LEFT JOIN).
    tmpdir = tempfile.mkdtemp()
    try:
        rows = [(TICKS0 + 30, 1, 3.0), (TICKS0 + 10, 2, 1.0), (TICKS0 + 20, 1, 2.0),
                (TICKS0 + 20, 3, 99.0), (TICKS0 + 20, 2, 2.5)]
        path = _write_dlg(tmpdir, "x.dlg", rows, {1: "EngineRPM", 2: "BFP_PRE_MZ"})
        out = bd.ingest_dlg(path)
        assert list(out.columns) == bd.INGEST_COLUMNS
        assert list(out["value"]) == [1.0, 2.0, 99.0, 2.5, 3.0]
        assert list(out["channel"].astype(object).where(out["channel"].notna(), None)) == \
            ["BFP_PRE_MZ", "EngineRPM", None, "BFP_PRE_MZ", "EngineRPM"]
        assert list(out["t_elapsed_s"]) == [0.0, 1e-6, 1e-6, 1e-6, 2e-6]
        assert out["unit"].astype(object).tolist()[0] == "kPa"  # CANONICAL_UNIT
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_derive_gear_status():
    raw = pd.DataFrame({
        "t": [0, 0, 1, 1, 2, 2, 3, 3, 4, 4],
        "signal": ["MT_Gear_Position", "MT_Gear_Select"] * 5,
        "value": ["N/1st", "Neutral", "N/1st", "InGear", "3rd", "InGear",
                  "6th", "Neutral", "23", "InGear"],  # 23 = zwischen zwei Gassen -> N
    })
    out = bd._derive_gear_status(raw)
    assert list(out["value"]) == [0, 1, 3, 0, 0]
    assert set(out["signal"]) == {"MT_Gear_Status"}


def test_insert_preserves_order_and_nulls():
    # _insert_measurements() loest die Strings per JOIN auf - ohne ORDER BY auf die
    # Zeilennummer kaeme die Einfuege-Reihenfolge durcheinander (auf die sich Leser
    # ohne ORDER BY verlassen). threads=8 erzwingt parallele JOIN-Ausfuehrung auch
    # auf Rechnern mit wenigen Kernen - ohne ORDER BY schlaegt der Test so zuverlaessig fehl.
    n = 300_000
    rng = np.random.default_rng(0)
    ch = rng.choice(["A", "B", "C", None], n)
    df = pd.DataFrame({
        "channel": ch, "channel_original": ch,
        "unit": np.where(ch == "A", "kPa", None),
        "t_elapsed_s": np.arange(n) * 0.001,
        "timestamp_local": pd.Timestamp("2026-09-26") + pd.to_timedelta(np.arange(n), unit="ms"),
        "value": np.where(np.arange(n) % 7 == 0, np.nan, rng.random(n)),
    })
    con = duckdb.connect()
    con.execute("SET threads = 8")
    bd._ensure_schema(con)
    bd._insert_measurements(con, df, "L", "L.dlg", "dlg")
    back = con.execute("SELECT * FROM measurements ORDER BY rowid").df()
    con.close()
    assert len(back) == n
    assert (back["t_elapsed_s"].to_numpy() == df["t_elapsed_s"].to_numpy()).all(), "Reihenfolge verloren"
    got_ch = back["channel"].astype(object)
    assert got_ch.where(got_ch.notna(), None).tolist() == ch.tolist()
    assert back["unit"].notna().sum() == (ch == "A").sum()
    assert back["value"].isna().sum() == df["value"].isna().sum()
    assert set(back["log_id"]) == {"L"} and set(back["source_format"]) == {"dlg"}


def test_can_ingest():
    # CAN-Pfad arbeitet seit 2026-09-26 mit Categoricals - Sonderfaelle pruefen:
    # Tacho-VehicleSpeed (HS_IC) getrennt, Enum-Strings -> Rohcode, Sentinels und
    # WheelSpeed-0xFFFF raus, Vorzeichen Lateral_Acc/YawRate, Gang aus 0x165.
    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "x_decoded.csv")
        pd.DataFrame([
            (0.00, 1, "HS_PCM", "VehicleSpeed", "100.5"),
            (0.01, 1, "HS_IC", "VehicleSpeed", "104"),
            (0.02, 1, "HS_PCM", "KeyState", "ON"),
            (0.03, 1, "HS_ABS", "WheelSpeed_1", "555.35"),
            (0.04, 1, "HS_ABS", "WheelSpeed_1", "80.0"),
            (0.05, 1, "HS_RCM", "Lateral_Acc_Raw", "0.5"),
            (0.06, 1, "X", "AmbientTemp", "-6.3"),
            (0.07, 1, "X", "AmbientTemp", "12.0"),
            (0.08, 1, "X", "Unbekannt", "7"),
            (0.09, 1, "HS_PCM", "EngineRPM", "kaputt"),
            (0.10, 1, "HS_TCM", "MT_Gear_Position", "2nd"),
            (0.10, 1, "HS_TCM", "MT_Gear_Select", "InGear"),
        ], columns=["t", "can_id", "message", "signal", "value"]).to_csv(path, index=False)
        out = bd.ingest_can(os.path.join(tmpdir, "x.log"), path, 1_789_000_000.0)
        got = list(zip(out["channel"], out["value"]))
        assert got == [("VehicleSpeed", 100.5), ("DisplaySpeed_CAN", 104.0), ("KeyState_CAN", 2.0),
                       ("WheelSpeed_CAN_1", 80.0), ("LateralAcc_CAN", -0.5), ("AmbientTemp_CAN", 12.0),
                       ("MT_Gear_Status", 2.0)], got
        assert list(out.columns) == bd.INGEST_COLUMNS
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verify_detects_changed_mapping_without_bump():
    raw_csv, restore = _scratch_env()
    original_aliases = dict(bd.NAME_ALIASES)
    try:
        _write_csv(raw_csv, "log1.csv", [(0.0, 1000), (0.1, 2000)])
        _write_dlg(bd.RAW_DLG_DIR, "log2.dlg", [(TICKS0 + 5, 1, 1.0), (TICKS0, 2, 2.0)],
                   {1: "EngineRPM", 2: "VehicleSpeed"})
        bd.run_build()
        assert bd.run_verify() == 0, "frisch gebaute DB weicht vom Neu-Einlesen ab"

        # Mapping geaendert, SCHEMA_VERSIONS vergessen -> --verify muss es finden
        bd.NAME_ALIASES["Motordrehzahl"] = "EngineRPM_umbenannt"
        assert bd.run_verify() == 1
    finally:
        bd.NAME_ALIASES.clear()
        bd.NAME_ALIASES.update(original_aliases)
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
