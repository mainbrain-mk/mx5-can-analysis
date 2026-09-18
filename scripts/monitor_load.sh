#!/bin/bash
# Sampelt Systemlast + CPU-Zeit/RSS eines PID jede Sekunde fuer DURATION Sekunden,
# schreibt eine CSV nach OUT. Gebaut fuer Lasttests von status_gui.py auf dem Pi
# (siehe "Kommunikation mit dem Pi" in docs/logs/can-bus-status.md), funktioniert aber
# fuer jeden PID/jede Dauer.
#
# Aufruf: monitor_load.sh <PID> <DURATION_SEKUNDEN> <OUTPUT_CSV>
# Beispiel: ./monitor_load.sh 1234 580 /tmp/load.csv
#
# CSV-Spalten: t (Sekunden seit Start), loadavg1 (1-Min-Load, System, alle Kerne),
# gui_cpu_pct (CPU-Anteil DES PID in Prozent EINES Kerns - 100%% = 1 Kern voll
# ausgelastet, bei N Kernen also bis zu N*100 moeglich), gui_rss_kb (RSS-Speicher
# des PID in KB).
PID=$1
DURATION=$2
OUT=$3
HZ=$(getconf CLK_TCK)

echo "t,loadavg1,gui_cpu_pct,gui_rss_kb" > "$OUT"

# Startwert aus dem aktuellen Prozess-Zustand lesen statt bei 0 zu beginnen -
# sonst zaehlt die erste Zeile faelschlich die gesamte bisherige Laufzeit des
# Prozesses (seit dessen Start, nicht seit Beginn der Messung) als Delta.
if [ -r /proc/$PID/stat ]; then
    stat=$(cat /proc/$PID/stat)
    prev_total=$(echo "$stat" | awk '{print $14+$15}')
else
    prev_total=0
fi
prev_time=$(date +%s.%N)

i=0
while [ $i -lt $DURATION ]; do
    sleep 1
    now=$(date +%s.%N)
    load=$(cut -d' ' -f1 /proc/loadavg)
    if [ -r /proc/$PID/stat ]; then
        stat=$(cat /proc/$PID/stat)
        utime=$(echo "$stat" | awk '{print $14}')
        stime=$(echo "$stat" | awk '{print $15}')
        total=$((utime+stime))
        rss=$(awk '/VmRSS/{print $2}' /proc/$PID/status 2>/dev/null)
        dt=$(echo "$now - $prev_time" | bc)
        dticks=$((total - prev_total))
        cpu_pct=$(echo "scale=1; $dticks / $HZ / $dt * 100" | bc)
        prev_total=$total
        prev_time=$now
    else
        cpu_pct="proc_gone"
        rss=0
    fi
    echo "$i,$load,$cpu_pct,$rss" >> "$OUT"
    i=$((i+1))
done
