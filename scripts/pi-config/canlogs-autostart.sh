#!/bin/sh
# Startet Backend und Dash aus dem labwc-Autostart, aber erst, wenn der Log-Stick haengt (dort liegen
# respawn.sh, Skripte und venv). Fehlt er nach SHOW s, steht eine grosse Meldung auf dem Display statt
# eines schwarzen Bildschirms (18.11.2026). Kommt der Stick spaeter doch (canlogs-bootcheck.sh haengt
# ihn nach), verschwindet die Meldung und das Dash startet normal.
#
# Zum Testen: CANLOGS_MOUNT=<kein Mountpoint> CANLOGS_SHOW=2 canlogs-autostart.sh
set -u
M=${CANLOGS_MOUNT:-/home/pi/canlogs}
SHOW=${CANLOGS_SHOW:-60}

i=0
msg=
while ! mountpoint -q "$M"; do
    if [ "$i" -ge "$SHOW" ] && [ -z "$msg" ]; then
        zenity --error --width=900 --title="Log-Stick fehlt" --text="<span size='xx-large'><b>Log-Stick nicht gefunden</b>

Es wird NICHTS aufgezeichnet, das Dash startet nicht.

Pi stromlos machen und neu starten.
Hilft das nicht: Stick im blauen USB-Port prüfen.</span>" &
        msg=$!
    fi
    sleep 2
    i=$((i + 2))
done
[ -n "$msg" ] && kill "$msg" 2>/dev/null

# respawn.sh startet beide Prozesse nach einem Absturz neu (Ausgabe: /home/pi/canlogs/applogs/<skript>.log).
DISPLAY=:0 "$M/respawn.sh" can_backend.py &
DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 MX5_FULLSCREEN=1 "$M/respawn.sh" dash_gui.py &
wait
