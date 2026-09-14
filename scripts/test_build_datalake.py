"""Minimaler Selbsttest fuer build_datalake.py - prueft, dass ingest_csv()
bei zwei auf denselben Kanal gemappten Rohspalten die informativere behaelt
statt beide blind zusammenzufuehren (siehe Docstring TM_GEST/AFR_MZ/etc.).
Kein Framework, kein pytest noetig: `python scripts/test_build_datalake.py`.
"""
import os
import tempfile

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


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
