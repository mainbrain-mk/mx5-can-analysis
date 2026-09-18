#!/bin/bash
# Laedt die 62 fehlenden, bei der LGB Brandenburg verfuegbaren Kacheln
# (Kernregion-Luecke, siehe docs/logs/projekt-stand.md) nach höhendaten/.
# Parallelitaet 8, mit Retry-Runde fuer unvollstaendige/beschaedigte Downloads.
cd /home/manuel/claude/höhendaten || exit 1

TILES="383-5850 383-5847 382-5848 384-5850 379-5840 381-5846 382-5849 387-5838 388-5838 380-5840 381-5840 389-5838 381-5848 382-5846 381-5849 386-5839 361-5820 361-5816 361-5817 361-5819 363-5832 365-5838 362-5826 365-5837 381-5850 359-5813 360-5815 362-5825 364-5834 359-5810 359-5812 361-5818 362-5827 364-5835 365-5841 386-5838 359-5811 361-5823 362-5829 362-5830 365-5839 365-5840 363-5833 362-5828 362-5822 383-5848 360-5814 390-5838 365-5836 382-5847 362-5821 383-5849 361-5824 362-5831 363-5831 359-5814 362-5824 361-5815 361-5821 364-5836 364-5833 361-5822"

download_batch() {
  local batch="$1"
  for t in $batch; do
    f="als_33${t}.zip"
    [ -f "$f" ] && unzip -tq "$f" >/dev/null 2>&1 && continue
    curl -s -o "$f" --max-time 1800 "https://data.geobasis-bb.de/geobasis/daten/als/laz/als_33${t}.zip" &
  done
  wait
}

# in Achterblöcken parallel laden
arr=($TILES)
n=${#arr[@]}
for ((i=0; i<n; i+=8)); do
  batch="${arr[@]:i:8}"
  echo "Batch: $batch"
  download_batch "$batch"
done

echo "=== Erste Runde fertig, pruefe Integritaet ==="
failed=""
for t in $TILES; do
  f="als_33${t}.zip"
  if ! unzip -tq "$f" >/dev/null 2>&1; then
    failed="$failed $t"
    rm -f "$f"
  fi
done

if [ -n "$failed" ]; then
  echo "Retry fuer:$failed"
  download_batch "$failed"
  echo "=== Retry fertig, finale Pruefung ==="
fi

ok=0; bad=0
for t in $TILES; do
  f="als_33${t}.zip"
  if unzip -tq "$f" >/dev/null 2>&1; then
    ok=$((ok+1))
  else
    bad=$((bad+1))
    echo "WEITERHIN BESCHAEDIGT: $t"
  fi
done
echo "ENDERGEBNIS: $ok OK, $bad beschaedigt/fehlend (von $n)"
