# Pi-Setup (Reproduzierbarkeit)

Momentaufnahme der Raspberry-Pi-Konfiguration (`pi@192.168.0.247` / `car.local`,
Raspberry Pi OS Bookworm), Stand 2026-09-17. Kein Deploy-Tooling — bei einer
Änderung am echten Pi müssen die Kopien hier von Hand nachgezogen werden.

## Hardware/Partitionierung

- 10"-Touchdisplay, CANable-2.0-Adapter (`can0`, 500kbit/s) am OBD-Port.
- SD-Karte: Root-Dateisystem (`overlayroot`, siehe unten).
- Separater USB-Stick (`/dev/sda1`, ext4) für `/home/pi/canlogs` — Logs, Skripte,
  venv liegen hier, physisch getrennt von der SD-Karte.
- Passwortloses SSH + `sudo` für den User `pi` bereits eingerichtet.

## overlayroot (schützt die SD-Karte vor Stromausfall-Korruption)

Kernel-Cmdline-Parameter in `/boot/firmware/cmdline.txt`:
```
overlayroot=tmpfs:recurse=0
```
`recurse=0` ist wichtig — sonst zieht overlayroot auch die separat gemountete
`/home/pi/canlogs`-Partition ins RAM-Overlay (Daten weg nach jedem Reboot).

**Konsequenz:** Alles außerhalb von `/home/pi/canlogs` (System-Configs, `/etc`,
systemd-Units, `.config`, installierte apt-Pakete) liegt read-only mit einem
RAM-Overlay obendrauf — ein einfaches `scp`/`ssh ... > datei` dorthin verschwindet
beim nächsten Reboot wieder. Um eine Änderung dauerhaft zu machen, ohne die
komplette Karte neu zu bespielen:

```bash
# Datei schreiben (Beispiel):
cat lokale_datei | ssh pi@192.168.0.247 "sudo overlayroot-chroot tee /pfad/zur/datei >/dev/null"
ssh pi@192.168.0.247 "sudo overlayroot-chroot chmod +x /pfad/zur/datei"  # falls noetig

# Verifikation: die ECHTE Basis pruefen, nicht den Live-Mount
ssh pi@192.168.0.247 "cat /media/root-ro/pfad/zur/datei"
```

`/home/pi/canlogs` selbst ist davon nicht betroffen — normales `scp` dorthin ist
immer schon sofort persistent.

## USB-Stick-Mount (`/etc/fstab`, via `overlayroot-chroot` gesetzt)

```
UUID=eb921b71-07f9-441a-aec0-46cb1c0ede35  /home/pi/canlogs  ext4  defaults,noatime,nofail  0  2
```
`nofail` ist wichtig, sonst hängt der Bootvorgang, falls der Stick mal fehlt.

## Python-Umgebung

Eigenes venv unter `/home/pi/canlogs/venv` (Debian 12 blockt system-weites `pip`
per PEP 668; ein System-Install würde durchs Overlay ohnehin nicht überleben):
```bash
python3 -m venv /home/pi/canlogs/venv
/home/pi/canlogs/venv/bin/pip install -r requirements.txt
```
`requirements.txt` in diesem Ordner ist ein `pip freeze` vom 2026-09-17.

## CAN-Logging (systemd + udev)

`can-logger.service` startet/stoppt `session_logger.py` automatisch, sobald der
CAN-Adapter er-/verschwindet (`BindsTo=` auf das `can0`-Device). Muss als root
laufen, nicht als `pi` — `ip link ... type can` braucht `CAP_NET_ADMIN`.

Installation (über `overlayroot-chroot`, siehe oben):
```bash
# can-logger.service -> /etc/systemd/system/can-logger.service
# 90-can-logger.rules -> /etc/udev/rules.d/90-can-logger.rules
sudo systemctl daemon-reload
sudo systemctl enable can-logger.service
sudo udevadm control --reload-rules
```

## Renncockpit-Autostart

`autostart` in diesem Ordner -> `/home/pi/.config/labwc/autostart` (per
`overlayroot-chroot`, ausführbar). Startet `can_backend.py` (Decode-Daemon) +
`dash_gui.py` (Kivy-Frontend) als zwei getrennte Prozesse — Hintergrund dazu in
[[mx5-renncockpit-kivy-rewrite-2026-09-16]]. Das alte `status_gui.py` bleibt als
auskommentierte Fallback-Zeile in derselben Datei stehen.

Seit 2026-09-27 laufen beide über `respawn.sh` (dieser Ordner -> `/home/pi/canlogs/respawn.sh`,
ausführbar, per normalem `scp`): startet das Skript nach einem Absturz neu, Ausgabe nach
`/tmp/can_backend.log` bzw. `/tmp/dash_gui.log`. Neustart von Hand, z.B. nach einem Deploy:
`pkill -f "python3 /home/pi/canlogs/dash_gui.py"` (die Schleife startet ihn nach 2 s neu;
`pkill -f dash_gui.py` würde auch die Schleife beenden).

## Was hier bewusst NICHT abgebildet ist

- DBC-Dateien und die eigentlichen Anwendungsskripte (`dash_gui.py`,
  `can_backend.py`, `session_logger.py`, ...) — die liegen schon regulär im
  Repo unter `scripts/`/`data/can/`, nur `SETUP.md` beschreibt, wohin sie auf
  dem Pi gehören (`/home/pi/canlogs/`, per `scp`).
- Ein Ansible/cloud-init-artiges volles Reproduktions-Tooling — für einen
  einzelnen Pi bewusst nicht gebaut, siehe Trade-off-Diskussion im Chat.

## Bootzeit-Optimierung (2026-09-27)

Alles per `overlayroot-chroot` bzw. `raspi-config`; Rollback-Liste der Unit-Zustaende vorher:
`/home/pi/canlogs/backup-boot-2026-09-26/unit-files-before.txt`.

- **journald:** `/etc/systemd/journald.conf.d/volatile.conf` mit `Storage=volatile`, `RuntimeMaxUse=30M`.
  Grund: 370 MB Alt-Journals im ro-Unterbau liessen `systemd-journal-flush` 30 s blockieren (sysinit 36 s -> 10 s).
- **Abgeschaltet (`systemctl disable`):** `apt-daily(-upgrade).timer`, `dpkg-db-backup.timer`, `man-db.timer`,
  `e2scrub_all.timer`, `e2scrub_reap.service`, `cups.{service,socket,path}`, `cups-browsed`, `udisks2`,
  `triggerhappy.{service,socket}`, `rpi-eeprom-update`, `dphys-swapfile`. **Maskiert:** `ModemManager` (sonst
  D-Bus-Reaktivierung durch NetworkManager). Bewusst AN: Avahi (`car.local`), Bluetooth, NetworkManager, ssh,
  fake-hwclock, timesyncd.
- **`/boot/firmware/config.txt`:** `camera_auto_detect=0` (`sudo mount -o remount,rw /boot/firmware`,
  `sudo raspi-config nonint do_camera 1`, danach wieder `remount,ro`). Vorher-Kopie `backup-boot-2026-09-26/config.txt.vorher`.
- **Gotcha:** mehrere `overlayroot-chroot`-Aufrufe hintereinander lassen `/media/root-ro` auf `rw` haengen
  (remount,ro = EBUSY); erst ein Reboot stellt `ro` sicher her. Aenderungen deshalb in EINEM Aufruf buendeln
  und danach `findmnt -no OPTIONS /media/root-ro` pruefen.
- **Desktop-Autostart:** in `/etc/xdg/labwc/autostart` sind `pcmanfm --desktop` und `wf-panel-pi` auskommentiert
  (Original: `backup-boot-2026-09-26/labwc-autostart.orig`). labwc fuehrt System- UND User-Autostart aus, der
  User-Autostart (`~/.config/labwc/autostart`) kann System-Eintraege nicht abschalten. WLAN ist davon unabhaengig
  (systemweites NM-Profil, `psk-flags 0`, kein Agent noetig); ohne Panel fehlt nur das Klick-Menue zum WLAN-Wechsel
  (dann `nmcli` per ssh). Wiederherstellen: `#` entfernen (per `overlayroot-chroot`).
- `initial_turbo=30` getestet, ohne Wirkung, wieder auskommentiert (`clear_config_var`). Kein Effekt hatte auch
  `camera_auto_detect=0`; beides ist harmlos.
- **Audio-Kette aus** (Dash nutzt keinen Ton; Bluetooth selbst bleibt an, nur kein BT-Audio):
  `systemctl --global mask pipewire.{socket,service} pipewire-pulse.{socket,service} wireplumber.service
  filter-chain.service pulseaudio.{service,socket}` (pulseaudio springt sonst als Ersatz ein).
- **Session-Autostart ausgeblendet** per `Hidden=true`-Overrides in `/home/pi/.config/autostart/`:
  `polkit-mate-authentication-agent-1`, `pwrkey`, `pprompt`, `pulseaudio`. Bewusst behalten: `autotouch` (Touch),
  `env-display`, `xwayauth` (Kivy laeuft ueber SDL2/X11 -> Xwayland), `kanshi`.
- **WLAN-Watchdog** (`wlan-watchdog.service` + `.timer`, erster Lauf 30 s nach Boot, dann alle 15 s, `AccuracySec=1s` - der systemd-Default von 1 min Spielraum haette ihn sonst verzoegert; ~40 ms CPU je Lauf): ohne Panel gibt es keinen NM-Passwort-Agenten;
  ein einzelner WPA-Handshake-Fehler beim Boot sperrt dann den Autoconnect bis zum Reboot ("no-secrets", am
  27.09. beobachtet). Der Watchdog macht `nmcli con up wireless` nur bei "getrennt" UND sichtbarer SSID.
- **Dateien per stdin in den Unterbau** (im chroot ist `/tmp` das des Unterbaus):
  `tar -c a b | ssh pi@... 'sudo overlayroot-chroot sh -c "mkdir -p /root/x && tar -x -C /root/x && install ..."'`
- **Nicht nachmachen:** ein Boot-Preload der Mesa-Bibliotheken (Page-Cache vorwaermen) wurde getestet und
  verworfen - bei ~5-7 MB/s Kartenlesegeschwindigkeit verdraengt er alles andere (lightdm 13 s -> 40 s).
