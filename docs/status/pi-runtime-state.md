# Pi-Laufzeitstatus: was läuft gerade auf dem Auto-Pi

Momentaufnahme des tatsächlichen Zustands auf dem Raspberry Pi im Auto (nicht
zu verwechseln mit [`../../scripts/pi-config/SETUP.md`](../../scripts/pi-config/SETUP.md),
das die Ersteinrichtung/Reproduzierbarkeit beschreibt). **In-place aktualisieren
bei jedem Neustart/Umbau auf dem Pi**. Welcher Code-Stand läuft, sagt seit 2026-09-28
das Git auf dem Pi (siehe unten), nicht mehr dieses Dokument.

> **Git auf dem Pi (2026-09-28, ~19:00):** `/home/pi/canlogs/repo`, Branch `deployed` = `main`
> `158f154` (nach Merge von PR #33-#35). Vorher alle Pi-Dateien per md5 gegen `main` geprüft:
> identisch, kein Drift. Die Dateien in `canlogs/` sind jetzt Symlinks ins Repo, Originale in
> `backup-2026-09-28-vor-git/`. Backend/Dash danach neu gestartet, beide Pi-Tests ok, Dash zeigt
> "WARTE AUF CAN-BUS" (kein `can0` am Heimnetz). Vorgehen: `scripts/pi-config/SETUP.md`, "Deploy per Git".
>
> **Dash-Überarbeitung deployt (2026-09-27, ~10:55-11:10 Uhr):** `dash_gui.py` (md5 45299806),
> `can_backend.py` (446e6f1c), `test_dash_gui.py`, `test_can_backend.py`, neu `respawn.sh` (a17ca9a2),
> labwc-`autostart` (e66c54c6, per `overlayroot-chroot tee` in den Unterbau) = Repo `830bcaf`.
> Nach Reboot laufen Backend/Dash über `respawn.sh`, beide Tests ok, Selbstneustart geprüft
> (`kill` der Dash-PID → nach 2 s neu). Live-CAN beim Deploy NICHT geprüft (can0 fehlte; der Screenshot
> mit "CAN 1249 Hz" um 11:04 war vermutlich die Vcan-Simulation). Vorstände (= Git
> `a66ec59`/`8a3663a` + alter autostart) in `backup-2026-09-27-dash/`; Rollback: zurückkopieren,
> autostart wieder per `overlayroot-chroot tee`. Inhalt: Logbuch "Dash-Überarbeitung… (27.09.2026)".
> **~20:00: Zustandslogs auf SSD** (Logbuch "Dash '–' bei laufendem OBD Fusion…"): `can_backend.py`
> (md5 30fdb8e9), `dash_gui.py` (566903b0), Tests, `respawn.sh` (8c6fbdef), autostart-Kommentar
> (f51ed5d4) deployt, Tests auf dem Pi ok, Reboot, beide loggen nach `canlogs/applogs/`.
> Vorstände in `backup-2026-09-27-healthlog/`.
> Pi im WLAN war an dem Tag 192.168.0.248 (DHCP, `car` löste darauf auf, Verbindung wackelig);
> per LAN-Kabel 192.168.0.247.

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

**Nachtrag 2026-09-27, ~00:05 Uhr (Bootzeit-Optimierung):** journald `Storage=volatile`, Boot-Timer/-Dienste abgeschaltet, ModemManager maskiert, `camera_auto_detect=0` (per `raspi-config nonint do_camera 1`), `dash_gui.py` (md5 4a7c1a56, Vorversion `backup-boot-2026-09-26/dash_gui.py.94b3dc87`) schreibt beim ersten Frame eine Zeile nach `/home/pi/canlogs/boot_timing.log`. Details/Messwerte: `logs/can-bus-status.md` ("Bootzeit-Optimierung 27.09."), Config-Kopien in `scripts/pi-config/SETUP.md`. Panel/pcmanfm im System-Autostart deaktiviert (`/etc/xdg/labwc/autostart`, Original `backup-boot-2026-09-26/labwc-autostart.orig`). Danach (27.09. ~00:45): Audio-Kette (pipewire/wireplumber/pulseaudio) global maskiert, polkit-Agent/pwrkey/pprompt aus dem Session-Autostart, `wlan-watchdog.timer` neu (siehe SETUP.md). Dash-Erstframe zuletzt bei 34,6 s nach Boot (ohne CANable). **Engpass ist die SD-Karte selbst (~5-7 MB/s auf belegten Bereichen)** - siehe Logbuch.

**Stand davor: 2026-09-26, 11:40 Uhr** (Deploy der Offline-Ausbeute; per SSH auf `pi@192.168.0.247` geprüft,
Hostname `car`, passwordless SSH+sudo — siehe `mx5_can_bus_logging`-Memory).

## Deploy-Stand = Git auf dem Pi

Seit 2026-09-28 ist `/home/pi/canlogs/repo` ein Git-Checkout (Branch `deployed`), die
Skripte/DBC in `canlogs/` sind Symlinks darauf. Deploy per `git push car HEAD:deployed`,
Einrichtung und Regeln in [`../../scripts/pi-config/SETUP.md`](../../scripts/pi-config/SETUP.md),
"Deploy per Git". Der frühere md5-Abgleich von Hand entfällt:

```bash
ssh pi@192.168.0.247 'cd canlogs/repo && git log --oneline -1 && git status --short'
```

Leere `git status`-Ausgabe = der Pi läuft exakt mit dem angezeigten Commit. Der
`pre-push`-Hook stellt sicher, dass dieser Commit auch auf GitHub liegt.

**Nicht im Git-Checkout** (Overlay-Unterbau, weiter per `overlayroot-chroot`): labwc-`autostart`,
`can-logger.service`, udev-Regel, `wlan-watchdog.*`, `config.txt`. Repo-Kopien in
`scripts/pi-config/`, Abgleich dort weiterhin von Hand.

## Laufende Prozesse

- **`can_backend.py`** (seit Boot 27.09. ~11:08, über `respawn.sh`, Log `/home/pi/canlogs/applogs/can_backend.log`) — Decode-Daemon,
  publiziert UDP-Snapshot auf Port 51234. Läuft immer, unabhängig von `can0`
  (zeigt bei fehlendem Bus nur `self.error` im Snapshot).
- **`dash_gui.py`** (über `respawn.sh`, Log `/home/pi/canlogs/applogs/dash_gui.log`) — Kivy-Frontend, liest den UDP-Snapshot.
- Beide über `/home/pi/.config/labwc/autostart` (Repo-Kopie `scripts/pi-config/autostart`) beim
  Desktop-Login gestartet (**nicht** über systemd), jeweils in einer `respawn.sh`-Schleife, die sie
  nach einem Absturz neu startet. Von Hand neu starten: nur die Python-PID beenden (nicht
  `pkill -f dash_gui.py`, das trifft auch die Schleife).

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

- **Uhr:** seit 2026-09-28 DS3231-RTC (Einrichtung siehe `scripts/pi-config/SETUP.md`, "RTC").
  Kernel stellt die Zeit beim Boot aus der RTC, fake-hwclock ist aus. `session_logger.py`
  (md5 78a47573) schreibt Uhr-Zustand `rtc` in den Marker und protokolliert NTP-Uhrspruenge
  als `clockjump-*.txt`. Dash-Fusszeile (`dash_gui.py` md5 b58c056d): `NTP  SYNC` gruen,
  ohne NTP `RTC  OK` gruen, ohne glaubwuerdige RTC `UHR  ?` rot. Vorstand in
  `backup-2026-09-28-rtc/`. Haltetest der Knopfzelle
  (kalter Stromausfall + Boot ohne Netz) noch offen. CAN-Logs VOR dem 28.09. bleiben
  No-RTC-verdaechtig (siehe `docs/logs/can-bus-status.md`).
- **Pi-Uptime bei dieser Prüfung:** 31 Minuten (kürzlich gebootet, nicht durch
  den Shiftlight-Deploy verursacht — nur `dash_gui.py` wurde einzeln
  neugestartet, nicht der ganze Pi).
- DBC auf dem Pi (`MX5ND_6thGenMazda_HSCAN_extended.dbc`) ist byteidentisch
  mit dem lokalen Repo-Stand (zuletzt 15.09., ABS-Indikator).
