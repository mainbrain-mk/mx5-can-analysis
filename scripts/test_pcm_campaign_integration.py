"""
Integrationstest PCM-Kampagne: der ECHTE tpms_poller.main() gegen ein Ersatz-PCM auf dem
virtuellen python-can-Bus (wie SocketCAN: eigene Frames kommen nicht an den Sender zurueck).

Je Szenario ein eigener Prozess (globaler Zustand im Poller), Messung ueber einen
Beobachter-Bus:
  off    Kampagne aus (Bezug)
  on     Kampagne Stufe 0 (Pilot), PCM beantwortet unbekannte DIDs mit NRC 0x31
  silent Kampagne Stufe 0, PCM schweigt bei unbekannten DIDs
  phone  Kampagne Stufe 0, ein simuliertes Handy fragt mit ~16/s auf 0x7E0 an

Geprueft:
  * Raten der schnellen Gruppe (Lambda 0x44, KnockRetard 0x03EC) mit Kampagne >= 85 % des Bezugs
    (Ziel 90 %, die Grenze laesst Raum fuer Laufzeitschwankungen des Tests)
  * Oeltemperatur kommt weiter an
  * Kampagne sendet nur 0x22/0x01 (+ Flow Control) und findet im Pilot-Block 0x1310
  * bei aktivem Handy sendet die Kampagne nichts

Aufruf: .venv/bin/python scripts/test_pcm_campaign_integration.py   (~2 min, echte Zeit)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CHANNEL = "pcmtest"
RUN_S = float(os.environ.get("RUN_S", 40.0))
SUPPORTED = {0x1310: b"\x1f\x40", 0x03EC: b"\xff\x80", 0x1301: bytes(range(12))}
MODE01 = {0x00: b"\x98\x3b\x80\x11", 0x20: b"\x00\x00\x00\x00", 0x44: b"\x80\x00",
          0x11: b"\x30", 0x10: b"\x01\x20", 0x3C: b"\x10\x00", 0x34: b"\x80\x00\x80\x00", 0x0C: b"\x0c\x80"}


def fake_pcm(stop, silent, latency=0.0035):
    import can
    bus = can.interface.Bus(channel=CHANNEL, interface="virtual")
    mf_rest = None
    next_engine = time.time()
    while not stop.is_set():
        now = time.time()
        if now >= next_engine:
            raw = 800 * 4
            bus.send(can.Message(arbitration_id=0x202, data=[raw >> 8, raw & 255, 0, 0, 0, 0, 0x19, 0],
                                 is_extended_id=False))
            next_engine = now + 0.01
        msg = bus.recv(timeout=0.002)
        if msg is None or msg.arbitration_id not in (0x7E0, 0x7DF):
            continue
        d = list(msg.data)
        time.sleep(latency)
        out = None
        if d[0] == 0x30 and mf_rest:
            seq = 1
            for i in range(0, len(mf_rest), 7):
                chunk = list(mf_rest[i:i + 7])
                bus.send(can.Message(arbitration_id=0x7E8, data=[0x20 | seq] + chunk + [0] * (7 - len(chunk)),
                                     is_extended_id=False))
                seq += 1
            mf_rest = None
            continue
        if d[1] == 0x22:
            did = (d[2] << 8) | d[3]
            if did in SUPPORTED:
                out = bytes([0x62, d[2], d[3]]) + SUPPORTED[did]
            elif not silent:
                out = bytes([0x7F, 0x22, 0x31])
        elif d[1] == 0x01 and d[2] in MODE01:
            out = bytes([0x41, d[2]]) + MODE01[d[2]]
        if out is None:
            continue
        if len(out) <= 7:
            frame = [len(out)] + list(out) + [0] * (7 - len(out))
        else:
            frame = [0x10, len(out)] + list(out[:6])
            mf_rest = out[6:]
        bus.send(can.Message(arbitration_id=0x7E8, data=frame, is_extended_id=False))
    bus.shutdown()


def phone(stop):
    import can
    bus = can.interface.Bus(channel=CHANNEL, interface="virtual")
    while not stop.is_set():
        bus.send(can.Message(arbitration_id=0x7E0, data=[2, 1, 0x0C, 0, 0, 0, 0, 0], is_extended_id=False))
        time.sleep(1 / 16)
    bus.shutdown()


def scenario(name):
    import can
    import tpms_poller

    # python-can virtual statt SocketCAN, sonst unveraendert
    real_bus = can.interface.Bus

    def vbus(channel=None, interface=None, can_filters=None, **kw):
        return real_bus(channel=CHANNEL, interface="virtual", can_filters=can_filters)
    can.interface.Bus = vbus

    tmp = tempfile.mkdtemp()
    cfg = os.path.join(tmp, "cfg.json")
    with open(cfg, "w") as fh:
        json.dump({"stage": -1 if name == "off" else 0, "start_delay_s": 2.0}, fh)
    sdir = os.path.join(tmp, "state")

    stop = threading.Event()
    obs = real_bus(channel=CHANNEL, interface="virtual")
    threading.Thread(target=fake_pcm, args=(stop, name == "silent"), daemon=True).start()
    if name == "phone":
        threading.Thread(target=phone, args=(stop,), daemon=True).start()
    time.sleep(0.3)
    sys.argv = ["tpms_poller.py", "--channel", CHANNEL, "--campaign-config", cfg, "--campaign-dir", sdir]
    threading.Thread(target=tpms_poller.main, daemon=True).start()

    counts = {"lambda": 0, "knock": 0, "oil": 0, "camp_req": 0, "bad_service": 0}
    t_end = time.time() + RUN_S
    while time.time() < t_end:
        m = obs.recv(timeout=0.1)
        if m is None:
            continue
        d = m.data
        if m.arbitration_id == 0x7E8 and d[0] < 8:
            if d[1] == 0x41 and d[2] == 0x44:
                counts["lambda"] += 1
            elif d[1] == 0x62 and d[2:4] == b"\x03\xec":
                counts["knock"] += 1
            elif d[1] == 0x62 and d[2:4] == b"\x13\x10":
                counts["oil"] += 1
        elif m.arbitration_id == 0x7E0 and d[0] != 2 or (m.arbitration_id == 0x7E0 and d[2] not in (0x0C, 0x44, 0x11, 0x10, 0x3C, 0x34)):
            # alles ausser den bekannten Poller-/Handy-Anfragen stammt von der Kampagne
            if d[0] == 0x03 and d[1] == 0x22 and bytes(d[2:4]) in (b"\x03\xec", b"\x13\x10"):
                pass
            else:
                counts["camp_req"] += 1
                if not (d[1] in (0x01, 0x22) or d[0] == 0x30):
                    counts["bad_service"] += 1
    stop.set()
    state = {}
    try:
        with open(os.path.join(sdir, "state.json")) as fh:
            state = json.load(fh)
    except OSError:
        pass
    counts["found"] = sorted(state.get("sweep", {}).get("found", {}))
    counts["mode01_done"] = state.get("mode01", {}).get("done")
    print("RESULT " + json.dumps(counts), flush=True)
    os._exit(0)


def run(name):
    out = subprocess.run([sys.executable, __file__, "--scenario", name], capture_output=True, text=True, timeout=120)
    line = [l for l in out.stdout.splitlines() if l.startswith("RESULT ")]
    assert line, out.stdout[-2000:] + out.stderr[-2000:]
    return json.loads(line[0][7:])


def main():
    res = {n: run(n) for n in ("off", "on", "silent", "phone")}
    for n, r in res.items():
        print(n, r)
    base = res["off"]
    assert base["camp_req"] == 0, base
    for n in ("on", "silent"):
        r = res[n]
        for k in ("lambda", "knock"):
            assert r[k] >= 0.85 * base[k], (n, k, r[k], base[k])
        assert r["oil"] >= base["oil"] - 1, (n, r["oil"], base["oil"])
        assert r["camp_req"] > 0 and r["bad_service"] == 0, (n, r)
    # Pilot im NRC-Fall in 25 s durch: Mode-01-Inventar + Block 0x13 (mit Mehrrahmen-DID)
    assert res["on"]["mode01_done"] and "1310" in res["on"]["found"] and "1301" in res["on"]["found"], res["on"]
    assert res["phone"]["camp_req"] == 0, res["phone"]
    print("alle Integrationstests ok")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--scenario":
        scenario(sys.argv[2])
    else:
        main()
