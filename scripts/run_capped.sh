#!/bin/bash
# Harte RAM-Grenze fuer grosse Laeufe (Projektregel 24.11., siehe scripts/memguard.py):
#   scripts/run_capped.sh .venv/bin/python scripts/pcm_candidates.py --all
# Laeuft in eigener systemd-Scope-Gruppe: wird MEMGUARD_MAX ueberschritten, beendet der
# Kernel nur diesen Lauf (inkl. aller Worker), Swap ist fuer ihn gesperrt, der Desktop bleibt frei.
exec systemd-run --user --scope --quiet --collect \
  -p MemoryMax="${MEMGUARD_MAX:-30G}" -p MemorySwapMax=0 -- "$@"
