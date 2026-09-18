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


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
