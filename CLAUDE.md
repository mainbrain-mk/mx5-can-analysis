# MX-5 ND RF G184: Projektkontext für Claude Code

Hobbyprojekt: Fahrleistungs-/Fahrdynamikmodell + CAN-Bus-Reverse-Engineering
für einen Mazda MX-5 ND RF G184, aus eigenen OBD/GPS/CAN-Logs. Details siehe
[`README.md`](README.md).

**Sprachkonvention:** wenn der Nutzer von "wir" spricht, meint das ihn als
Fahrer/Tester und Claude als Entwickler/Analyst.

## Erste Anlaufstelle: docs/README.md

**Vor inhaltlicher Arbeit an einem Thema immer zuerst
[`docs/README.md`](docs/README.md) lesen** — die Themen-Landkarte zeigt, wo
der aktuelle Stand steht und welches Logbuch die Herleitung dazu hat.

Die Doku ist zweistufig:
- `docs/status/*.md` — kurze, aktuell gehaltene Stand-Dokumente pro Thema.
  Erster Blick für "was wissen wir schon zu X". Werden in-place aktualisiert.
- `docs/logs/*.md` — vollständige chronologische Arbeitsprotokolle
  (Lab Notebook, append-only). Enthalten die Herleitungen, Messwerte und
  verworfenen Hypothesen hinter jedem Stand in `status/`.

**Nach neuen Erkenntnissen:** neuen datierten Abschnitt unten ans passende
`docs/logs/*.md` anhängen; ändert sich dadurch der Ist-Stand zu einem Thema,
den zugehörigen `docs/status/*.md` gleich mit aktualisieren (siehe
Konventionen in `docs/README.md`).

## RAM-Regel: kein Swap (seit 24.11.2026)

Am 24.11. liefen 39 GB RAM + 7,7 GB Swap voll (Pools mit 6–7 Workern, jeder
parst ein candump-Log in mehrere GB). Deshalb:
- Parallel über viele Logs nur mit `memguard.bounded_map` (statt
  `ProcessPoolExecutor.map`): ein weiterer Auftrag startet nur, wenn
  danach noch `MEMGUARD_RESERVE_GB` (Default 8) frei sind.
- DuckDB in Worker-Prozessen über `memguard.connect()` (memory_limit 4GB,
  DuckDB lagert dann selbst auf Platte aus).
- Große Läufe über `scripts/run_capped.sh <befehl>` starten (systemd-Scope,
  `MemoryMax` 30G, kein Swap): bei Überschreitung stirbt nur dieser Lauf.
- Vor großen Läufen `free -h` prüfen; neue Swap-Nutzung ist ein Fehler.

## Sonstiges

- Doku und Commit-Messages sind überwiegend Deutsch, Code/Identifiers
  Englisch/Deutsch gemischt.
- Rohdaten, Datalake (`data/datalake.duckdb`) und `results/` sind nicht im
  Repo (siehe README, "What's deliberately NOT in this repo") — Skripte
  erwarten einen lokal aufgebauten Datalake.
