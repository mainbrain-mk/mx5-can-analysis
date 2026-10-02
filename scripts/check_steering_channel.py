"""Gibt aus, wie viele Logs im Datalake einen Lenkwinkel haben (CAN SteeringAngle_CAN oder Handy
STEER_ANGL_EPS, siehe datalake_channels.py). Genutzt von run_daily_pipeline.py - als festes Skript
statt Inline-`python -c` One-Liner, damit der Aufruf immer exakt identisch ist."""

import duckdb

from datalake_channels import logs_with

con = duckdb.connect("data/datalake.duckdb", read_only=True)
print(len(logs_with(con, "STEER_ANGL_EPS")))
