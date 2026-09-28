"""Selbsttest fuer den Screen-Umschalter in dash_gui.py (MX5DashApp.update_state):
seit 2026-09-16 abends haengt "drive" vs "status" nur noch an snap["logging"]
(== Zuendung an, siehe session_logger.py KeyState-Check), nicht mehr an der
Motordrehzahl - sonst wirft Mazdas Start/Stop-System (Motor aus, Zuendung an)
den Fahrer waehrend der Fahrt zurueck auf den Status-Screen.

Braucht kein echtes Kivy-Fenster: ruft update_state() ungebunden auf einem
minimalen Fake-"self" auf, der nur die dafuer gelesenen/geschriebenen
Attribute bereitstellt. Kivy selbst muss trotzdem importierbar sein (laeuft
regulaer nur auf dem Pi) - ohne Kivy wird der Test uebersprungen.
"""
import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import dash_gui
except ImportError:
    dash_gui = None


class FakeScreenManager:
    def __init__(self):
        self.current = "status"


class FakeScreen:
    def __init__(self):
        self.refreshed = False

    def refresh(self, *_args):
        self.refreshed = True


class FakeApp:
    """Traegt nur, was MX5DashApp.update_state liest/schreibt."""

    def __init__(self, snap):
        self.client = self
        self._snap = snap
        self.sm = FakeScreenManager()
        self.drive_screen = FakeScreen()
        self.status_screen = FakeScreen()
        self.testmode_screen = FakeScreen()
        self._in_testmode = False

    def snapshot(self):
        return self._snap


class FakeSnapshotClient:
    """Minimaler Ersatz fuer SnapshotClient: gleiche .get()/.snapshot()-
    Signatur, Werte von Hand statt vom UDP-Socket."""

    def __init__(self):
        self._values = {}

    def set(self, can_id, signal, value):
        self._values[(can_id, signal)] = value

    def get(self, can_id, signal, max_age=None):
        return self._values.get((can_id, signal))

    def snapshot(self):
        return {}


def test_logging_without_rpm_stays_on_drive():
    app = FakeApp({"logging": True})
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "drive"
    assert app.drive_screen.refreshed  # beim Umschalten sofort gefuellt


def test_health_line_shows_what_the_tiles_display():
    client = FakeSnapshotClient()
    client.set(514, "VehicleSpeed", 87.4)
    line = dash_gui.dash_health_line(client, "drive")
    assert "screen=drive" in line and "'speed': 87.4" in line and "'lambda': None" in line, line


def test_drive_tick_renders_only_while_drive_screen_is_visible():
    app = FakeApp({})
    dash_gui.MX5DashApp._drive_tick(app, 1 / 30)
    assert not app.drive_screen.refreshed
    app.sm.current = "drive"
    dash_gui.MX5DashApp._drive_tick(app, 1 / 30)
    assert app.drive_screen.refreshed


def _client_with(snap, received_at=100.0):
    client = dash_gui.SnapshotClient(start=False)
    client._receive(snap, now=received_at)
    return client


def test_snapshot_client_reports_backend_down_instead_of_freezing_last_snapshot():
    """Stirbt can_backend.py waehrend der Fahrt, darf "logging" nicht auf True
    haengen bleiben (Drive-Screen + REC-Timer liefen sonst eingefroren weiter)."""
    snap = {"values": {}, "logging": True, "can_up": True}
    client = _client_with(snap)
    assert client.snapshot(now=101.0) is snap
    down = client.snapshot(now=100.0 + dash_gui.BACKEND_STALE_S + 0.5)
    assert down["backend_down"] and down["backend_seen"]
    assert down["logging"] is False and down["can_up"] is False
    assert "antwortet" in down["error"]

    app = FakeApp(down)
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "status"


def test_snapshot_client_before_first_snapshot_is_waiting_not_error():
    client = dash_gui.SnapshotClient(start=False)
    snap = client.snapshot()
    assert snap["backend_down"] and not snap["backend_seen"]
    assert snap["error"] == "warte auf can_backend.py"


def test_snapshot_client_get_uses_the_backends_clock():
    """Neues Backend stempelt monoton (NTP-Spruenge), altes mit der Wanduhr."""
    import time
    mono = {"values": {"514:EngineRPM": [3000, time.monotonic()],
                       "1832:Tire1_Pressure": [2.1, time.monotonic() - 150]},
            "clock": "monotonic"}
    client = _client_with(mono, received_at=time.monotonic())
    assert client.get(514, "EngineRPM") == 3000
    assert client.get(1832, "Tire1_Pressure") is None
    assert client.get(1832, "Tire1_Pressure", max_age=dash_gui.TPMS_STALE_S) == 2.1

    wall = {"values": {"514:EngineRPM": [2500, time.time()]}}
    client = _client_with(wall, received_at=time.monotonic())
    assert client.get(514, "EngineRPM") == 2500


def test_status_screen_shows_backend_failure_and_hides_sim_button():
    screen = dash_gui.StatusScreen(FakeSnapshotClient(), on_start_test=lambda: None)
    down = dash_gui.SnapshotClient(start=False)
    down._receive({"values": {}}, now=0.0)
    screen.refresh(down.snapshot(now=60.0))
    assert "BACKEND" in screen.state_label.text
    assert "antwortet" in screen.error_label.text
    assert not screen._sim_button_visible

    screen.refresh({"values": {}, "can_up": False, "error": "CAN-Bus nicht verfügbar (x)"})
    assert screen.state_label.text == "WARTE AUF CAN-BUS"
    assert screen.error_label.text == "CAN-Bus nicht verfügbar (x)"
    assert screen._sim_button_visible


def test_rec_timer_uses_monotonic_elapsed_not_wall_clock():
    """NTP stellt die Uhr oft erst waehrend der Fahrt (No-RTC) - der REC-Timer darf
    dabei nicht um Stunden springen."""
    import time
    bar = dash_gui.StatusBar()
    bar.update({"logging": True, "logging_elapsed": 65.4,
                "logging_since": time.time() - 6 * 86400})
    assert bar.log_label.text == "LOG  REC  01:05"
    bar.update({"logging": True, "logging_since": time.time() - 30})  # altes Backend
    assert bar.log_label.text == "LOG  REC  00:30"
    bar.update({"logging": False, "logging_elapsed": None})
    assert bar.log_label.text == "LOG  --"


def test_rpm_bar_only_recolors_changed_segments_and_matches_zones():
    bar = dash_gui._RpmBarCanvas(60)
    bar.size = (600, 40)
    bar.update(4000)
    assert bar.lit_n == 30
    lit = [tuple(c.rgba) for c in bar._colors]
    assert lit[0] == dash_gui.TEXT and lit[29] == dash_gui.TEXT
    assert lit[30] == dash_gui._darken(dash_gui.TEXT)
    bar.update(8000)
    assert tuple(bar._colors[-1].rgba) == dash_gui.RED
    bar.update(None)
    assert bar.lit_n == 0
    assert all(tuple(c.rgba) == dash_gui._darken(z) for c, z in zip(bar._colors, bar._zone_colors))
    # Geometrie: 60 Segmente fuellen die Breite ohne Ueberlauf
    last = bar._rects[-1]
    assert abs(last.pos[0] + last.size[0] - 600) < 1e-3  # Kivy speichert float32


def test_led_dot_recolors_without_rebuilding_canvas():
    dot = dash_gui.LedDot()
    n_instr = len(dot.canvas.children)
    dot.set_lit(True, dash_gui.RED)
    assert tuple(dot._color_instr.rgba) == dash_gui.RED
    dot.set_lit(False, dash_gui.RED)
    assert tuple(dot._color_instr.rgba) == dash_gui._darken(dash_gui.RED)
    assert len(dot.canvas.children) == n_instr


def test_fuel_smoothing_is_frame_rate_independent_and_resets_on_new_drive():
    """Gleiche Zeitkonstante bei 30 Hz und bei eingebrochenen 10 Hz; neue Fahrt
    (Drive-Screen erscheint) startet ohne den Fuellstand von vor dem Tanken."""
    def run(hz, seconds, raw_after):
        client = FakeSnapshotClient()
        screen = dash_gui.DriveScreen(client)
        client.set(158, "Fuel_Tank", (20.0 + 0.02) / 2.486)
        screen.refresh(1 / hz)
        client.set(158, "Fuel_Tank", raw_after)
        for _ in range(int(seconds * hz)):
            screen.refresh(1 / hz)
        return screen

    raw80 = (80.0 + 0.02) / 2.486
    fast = run(30, 8, raw80)._fuel_smooth
    slow = run(10, 8, raw80)._fuel_smooth
    assert abs(fast - slow) < 0.5, (fast, slow)
    assert 55 < fast < 62  # nach 1 tau ~63 % des Sprungs 20 -> 80

    screen = run(30, 1, raw80)
    screen.on_pre_enter()
    screen.refresh()
    assert abs(screen._fuel_smooth - 80.0) < 0.1


class RecordingClient(FakeSnapshotClient):
    def __init__(self):
        super().__init__()
        self.requested = set()

    def get(self, can_id, signal, max_age=None):
        self.requested.add((can_id, signal))
        return None


def test_every_signal_the_dash_reads_passes_the_backend_filter_and_exists():
    """Jede CAN-ID, die das Dash liest, muss im SocketCAN-Kernelfilter von
    can_backend.py stehen (sonst zeigt die Kachel dauerhaft "–"), und jedes
    DBC-Signal muss in der Repo-DBC existieren (Tippfehler/Umbenennung)."""
    import can_backend
    client = RecordingClient()
    dash_gui.DriveScreen(client).refresh()
    dash_gui.PedalRow(client).refresh()
    dash_gui.LiveGrid(client).refresh()
    assert len(client.requested) > 30

    derived = {"_BrakePedalPercent_derived", "_OilTemp_derived"} | {
        f"_{name}_derived" for name, _, _ in can_backend._OBD1_PIDS.values()}
    # Repo: ../data/can/, auf dem Pi liegt die DBC neben den Skripten in LOG_DIR.
    here = os.path.dirname(os.path.abspath(__file__))
    dbc_paths = [os.path.join(here, "..", "data", "can", "MX5ND_6thGenMazda_HSCAN_extended.dbc"),
                 os.path.join(can_backend.LOG_DIR, "MX5ND_6thGenMazda_HSCAN_extended.dbc")]
    dbc_path = next((p for p in dbc_paths if os.path.exists(p)), None)
    try:
        import cantools
    except ImportError:
        cantools = None
    dbc = (cantools.database.load_file(dbc_path, strict=False)
           if cantools is not None and dbc_path is not None else None)
    for can_id, signal in sorted(client.requested, key=str):
        assert int(can_id) in can_backend.NEEDED_CAN_IDS, (can_id, signal)
        if signal.startswith("_"):
            assert signal in derived, (can_id, signal)
        elif dbc is not None:
            names = {s.name for s in dbc.get_message_by_frame_id(int(can_id)).signals}
            assert signal in names, (can_id, signal)


def test_not_logging_falls_back_to_status():
    app = FakeApp({"logging": False})
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "status"
    assert app.status_screen.refreshed


def test_testmode_has_priority_over_logging():
    app = FakeApp({"logging": True})
    app._in_testmode = True
    dash_gui.MX5DashApp.update_state(app)
    assert app.sm.current == "testmode"
    assert app.testmode_screen.refreshed


def test_prepare_replay_log_streams_without_buffering_all_lines():
    """Regression fuer den OOM-Absturz beim Klick auf "Vcan-Simulation starten"
    (2026-09-17): _prepare_replay_log() sammelte frueher jede Zeile in einer
    Liste, bevor sie geschrieben wurde. Bei den ~50MB-gzip-Logs, die hier
    taeglich anfallen, entpackt das auf mehrere hundert MB Text und hat den
    Pi (1.8GB RAM) per OOM-Killer abgeschossen. Der Test prueft nur noch das
    fachliche Verhalten (abgeschnittene letzte Zeile wird verworfen), nicht
    den Speicherverbrauch."""
    with tempfile.TemporaryDirectory() as tmp:
        gz_path = os.path.join(tmp, "in.log.gz")
        out_path = os.path.join(tmp, "out.log")
        good = "(1700000000.123456) can0 123#DEADBEEF\n"
        truncated = "(1700000000.223456) can0 12"
        with gzip.open(gz_path, "wt") as f:
            f.write(good * 3 + truncated)

        dash_gui.SimController._prepare_replay_log(gz_path, out_path)

        with open(out_path) as f:
            result = f.read()
        assert result == good * 3


def test_gear_display_turns_baby_blue_when_clutch_not_closed():
    """Kupplung nicht geschlossen (Pedal getreten) -> GEAR-Anzeige babyblau,
    sonst normale Textfarbe - siehe CLUTCH_ACTIVE_RAW (gleiche Schwelle wie
    can_traction_circle.py/shift_time_analysis.py)."""
    client = FakeSnapshotClient()
    client.set(253, "MT_Gear_Actual", 3)
    screen = dash_gui.DriveScreen(client)

    client.set(304, "Clutch_Pedal_Position_raw", 0.0)
    screen.refresh()
    assert screen.gear_value.color == list(dash_gui.TEXT)

    client.set(304, "Clutch_Pedal_Position_raw", 20.0)
    screen.refresh()
    assert screen.gear_value.color == list(dash_gui.BABY_BLUE)

    client.set(304, "Clutch_Pedal_Position_raw", None)
    screen.refresh()
    assert screen.gear_value.color == list(dash_gui.TEXT)


def test_fuel_gauge_smooths_out_tank_slosh():
    """Tankschwappen zeigt sich als schnelles Rauschen um den echten
    Fuellstand - der EMA-Filter (FUEL_SMOOTH_TAU_S, tau=8s) soll die
    Schwankung deutlich daempfen, aber einem echten Stufenwechsel (Tanken)
    binnen weniger Sekunden folgen."""
    import math

    client = FakeSnapshotClient()
    screen = dash_gui.DriveScreen(client)

    # Fuel_Tank ist der Rohwert vor der 2.486x-0.02-Umrechnung - Mittelwert
    # hier so gewaehlt, dass fuel_pct um 50% herum schwankt.
    base_raw = (50.0 + 0.02) / 2.486
    readings = []
    for i in range(240):  # 8s bei 30Hz
        noisy_raw = base_raw + 4.0 * math.sin(i * 1.3)  # schnelles Schwappen
        client.set(158, "Fuel_Tank", noisy_raw)
        screen.refresh()
        readings.append(screen._fuel_smooth)

    raw_values = [max(0.0, min(100.0, 2.486 * (base_raw + 4.0 * math.sin(i * 1.3)) - 0.02))
                  for i in range(240)]
    tail_raw = raw_values[-60:]
    tail_smoothed = readings[-60:]
    raw_spread = max(tail_raw) - min(tail_raw)
    smoothed_spread = max(tail_smoothed) - min(tail_smoothed)
    assert smoothed_spread < raw_spread * 0.5, (smoothed_spread, raw_spread)

    # Ein echter Sprung (z.B. Tanken) muss trotzdem durchkommen, nicht auf
    # ewig weggeglaettet werden.
    client.set(158, "Fuel_Tank", (90.0 + 0.02) / 2.486)
    for _ in range(720):  # weitere 24s (~3 Tau, ~95% Annaeherung)
        screen.refresh()
    assert screen._fuel_smooth > 80.0, screen._fuel_smooth


def test_shiftlight_colors_cover_the_narrow_orange_zone():
    """Regression: bei 16 LEDs ueber 4000-8000rpm (250rpm/LED) faellt die nur
    100rpm breite Orange-Zone (7400-7500) durchs Punktraster, wenn man jede
    LED nur an ihrem exakten rpm-Wert einfaerbt - keine LED trifft je
    hinein. _rpm_zone_color_range() faerbt stattdessen ueber den von der LED
    abgedeckten Bereich, damit Orange sichtbar bleibt."""
    span = dash_gui.DRIVE_RPM_MAX - dash_gui.DRIVE_SHIFTLIGHT_MIN
    step = span / dash_gui.DRIVE_SHIFTLIGHT_N
    colors = []
    for i in range(dash_gui.DRIVE_SHIFTLIGHT_N):
        lo = dash_gui.DRIVE_SHIFTLIGHT_MIN + i * step
        colors.append(dash_gui._rpm_zone_color_range(lo, lo + step))
    assert dash_gui.ORANGE in colors
    assert colors.count(dash_gui.RED) >= 1
    assert colors.count(dash_gui.YELLOW) >= 1
    assert colors.count(dash_gui.GREEN) >= 1
    # Reihenfolge bleibt aufsteigend: gruen -> gelb -> orange -> rot, keine LED
    # springt zurueck auf eine "kaeltere" Farbe.
    severity = {dash_gui.GREEN: 0, dash_gui.YELLOW: 1, dash_gui.ORANGE: 2, dash_gui.RED: 3}
    levels = [severity[c] for c in colors]
    assert levels == sorted(levels)


def test_limiter_active_needs_all_three_conditions():
    """RPM>7000 & Vollgas & Drosselklappe trotzdem zu - siehe die 5 im CAN-Log
    bestaetigten ECU-Limiter-Eingriffe (docs/status/can-bus.md)."""
    assert dash_gui._is_limiter_active(7300, 100, 48)
    assert not dash_gui._is_limiter_active(6900, 100, 48)   # RPM zu niedrig
    assert not dash_gui._is_limiter_active(7300, 50, 48)    # Pedal nicht voll
    assert not dash_gui._is_limiter_active(7300, 100, 92)   # Drosselklappe noch offen
    assert not dash_gui._is_limiter_active(None, 100, 48)   # fehlender Kanal


def test_limiter_blink_toggles_over_time():
    hz = dash_gui.LIMITER_BLINK_HZ
    period = 1.0 / hz
    assert dash_gui._blink_on(0.0, hz) != dash_gui._blink_on(period / 2, hz)
    assert dash_gui._blink_on(0.0, hz) == dash_gui._blink_on(period, hz)


def test_gear_label_shows_reverse_neutral_and_first_at_standstill():
    """Werte wie sie can_backend.py liefert (decode_choices: Select/Position als Text, Position
    ohne VAL_-Eintrag als Zahl). MT_Gear_Actual ist im Stand/R 0 und nach Zuendung EIN 7."""
    label = dash_gui._gear_label
    assert label(1, "InGear", 0, "N/1st") == "R"
    assert label(0, "Neutral", 0, "N/1st") == "N"
    assert label(0, "Neutral", 7, "N/1st") == "N"          # Initwert 7 ist kein R
    assert label(0, "InGear", 0, "N/1st") == "1"            # 1. Gang im Stand
    assert label(0, "InGear", 0, 20) == "1"                 # Anfahren, Kupplung schleift
    assert label(0, "InGear", 3, "3rd") == "3"
    assert label(0, "InGear", 0, 9) is None                 # Kupplung beim Schalten getreten
    assert label(None, None, None, None) is None
    assert label(0, "Neutral", 4, "N/1st", clutch_raw=150, speed=80) is None  # Schalten
    assert label(0, "Neutral", 0, "N/1st", clutch_raw=150, speed=0) == "N"     # Ampel
    assert label(0, "Neutral", 4, "N/1st", clutch_raw=0, speed=80) == "N"      # rollt in N


def test_gear_display_keeps_last_gear_while_clutch_pressed():
    client = FakeSnapshotClient()
    client.set(357, "MT_Gear_Select", "InGear")
    client.set(253, "MT_Gear_Actual", 2)
    client.set(357, "MT_Gear_Position", "2nd")
    screen = dash_gui.DriveScreen(client)
    screen.refresh()
    assert screen.gear_value.text == "2"
    client.set(253, "MT_Gear_Actual", 0)
    client.set(357, "MT_Gear_Position", 9)
    screen.refresh()
    assert screen.gear_value.text == "2"
    client.set(159, "ReverseGear_IC", 1)
    screen.refresh()
    assert screen.gear_value.text == "R"


def test_gas_card_shows_throttle_in_green_only_while_cruise_holds_the_pedal():
    """Tempomat regelt + Pedal losgelassen -> Drosselklappe in Gruen; sobald der
    Fahrer selbst Gas gibt oder der Tempomat aus ist -> APP in Normalfarbe."""
    assert dash_gui._gas_card_source(0.4, 23.0, True) == (23.0, dash_gui.GREEN)
    assert dash_gui._gas_card_source(None, 23.0, True) == (23.0, dash_gui.GREEN)  # APP stale
    assert dash_gui._gas_card_source(35.0, 40.0, True) == (35.0, dash_gui.TEXT)   # Fahrer gibt Gas
    assert dash_gui._gas_card_source(0.4, 23.0, False) == (0.4, dash_gui.TEXT)    # Tempomat aus
    assert dash_gui._gas_card_source(0.4, None, True) == (0.4, dash_gui.TEXT)     # ETC-Poll stale


def test_cruise_active_prefers_0x0fd_flag_and_falls_back_to_0x165():
    assert dash_gui._cruise_active(0, 141) is True     # Flag gewinnt (0 = regelt)
    assert dash_gui._cruise_active(1, 149) is False
    assert dash_gui._cruise_active(None, 149) is True  # alte DBC: Rueckfall
    assert dash_gui._cruise_active(None, 141) is False
    assert dash_gui._cruise_active(None, None) is False


def test_metric_card_set_value_applies_color_and_resets_to_dim_on_none():
    card = dash_gui.MetricCard("GASPEDAL", unit="%", bar_color=dash_gui.GREEN)
    card.set_value(23.0, dash_gui.GREEN)
    assert card.value_label.text == "23%"
    assert tuple(card.value_label.color) == dash_gui.GREEN
    card.set_value(40.0)
    assert tuple(card.value_label.color) == dash_gui.TEXT
    card.set_value(None)
    assert card.value_label.text == "–"
    assert tuple(card.value_label.color) == dash_gui.TEXT_DIM


def test_clock_source_ntp_then_rtc_then_unknown():
    """Seit 28.09. RTC: offline gebootet ist die Uhr trotzdem richtig -> gruen "RTC" statt rot."""
    if dash_gui is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        ntp, rtc = os.path.join(tmp, "synchronized"), os.path.join(tmp, "since_epoch")
        assert dash_gui.clock_source(ntp, rtc) == ("UHR  ?", False)        # keine RTC
        with open(rtc, "w") as fh:
            fh.write("946685210\n")                                         # leere Zelle: 2000-01-01
        assert dash_gui.clock_source(ntp, rtc) == ("UHR  ?", False)
        with open(rtc, "w") as fh:
            fh.write("1790596327\n")
        assert dash_gui.clock_source(ntp, rtc) == ("RTC  OK", True)
        open(ntp, "w").close()
        assert dash_gui.clock_source(ntp, rtc) == ("NTP  SYNC", True)


if __name__ == "__main__":
    if dash_gui is None:
        print("kein Kivy installiert - dash_gui-Tests uebersprungen")
        sys.exit(0)
    test_logging_without_rpm_stays_on_drive()
    test_drive_tick_renders_only_while_drive_screen_is_visible()
    test_snapshot_client_reports_backend_down_instead_of_freezing_last_snapshot()
    test_snapshot_client_before_first_snapshot_is_waiting_not_error()
    test_snapshot_client_get_uses_the_backends_clock()
    test_status_screen_shows_backend_failure_and_hides_sim_button()
    test_rec_timer_uses_monotonic_elapsed_not_wall_clock()
    test_rpm_bar_only_recolors_changed_segments_and_matches_zones()
    test_led_dot_recolors_without_rebuilding_canvas()
    test_fuel_smoothing_is_frame_rate_independent_and_resets_on_new_drive()
    test_every_signal_the_dash_reads_passes_the_backend_filter_and_exists()
    test_not_logging_falls_back_to_status()
    test_testmode_has_priority_over_logging()
    test_prepare_replay_log_streams_without_buffering_all_lines()
    test_gear_display_turns_baby_blue_when_clutch_not_closed()
    test_gear_label_shows_reverse_neutral_and_first_at_standstill()
    test_gear_display_keeps_last_gear_while_clutch_pressed()
    test_clock_source_ntp_then_rtc_then_unknown()
    test_fuel_gauge_smooths_out_tank_slosh()
    test_shiftlight_colors_cover_the_narrow_orange_zone()
    test_limiter_active_needs_all_three_conditions()
    test_limiter_blink_toggles_over_time()
    test_gas_card_shows_throttle_in_green_only_while_cruise_holds_the_pedal()
    test_cruise_active_prefers_0x0fd_flag_and_falls_back_to_0x165()
    test_metric_card_set_value_applies_color_and_resets_to_dim_on_none()
    test_health_line_shows_what_the_tiles_display()
    print("ok")
