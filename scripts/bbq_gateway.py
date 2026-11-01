"""BLE->WLAN-Gateway fuer das Day_BBQ-Grillthermometer, laeuft auf dem Auto-Pi.

Solange kein CAN-Adapter steckt (Pi zu Hause), haelt der Pi die BLE-Verbindung zum Thermometer
und schickt dieselbe Live-Zeile wie das Handskript an jeden TCP-Client. Dazu gehen die Werte per UDP
im can_backend-Snapshotformat an dash_gui.py (Port 51236), das sie auf dem Wartebildschirm zeigt.
Auf dem Laptop:

    nc car.local 51235

Mit gestecktem Adapter (im Auto) wartet das Skript nur. Start ueber respawn.sh im labwc-autostart;
die Temperaturzeilen gehen nur an die Clients, ins applogs-Log nur Verbindungsereignisse.
"""
import asyncio, json, os, socket, struct, time
from bleak import BleakClient, BleakScanner

MAC = "F4:5E:AB:92:63:B9"
NOTIFY = "fe551582-4180-8a02-ef2c-1b42a0ac3f83"
PORT = 51235
CAN_IFACE = "/sys/class/net/can0"
DASH_ADDR = ("127.0.0.1", 51236)  # dash_gui.BBQ_UDP_PORT

clients = set()
last = ""  # letzte Temperaturzeile, bekommt jeder neue Client sofort
dash = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send(text: str, log=False):
    for w in clients:
        w.write(text.encode())
    if log:
        print(f"{time.strftime('%F %T')} {text.strip()}", flush=True)


def show(_, data: bytearray):
    global last
    p1, p2, t1, t2 = struct.unpack("<4H", data[:8])  # °C: Ist P1, Ist P2, Ziel P1, Ziel P2
    last = f"\r{time.strftime('%H:%M:%S')}   P1 {p1:3d} °C (Ziel {t1})   P2 {p2:3d} °C (Ziel {t2})   "
    send(last)
    now = time.monotonic()
    values = {f"bbq:{k}": [v, now] for k, v in zip(("P1", "P2", "Ziel1", "Ziel2"), (p1, p2, t1, t2))}
    dash.sendto(json.dumps({"values": values, "clock": "monotonic"}).encode(), DASH_ADDR)


async def on_client(reader, writer):
    clients.add(writer)
    writer.write(last.encode())
    await reader.read()  # liest nichts, wartet nur, bis der Client trennt
    clients.discard(writer)
    writer.close()


async def main():
    await asyncio.start_server(on_client, port=PORT)
    while True:  # Signal ist schwach: bei Abbruch einfach neu verbinden
        if os.path.exists(CAN_IFACE):  # im Auto: kein Grill, BLE in Ruhe lassen
            await asyncio.sleep(30)
            continue
        try:
            dev = await BleakScanner.find_device_by_address(MAC, timeout=20)
            if not dev:
                send("\nDay_BBQ nicht gefunden (Handy-Bluetooth aus?), suche weiter …\n", log=True)
                continue
            gone = asyncio.Event()
            async with BleakClient(dev, timeout=20, disconnected_callback=lambda _: gone.set()) as c:
                send("\nverbunden\n", log=True)
                await c.start_notify(NOTIFY, show)
                await gone.wait()
            send("\nVerbindung verloren, verbinde neu …\n", log=True)
        except Exception as e:
            send(f"\n{e!r}, neuer Versuch …\n", log=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
