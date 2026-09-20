"""Minimaler Selbsttest fuer gunzip_or_recover() aus run_daily_pipeline.py -
deckt genau den Fall ab, der am 2026-09-18 zwei CAN-Logs verschluckt hat
(truncated .gz durch Stromverlust auf dem Pi). Kein Framework noetig:
`python scripts/test_run_daily_pipeline.py`.
"""
import gzip
import os
import shutil
import tempfile

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


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
