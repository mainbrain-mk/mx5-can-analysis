# Pi-Laufzeitstatus: was läuft gerade auf dem Auto-Pi

Momentaufnahme des tatsächlichen Zustands auf dem Raspberry Pi im Auto. Die Ersteinrichtung und
Reproduzierbarkeit beschreibt [`../../scripts/pi-config/SETUP.md`](../../scripts/pi-config/SETUP.md).
**Bei jedem Umbau auf dem Pi in-place aktualisieren.** Den laufenden Code-Stand zeigt das Git auf dem Pi
(siehe unten). Ältere Stände bis 28.09. sind ins Logbuch ausgelagert, siehe `logs/can-bus-status.md`,
„Pi-Infrastruktur: Lücken zu Hause reproduziert…“ (10.10.2026).

**Stand 2026-10-10, ~23:50** (per SSH geprüft, Pi zu Hause ohne CAN-Adapter, nach dem Stick-Tausch):
- Deploy: `deployed` = Kopf von Branch `gzip-hintergrund-2026-10-10` (PR #45, noch nicht in `main`), zuletzt `487d19c` (Dash: Sim-Logliste wird laufend nachgelesen). PR #46 offen.
- `throttled=0x0`, keine failed units, `/media/root-ro` ro, Stick 2,7 GB belegt, 106 GB frei.
- Alle Fahrt-Logs gepackt. Repo-Kopien der Overlay-Dateien sind identisch mit dem Unterbau.

**Nachtrag 2026-11-06:** `deployed` = Kopf von Branch `gps-bluetooth-2026-11-06` (`16e8cf4`, noch nicht in `main`). Handy per
Bluetooth gekoppelt, Schlüssel in der Basisschicht. Nach dem Neustart: keine failed units, `/media/root-ro` ro.

**Nachtrag 2026-11-08:** `deployed` = `3eff2b3` (Branch `spreewaldring-streckenmodell-2026-11-07`, noch nicht in `main`):
Editor-Simulation aufs Dash, `can_backend.py` lauscht zusätzlich auf UDP 51235. Kivy- und Backend-Tests auf dem Pi grün,
Wiedergabe am Display geprüft.

## Hardware

Pi 4B Rev 1.5 (1,8 GB RAM), Hostname `car`, `pi@192.168.0.247`, Kernel 6.12.115 (seit 06.11.). DS3231-RTC seit 28.09.
CANable 2.0 an `can0`, steckt nur im Auto. SD-Karte 64 GB mit overlayroot, also ro und mit RAM-Overlay.
`/home/pi/canlogs` liegt seit 10.10. auf einem SanDisk Ultra Flair 128 GB (ext4, Label `canlogs`) im blauen
USB-3-Port, `write()` max. 0,02 s. Der alte USB-2.0-Werbestick hing beim Schreiben bis 3 s, er ist als Backup
aufbewahrt. candump behält den 16-MiB-Empfangspuffer (~13 s) als Reserve, siehe Logbuch 10.10.
Der Pi sitzt auf der Rückseite des 10,1"-Displays („10.1inch LCD Display-H“), Strom kommt im Auto noch von
einer Powerbank. Eine USV ist geplant, siehe Logbuch 10.10. „USV-Recherche“.

## Prozesse und Dienste

| Was | gestartet von | läuft | Ausgabe |
|---|---|---|---|
| `can_backend.py` (Decode-Daemon, UDP-Snapshot Port 51234, sendet selbst nichts) | labwc-`autostart` → `respawn.sh` | immer | `applogs/can_backend.log` |
| `dash_gui.py` (Kivy-Frontend; Touch nur einfach seit 10.10., `mouse,disable_on_activity`) | labwc-`autostart` → `respawn.sh` | immer | `applogs/dash_gui.log` |
| `can-logger.service` → `session_logger.py` (KeyState-Trigger, Kernelfilter auf 0x050) | udev, `BindsTo` `can0` | solange der Adapter steckt | Journal (volatile) |
| └ `candump -l -r 16777216 can0` | `session_logger`, KeyState ≠ OFF | Zündung an | `candump-*.log`, `clockstate-*`, `ifstats-*` (minütlich) |
| └ `tpms_poller.py --interval 120` (TPMS, Öl, Lambda/ETC/Klopfen per OBD/UDS) | dito | Zündung an | landet im candump |
| `canlogs-pack.timer` → `canlogs-pack.service` (`session_logger.py --pack`) | `timers.target` | 10 min nach dem Boot, dann alle 15 min | Journal |
| `wlan-watchdog.timer` | `timers.target` | alle 15 s | – |
| `gps-logger.service` → `gps_logger.py` (Handy-GPS per Bluetooth, seit 06.11.; Handy-Adresse in `gps_phone.env`) | `multi-user.target` | immer, schreibt sobald die App sendet | `gps-*.nmea`, `/tmp/mx5_gps_status.json`, Journal |

- **Packen:** nur `candump-*.log` und nur, solange kein candump läuft. Fährt man währenddessen los, stoppt
  `session_logger` den Packdienst, die Rohdatei bleibt heil. Ein 600-MB-Log braucht ~1 min.
- **`tpms_poller.py`** schickt die Schnellabfragen (PID 0x11/0x44/0x10, DID 0x03EC), deren Antworten Dash und
  Log zeigen. Ohne Handy-Dongle laufen sie ungebremst (~150 Anfragen/s auf 0x7E0). Sieht er fremde Tester-Anfragen,
  sind es 5 Runden/s (`FAST_PERIOD_SHARED_S`).
- **Vcan-Simulation** (Knopf auf dem Status-Bildschirm, wenn kein `can0` da ist): spielt das in der Liste
  daneben gewählte Log (Standard: neuestes) komplett und in Schleife ab und zeigt den Fahrbildschirm. Die Liste
  wird alle 2 s neu eingelesen; gerade gepackte Logs erscheinen also ohne Dash-Neustart. Gestreamt
  per `zcat | canplayer -s 5`, nichts landet in `/tmp`. Beenden über „SIMULATION BEENDEN“ in dessen Fußzeile.
  Die Trigger-Datei `/tmp/mx5_sim_active` enthält den Logpfad; der Tank startet mit dem Stand aus dem Tankbuch.
  Ein Dash-Neustart während der Simulation lässt sie verwaist weiterlaufen (Knopf weg): vorher beenden.
- **Editor-Simulation** (seit 08.11.): Beim Abspielen im Ideallinien-Editor (`spreewaldring_racing_line_editor.py`
  auf dem PC, Endpunkt `/api/dash`) kommen die angezeigten Werte per UDP auf Port **51235** (alle Adressen) in
  `can_backend.py` an, etwa 16 Pakete/s. Solange sie jünger als 1 s sind, besteht der Snapshot nur aus ihnen:
  Tempo (wahres Simulationstempo), Drehzahl, Gang, Gas, Bremse plus Bremslicht, Lenkrad. Alles andere zeigt das Dash
  als „–“, die Fußzeile „EDITOR-SIMULATION“ und die Rundenzeit. Pause → nach 1 s wieder CAN. Mit echtem `can0`
  (im Auto) nimmt das Backend nichts an. Ziel ist `MX5_DASH_HOST` (Standard `car`).
- Neustart nach einem Deploy: nur die Python-PID beenden, `respawn.sh` startet nach 2 s neu (nicht
  `pkill -f dash_gui.py`, das trifft auch die Schleife). `session_logger` startet beim nächsten Einstecken des
  Adapters, im Auto genügt `sudo systemctl restart can-logger`.

![dash_gui.py auf dem Pi (2026-09-20)](images/pi-dashboard-2026-09-20.png)

## `/home/pi/canlogs` (Stick)

- **Fahrtdaten** (die Pipeline holt sie per `find -maxdepth 1`): `candump-*.log(.gz)`, `clockstate-*.txt`,
  `clockjump-*.txt`, `ifstats-*.txt`, seit 06.11. `gps-*.nmea` (nicht gepackt, je Logger-Prozess eine Datei).
- **Symlinks ins Repo:** `can_backend.py`, `dash_gui.py`, `session_logger.py`, `status_gui.py`, `tpms_poller.py`,
  `uds_did_sweep.py`, `test_*.py`, `mx5_top_view.png`, `fonts/`, `respawn.sh`, beide HSCAN-DBC.
- `repo/` (Git-Checkout, Branch `deployed`), `venv/`, `applogs/` (rotiert bei 5 MB beim Neustart),
  `boot_timing.log` (Dash-Erstframe je Boot), `last_known_time` (Uhr-Anker), `fuel_state.json` (Tankinhalt in Litern für die KRAFTSTOFF-Kachel, schreibt
  `can_backend.py`; seit 04.11. aktiv, zuletzt 20:32 von Hand auf 41,42 l gesetzt (Tankbuch mit Sättigungsanker),
  `await_desat` = Volltanken gesehen, Sättigungsende steht aus. Löschen = Neustart beim Geberwert),
  `fuel_ledger.jsonl` (Tankbuch, nur angehängt: je Log Liter bei Start/Ende, Tankstopps und Abgleiche; schreibt
  `can_backend.py` bei jeder echten Fahrt, die Vcan-Simulation startet mit dem `start`-Stand des abgespielten Logs.
  Die Logs bis 04.11. 16:38 sind mit `scripts/fuel_ledger_backfill.py` nachberechnet, `source: nachberechnet`).
- `archiv/`: alte Backups (`backup-*`), Vorversionen und Ausgaben bis 10.10. Nichts davon wird im Betrieb gebraucht.

## Deploy-Stand = Git auf dem Pi

`/home/pi/canlogs/repo` ist ein Git-Checkout (Branch `deployed`). Deploy per `git push car HEAD:deployed`, der
`pre-push`-Hook verlangt, dass der Commit schon auf GitHub liegt. Einrichtung und Regeln:
[`../../scripts/pi-config/SETUP.md`](../../scripts/pi-config/SETUP.md), „Deploy per Git“.

```bash
ssh pi@192.168.0.247 'cd canlogs/repo && git log --oneline -1 && git status --short'
```

Ist die `git status`-Ausgabe leer, läuft der Pi exakt mit dem angezeigten Commit.

**Nicht im Git-Checkout** (Overlay-Unterbau, per `overlayroot-chroot`): labwc-`autostart`,
`can-logger.service`, udev-Regel, `wlan-watchdog.*`, `canlogs-pack.*`, `config.txt`. Die Repo-Kopien liegen in
`scripts/pi-config/`, der Abgleich geht von Hand (zuletzt 10.10.: identisch).

## Uhr

DS3231-RTC: Der Kernel stellt die Zeit beim Boot, fake-hwclock ist aus. `session_logger.py` schreibt den
Uhr-Zustand (`ntp`/`rtc`/…) je Fahrt in `clockstate-*.txt` und NTP-Sprünge als `clockjump-*.txt`. Dash-Fußzeile:
`NTP SYNC` bzw. `RTC OK` in Grün, `UHR ?` in Rot. Der Haltetest der Knopfzelle (kalter Stromausfall, Boot ohne
Netz) steht noch aus.
