"""
PCM-Kampagne: autonome Diagnose-Abfragen des Motorsteuergeraets im Hintergrund jeder Fahrt
(docs/plans/pcm-bits-plan.md, Strang B).

Laeuft NICHT als eigener Prozess, sondern wird von tpms_poller.py nach jeder schnellen
Abfragerunde einmal aufgerufen (tick). Gruende:
  * Auf 0x7E0/0x7E8 darf nur ein Tester des Pi sprechen, sonst laufen zwei Anfragen
    gleichzeitig beim PCM auf.
  * Gesendet wird ueber den Socket des Pollers: SocketCAN liefert eigene Frames nicht an
    den sendenden Socket zurueck. Saehe der Poller unsere Anfragen, hielte er sie fuer einen
    fremden Tester (Handy) und drosselte sich selbst (fast_poll_delay).
  * Empfangen wird ueber einen eigenen Socket (Filter 0x7E8 + 0x202), damit die
    Abfrageschleife des Pollers unveraendert bleibt.

Ablauf (der Pi arbeitet ohne Kontakt; Fortschritt auf dem Stick, jede Fahrt macht weiter):
  J0  Mode-01-Inventar: unterstuetzte Standard-PIDs, jede einmal lesen
  J1  DID-Sweep: Mode 0x22, je Block (High-Byte) 256 DIDs, Prioritaetsbloecke zuerst
  J2  Anker-Rotation: alle positiv antwortenden DIDs in Gruppen, je Gruppe window_s
  J3  Hotlist: nur die am PC ausgewaehlten DIDs
Freigegeben wird per Stufe in der Konfiguration (Repo, per Deploy):
  stage -1 aus, 0 Pilot (J0 + Block 0x13), 1 voller Sweep, 2 Rotation, 3 nur Hotlist.

Schutz der bestehenden Funktion:
  * hoechstens eine Anfrage je tick, Zeitanteil (max_share) und Rate (max_rate_hz) per
    Token-Bucket begrenzt, ohne angesparte Bursts
  * Pause bei fremdem Tester, Motor aus, in den ersten start_delay_s nach Motorstart und ab
    app_pause_pct Fahrpedal
  * nur lesende Dienste 0x22 und 0x01 (keine Sitzung, kein Schreiben, keine Routinen)
  * nach max_errors Fehlern aus bis zum naechsten Start; Notaus per Datei STOP im Zustandsordner

Grundwahrheit ist der candump (jede Anfrage und Antwort steht darin). state.json steuert nur
den Pi; die Auswertung am PC rechnet aus dem candump neu.

Aufruf (Test): .venv/bin/python scripts/pcm_campaign.py --self-test
"""
import json
import os
import time

PCM_REQ = 0x7E0
PCM_RESP = 0x7E8
ENGINE_ID = 0x202          # EngineRPM 7|16 x0,25, APP 39|16 x0,0015625 (DBC)

DEFAULT_CONFIG = {
    "stage": -1,                    # ohne gueltige Konfiguration: aus
    "max_share": 0.10,              # Zeitanteil der Kampagne; 0,15 kostete im Integrationstest
                                    # bei schweigendem PCM 16 % der schnellen Gruppe (22.11.)
    "max_rate_hz": 15.0,
    "resp_timeout_s": 0.03,         # PCM antwortet in max. 16 ms (22 03EC, 3550 Anfragen 19.09.);
                                    # spaetere Antworten stehen trotzdem im candump
    "pending_timeout_s": 0.5,       # nach NRC 0x78 (responsePending)
    "start_delay_s": 30.0,
    "app_pause_pct": 80.0,
    "min_rpm": 400.0,
    "max_errors": 20,
    "save_every_s": 30.0,
    "pilot_blocks": ["13"],
    "priority_blocks": ["03", "04", "09", "13", "28", "DA", "F4", "0C"],
    "rotation_group_size": 8,
    "rotation_window_s": 900.0,
    "rotation_exclude": [],
    "hotlist": [],
}

NRC_PENDING = 0x78


# --------------------------------------------------------------------------------------------
# Konfiguration und Zustand
# --------------------------------------------------------------------------------------------
def load_config(path):
    """Konfiguration mit Defaults. Fehlt die Datei oder ist sie kaputt: stage -1 (aus)."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path) as fh:
            user = json.load(fh)
        cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        cfg["stage"] = -1
    return cfg


def new_state():
    return {
        "version": 1,
        "mode01": {"done": False, "bases_done": [], "supported": [], "values": {}, "read_done": []},
        "sweep": {"blocks_done": [], "block": None, "low": 0,
                  "found": {}, "nrc": {}, "silent": {}, "special": {}},
        "rotation": {"group": 0, "elapsed_s": 0.0, "pos": 0},
        "hotlist": {"pos": 0},
        "sessions": [],
    }


def load_state(path):
    try:
        with open(path) as fh:
            st = json.load(fh)
        if st.get("version") == 1:
            return st
    except (OSError, ValueError):
        pass
    # kaputt oder fehlt: Sicherung versuchen, sonst neu (der candump hat alles ohnehin)
    try:
        with open(path + ".bak") as fh:
            st = json.load(fh)
        if st.get("version") == 1:
            return st
    except (OSError, ValueError):
        pass
    return new_state()


def save_state(path, st):
    """Atomar: tmp schreiben, fsync, alte Datei als .bak behalten, rename, Ordner fsync."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    if os.path.exists(path):
        os.replace(path, path + ".bak")
    os.replace(tmp, path)
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------------------------
# Planer: welche Anfrage als naechstes (rein, ohne CAN - testbar)
# --------------------------------------------------------------------------------------------
def _hx(v, n=4):
    return f"{v:0{n}X}"


def block_order(cfg):
    """High-Bytes in Abarbeitungsreihenfolge fuer die freigegebene Stufe."""
    if cfg["stage"] == 0:
        return [int(b, 16) for b in cfg["pilot_blocks"]]
    prio = [int(b, 16) for b in cfg["priority_blocks"]]
    return prio + [b for b in range(256) if b not in prio]


def rotation_groups(st, cfg):
    dids = sorted(set(st["sweep"]["found"]) - set(cfg["rotation_exclude"]))
    n = max(1, int(cfg["rotation_group_size"]))
    return [dids[i:i + n] for i in range(0, len(dids), n)]


class Planner:
    def __init__(self, cfg, st):
        self.cfg = cfg
        self.st = st

    # --- welcher Job -------------------------------------------------------------------------
    def job(self):
        stage = self.cfg["stage"]
        if stage < 0:
            return None
        if not self.st["mode01"]["done"]:
            return "J0"
        if not self._sweep_done():
            return "J1"
        if stage == 3:
            return "J3" if self.cfg["hotlist"] else None
        if stage >= 2 and rotation_groups(self.st, self.cfg):
            return "J2"
        return None

    def _sweep_done(self):
        done = set(self.st["sweep"]["blocks_done"])
        return all(b in done for b in block_order(self.cfg))

    # --- naechste Anfrage --------------------------------------------------------------------
    def next_request(self):
        """('01', pid) | ('22', did) | None"""
        j = self.job()
        if j == "J0":
            return self._next_mode01()
        if j == "J1":
            return self._next_sweep()
        if j == "J2":
            g = rotation_groups(self.st, self.cfg)
            rot = self.st["rotation"]
            grp = g[rot["group"] % len(g)]
            return ("22", int(grp[rot["pos"] % len(grp)], 16))
        if j == "J3":
            hl = self.cfg["hotlist"]
            return ("22", int(hl[self.st["hotlist"]["pos"] % len(hl)], 16))
        return None

    def _next_mode01(self):
        m = self.st["mode01"]
        # Unterstuetzungsmasken 0x00, 0x20, ... solange Bit "naechste Maske" gesetzt ist
        for base in range(0, 0x100, 0x20):
            if base in m["bases_done"]:
                if base + 0x20 not in m["supported"]:
                    break
                continue
            return ("01", base)
        for pid in m["supported"]:
            if pid % 0x20 and pid not in m["read_done"]:
                return ("01", pid)
        m["done"] = True
        return self.next_request()

    def _next_sweep(self):
        sw = self.st["sweep"]
        if sw["block"] is None or sw["block"] in sw["blocks_done"]:
            done = set(sw["blocks_done"])
            sw["block"] = next(b for b in block_order(self.cfg) if b not in done)
            sw["low"] = 0
        return ("22", (sw["block"] << 8) | sw["low"])

    # --- Ergebnis verbuchen ------------------------------------------------------------------
    def record(self, req, result, payload=b"", dt=0.0):
        """result: 'pos' (payload = Daten nach SID/PID bzw. DID) | 'nrc' (payload = [code])
        | 'silent'. Schaltet Sweep/Rotation weiter."""
        kind, ident = req
        if kind == "01":
            m = self.st["mode01"]
            if ident % 0x20 == 0:
                if result == "pos" and len(payload) >= 4:
                    mask = int.from_bytes(payload[:4], "big")
                    for i in range(32):
                        if mask & (1 << (31 - i)):
                            p = ident + i + 1
                            if p not in m["supported"]:
                                m["supported"].append(p)
                m["bases_done"].append(ident)
            else:
                if result == "pos":
                    m["values"][_hx(ident, 2)] = payload.hex()
                m["read_done"].append(ident)
            return
        did = ident
        j = self.job()
        if j == "J1":
            sw = self.st["sweep"]
            blk = _hx(did >> 8, 2)
            if result == "pos":
                sw["found"][_hx(did)] = {"n": len(payload), "v": payload[:32].hex()}
            elif result == "nrc":
                code = _hx(payload[0], 2) if payload else "??"
                per = sw["nrc"].setdefault(blk, {})
                per[code] = per.get(code, 0) + 1
                if code not in ("31", "11", "12"):          # 0x33/0x22 & Co.: DID existiert
                    sw["special"][_hx(did)] = code
            else:
                sw["silent"][blk] = sw["silent"].get(blk, 0) + 1
            sw["low"] += 1
            if sw["low"] > 0xFF:
                sw["blocks_done"].append(sw["block"])
                sw["block"] = None
                sw["low"] = 0
        elif j == "J2":
            rot = self.st["rotation"]
            rot["pos"] += 1
        elif j == "J3":
            self.st["hotlist"]["pos"] += 1

    def advance_time(self, dt):
        """Motorlaufzeit fuer die Rotation verbuchen (nur, solange die Kampagne laeuft)."""
        if self.job() != "J2":
            return
        rot = self.st["rotation"]
        rot["elapsed_s"] += dt
        if rot["elapsed_s"] >= self.cfg["rotation_window_s"]:
            rot["elapsed_s"] = 0.0
            rot["pos"] = 0
            rot["group"] = (rot["group"] + 1) % max(1, len(rotation_groups(self.st, self.cfg)))


# --------------------------------------------------------------------------------------------
# ISO-TP / UDS (Single Frame raus, Single oder Multi Frame rein)
# --------------------------------------------------------------------------------------------
def build_request(req):
    kind, ident = req
    if kind == "01":
        return [0x02, 0x01, ident, 0, 0, 0, 0, 0]
    return [0x03, 0x22, (ident >> 8) & 0xFF, ident & 0xFF, 0, 0, 0, 0]


class Reassembler:
    """Sammelt die Antwort auf GENAU eine offene Anfrage. feed(data) -> None (weiter
    warten) | ('pos', payload) | ('nrc', [code]) | ('fc',) (Flow Control senden, weiter)."""

    def __init__(self, req):
        self.req = req
        self.buf = None
        self.need = 0
        self.seq = 1

    def _match(self, msg):
        kind, ident = self.req
        if kind == "01":
            if len(msg) >= 2 and msg[0] == 0x41 and msg[1] == ident:
                return ("pos", bytes(msg[2:]))
        else:
            if len(msg) >= 3 and msg[0] == 0x62 and ((msg[1] << 8) | msg[2]) == ident:
                return ("pos", bytes(msg[3:]))
        if len(msg) >= 3 and msg[0] == 0x7F and msg[1] == (0x01 if kind == "01" else 0x22):
            return ("nrc", bytes([msg[2]]))
        return None

    def feed(self, data):
        if not data:
            return None
        pci = data[0] >> 4
        if pci == 0:                                   # Single Frame
            n = data[0] & 0x0F
            return self._match(data[1:1 + n])
        if pci == 1:                                   # First Frame
            n = ((data[0] & 0x0F) << 8) | data[1]
            first = bytes(data[2:8])
            kind, ident = self.req
            ok = (first[:1] == b"\x41" and first[1:2] == bytes([ident])) if kind == "01" else \
                 (first[:1] == b"\x62" and int.from_bytes(first[1:3], "big") == ident)
            if not ok:
                return None                            # Antwort an jemand anderen
            self.buf = bytearray(first)
            self.need = n
            self.seq = 1
            return ("fc",)
        if pci == 2 and self.buf is not None:          # Consecutive Frame
            if (data[0] & 0x0F) != (self.seq & 0x0F):
                self.buf = None                        # Reihenfolge kaputt: verwerfen
                return None
            self.seq += 1
            self.buf += bytes(data[1:8])
            if len(self.buf) >= self.need:
                return self._match(bytes(self.buf[:self.need]))
        return None


FLOW_CONTROL = [0x30, 0x00, 0x00, 0, 0, 0, 0, 0]


# --------------------------------------------------------------------------------------------
# Kampagne (I/O), vom Poller je Runde aufgerufen
# --------------------------------------------------------------------------------------------
class Campaign:
    def __init__(self, tx_bus, rx_bus, state_dir, config_path, clock=time.monotonic,
                 wall=time.time, make_msg=None):
        self.tx = tx_bus
        self.rx = rx_bus
        self.dir = state_dir
        self.state_path = os.path.join(state_dir, "state.json")
        self.events_path = os.path.join(state_dir, "events.log")
        self.cfg = load_config(config_path)
        self.st = load_state(self.state_path)
        self.plan = Planner(self.cfg, self.st)
        self.clock = clock
        self.wall = wall
        self.make_msg = make_msg or _python_can_msg
        now = clock()
        self.errors = 0
        self.disabled = self.cfg["stage"] < 0
        self.stopped = False
        self.last_stop_check = -1e9
        self.last_save = now
        self.last_tick = now
        self.time_tokens = 0.0
        self.req_tokens = 0.0
        self.rpm = 0.0
        self.app = 0.0
        self.last_engine_frame = -1e9
        self.engine_since = None
        self.session = {"start": round(wall(), 1), "stage": self.cfg["stage"], "req": 0, "pos": 0,
                        "nrc": 0, "silent": 0, "busy_s": 0.0, "engine_s": 0.0,
                        "active_s": 0.0, "pause_foreign_s": 0.0, "pause_app_s": 0.0, "errors": 0}
        self.st["sessions"] = (self.st["sessions"] + [self.session])[-50:]
        self.event(f"start stage={self.cfg['stage']} job={self.plan.job()}")

    # --- Hilfen ------------------------------------------------------------------------------
    def event(self, text):
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(self.events_path, "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def save(self):
        try:
            save_state(self.state_path, self.st)
        except OSError as exc:
            self.event(f"save fehlgeschlagen: {exc!r}")
        self.last_save = self.clock()

    def _drain(self):
        """Alle wartenden Frames lesen: 0x202 aktualisiert Motorzustand, alte 0x7E8 verwerfen."""
        while True:
            msg = self.rx.recv(timeout=0)
            if msg is None:
                return
            if msg.arbitration_id == ENGINE_ID and len(msg.data) >= 6:
                d = msg.data
                self.rpm = ((d[0] << 8) | d[1]) * 0.25
                self.app = ((d[4] << 8) | d[5]) * 0.0015625
                self.last_engine_frame = self.clock()

    def _engine_ok(self, now):
        running = self.rpm >= self.cfg["min_rpm"] and now - self.last_engine_frame < 1.0
        if not running:
            self.engine_since = None
            return False
        if self.engine_since is None:
            self.engine_since = now
        return now - self.engine_since >= self.cfg["start_delay_s"]

    # --- ein Schritt -------------------------------------------------------------------------
    def tick(self, foreign_active):
        if self.disabled:
            return
        now = self.clock()
        dt = min(max(now - self.last_tick, 0.0), 1.0)
        self.last_tick = now
        if now - self.last_stop_check > 10.0:
            self.last_stop_check = now
            if os.path.exists(os.path.join(self.dir, "STOP")):
                self.event("STOP-Datei gefunden, Kampagne aus")
                self.disabled = True
                self.save()
                return
        try:
            self._tick(now, dt, foreign_active)
        except Exception as exc:                       # nie den Poller mitreissen
            self.errors += 1
            self.session["errors"] += 1
            self.event(f"Fehler {self.errors}: {exc!r}")
            if self.errors >= self.cfg["max_errors"]:
                self.event("zu viele Fehler, Kampagne aus bis zum naechsten Start")
                self.disabled = True
                self.save()

    def _tick(self, now, dt, foreign_active):
        self._drain()
        engine = self._engine_ok(now)
        if engine:
            self.session["engine_s"] += dt
        if now - self.last_save >= self.cfg["save_every_s"]:
            self.save()
        if not engine:
            self.time_tokens = self.req_tokens = 0.0
            return
        if foreign_active:
            self.session["pause_foreign_s"] += dt
            self.time_tokens = self.req_tokens = 0.0
            return
        if self.app >= self.cfg["app_pause_pct"]:
            self.session["pause_app_s"] += dt
            self.time_tokens = self.req_tokens = 0.0
            return
        job = self.plan.job()
        if job is None:
            return
        self.session["active_s"] += dt
        self.plan.advance_time(dt)
        # Token-Buckets ohne Ansparen: hoechstens eine Anfrage bzw. eine Wartezeit auf Vorrat
        self.time_tokens = min(self.time_tokens + dt * self.cfg["max_share"],
                               self.cfg["pending_timeout_s"])
        self.req_tokens = min(self.req_tokens + dt * self.cfg["max_rate_hz"], 1.0)
        if self.req_tokens < 1.0 or self.time_tokens < self.cfg["resp_timeout_s"]:
            return
        before = job                    # vor next_request: das schliesst J0 ggf. selbst ab
        req = self.plan.next_request()
        if req is None:
            return
        t0 = self.clock()
        result, payload = self._exchange(req)
        busy = self.clock() - t0
        self.time_tokens -= busy
        self.req_tokens -= 1.0
        self.session["busy_s"] += busy
        self.session["req"] += 1
        self.session[result] += 1
        self.plan.record(req, result, payload)
        after = self.plan.job()
        if after != before:
            self.event(f"{before} fertig, weiter mit {after}")
            self.save()

    def _exchange(self, req):
        """Anfrage senden, auf die Antwort warten (mit Flow Control, responsePending)."""
        self._drain()
        self.tx.send(self.make_msg(PCM_REQ, build_request(req)))
        r = Reassembler(req)
        deadline = self.clock() + self.cfg["resp_timeout_s"]
        while True:
            left = deadline - self.clock()
            if left <= 0:
                return "silent", b""
            msg = self.rx.recv(timeout=left)
            if msg is None:
                continue
            if msg.arbitration_id == ENGINE_ID:
                continue
            if msg.arbitration_id != PCM_RESP:
                continue
            out = r.feed(bytes(msg.data))
            if out is None:
                continue
            if out[0] == "fc":
                self.tx.send(self.make_msg(PCM_REQ, FLOW_CONTROL))
                deadline = self.clock() + self.cfg["resp_timeout_s"]
                continue
            if out[0] == "nrc" and out[1][0] == NRC_PENDING:
                deadline = self.clock() + self.cfg["pending_timeout_s"]
                continue
            return out

    def close(self):
        self.event("ende " + json.dumps({k: (round(v, 1) if isinstance(v, float) else v)
                                          for k, v in self.session.items()}))
        self.save()


def _python_can_msg(arb_id, data):
    import can
    return can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)


# --------------------------------------------------------------------------------------------
# Selbsttest (ohne CAN): Planer, Zustand, Reassembler, Token-Bucket mit Ersatz-PCM
# --------------------------------------------------------------------------------------------
class _Msg:
    def __init__(self, arb, data):
        self.arbitration_id = arb
        self.data = bytes(data)


class FakePCM:
    """Antwortet wie ein PCM. supported: {did: payload}; mode01: {pid: payload}.
    silent=True: nicht vorhandene DIDs bleiben unbeantwortet (sonst NRC 0x31)."""

    def __init__(self, clock, supported, mode01, rpm=800.0, app=0.0, silent=False, pending=()):
        self.clock = clock
        self.supported = supported
        self.mode01 = mode01
        self.silent = silent
        self.pending = set(pending)
        self.queue = []
        self.rpm = rpm
        self.app = app
        self.sent = []
        self._mf = None

    # tx-Seite
    def send(self, msg):
        d = list(msg.data)
        self.sent.append((msg.arbitration_id, d))
        if d[0] == 0x30 and self._mf:
            rest = self._mf
            self._mf = None
            seq = 1
            for i in range(0, len(rest), 7):
                self.queue.append(_Msg(PCM_RESP, [0x20 | (seq & 15)] + list(rest[i:i + 7]) + [0] * (7 - len(rest[i:i + 7]))))
                seq += 1
            return
        if d[1] == 0x22:
            did = (d[2] << 8) | d[3]
            if did in self.pending:
                self.pending.discard(did)
                self.queue.append(_Msg(PCM_RESP, [0x03, 0x7F, 0x22, 0x78, 0, 0, 0, 0]))
            if did in self.supported:
                self._reply(bytes([0x62, d[2], d[3]]) + self.supported[did])
            elif not self.silent:
                self.queue.append(_Msg(PCM_RESP, [0x03, 0x7F, 0x22, 0x31, 0, 0, 0, 0]))
        elif d[1] == 0x01:
            if d[2] in self.mode01:
                self._reply(bytes([0x41, d[2]]) + self.mode01[d[2]])

    def _reply(self, payload):
        if len(payload) <= 7:
            self.queue.append(_Msg(PCM_RESP, [len(payload)] + list(payload) + [0] * (7 - len(payload))))
        else:
            n = len(payload)
            self.queue.append(_Msg(PCM_RESP, [0x10 | (n >> 8), n & 0xFF] + list(payload[:6])))
            self._mf = payload[6:]

    # rx-Seite: 0x202 alle 10 ms (nach der Testuhr), Antworten aus der Warteschlange
    _last_engine = -1e9

    def recv(self, timeout=0):
        if self.clock() - self._last_engine >= 0.01:
            self._last_engine = self.clock()
            raw = int(self.rpm / 0.25)
            a = int(self.app / 0.0015625)
            return _Msg(ENGINE_ID, [raw >> 8, raw & 255, 0, 0, a >> 8, a & 255, 0, 0])
        if self.queue:
            return self.queue.pop(0)
        if timeout:
            self.clock.t += timeout
        return None


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def self_test():
    import tempfile

    # Reassembler: Single, Multi, NRC, fremde Antwort
    r = Reassembler(("22", 0x1310))
    assert r.feed(bytes([0x05, 0x62, 0x13, 0x10, 0x12, 0x34, 0, 0])) == ("pos", b"\x12\x34")
    r = Reassembler(("22", 0x1310))
    assert r.feed(bytes([0x05, 0x62, 0x03, 0xEC, 0x12, 0x34, 0, 0])) is None
    assert r.feed(bytes([0x03, 0x7F, 0x22, 0x31, 0, 0, 0, 0])) == ("nrc", b"\x31")
    r = Reassembler(("22", 0xF400))
    assert r.feed(bytes([0x10, 0x0A, 0x62, 0xF4, 0x00, 1, 2, 3])) == ("fc",)
    assert r.feed(bytes([0x21, 4, 5, 6, 7, 0, 0, 0])) == ("pos", bytes([1, 2, 3, 4, 5, 6, 7]))
    # Erstes Frame einer fremden Mehrrahmen-Antwort mit Laenge 0x62 (Grenzfall Backend)
    r = Reassembler(("22", 0x1310))
    assert r.feed(bytes([0x10, 0x62, 0x62, 0x03, 0xEC, 1, 2, 3])) is None

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "cfg.json")
        sdir = os.path.join(tmp, "state")

        # kaputte Konfiguration -> aus
        with open(cfg_path, "w") as fh:
            fh.write("{kaputt")
        clk = _Clock()
        pcm = FakePCM(clk, {}, {})
        c = Campaign(pcm, pcm, sdir, cfg_path, clock=clk, wall=clk, make_msg=_Msg)
        assert c.disabled

        # Pilot: Mode-01-Inventar + Block 0x13, Oeltemperatur muss gefunden werden
        with open(cfg_path, "w") as fh:
            json.dump({"stage": 0}, fh)
        supported = {0x1310: b"\x1f\x40", 0x1301: bytes(range(20))}
        mode01 = {0x00: b"\xbe\x1f\xa8\x13", 0x20: b"\x00\x00\x00\x00", 0x0C: b"\x0c\x80", 0x05: b"\x5a"}
        clk = _Clock()
        pcm = FakePCM(clk, supported, mode01)
        c = Campaign(pcm, pcm, sdir, cfg_path, clock=clk, wall=clk, make_msg=_Msg)
        for _ in range(200000):
            clk.t += 0.027                         # eine schnelle Runde des Pollers
            c.tick(False)
            if c.plan.job() is None:
                break
        st = c.st
        assert st["mode01"]["done"], st["mode01"]
        assert "0C" in st["mode01"]["values"] and "05" in st["mode01"]["values"]
        assert st["sweep"]["blocks_done"] == [0x13], st["sweep"]["blocks_done"]
        assert st["sweep"]["found"]["1310"]["v"] == "1f40"
        assert st["sweep"]["found"]["1301"]["n"] == 20                 # Mehrrahmen
        assert st["sweep"]["nrc"]["13"]["31"] == 254
        with open(os.path.join(sdir, "events.log")) as fh:
            ev = fh.read()
        assert "J0 fertig, weiter mit J1" in ev and "J1 fertig, weiter mit None" in ev, ev
        # Rate und Zeitanteil eingehalten (Motor lief nach 30 s Startverzoegerung)
        s = c.session
        assert s["req"] / s["active_s"] <= 15.0 + 1e-6, s
        assert s["busy_s"] / s["active_s"] <= 0.10 + 1e-6, s
        # nur lesende Dienste gesendet
        for arb, d in pcm.sent:
            assert arb == PCM_REQ and (d[1] in (0x01, 0x22) or d[0] == 0x30), d
        c.close()

        # Fortsetzung nach Neustart: Stufe 1 macht beim naechsten Block weiter, Prioritaet zuerst
        with open(cfg_path, "w") as fh:
            json.dump({"stage": 1, "priority_blocks": ["03", "13"]}, fh)
        clk = _Clock()
        pcm = FakePCM(clk, {0x03EC: b"\xff\x80"}, mode01, silent=True)
        c = Campaign(pcm, pcm, sdir, cfg_path, clock=clk, wall=clk, make_msg=_Msg)
        assert c.plan.next_request() == ("22", 0x0300)
        # schweigendes PCM: jede DID kostet die volle Wartezeit -> ~max_share/resp_timeout = 3,3/s
        t_start = clk.t
        for _ in range(8000):
            clk.t += 0.027
            c.tick(False)
            if "03EC" in c.st["sweep"]["found"]:
                break
        assert c.st["sweep"]["found"].get("03EC") == {"n": 2, "v": "ff80"}
        rate = c.session["req"] / (clk.t - t_start - 30.0)
        assert 2.8 < rate <= 3.4, rate
        assert c.st["sweep"]["silent"].get("03", 0) > 0

        # Pausen: fremder Tester, Volllast, Motor aus -> keine Anfrage
        n0 = len(pcm.sent)
        for _ in range(500):
            clk.t += 0.027
            c.tick(True)
        pcm.app = 95.0
        for _ in range(500):
            clk.t += 0.027
            c.tick(False)
        pcm.app, pcm.rpm = 0.0, 0.0
        for _ in range(500):
            clk.t += 0.027
            c.tick(False)
        assert len(pcm.sent) == n0, len(pcm.sent) - n0
        # Motor wieder an: erst nach Startverzoegerung
        pcm.rpm = 800.0
        for _ in range(int(29 / 0.027)):
            clk.t += 0.027
            c.tick(False)
        assert len(pcm.sent) == n0
        for _ in range(200):
            clk.t += 0.027
            c.tick(False)
        assert len(pcm.sent) > n0

        # STOP-Datei
        open(os.path.join(sdir, "STOP"), "w").close()
        clk.t += 11
        c.tick(False)
        assert c.disabled
        os.remove(os.path.join(sdir, "STOP"))

        # Zustand ueberlebt Absturz mitten im Schreiben (kaputte state.json, .bak bleibt)
        c.save()
        with open(os.path.join(sdir, "state.json"), "w") as fh:
            fh.write('{"version": 1, "kaput')
        st2 = load_state(os.path.join(sdir, "state.json"))
        assert st2["version"] == 1 and "1310" in st2["sweep"]["found"]

        # responsePending wird abgewartet
        with open(cfg_path, "w") as fh:
            json.dump({"stage": 3, "hotlist": ["1310"]}, fh)
        st3 = load_state(os.path.join(sdir, "state.json"))
        st3["mode01"]["done"] = True
        st3["sweep"]["blocks_done"] = list(range(256))
        save_state(os.path.join(sdir, "state.json"), st3)
        clk = _Clock()
        pcm = FakePCM(clk, {0x1310: b"\x20\x00"}, {}, pending=[0x1310])
        c = Campaign(pcm, pcm, sdir, cfg_path, clock=clk, wall=clk, make_msg=_Msg)
        assert c._exchange(("22", 0x1310)) == ("pos", b"\x20\x00")

        # Rotation: Gruppen aus gefundenen DIDs, Wechsel nach window_s
        cfg = dict(DEFAULT_CONFIG, stage=2, rotation_group_size=2, rotation_window_s=10.0)
        st4 = new_state()
        st4["mode01"]["done"] = True
        st4["sweep"]["blocks_done"] = list(range(256))
        st4["sweep"]["found"] = {"0001": {}, "0002": {}, "0003": {}}
        p = Planner(cfg, st4)
        assert p.next_request() == ("22", 1)
        p.record(("22", 1), "pos", b"\x00")
        assert p.next_request() == ("22", 2)
        p.advance_time(10.0)
        assert p.next_request() == ("22", 3)
    print("self-test ok")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    if ap.parse_args().self_test:
        self_test()
