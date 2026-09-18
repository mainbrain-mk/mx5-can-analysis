---
name: mx5-can-reverse-engineering
description: >
  Ein unbekanntes/proprietäres CAN-Signal im MX-5-Projekt reverse engineeren: welche
  CAN-ID/welches Byte/Bit einen echten Wert (Drehzahl, Bremsdruck, ABS-Eingriff, ...)
  trägt, Startbit/Länge/Endianness/Scale/Offset bestimmen und als DBC-Signal
  eintragen. Trigger: "reverse engineer <Signal>", "finde das CAN-Byte für X",
  "welche ID trägt Y", "kalibriere <Signal> gegen OBD", "suche nach dem
  ABS/DSC-Eingriffsindikator", oder wenn ein neues, unbelegtes Byte im DBC auffällt.
  Nicht für bereits bekannte/dekodierte Signale (siehe stattdessen
  docs/status/can-bus.md für den aktuellen Stand).
---

# MX-5 CAN Reverse Engineering

Eigene, vereinfachte Version der Methodik aus
[CSS-Electronics/can-bus-reverse-engineering-skills](https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills)
(CANsub-Hardware + Claude-Code-Skill), auf unseren Stack reduziert: **keine
CANsub, keine Live-Sweep-Referenz, keine Vision/OCR** — unsere führende
Referenzgröße sind ausschließlich bereits im CAN-Log vorhandene Daten (OBD-Traffic
über das Y-Splitter-Kabel, oder ein anderes bereits bekanntes DBC-Signal). Details
zur Portierung/den bewussten Auslassungen: `mx5_can_bus_logging.md`-Memory,
Eintrag 2026-09-14.

## Werkzeugkette

1. **Log parsen.** `scripts/can_log_parser.py` (`parse_candump`, `load_db`) —
   liest `candump -l`-Logs, lädt die Master-DBC
   (`data/can/MX5ND_6thGenMazda_HSCAN_extended.dbc`).
2. **Referenz bestimmen.** Bevorzugt in dieser Reihenfolge:
   - **OBD aus demselben Log** (`scripts/obd_from_can.py::decode_obd_traffic` +
     `extract_did_series`) — nur wenn das Log über das Y-Splitter-Kabel
     aufgenommen wurde (OBD-Traffic auf `0x7E0`/`0x7E8`). Bekannte Mode-22-DIDs:
     siehe `KNOWN_DIDS` in `scripts/can_byte_search.py`.
   - **Ein anderes, bereits validiertes DBC-Signal** (z.B. `WheelSpeed_1` für alles
     Geschwindigkeitsbezogene, `YawRate_Raw` für Gierrate-Ableitungen) — direkt per
     `msg.decode()` aus demselben Log.
   - Kein Referenzsignal vorhanden → **ehrlich melden, nicht erzwingen** (kein
     Live-Referenz-Tool vorhanden, siehe "Nicht vorhandene Fähigkeiten" unten).
3. **Grober Scan.** `scripts/can_byte_search.py` — testet alle unbelegten
   Byte-Bereiche einer DBC (ganze Bytes/Byte-Paare, BE/LE, signed/unsigned) gegen
   ein festes Set bekannter Anker-Signale + OBD-DIDs, Pearson **und** Spearman,
   roh **und** detrended (Schutz gegen Trend-Scheinkorrelationen). Ausgabe:
   `results/can_byte_search_<log>.csv`.
4. **Feinsuche bei Bedarf.** `scripts/can_bitsearch.py <can_id> --ref-did <NAME> |
   --ref-signal <NAME> --ref-id <hex>` — exhaustive Startbit×Länge×Endianness×Sign-
   Suche für EINE CAN-ID, mit Parsimonie-Regel (kürzeres, gleich gut fittendes Feld
   gewinnt gegen einen Over-Wide-Read). Nächster Schritt, wenn Byte-Ebene auffällig
   ist, die genaue Bitlage aber unklar bleibt, oder für gezielt bitgepackte Flags
   (z.B. ein ABS/DSC-Eingriffs-Bit).
5. **Kalibrierung prüfen/verfeinern.** `scripts/can_re_toolkit.py`:
   - `detect_extreme_outliers`/`describe_extreme_outliers` — agnostische
     Sentinel-Erkennung, VOR jeder Regression aufrufen (ein "Signal ungültig"-Code
     verzerrt sonst den Fit).
   - `propose_round_calibration`/`propose_anchor_calibration` — Scale/Offset nur
     dann anpassen, wenn der eingeführte Bias klein bleibt (**nicht** auf R²/
     Spearman verlassen, die sind blind für kleine Scale-Fehler und konstanten
     Offset-Bias).
   - `plausibility` — referenzfreier Score (glatt/nicht modular umlaufend) als
     zusätzliche Rangier-Dimension.
6. **In die Master-DBC eintragen.** Von Hand, mit Herleitung/Quelle als DBC-
   Kommentar (Projektkonvention, siehe bestehende Einträge in
   `MX5ND_6thGenMazda_HSCAN_extended.dbc`) — kein automatischer DBC-Merge (wir
   pflegen eine einzige, dokumentierte Datei, kein Per-Signal-DBC-Verzeichnis).
7. **Cross-Log-Validierung.** Mindestens 2-4 unabhängige Logs bevor ein Fund als
   "bestätigt" gilt (Projektkonvention, siehe `docs/logs/can-bus-status.md`) — ein
   einzelner Log kann durch Zufallskorrelation täuschen.

## Prinzipien (aus der Referenz-Methodik übernommen)

- **Werkzeug vor Ad-hoc-Eyeballing.** `can_bitsearch.py`s R²+Plausibilität+
  Spearman-Ranking und `can_re_toolkit.py`s Bias-Gates kodieren bereits robuste
  Logik — nicht mit einer eigenen "sieht glatt aus"-Einschätzung überstimmen.
- **Referenzfreie Plausibilität ergänzt, ersetzt aber nicht** die Korrelation
  gegen eine echte Referenz — beides zusammen verhindert, dass ein zufällig
  monotones, aber falsches Feld gewinnt.
- **Parsimonie:** bei zwei gleich gut fittenden, ineinander verschachtelten
  Kandidaten gewinnt der kürzere (verhindert Over-Wide-Reads wie den
  `TM_GEST`-Dual-Column-Bug, siehe `mx5_build_datalake_dual_column_bug.md`-Memory).
- **Bias-Budget statt R²-Gate** für Scale/Offset-Nachkorrektur — ein 2-3%
  Skalenfehler bleibt in R²/Spearman fast unsichtbar.
- **Sentinel-Erkennung agnostisch:** keine Annahme über Wert/Vorzeichen/
  Häufigkeit eines "Signal ungültig"-Codes.

## Bekannte Grenzen unseres Ports

- **`can_bitsearch.py` hat kein Resolution-Refinement** (bräuchte kontinuierliche
  Live-Sweep-Anregung, die wir nicht haben). Bei glatten, langsam veränderlichen
  Signalen kann eine kürzere Teilspanne knapp gewinnen, obwohl ein längeres Feld
  die "richtige" volle Auflösung wäre — das Tool druckt dann einen
  `[Auflösungshinweis]` mit dem längeren Alternativkandidaten; diesen prüfen
  statt den Gewinner blind zu übernehmen.
- **Keine Lag-Suche** — unsere Referenzen (OBD aus demselben Log, andere
  DBC-Signale) haben praktisch keinen Zeitversatz zum Target, anders als eine
  Handeingabe. Falls doch ein Log mit spürbarem Versatz auftaucht (z.B. externe
  GPS-Quelle), müsste das ergänzt werden.

## Nicht vorhandene Fähigkeiten (bewusst nicht gebaut — Blueprint bei Bedarf)

Das Original-Repo hat zusätzlich eine **Live-Referenz-Erfassung**: eine
Flask-Web-UI (`flask_sync.py`, "CANsub Reference Generator") mit einem
Holds/Sweep-Umschalter — man hält das Signal auf einem bekannten Wert und
klickt/tippt ihn an (feste Zeitfenster als Kalibrier-Anker), oder zieht einen
Schieberegler synchron zum Signal (dichte kontinuierliche Referenz zur
Felderkennung). Wir haben dafür **kein Äquivalent**, weil bisher jede Referenz
aus bereits geloggten Daten kommt (OBD/anderes CAN-Signal) — für ein Signal ganz
ohne jede Referenz (z.B. der ABS/DSC-Eingriffsindikator, wenn kein Korrelat
gefunden wird, oder die TPMS-Vorderachsen-Zuordnung) bräuchten wir das aber.

**Technisch steckt dahinter, und so wäre es bei uns reproduzierbar:**
- Die Seite ist reines REST-Polling, **keine WebSockets**: der Browser puffert
  Zeitstempel+Wert client-seitig (bei Sweep alle ~50ms vom Schieberegler, bei
  Holds ein festes Zeitfenster nach jedem Klick) und schickt den Puffer alle
  150ms per POST an `/api/log`; der Flask-Server hängt das nur an eine
  Sidecar-CSV an (`epoch;kind;label;value`). Ein separater
  Start/Stop-Button startet/stoppt serverseitig eine ECHTE CAN-Aufnahme über
  Standard-`python-can` (`can.Bus` + `can.Notifier` + `can.Logger`) — die Web-UI
  selbst enthält keinerlei CAN-spezifischen Code, das ist vollständig
  wiederverwendbar.
- **CANsub-spezifisch (nicht direkt übernehmbar, aber für uns auch nicht
  nötig):** `common.py` nutzt `python_can_cansub` als python-can-Backend
  (`interface="cansub"`, mDNS-Geräteerkennung, passive Bitrate-Auto-Erkennung
  durch Duchprobieren mehrerer Timings, plus eine REST-Statusabfrage
  `GET https://<host>/api/can/<ch>` für Bus-Zustand/Fehlerzähler) und
  registriert einen eigenen "webCAN"-CSV-Codec für `can.Logger`.
- **Unser Ersatz — alles mit Standard-Bordmitteln, kein Reverse Engineering
  nötig:**
  - `interface="socketcan"`, `channel="can0"` statt `"cansub"` — SocketCAN ist
    ein voll unterstütztes, dokumentiertes `python-can`-Backend (unser CANable
    2.0 über `gs_usb` meldet sich exakt so an). Bitrate wird bei uns bereits
    einmalig per `ip link set can0 up type can bitrate 500000` gesetzt
    (`can-logger.service`/`session_logger.py` auf dem Pi macht das schon) —
    keine Auto-Erkennung nötig, wir kennen die Bitrate (500 kbit) längst.
  - Statt des proprietären "webCAN"-CSV-Formats: `can.Logger("trace.log")`
    (Endung `.log` statt `.csv`) schreibt nativ im `candump -l`-Format — GENAU
    das Format, das `scripts/can_log_parser.py::parse_candump` bereits liest.
    Kein neuer Parser nötig, volle Wiederverwendung der bestehenden Pipeline.
  - Statt der CANsub-REST-Statusabfrage: `ip -details -statistics link show
    can0` (Linux-Bordmittel, kein root für den reinen Lesezugriff nötig) liefert
    Bus-Zustand (`ERROR-ACTIVE`/`ERROR-WARNING`/`ERROR-PASSIVE`/`BUS-OFF`),
    Bitrate/Timing und Fehlerzähler (`berr-counter`, `bus-error`,
    `error-warning`, `error-passive`, `bus-off`-Zähler in den Statistiken) —
    inhaltlich dieselben Felder wie CANsubs `state`/`rx_error_count`/
    `tx_error_count`/`bus_error_count`, nur über `ip` statt HTTPS.
    ([Quelle: python-can-Dokumentation/-Issues zu `BusState`](https://python-can.readthedocs.io/en/stable/interfaces/socketcan.html),
    [Kernel-ML-Threads mit realen `ip -details -statistics link show`-Beispielen](https://lkml.rescloud.iu.edu/2410.3/07678.html))
  - mDNS-Geräteerkennung entfällt komplett — wir haben genau einen Adapter, fest
    unter `can0`.
- **Aufwand, falls gewünscht:** ein eigenes `scripts/can_live_reference.py`
  (Flask-App, Holds/Sweep-UI 1:1 aus `flask_sync.py` übernehmbar — die
  HTML/JS-Seite ist komplett hardwareunabhängig) wäre überschaubar, da der
  komplette CAN-spezifische Teil auf die drei Zeilen "`can.Bus(interface=
  'socketcan', channel='can0', ...)`" + "`can.Logger('trace.log')`" schrumpft.
  **Noch nicht gebaut** — bisher hatten wir für jedes Signal einen Bezug
  (OBD/anderes CAN-Signal). Bei Bedarf (z.B. gezielte ABS/DSC-Testfahrt ohne
  brauchbaren Referenzkanal) ist das ein guter nächster Ausbauschritt.
