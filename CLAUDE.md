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

## Sonstiges

- Doku und Commit-Messages sind überwiegend Deutsch, Code/Identifiers
  Englisch/Deutsch gemischt.
- Rohdaten, Datalake (`data/datalake.duckdb`) und `results/` sind nicht im
  Repo (siehe README, "What's deliberately NOT in this repo") — Skripte
  erwarten einen lokal aufgebauten Datalake.
