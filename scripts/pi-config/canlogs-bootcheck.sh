#!/bin/sh
# Prueft beim Boot, ob der Log-Stick unter /home/pi/canlogs haengt (Anlass: 18.11.2026 morgens fehlte er,
# Dash schwarz, nichts aufgezeichnet, Logbuch can-bus-status.md). Laeuft als canlogs-bootcheck.service (root).
#
# - Stick da: nichts tun.
# - Stick fehlt: bis zu WAIT s lang alle 10 s "mount /home/pi/canlogs" (fstab, nofail gibt nach 90 s auf
#   und haengt einen spaeter auftauchenden Stick nicht mehr ein). Klappt es, die Logger nachstarten,
#   die wegen RequiresMountsFor nicht starten konnten.
# - Stick bleibt weg: Diagnose (Journal, dmesg, USB, Blockgeraete) gepackt auf die SD-Basis schreiben,
#   damit sie den Stromverlust ueberlebt: /media/root-ro/var/log/canlogs-bootcheck/ (live auch unter
#   /var/log/canlogs-bootcheck/ sichtbar). Die Meldung auf dem Display macht canlogs-autostart.sh.
#
# Zum Testen: CANLOGS_MOUNT=<kein Mountpoint> CANLOGS_WAIT=4 CANLOGS_DIAG=/tmp/x canlogs-bootcheck.sh
set -u
M=${CANLOGS_MOUNT:-/home/pi/canlogs}
WAIT=${CANLOGS_WAIT:-120}
DIAG=${CANLOGS_DIAG:-/media/root-ro/var/log/canlogs-bootcheck}

mountpoint -q "$M" && exit 0

i=0
while [ "$i" -lt "$WAIT" ]; do
    if [ $((i % 10)) -eq 0 ]; then
        mount "$M" 2>/dev/null
    fi
    if mountpoint -q "$M"; then
        echo "Stick nach ${i} s nachtraeglich eingehaengt, Logger werden nachgestartet"
        [ -e /sys/class/net/can0 ] && systemctl start --no-block can-logger.service
        systemctl start --no-block gps-logger.service
        exit 0
    fi
    sleep 2
    i=$((i + 2))
done

echo "Stick nach ${WAIT} s nicht da, schreibe Diagnose nach $DIAG"
# ponytail: schreibt bei fehlendem Stick kurz in die ro-Basis (ext4, Journal). Faellt genau dann der Strom aus,
# repariert fsck.repair=yes beim naechsten Boot. Keine Rotation, kommt nur bei fehlendem Stick vor (~50 kB je Fall).
case "$DIAG" in /media/root-ro/*) mount -o remount,rw /media/root-ro || exit 1 ;; esac
mkdir -p "$DIAG"
{
    echo "== $(date '+%F %T')  uptime $(cut -d' ' -f1 /proc/uptime) s"
    echo "== lsusb -t"; lsusb -t
    echo "== lsblk -f"; lsblk -f
    echo "== /dev/disk/by-uuid"; ls -l /dev/disk/by-uuid
    echo "== systemctl status home-pi-canlogs.mount"; systemctl status --no-pager home-pi-canlogs.mount
    echo "== systemctl --failed"; systemctl --failed --no-pager
    echo "== dmesg"; dmesg
    echo "== journalctl -b"; journalctl -b -o short-monotonic --no-pager
} 2>&1 | gzip > "$DIAG/bootcheck-$(date +%F_%H%M%S).txt.gz"
sync
case "$DIAG" in /media/root-ro/*) mount -o remount,ro /media/root-ro ;; esac
exit 0
