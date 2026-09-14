"""Startet DuckDBs eingebaute Web-UI (Tabellen-/Schema-Browser + SQL-Editor)
auf data/datalake.duckdb. Keine eigene Viewer-Implementierung noetig - die
'ui'-Extension bringt das schon mit (https://duckdb.org/docs/extensions/ui)."""

import time
import duckdb

con = duckdb.connect("data/datalake.duckdb")
con.sql("INSTALL ui")
con.sql("LOAD ui")
con.sql("CALL start_ui_server()")
print(con.sql("CALL get_ui_url()").fetchall()[0][0])

while True:
    time.sleep(3600)
