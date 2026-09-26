# Pi-Laufzeitstatus: was läuft gerade auf dem Auto-Pi

Momentaufnahme des tatsächlichen Zustands auf dem Raspberry Pi im Auto (nicht
zu verwechseln mit [`../../scripts/pi-config/SETUP.md`](../../scripts/pi-config/SETUP.md),
das die Ersteinrichtung/Reproduzierbarkeit beschreibt). **In-place aktualisieren
bei jedem Deploy/Neustart auf dem Pi** — sonst veraltet das schnell, weil hier
kein Git läuft (siehe unten).

**Nachtrag 2026-09-26, ~17:30-18:15 Uhr:** Pi lief ab 16:12:42 (`uptime -s`) am Heimnetz **ohne
CAN-Adapter** (`lsusb` ohne CANable, `can-logger.service` "dependency failed"); jemand hatte um
17:30 die Vcan-Simulation im Dash gestartet (`/tmp/mx5_sim_active`). Die sechs Fahrt-Logs des
Tages lagen dort unter falschem Datum (No-RTC, Anker 19.09.) und wurden **auf dem Pi umbenannt**
(`candump-2026-09-26_120029` … `_154000`, samt `clockstate-*`), siehe Logbuch "Fahrtag 26.09.".
`last_known_time` steht noch auf "2026-09-20 02:31:42" (falscher Anker aus der letzten Session) -
beim nächsten Start ohne NTP wird die Uhr darauf gestellt, der Marker warnt aber korrekt.
**18:55 Uhr, Pi wieder online (Boot 18:53:41, NTP ok):** in der Offline-Zeit liefen zwei weitere
Fahrten (ohne NTP, als `candump-2026-09-20_023215/_031203` benannt) - auf dem Pi umbenannt in
`candump-2026-09-26_180459/_184447` (geschätzt, siehe Logbuch). `last_known_time` von der falschen
"2026-09-20 03:18:16" auf "2026-09-26 18:53:00" gesetzt, damit der nächste Start ohne NTP
wenigstens beim heutigen Abend beginnt. **`tpms_poller.py` deployt** (md5 = Repo, altes Skript in
`backup-2026-09-26b/`), 6-s-Lauftest auf vcan0 ohne Fehler. Greift ab der nächsten Session.

**Stand davor: 2026-09-26, 11:40 Uhr** (Deploy der Offline-Ausbeute; per SSH auf `pi@192.168.0.247` geprüft,
Hostname `car`, passwordless SSH+sudo — siehe `mx5_can_bus_logging`-Memory).

## Kein Git auf dem Pi

`/home/pi/canlogs` ist kein Git-Repo — Deploys laufen bisher **manuell per
`scp`** einzelner Dateien. Das heißt: der Ist-Zustand auf dem Pi lässt sich
nicht aus `git log` ablesen, sondern nur durch Abgleich (mtime/md5sum) gegen
das lokale Repo. Deshalb dieser Abschnitt.

**md5sum-Abgleich der zentralen Skripte (2026-09-26, 11:40, nach Deploy):** alle unten genannten Dateien identisch mit dem Repo (Branch `can-offline-ausbeute`; DBC um 11:52 auf den Stand nach dem Merge mit `main` nachgezogen, `can_backend.py` erneut neu gestartet). Vorherige Pi-Stände gesichert in `/home/pi/canlogs/backup-2026-09-26/`.

| Datei | Pi = lokales Repo? | Bemerkung |
|---|---|---|
| `dash_gui.py` | ✅ identisch (deployt 26.09. nachts, md5 94b3dc87; Neustart PID 3699) | Tempomat-Trigger `0x0FD` Bit 61 `CruiseActive_Inv` mit Rückfall auf `0x165 == 149`. Vorherige Version: `backup-2026-09-26e/dash_gui.py`. Im Stand ohne Bus gestartet, Lauf im Fahrzeug noch nicht gesehen. | Ganganzeige R/N/1, Bordnetzspannung aus `0x08A`, Tempomat-Anzeige (Trigger `0x165 CC_Mode_Related == 149`, siehe `status/can-bus.md`). Vorheriger Pi-Stand: `backup-2026-09-26/dash_gui.py.a66ec59` |
| `test_dash_gui.py` | – | läuft nicht auf dem Pi (Tests liegen nur im Repo) |
| `can_backend.py` | ✅ identisch | `0x08A`-Bordnetzspannung (`DCDC_CAN_ID`), `ReverseGear_IC`; die Tempomat-Roh-Extraktion von `0x21F` ist entfernt (Trigger kommt per DBC-Snapshot aus `0x165`) |
| `tpms_poller.py` | ✅ identisch (deployt 26.09., 18:57; status_gui.py + DBC um ~19:25 ebenfalls, TPMS-Vorderachse bestätigt) | Drosselung der schnellen Gruppe auf 5 Runden/s, solange der Handy-Dongle auf 0x7DF/0x7E0 fragt, + Kernel-Filter auf Diagnose-IDs (Dongle-Konflikt, siehe `status/can-bus.md`). ~20:25: PID 0x42/0x2F entfernt (Broadcast), `can_backend.py`/`dash_gui.py` zeigen die Batterie aus 0x08A `DCDC_Voltage` - beide deployt und neu gestartet (Backup `backup-2026-09-26c/`). Abends zusätzlich PID 0x10 (MAF) in der schnellen Gruppe (Backup `backup-2026-09-26b/tpms_poller.py.vor_maf`). Davor 11:40: PIDs 0x3C/0x34/0x2F. 21:01: `dash_gui.py` (neue Ganganzeige R/N/1), `test_dash_gui.py`, `can_backend.py`/`status_gui.py` (Live-Liste `ReverseGear_IC`) und DBC deployt, Backend/Dash neu gestartet (Backup `backup-2026-09-26d/`). |
| `session_logger.py` | ✅ identisch (deployt 26.09.) | Kommentar-Drift vom 18.09. mitgenommen (nur Pfadangaben). `status_gui.py` und `uds_did_sweep.py` ebenso angeglichen. |
| `MX5ND_6thGenMazda_HSCAN_extended.dbc` | ✅ identisch (26.09. nachts um `CruiseActive_Inv`, `CruiseMainSwitch_maybe` und CM_-Kommentare ergänzt, md5 b402ed43, strikt geladen 151 Botschaften, `can_backend.py` neu gestartet PID 3694; Vorversion `backup-2026-09-26e/`; davor: deployt 26.09., abends um CM_-Kommentare zu `CC_Mode_Related`/`CC_SetSpeed` ergänzt, Vorversion `backup-2026-09-26/*.vor-cm`) | Offline-Ausbeute (TCS, Rückwärtsgang, Spannungen, i-stop, Steigung, `AmbientTemp`-Korrektur …). Auf dem Pi mit cantools 44.0 strikt geladen (151 Botschaften). Das Dash zeigt die neuen Signale noch nicht an; die Testmodus-Zeile "Rückwärtsgang" nutzt weiterhin das tote `Reverse_Flag_maybe` statt `ReverseGear`. |

**Vorgehen für den Abgleich (bei Bedarf wiederholen):**
```bash
for f in dash_gui.py can_backend.py session_logger.py tpms_poller.py; do
  diff <(md5sum scripts/$f | cut -d' ' -f1) \
       <(ssh pi@192.168.0.247 "md5sum /home/pi/canlogs/$f | cut -d' ' -f1") \
       && echo "$f OK" || echo "$f WEICHT AB"
done
```

## Laufende Prozesse

- **`can_backend.py`** (26.09.: nach dem Deploy per SSH neu gestartet, PID 2440, gleicher Aufruf wie im Autostart, Ausgabe nach `/tmp/can_backend_restart.log` - geht beim nächsten Reboot wieder über den Autostart; vorher PID 1056, läuft seit Boot 13:37 Uhr) — Decode-Daemon,
  publiziert UDP-Snapshot auf Port 51234. Läuft immer, unabhängig von `can0`
  (zeigt bei fehlendem Bus nur `self.error` im Snapshot).
- **`dash_gui.py`** (PID 2198, seit 13:55 Uhr — **gerade neu gestartet** für
  das Shiftlight-Feature) — Kivy-Frontend, liest den UDP-Snapshot.
- Beide über `/home/pi/canlogs/../pi-config/autostart` beim Desktop-Login
  gestartet (**nicht** über systemd).

**Screenshot vom laufenden Dashboard (2026-09-20, 14:44 Uhr, per `grim` über
Wayland/labwc):**

![dash_gui.py auf dem Pi](images/pi-dashboard-2026-09-20.png)

## `can-logger.service` (systemd, KeyState-getriggert)

- **Aktuell `inactive`** — erwartet, kein Fehler: der Service ist per
  `BindsTo=sys-subsystem-net-devices-can0.device` an das `can0`-Interface
  gebunden (`/etc/udev/rules.d/90-can-logger.rules`), das gerade nicht
  existiert (Adapter nicht dran / Zündung aus). Startet automatisch, sobald
  `can0` auftaucht.
- Wenn aktiv, startet `session_logger.py` **zwei Subprozesse**:
  1. `candump -l can0` (Rohlog)
  2. `tpms_poller.py --channel can0 --interval 120` — **das ist der Prozess,
     der die ETC_ACT/Lambda/KnockRetard-Fast-Polls (PID 0x11/0x44, DID
     0x03EC) tatsächlich auf den Bus sendet.** `can_backend.py` selbst sendet
     nichts, es hört die Antworten nur passiv mit (0x7E8) — ohne laufenden
     `tpms_poller.py` gäbe es kein ETC_ACT/Lambda/KnockRetard im Dash oder im
     Log. Läuft `--no-oil` NICHT (Flag wird von `session_logger.py` nicht
     gesetzt) → Fast-Gruppe (`poll_obd1_fast`, `poll_knock_fast`) läuft
     ungegatet jede Schleifenrunde.
  3. TPMS-Abfrage selbst (langsam, alle 120s) läuft im selben Prozess mit.
- `enabled` (startet automatisch bei jedem Boot bzw. `can0`-Auftauchen).

## Sonstiges

- **NTP:** `NTPSynchronized=yes` (Stand jetzt). Pi hat keine RTC — nach einem
  Boot ohne Netz bleibt die Uhr falsch, ohne erkennbaren Sprung im Log (vierter
  dokumentierter Fall siehe `docs/logs/can-bus-status.md`, "Viertes
  No-RTC-Vorkommnis"). Bei jedem CAN-only-Log ohne GPS-Zeitanker: Datum mit
  Vorsicht behandeln.
- **Pi-Uptime bei dieser Prüfung:** 31 Minuten (kürzlich gebootet, nicht durch
  den Shiftlight-Deploy verursacht — nur `dash_gui.py` wurde einzeln
  neugestartet, nicht der ganze Pi).
- DBC auf dem Pi (`MX5ND_6thGenMazda_HSCAN_extended.dbc`) ist byteidentisch
  mit dem lokalen Repo-Stand (zuletzt 15.09., ABS-Indikator).
