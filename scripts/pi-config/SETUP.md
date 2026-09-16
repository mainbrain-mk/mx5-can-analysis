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

## Was hier bewusst NICHT abgebildet ist

- DBC-Dateien und die eigentlichen Anwendungsskripte (`dash_gui.py`,
  `can_backend.py`, `session_logger.py`, ...) — die liegen schon regulär im
  Repo unter `scripts/`/`data/can/`, nur `SETUP.md` beschreibt, wohin sie auf
  dem Pi gehören (`/home/pi/canlogs/`, per `scp`).
- Ein Ansible/cloud-init-artiges volles Reproduktions-Tooling — für einen
  einzelnen Pi bewusst nicht gebaut, siehe Trade-off-Diskussion im Chat.
