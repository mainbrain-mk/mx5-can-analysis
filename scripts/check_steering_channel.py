"""Prueft, ob mindestens ein Log im Datalake den Kanal STEER_ANGL_EPS hat.
Genutzt von der Scheduled-Task-Routine (process-new-mx5-logs, Schritt 12) -
als festes Skript statt Inline-`python -c` One-Liner, damit der Bash-Aufruf
immer exakt identisch ist und dauerhaft freigegeben werden kann."""

import duckdb

con = duckdb.connect("data/datalake.duckdb", read_only=True)
count = con.execute(
    "SELECT COUNT(DISTINCT log_id) FROM measurements WHERE channel='STEER_ANGL_EPS'"
).fetchone()[0]
print(count)
