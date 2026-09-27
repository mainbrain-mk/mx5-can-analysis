#!/bin/sh
# Startet ein Python-Skript aus /home/pi/canlogs neu, sobald es endet (Absturz, OOM-Kill,
# versehentlich geschlossenes Fenster). Aufruf aus dem labwc-Autostart, siehe autostart.
# Vorher liefen can_backend.py und dash_gui.py per "&" ohne Aufsicht - ein Absturz liess
# das Dash bis zum naechsten Reboot schwarz bzw. eingefroren.
#
#   respawn.sh <skript.py> [args...]
#
# Ausgabe (stdout+stderr) geht nach /tmp/<skript>.log (tmpfs, weg nach Reboot), dazu je
# Ende eine Zeile mit Exit-Code und Laufzeit. Endet das Skript nach weniger als 10 s,
# wartet die Schleife 10 s statt 2 s (kein Dauerneustart bei kaputter Datei).
#
# Von Hand neu starten (z.B. nach einem Deploy): nur den Python-Prozess beenden, die
# Schleife startet ihn nach 2 s neu:
#   pkill -f "python3 /home/pi/canlogs/dash_gui.py"
# NICHT "pkill -f dash_gui.py" - das trifft auch diese Schleife (Name steht in ihrer
# Kommandozeile) und beendet die Ueberwachung.
set -u
script="$1"
shift
base=/home/pi/canlogs
log="/tmp/${script%.py}.log"
while true; do
    start=$(cut -d. -f1 /proc/uptime)
    "$base/venv/bin/python3" "$base/$script" "$@" >>"$log" 2>&1
    rc=$?
    ran=$(( $(cut -d. -f1 /proc/uptime) - start ))
    echo "$(date '+%F %T') respawn: $script beendet (rc=$rc) nach ${ran} s" >>"$log"
    if [ "$ran" -lt 10 ]; then sleep 10; else sleep 2; fi
done
