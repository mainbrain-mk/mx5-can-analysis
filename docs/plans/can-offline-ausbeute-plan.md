# Plan: mehr Information aus den vorhandenen CAN-Logs, ohne neue Fahrten (2026-09-26)

Nachfolger von [`can-deep-search-plan.md`](can-deep-search-plan.md). Randbedingung: **keine neuen
Daten** — nur die 32 CAN-Logs im Datalake (28 davon >= 20 MB, die meisten mit Y-Splitter-OBD-Verkehr).

## Ausgangslage (gemessen, nicht geschätzt)

- Größtes Log (`candump-2026-09-18_093544`): 105 IDs, **1977 Bits variieren, 1311 davon liegen in
  keinem DBC-Signal** (inkl. `_maybe`). Die Zahl ist aufgebläht durch Zähler, Prüfsummen und
  Multiplex: `0x45B` z. B. zählt 56 "freie" Bits, ist aber eine Mux-Botschaft (Byte0 zykliert
  1..5), in der nur Seite 1 Byte2 und Seite 2 Byte3-4 überhaupt variieren.
- Bisherige Methoden haben zwei Grenzen:
  1. **Korrelation braucht eine Referenz**, und die Anker sind nur *gemessene* Kanäle (29 Stück).
     Steuergeräte senden aber vor allem *interne Modellgrößen* (Schlupf, Steigung, Soll-Gierrate,
     Verbrauch, Zustände), für die es keinen gemessenen Kanal gibt.
  2. **Seltene Zustände** fallen in Ganzlog-Statistiken durch (Lehre aus ABS 15.09. und 0x4DB
     26.09.: erst der Vergleich gegen *gleichartige Situationen ohne das Merkmal* trägt).
- Vorprüfungen für diesen Plan (26.09., Scratch-Skripte):
  - **Rückwärtsfahrt ist offline erkennbar:** bei 1-12 km/h, |Lenkwinkel| > 60°, |Gierrate| > 2°/s
    hat die Gierrate in Vorwärtsfahrt das eine Vorzeichen relativ zum Lenkwinkel, in 20 von 32 Logs
    gibt es zusätzlich 4-13 s mit dem **umgekehrten** Vorzeichen (Einparken). `Gear_CAN=7` ist in
    diesen Phasen praktisch nie gesetzt (3 Abtastungen in einem Log) — passt zum offenen Punkt
    "Reverse=7 nur bei voll eingekuppelter Kupplung".
  - **Zähler mit variabler Rate gegen Geschwindigkeit:** `0x4FE`, `0x45B`, `0x21D`, `0x4DA`
    negativ (|r| <= 0,25 für Inkrement/5 s gegen Speed). Kein Wegimpulszähler dort.
  - **DIDs `DA86`/`F40C`/`4028`:** in keinem Log seit 14.09. angefragt — offline tot.

## Leitidee

Statt mehr Suchwerkzeuge: **bessere Referenzen bauen.** Aus den schon validierten Kanälen lassen
sich physikalische Größen und Ereignislisten ableiten, die genau die internen Größen der
Steuergeräte abbilden. Die vorhandenen Werkzeuge (`can_byte_search.py`, `can_bitsearch.py`,
`can_event_bit_diff.py`, `can_field_families.py`) bleiben, sie bekommen nur neue Eingaben.

## Schritte (nach Ertrag/Aufwand)

### 0. Rest-Budget-Tabelle (Aufwand: klein)
Aus `results/can_field_segmentation_consolidated.csv` (READ klassifiziert schon CONST/COUNTER/CRC)
plus DBC-Abdeckung plus Mux-Aufspaltung eine Tabelle "offene Felder je ID" über alle Logs. Das ist
der Fortschrittszähler für alles Weitere und nimmt Zähler/Prüfsummen sauber aus dem Suchraum.
Kein neues Skript nötig, falls sich das als Option an `can_field_segmentation.py` anhängen lässt.

### 1. Rückwärtsgang-Signal über synthetische Ereignisse (Aufwand: klein, Ertrag: schließt offenen Punkt)
- Ereignisliste: Phasen mit sign(Gierrate) = sign(Lenkwinkel) bei 1-12 km/h (s. Vorprüfung),
  mindestens 1 s lang. Wo GPS vorhanden: Kurs gegen Gier-Heading als Gegenprobe.
- **Kontrolle:** Vorwärts-Rangieren mit gleicher Geschwindigkeit und gleichem Lenkeinschlag,
  nicht "der Rest des Logs".
- `can_event_bit_diff.py` über alle ~20 Logs mit Ereignissen; Erwartung: Rückwärts-Flag im PCM
  (`0x165`/`0xFD`-Umfeld), Rückfahrlicht im BCM, ggf. `Reverse_Flag_maybe` (0x9F) bestätigen
  oder verwerfen.
- Nebenertrag: vorzeichenrichtige Geschwindigkeit fürs Datalake (Rangierphasen sind heute als
  Vorwärtsfahrt verbucht).

### 2. Synthetische Anker für den Sweep (Aufwand: mittel, Ertrag: am höchsten)
Abgeleitete Kanäle auf dem 10-Hz-Raster, als zusätzliche Anker in `can_byte_search.py` bzw.
`can_field_families.py`:

| Anker | Formel/Quelle | Wonach er sucht |
|---|---|---|
| Kraftstoffstrom | `MAF / (14,7·λ_soll)`, 0 bei `FuelCut` | Momentanverbrauch fürs Kombiinstrument, Reichweite |
| Kraftstoff-Integral | kumulierte Summe davon | Verbrauchszähler (die heute als "monoton" verworfenen Reihen) |
| Fahrbahnsteigung | `LongitudinalAcc − dv/dt` (Radgeschw.), geglättet | Steigungsschätzung im ABS (Berganfahrhilfe braucht sie) |
| Soll-Gierrate / Gierabweichung | Einspurmodell aus `steering_lateral_model.py` minus `YawRate` | DSC-interne Über-/Untersteuer-Größe |
| Antriebsschlupf | Hinterräder − Vorderräder, relativ | ABS/TCS-Schlupfschätzung |
| Radbeschleunigungen | dv/dt je Rad | ABS-Regelgrößen |
| Batteriespannung | PID 0x42 (seit 16.09., 10-s-Takt) | `0x08A` (DCDC, 100 Hz, 5 analoge Felder, in keiner DBC) |
| Zeit seit Motorstart, Kühlwasser-Gradient | aus `KeyState`/`EngineRPM`/`CoolantTemp` | Warmlauf-/Katheiz-Zustände |

Bestätigungsregel wie gehabt: stabile Bitlage über >= 4 Logs, r > 0,9 bzw. Ereignis-Lift mit
Kontrolle; sonst `_maybe` oder verwerfen. Zähler-Rate (Inkrement pro Fenster) gegen den
Kraftstoffstrom gleich mitprüfen — das ist der Rest von "Zähler mit variabler Rate", der nach der
Vorprüfung gegen Speed noch offen ist.

### 3. Natürliche Experimente: Ereignisse, die in jedem Log ohnehin vorkommen (Aufwand: klein-mittel)
Ereignislisten aus bekannten Signalen erzeugen, jeweils gegen gleichartige Kontrollfenster diffen:
Motorstart, i-stop-Stopp/-Start (`0x20A` Byte0 = 245/246), Kupplung treten, Gangwechsel,
Blinker links/rechts, Tür, Licht an (Abendlog `233436`), KeyState-Übergänge, Lüfternachlauf nach
Motor aus. Ziel sind die Zustandsbotschaften, die heute ohne Deutung sind: `0x21D` (50 Hz, ändert
sich selten, 16 Werte je Byte), `0x4FA`/`0x42B`-Familie, `0x4DB`-Zustand 11, `0x4DA`, `0x3D2`.
Ein kleines Skript, das die Ereignislisten baut; die Auswertung macht `can_event_bit_diff.py`.

### 4. Multiplex und Zeitverhalten (Aufwand: klein)
- Mux-Kandidaten systematisch aufspalten (`0x45B` bestätigt; die Scratch-Heuristik schlägt sonst
  nur bei Zähler/Prüfsumme an). Seiten mit variablen Bytes einzeln in Schritt 2/3 geben.
- Botschaftstakt: periodisch vs. ereignisgetrieben, Ratenwechsel als eigene Ereignisse;
  Phasengruppen gleichen Takts ordnen IDs einem Sender zu (Kontrolle gegen die Sendernamen der DBC).

### 5. Zwischen-Log-Konstanten (Aufwand: klein, Ertrag: Flags)
Bits, die innerhalb eines Logs konstant, zwischen Logs aber verschieden sind: je Log ein Wert,
gegen Log-Metadaten (Uhrzeit/Dunkelheit, Außentemperatur, Beifahrer/Gurt, Tank,
Verdeck — `RoofGraphicStatus`) stellen. Nur 32 Stichproben, reicht für Flags, nicht für Skalen.

### 6. Bekanntes verwerten statt neu suchen (Aufwand: klein, sofortiger Nutzen)
Offene Punkte aus Status/Logbuch, die ohne Fahrt gehen:
- `SteeringRate`, `DCDC_State/RegenFlag`, `EngineState`, `SteeringAngle_EPAS_Abs` (nullpunktstabil)
  in `CAN_SIGNAL_MAP` übernehmen.
- Schleppmoment in `engine_braking_analysis.py` nach Rekuperation (Zustand 4 vs. 8) trennen.
- Bremsmodell gegen die Verzögerung während der ABS-Regelung (Heimfahrt 15.09. ab t=1880 s),
  Grip-Schätzer gegen die ±1,0-g-Kurven.
- WOT-Schwelle mit dem echten Lambda-PID 0x44 nachrechnen.
- `STEER_SPD_EPS` gegen `SteeringRate` (Vorzeichenkonvention, Skala 0,5 °/s/LSB kalibrieren).

### 7. Frontkamera `0x242-0x246` (Aufwand: mittel, Ertrag fürs Projekt gering)
Viele variable Bits, aber Spurdaten nützen auf der Rennstrecke wenig. Anker wären
Straßenkrümmung = Gierrate/v und Spurwechsel (Blinker + integrierte Querbewegung). Nur, wenn
1-6 durch sind.

## Bewusst nicht in diesem Plan (braucht neue Daten oder Fahrzeug)
Soft-Limiter-Auslöser (gezielte Vollgaszüge, s. Logbuch 26.09.), DSC-Eingriff ohne Bremsung,
TPMS-Vorderachse (120-s-Takt, offline nicht trennbar), gemessenes Lambda (PID 0x34 nie
angefragt), DID-Sweep, MS-CAN.

## Reihenfolge
0 → 1 → 6 (schnelle Gewinne, parallel machbar) → 2 → 3 → 4 → 5 → 7.
Ergebnisse wie üblich datiert ins Logbuch `docs/logs/can-bus-status.md`, Stand in
`docs/status/can-bus.md` nachziehen.
