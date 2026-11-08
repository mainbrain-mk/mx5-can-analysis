"""Selbsttest fuer can_backend.kill_processes() - der Helper, der den per
subprocess.Popen in dash_gui.py gestarteten canplayer-Prozess beendet, sobald
run_process_watch() erkennt, dass der echte can0-Adapter jetzt da ist (siehe
Docstring dort). kill_processes() ist reines /proc-Scanning + os.kill(), lässt
sich ohne Kivy/CAN-Hardware testen - der eigentliche Reconnect-Ablauf
(run_decode_loop()) braucht echtes SocketCAN und wurde stattdessen live auf
dem Pi gegen eine echte Vcan-Simulation verifiziert.
"""
import os
import tempfile
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import can_backend
from can_backend import kill_processes


def test_kill_processes_terminates_matching_process():
    proc = subprocess.Popen(["sleep", "50"])
    try:
        for _ in range(20):
            with open(f"/proc/{proc.pid}/cmdline", "rb") as f:
                if b"sleep" in f.read():
                    break
            time.sleep(0.05)

        kill_processes("sleep 50")

        assert proc.wait(timeout=3) != 0  # per SIGTERM beendet, kein regulaerer Exit
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_kill_processes_ignores_non_matching():
    proc = subprocess.Popen(["sleep", "50"])
    try:
        kill_processes("this-pattern-matches-nothing")
        time.sleep(0.2)
        assert proc.poll() is None  # laeuft unberuehrt weiter
    finally:
        proc.kill()
        proc.wait()


def _backend_without_dbc(candidates=()):
    saved = can_backend.DBC_CANDIDATES
    can_backend.DBC_CANDIDATES = list(candidates)
    try:
        # Tankbuch in ein leeres Temp-Verzeichnis: auf dem Pi laege sonst das echte daneben
        return can_backend.CanBackend(channel="nonexistent0", fuel_state_path=None,
                                      fuel_ledger_path=os.path.join(tempfile.mkdtemp(), "fuel_ledger.jsonl"))
    finally:
        can_backend.DBC_CANDIDATES = saved


def test_new_logging_session_resets_vmax_and_starts_monotonic_timer():
    """Der Backend-Prozess laeuft seit dem Desktop-Login, zu Hause ueber mehrere
    Fahrten - VMAX muss mit jeder Logging-Session neu beginnen."""
    backend = _backend_without_dbc()
    backend.session_max_speed = 187.0
    backend._update_logging_edge(False)
    assert backend.snapshot()["logging_elapsed"] is None
    assert backend.session_max_speed == 187.0   # ohne neue Session bleibt der Wert

    backend._update_logging_edge(True)
    assert backend.session_max_speed == 0.0
    backend.session_max_speed = 55.0
    backend._update_logging_edge(True)          # laufende Session: kein erneuter Reset
    assert backend.session_max_speed == 55.0

    snap = backend.snapshot()
    assert snap["clock"] == "monotonic"
    assert 0 <= snap["logging_elapsed"] < 5
    assert snap["logging_since"] is not None     # Wanduhr fuer aeltere dash_gui.py

    backend._update_logging_edge(False)
    snap = backend.snapshot()
    assert snap["logging_elapsed"] is None and snap["logging_since"] is None


def test_snapshot_values_are_stamped_on_the_monotonic_clock():
    backend = _backend_without_dbc()
    backend._set(514, "EngineRPM", 3000, time.monotonic())
    snap = backend.snapshot()
    value, ts = snap["values"]["514:EngineRPM"]
    assert value == 3000
    assert abs(snap["t_mono"] - ts) < 1.0


def test_editor_feed_replaces_can_values_only_while_fresh():
    """Editor-Simulation: nur simulierte Groessen im Snapshot, nach EDITOR_FEED_STALE_S wieder CAN."""
    backend = _backend_without_dbc()
    backend.channel = "kein_can_test"
    backend._set(1056, "CoolantTemp", 90, time.monotonic())
    backend.editor_feed({"speed_kmh": 120.0, "rpm": 6500, "gear": 4, "brake_pct": 40, "steer_deg": float("nan"),
                         "lap_t": 12.5, "oil": 100, "throttle_pct": True})
    snap = backend.snapshot()
    assert snap["source"] == "editor" and snap["logging"] and snap["logging_elapsed"] == 12.5
    assert set(snap["values"]) == {"514:VehicleSpeed", "514:EngineRPM", "253:MT_Gear_Actual",
                                   "120:_BrakePedalPercent_derived", "359:BrakeSwitch_PCM"}
    assert snap["values"]["359:BrakeSwitch_PCM"][0] == 1 and snap["fuel_liters"] is None
    backend.editor_feed({"speed_kmh": 90.0, "load_pct": 23.0, "tp_pct": 30.5, "clutch_raw": 99.5})
    assert backend.snapshot()["values"]["304:Clutch_Pedal_Position_raw"][0] == 99.5
    snap = backend.snapshot()
    assert snap["session_max_speed"] == 120.0
    assert snap["values"]["359:ActualEnginePercentTorque"][0] == 23.0
    assert snap["values"]["2024:_ThrottlePosition_pct_derived"][0] == 30.5
    backend._editor = (time.monotonic() - can_backend.EDITOR_FEED_STALE_S - 0.1,) + backend._editor[1:]
    snap = backend.snapshot()
    assert "source" not in snap and "1056:CoolantTemp" in snap["values"]
    backend.editor_feed({"speed_kmh": 50.0})  # neue Wiedergabe: VMAX beginnt neu
    assert backend.snapshot()["session_max_speed"] == 50.0


def test_health_line_reports_value_ages_and_resets_obd_request_counts():
    backend = _backend_without_dbc()
    now = time.monotonic()
    backend._set(514, "VehicleSpeed", 42.0, now - 3.0)
    backend._obd_requests[0x7DF] = 7
    line = backend.health_line(now)
    assert "'speed': 3.0" in line and "'lambda': None" in line, line
    assert "7DF=7 7E0=0" in line, line
    assert "7DF=0" in backend.health_line(now)   # Zaehler je Zeile zurueckgesetzt


# --- Tankinhalt aus dem Kraftstoffzaehler (04.11.2026) ---

def _drive(fuel, t, seconds, liters_per_h, counter, hz=10, sensor_raw=None):
    """Zaehlerframes wie 0x420 (8 Bit umlaufend) fuer eine Fahrt mit konstantem Verbrauch."""
    acc = 0.0
    for _ in range(int(seconds * hz)):
        t += 1.0 / hz
        acc += liters_per_h / 3600.0 / hz * can_backend.FUEL_COUNTS_PER_L
        step, acc = int(acc), acc - int(acc)
        counter = (counter + step) % 256
        fuel.on_counter(counter, t)
        if sensor_raw is not None:
            fuel.on_sensor(sensor_raw, t)
    return t, counter


def test_fuel_full_tank_reads_45_l_and_counts_down_with_the_counter():
    """Nach dem Volltanken (Geber gesaettigt, gespeicherter Stand niedrig) gilt sofort 45 l,
    nicht die 42,8 l des gesaettigten Gebers; danach zaehlt der Verbrauch ueber den Umlauf."""
    logs = []
    fuel = can_backend.FuelEstimator(None, log=logs.append)
    fuel.liters = 10.0                       # Stand vor dem Tankstopp
    t = 1000.0
    for i in range(40):                      # 4 s im Stand, Geber gesaettigt
        t += 0.1
        fuel.on_sensor(36.2, t)
        fuel.on_counter(17, t)
    assert fuel.liters == 45.0, (fuel.liters, logs)
    assert fuel.published_liters(t) == 45.0
    assert any("Volltanken" in line for line in logs)

    # 1 h mit 6 l/h, Geber bleibt anfangs gesaettigt (erste ~2,3 l unsichtbar)
    t, _ = _drive(fuel, t, 3600, 6.0, 17, sensor_raw=36.2)
    assert abs(fuel.liters - 39.0) < 0.05, fuel.liters


def test_fuel_sensor_init_zero_after_ignition_is_ignored():
    """Nach Zuendung an meldet der Geber kurz 0,0 (Tankstopp 04.11.2026: 30 ms, Abend davor 5,5 s).
    Die Glaettung darf nicht bei 0 starten: sonst Zwischen-Abgleich statt Volltanken."""
    logs = []
    fuel = can_backend.FuelEstimator(None, log=logs.append)
    fuel.liters = 9.64
    t = 1000.0
    fuel.on_sensor(0.0, t)
    for _ in range(40):
        t += 0.1
        fuel.on_sensor(36.2, t)
    assert fuel.liters == 45.0, (fuel.liters, logs)
    assert not any("Abgleich" in line for line in logs), logs

    fresh = can_backend.FuelEstimator(None, log=lambda _l: None)   # ohne gespeicherten Stand
    for i in range(55):
        fresh.on_sensor(0.0, i * 0.1)
    assert fresh.liters is None
    fresh.on_sensor(6.2, 5.6)
    assert abs(fresh.liters - can_backend.fuel_sensor_liters(6.2)) < 1e-9

    # Sim-Start direkt nach dem Tanken (Log 04.11. 080151): gesaettigter Geber = voll, nicht 42,8 l
    full = can_backend.FuelEstimator(None, log=lambda _l: None)
    full.on_sensor(0.0, 0.0)
    full.on_sensor(36.2, 0.05)
    assert full.liters == can_backend.FUEL_FULL_L, full.liters


def test_fuel_saturated_sensor_does_not_override_counter_near_full():
    fuel = can_backend.FuelEstimator(None, log=lambda _l: None)
    fuel.liters = 44.0
    t = 0.0
    for _ in range(100):
        t += 0.1
        fuel.on_sensor(36.2, t)
    assert fuel.liters == 44.0


def test_fuel_sensor_only_counts_at_ignition_on_not_while_driving():
    """Getankt wird mit Zuendung aus (Nutzer 04.11.): der Geber gleicht nur im Startfenster ab. In
    Fahrt nie, auch nicht minutenlang daneben (Steigung/Schwappen, 27.10.: 38,5 -> 33,4 -> 39,4 l).
    Steht er bei Zuendung an deutlich tiefer (Fahrt ohne Pi), gilt er."""
    fuel = can_backend.FuelEstimator(None, log=lambda _l: None)
    fuel.liters = 30.0
    t = 0.0
    raw_30 = (30.0 - 3.0) / 1.10
    for _ in range(40):                       # Startfenster passt
        t += 0.1
        fuel.on_sensor(raw_30, t)
    assert not fuel.start_pending
    for _ in range(3000):                     # 5 min Geber 8 l daneben, und gesaettigt
        t += 0.1
        fuel.on_sensor(raw_30 - 8.0 if t < 200 else 36.2, t)
    assert fuel.liters == 30.0

    t += 600                                  # Zuendung aus, Fahrt ohne Pi, neuer Start
    fuel.start_session()
    assert fuel.start_pending
    raw_20 = (20.0 - 3.0) / 1.10
    for _ in range(50):
        t += 0.1
        fuel.on_sensor(raw_20, t)
    assert abs(fuel.liters - 20.0) < 1e-6


def test_fuel_counter_glitch_and_gap_are_ignored():
    fuel = can_backend.FuelEstimator(None, log=lambda _l: None)
    fuel.liters = 30.0
    fuel.on_counter(10, 0.0)
    fuel.on_counter(12, 0.1)                  # +2
    fuel.on_counter(200, 0.2)                 # Lesefehler (188 Schritte in 0,1 s)
    fuel.on_counter(14, 0.3)                  # danach wieder normal, Sprung zurueck verworfen
    fuel.on_counter(15, 0.4)                  # +1
    fuel.on_counter(100, 60.0)                # nach Luecke: nur neu aufsetzen
    fuel.on_counter(101, 60.1)                # +1
    assert abs(fuel.liters - (30.0 - 4 / can_backend.FUEL_COUNTS_PER_L)) < 1e-9


def test_fuel_without_counter_publishes_nothing_so_dash_uses_the_sensor():
    fuel = can_backend.FuelEstimator(None, log=lambda _l: None)
    fuel.on_sensor(20.0, 1.0)
    assert fuel.liters is not None
    assert fuel.published_liters(1.0) is None
    fuel.on_counter(5, 1.0)
    fuel.on_counter(5, 1.1)
    assert fuel.published_liters(1.2) is not None
    assert fuel.published_liters(1.1 + can_backend.FUEL_COUNTER_FRESH_S + 1) is None


def test_fuel_state_survives_restart_and_is_saved_on_ignition_off():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fuel_state.json")
        fuel = can_backend.FuelEstimator(path, log=lambda _l: None)
        assert fuel.liters is None
        fuel.liters = 10.0
        for i in range(40):
            fuel.on_sensor(36.2, 100.0 + i * 0.1)   # Volltanken -> sofort gespeichert
        assert can_backend.FuelEstimator(path).liters == 45.0

        fuel.on_counter(0, 104.0)
        fuel.on_counter(50, 105.0)            # 50 Schritte, Speichern erst 30 s nach dem letzten
        assert can_backend.FuelEstimator(path).liters == 45.0
        fuel.save(force=True)
        assert abs(can_backend.FuelEstimator(path).liters - (45.0 - 50 / 4985.0)) < 1e-3

        backend = can_backend.CanBackend(channel="nonexistent0", fuel_state_path=path,
                                         fuel_ledger_path=os.path.join(d, "fuel_ledger.jsonl"))
        backend._fuel_real.liters = 33.3
        backend._update_logging_edge(True)
        backend._update_logging_edge(False)   # Zuendung aus
        assert abs(can_backend.FuelEstimator(path).liters - 33.3) < 1e-6


def test_fuel_ledger_records_drive_and_sim_reads_start_liters():
    """Tankbuch 04.11.2026: echte Fahrt schreibt Start/Tankstopp/Ende zum laufenden candump-Log; die Sim
    liest den Startstand. Erster Live-Eintrag gilt (Backend-Neustart in Fahrt), Live vor nachberechnet;
    waehrend der Sim wird nichts geschrieben."""
    import json
    from unittest import mock
    with tempfile.TemporaryDirectory() as d:
        ledger = os.path.join(d, "fuel_ledger.jsonl")
        open(os.path.join(d, "candump-2026-11-05_080000.log.gz"), "w").close()   # gepackt: nicht laufend
        open(os.path.join(d, "candump-2026-11-05_120000.log"), "w").close()      # laeuft gerade
        with mock.patch.object(can_backend, "SIM_TRIGGER_PATH", os.path.join(d, "kein_trigger")):
            backend = can_backend.CanBackend(channel="nonexistent0", fuel_state_path=os.path.join(d, "s.json"),
                                             fuel_ledger_path=ledger)
            backend._fuel_real._log = lambda _l: None
            backend._fuel_real.liters = 20.0
            backend._fuel_real.on_sensor(36.2, 50.0)           # Startfenster vom Backend-Start (Vorfahrt)
            backend._fuel_real.on_sensor(36.2, 53.0)
            backend._fuel_real.liters = 20.0                   # Stand am Ende der Vorfahrt
            backend._update_logging_edge(True)                 # Zuendung an nach dem Tanken
            assert backend._fuel_real.start_pending
            backend._update_logging_edge(True)                 # Startfenster laeuft: noch kein Eintrag
            for i in range(40):
                backend._fuel_real.on_sensor(36.2, 100.0 + i * 0.1)
            backend._update_logging_edge(True)                 # naechste Runde der Prozessueberwachung
            backend._update_logging_edge(True)                 # laufend: kein zweiter Start
            backend._update_logging_edge(False)

        with mock.patch.object(can_backend, "SIM_TRIGGER_PATH", ledger):   # Trigger da = Sim laeuft
            sim = can_backend.CanBackend(channel="nonexistent0", fuel_state_path=None, fuel_ledger_path=ledger)
            sim._fuel_real.liters = 5.0
            sim._update_logging_edge(True)
            sim._update_logging_edge(False)

        with open(ledger) as fh:
            entries = [json.loads(line) for line in fh]
        assert [(e["log"], e["event"], e["liters"]) for e in entries] == [
            ("candump-2026-11-05_120000", "refuel", 45.0),     # Tankstopp vor dem Log, dann Start voll
            ("candump-2026-11-05_120000", "start", 45.0),
            ("candump-2026-11-05_120000", "end", 45.0)], entries
        assert entries[0]["before"] == 20.0 and all(e["source"] == "live" for e in entries)
        with open(ledger, "a") as fh:                          # Backend-Neustart in Fahrt: zweiter Start
            fh.write('{"log": "candump-2026-11-05_120000", "event": "start", "liters": 41.0, "source": "live"}\n')
        start = can_backend.fuel_ledger_start("/x/candump-2026-11-05_120000.log.gz", ledger)
        assert start["liters"] == 45.0 and start["await_desat"] is True, start

        with open(ledger, "a") as fh:
            fh.write('{"log": "candump-2026-11-01_090000", "event": "start", "liters": 30.0, '
                     '"source": "nachberechnet"}\n{"log": "candump-2026-11-01_09')   # halbe Zeile (Strom weg)
        assert can_backend.fuel_ledger_start("candump-2026-11-01_090000.log.gz", ledger)["liters"] == 30.0
        assert can_backend.fuel_ledger_start("candump-2026-10-01_090000.log.gz", ledger) is None
        assert can_backend.fuel_ledger_start("x", os.path.join(d, "fehlt.jsonl")) is None


def test_fuel_desaturation_is_second_anchor_and_survives_restart():
    """Fuellstreuung (26.10. ~44,1 l, 04.11. ~46,1 l): sinkt der Geber nach dem Volltanken erstmals
    knapp unter die Saettigung, gilt FUEL_DESAT_L. Das Warten ueberlebt Zuendung aus/Neustart;
    tiefes Schwappen loest nichts aus."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fuel_state.json")
        events = []
        fuel = can_backend.FuelEstimator(path, log=lambda _l: None, on_resync=lambda *e: events.append(e))
        fuel.liters = 11.5
        t = 0.0
        for _ in range(40):                    # Zuendung an nach dem Tanken
            t += 0.1
            fuel.on_sensor(36.2, t)
        assert fuel.liters == 45.0 and fuel.await_desat
        fuel.liters = 43.0                     # 2 l gefahren, Geber noch gesaettigt
        fuel.save(force=True)

        fuel = can_backend.FuelEstimator(path, log=lambda _l: None, on_resync=lambda *e: events.append(e))
        assert fuel.liters == 43.0 and fuel.await_desat   # naechstes Log, Backend neu
        fuel.start_session()
        t = 1000.0
        for _ in range(40):
            t += 0.1
            fuel.on_sensor(36.2, t)
        for _ in range(240):                   # 2 min tiefes Schwappen: kein Anker
            t += 0.5
            fuel.on_sensor(20.0, t)
        assert fuel.liters == 43.0 and fuel.await_desat
        for _ in range(240):                   # langsames Absinken knapp unter die Saettigung
            t += 0.5
            fuel.on_sensor(35.8, t)
        assert fuel.liters == can_backend.FUEL_DESAT_L and not fuel.await_desat
        assert events[-1] == ("anchor", 43.0, can_backend.FUEL_DESAT_L), events
        fuel.save(force=True)
        assert can_backend.FuelEstimator(path).await_desat is False

        fuel.await_desat, fuel.liters = True, 39.0   # Ende nie gesehen, Zaehler laengst darunter
        fuel.on_sensor(36.2, t + 10)
        assert not fuel.await_desat


def test_consumption_meter_l100km_with_counter_wrap():
    """72 km/h (20 m/s) bei 8 l/100km = 1,6 ml/s: beide 8-Bit-Zaehler laufen dabei ueber."""
    meter = can_backend.ConsumptionMeter()
    steps_per_s_dist = 20.0 / can_backend.TRAVEL_M_PER_STEP
    steps_per_s_fuel = 8.0 / 1e5 * 20.0 * can_backend.FUEL_STEPS_PER_L
    for i in range(100):
        t = i * 0.1
        data = bytes([0, int(steps_per_s_dist * t) % 256, int(steps_per_s_fuel * t) % 256])
        l100, lh = meter.update(data, t)
        if i < 5:
            assert l100 is None and lh is None   # Fenster noch nicht halb voll
    assert abs(l100 - 8.0) < 0.5, l100
    assert abs(lh - 5.76) < 0.4, lh               # 8 l/100km * 72 km/h
    # Stand mit Leerlauf (~0,8 l/h): kein l/100km, aber l/h
    fuel = data[2]
    for i in range(100, 160):
        fuel_now = (fuel + int(0.8 / 3600 * can_backend.FUEL_STEPS_PER_L * (i - 100) * 0.1)) % 256
        l100, lh = meter.update(bytes([0, data[1], fuel_now]), i * 0.1)
    assert l100 is None
    assert 0.5 < lh < 1.1, lh
    # Luecke > 1 s (Bus weg): neu anfangen, nicht ueber die Luecke differenzieren
    assert meter.update(bytes([0, 200, 200]), 20.0) == (None, None)


if __name__ == "__main__":
    test_kill_processes_terminates_matching_process()
    test_kill_processes_ignores_non_matching()
    test_new_logging_session_resets_vmax_and_starts_monotonic_timer()
    test_snapshot_values_are_stamped_on_the_monotonic_clock()
    test_editor_feed_replaces_can_values_only_while_fresh()
    test_health_line_reports_value_ages_and_resets_obd_request_counts()
    test_fuel_full_tank_reads_45_l_and_counts_down_with_the_counter()
    test_fuel_sensor_init_zero_after_ignition_is_ignored()
    test_fuel_saturated_sensor_does_not_override_counter_near_full()
    test_fuel_sensor_only_counts_at_ignition_on_not_while_driving()
    test_fuel_counter_glitch_and_gap_are_ignored()
    test_fuel_without_counter_publishes_nothing_so_dash_uses_the_sensor()
    test_fuel_state_survives_restart_and_is_saved_on_ignition_off()
    test_fuel_ledger_records_drive_and_sim_reads_start_liters()
    test_fuel_desaturation_is_second_anchor_and_survives_restart()
    test_consumption_meter_l100km_with_counter_wrap()
    print("ok")
