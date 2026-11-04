"""Tankbuch fuer alle bisherigen CAN-Logs nachberechnen (04.11.2026).

Spielt die Logs chronologisch durch denselben FuelEstimator wie can_backend.py (Zaehler 0x420
abziehen, Volltanken/Abgleich ueber den Tankgeber) und traegt den Stand von Log zu Log weiter.
Ergebnis: data/derived/fuel_ledger_backfill.jsonl im Format von can_backend.FUEL_LEDGER_PATH
(source "nachberechnet"), dazu eine Tabelle auf stdout. Auf den Pi kopieren:
    scp data/derived/fuel_ledger_backfill.jsonl pi@192.168.0.247:canlogs/fuel_ledger.jsonl
(nur solange dort noch keine Live-Eintraege stehen, sonst voranstellen statt ueberschreiben).

Quelle: data/can/*_decoded.parquet (Datalake-Aufbau), Zeitversatz alter No-RTC-Logs aus
data/can_clock_offsets.json fuer die Reihenfolge.
"""
import datetime
import glob
import json
import os
import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_backend  # noqa: E402

PARQUET_GLOB = "data/can/candump-*_decoded.parquet"
CLOCK_OFFSETS_PATH = "data/can_clock_offsets.json"
OUT_PATH = "data/derived/fuel_ledger_backfill.jsonl"
SIGNALS = (can_backend.FUEL_COUNTER_SIGNAL, can_backend.FUEL_TANK_SIGNAL, "VehicleSpeed")


def log_start(key, offsets):
    """candump-2026-11-04_152230 -> datetime, korrigiert um den No-RTC-Versatz (s)."""
    t = datetime.datetime.strptime(key[8:], "%Y-%m-%d_%H%M%S")
    return t + datetime.timedelta(seconds=offsets.get(key + ".log", 0.0))


def replay(fuel, samples, base_epoch, on_start):
    """samples: [(t_rel, signal, value)] in Dateireihenfolge. on_start(liters) beim ersten bekannten Stand."""
    speed = None
    started = fuel.liters is not None
    if started:
        on_start(fuel.liters)
    for t, signal, value in samples:
        now = base_epoch + t
        if signal == "VehicleSpeed":
            speed = value
        elif signal == can_backend.FUEL_COUNTER_SIGNAL:
            fuel.on_counter(value, now)
        else:
            fuel.on_sensor(value, now, speed)
        if not started and fuel.liters is not None:
            started = True
            on_start(fuel.liters)


def main():
    try:
        with open(CLOCK_OFFSETS_PATH) as fh:
            offsets = json.load(fh)
    except OSError:
        offsets = {}
    con = duckdb.connect()
    keys = [os.path.basename(p)[:-len("_decoded.parquet")] for p in glob.glob(PARQUET_GLOB)]
    keys.sort(key=lambda k: log_start(k, offsets))

    entries, rows = [], []
    current = {}  # Log/Zeit fuer den Resync-Callback
    fuel = can_backend.FuelEstimator(
        None, log=lambda _line: None,
        on_resync=lambda full, before, after: entries.append({
            "log": current["key"], "event": "refuel" if full else "resync", "liters": round(after, 2),
            "before": round(before, 2), "time": current["time"](), "source": "nachberechnet"}))

    prev_end = 0.0
    for key in keys:
        start_dt = log_start(key, offsets)
        samples = con.execute(
            f"SELECT t, signal, value FROM '{PARQUET_GLOB.replace('candump-*', key)}' "
            f"WHERE signal IN (?, ?, ?) AND value IS NOT NULL ORDER BY file_row_number", SIGNALS).fetchall()
        if not samples:
            rows.append((key, None, None, "ohne Tank-/Zaehlersignal"))
            continue
        last_t = [0.0]
        current["key"] = key
        current["time"] = lambda: (start_dt + datetime.timedelta(seconds=last_t[0])).strftime("%F %T")
        start = {}
        n_before = len(entries)

        def on_start(liters):
            start["l"] = liters
            entries.insert(n_before, {"log": key, "event": "start", "liters": round(liters, 2),
                                      "time": start_dt.strftime("%F %T"), "source": "nachberechnet"})

        def tracked():
            for sample in samples:
                last_t[0] = sample[0]
                yield sample

        # Zeitbasis je Log: Wanduhr des Logbeginns, damit Luecken zwischen Logs als Neubeginn gelten.
        # Ueberlappt sie das Vorgaengerlog (Namen/Versatz ungenau), dahinter schieben: der Schaetzer
        # verkraftet keine rueckwaerts laufende Zeit.
        base = max(start_dt.timestamp(), prev_end + 2 * can_backend.FUEL_SAMPLE_GAP_S)
        replay(fuel, tracked(), base, on_start)
        prev_end = base + last_t[0]
        if fuel.liters is not None:
            entries.append({"log": key, "event": "end", "liters": round(fuel.liters, 2),
                            "time": current["time"](), "source": "nachberechnet"})
        events = [f"{e['event']} {e['before']:.1f}->{e['liters']:.1f}" for e in entries[n_before:]
                  if e["event"] in ("refuel", "resync")]
        rows.append((key, start.get("l"), fuel.liters, ", ".join(events)))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    for key, s, e, note in rows:
        fmt = lambda v: "   -  " if v is None else f"{v:6.2f}"
        print(f"{key}  {fmt(s)} -> {fmt(e)} l  {note}")
    print(f"{len(entries)} Eintraege -> {OUT_PATH}")


def _selftest():
    """Volltanken mitten im Log, Stand traegt ins naechste Log."""
    entries = []
    fuel = can_backend.FuelEstimator(None, log=lambda _l: None,
                                     on_resync=lambda full, b, a: entries.append((full, b, a)))
    fuel.liters = 10.0
    samples = [(i * 0.1, can_backend.FUEL_TANK_SIGNAL, 36.2) for i in range(50)]
    starts = []
    replay(fuel, samples, 1000.0, starts.append)
    assert starts == [10.0] and fuel.liters == 45.0 and entries == [(True, 10.0, 45.0)], (starts, entries)
    counter = [(i * 0.1, can_backend.FUEL_COUNTER_SIGNAL, i % 256) for i in range(500)]
    replay(fuel, counter, 9000.0, starts.append)  # 499 Schritte
    assert abs(fuel.liters - (45.0 - 499 / can_backend.FUEL_COUNTS_PER_L)) < 1e-9, fuel.liters
    assert starts == [10.0, 45.0]


if __name__ == "__main__":
    _selftest()
    main()
