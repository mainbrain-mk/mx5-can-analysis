#!/bin/sh
# Startet ein Python-Skript aus /home/pi/canlogs neu, sobald es endet (Absturz, OOM-Kill,
# versehentlich geschlossenes Fenster). Aufruf aus dem labwc-Autostart, siehe autostart.
# Vorher liefen can_backend.py und dash_gui.py per "&" ohne Aufsicht - ein Absturz liess
# das Dash bis zum naechsten Reboot schwarz bzw. eingefroren.
#
#   respawn.sh <skript.py> [args...]
#
# Ausgabe (stdout+stderr) geht nach /home/pi/canlogs/applogs/<skript>.log (SSD, ueberlebt den
# Reboot - bis 27.09. lag sie in /tmp und war nach jeder Fahrt weg), dazu je Ende eine Zeile
# mit Exit-Code und Laufzeit. Ab 5 MB wird vor dem naechsten Start nach <skript>.log.1 rotiert.
# Eigener Unterordner, weil session_logger.py jede *.log direkt in canlogs/ gzippt.
# ponytail: rotiert nur beim (Neu-)Start - laeuft der Pi tagelang am Netz, waechst das
# Backend-Log um ~5 MB/Tag (python-can-Warnung alle 2 s ohne can0 + Zustandszeile alle 10 s);
# logrotate/Groessencheck im Skript, falls das die SSD je stoert.
# Endet das Skript nach weniger als 10 s, wartet die Schleife 10 s statt 2 s (kein
# Dauerneustart bei kaputter Datei).
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
logdir="$base/applogs"
mkdir -p "$logdir"
log="$logdir/${script%.py}.log"
while true; do
    if [ "$(stat -c %s "$log" 2>/dev/null || echo 0)" -gt 5000000 ]; then
        mv "$log" "$log.1"
    fi
    start=$(cut -d. -f1 /proc/uptime)
    "$base/venv/bin/python3" "$base/$script" "$@" >>"$log" 2>&1
    rc=$?
    ran=$(( $(cut -d. -f1 /proc/uptime) - start ))
    echo "$(date '+%F %T') respawn: $script beendet (rc=$rc) nach ${ran} s" >>"$log"
    if [ "$ran" -lt 10 ]; then sleep 10; else sleep 2; fi
done
