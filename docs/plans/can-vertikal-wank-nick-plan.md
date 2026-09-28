# Plan: Auf-/Abbewegung, Wanken und Nicken in den CAN-Logs finden (2026-09-28)

## Ziel
Drei Größen, die bisher nur die Handy-IMU liefert, auf dem CAN-Bus finden:
Vertikalbeschleunigung `a_z`, Wankrate/-winkel `p`/`phi`, Nickrate/-winkel `q`/`theta`.
Wenn das klappt, braucht es das Handy nur noch für GPS (siehe Logbuch
"CAN-IMU statt Handy-IMU? ... (2026-09-28)" in `logs/projekt-stand.md`).

## Ausgangslage
- **Anker = Handy-IMU aus den `.dlg`**: `AccelerationX/Y/Z` (ohne g), `AccelerationWithGravityX/Y/Z`,
  `RotationRateX/Y/Z`, `Pitch`, `Roll`. Alles in Handy-Koordinaten, Halterung seit 06.09. variabel.
- **Paare CAN+Handy:** ca. 20 seit 12.09. (z. B. 09-12 211833, 09-14 081105, 09-15 084853,
  09-16 081915/083214, 09-17 081218, 09-18 090404/155230/161130, 09-19 163755/233436,
  09-26 120235/130440/135855/142216, 09-27 112539/113615), zusammen grob 8 h.
- **Zeitfallen:** `logs.start_time_local` der `.dlg` steht in UTC (−2 h); einzelne CAN-Logs sind
  wegen No-RTC falsch datiert (siehe can-bus-Logbuch). Startzeit reicht zum Paaren nicht.
- **Werkzeuge:** `can_anchor_sweep.py` (Grobsuche, Cross-Log-Aggregation, freie Bits aus DBC),
  `can_bitsearch.py` (bitgenau; bisher nur Referenzen aus demselben CAN-Log, keine Lag-Suche),
  `can_event_bit_diff.py` (ereignisbasiert), `imu_orientation.py`, `uds_did_sweep.py`.
- **Fahrzeug (Nutzer 28.09.):** LED-Matrix-Scheinwerfer **mit automatischer Leuchtweitenregulierung**;
  die Scheinwerfer fahren beim Einschalten des Lichts sichtbar einmal hoch und runter
  (Selbsttest-Referenzfahrt).
- **CAN-IMU vorhanden:** `0x075` (Lateral_Acc_Raw, YawRate_Raw) und `0x076` (Longitudinal_Acc_Raw),
  je 100 Hz.

## Schritt 0: Paare bilden und fein ausrichten
1. Kandidaten aus dem Datalake: `.dlg`-Zeit +2 h, großzügige Überlappungstoleranz.
2. Feinausrichtung per Kreuzkorrelation: `YawRate_Raw` gegen Handy-Gierachse (`imu_orientation.py`),
   zusätzlich `Lateral_Acc_Raw` gegen horizontale Handy-Beschleunigung. Versatz auf ms genau,
   Drift aus Versatz Anfang/Ende.
3. Qualitätsgrenze: Gierraten-Korrelation r > 0,9, sonst Paar verwerfen.
4. Ausgabe `data/can_phone_pairs.json` (Paar, Versatz, Drift, r).

## Schritt 1: Handy in Fahrzeugkoordinaten
Je Paar eine Rotationsmatrix Handy → Fahrzeug:
- Hochachse aus g (Stand und ruhige Geradeausfahrt),
- Längs-/Querachse per Regression gegen CAN `Longitudinal_Acc_Raw`/`Lateral_Acc_Raw`
  (CAN ist hier der Maßstab, keine Annahme über die Halterung),
- Kontrolle: rotierte Handy-Gierrate ≈ `YawRate_Raw` (Steigung ≈ 1).

Anker, einheitlich 50 Hz:

| Anker | Quelle | Filter |
|---|---|---|
| `a_z` | Handy-Beschl. Fahrzeug-Hochachse | HP 0,5 Hz; zusätzlich Band 5-20 Hz (Fahrbahn/Radordnung) |
| `p` Wankrate | Drehrate um Längsachse | HP 0,2 Hz |
| `q` Nickrate | Drehrate um Querachse | HP 0,2 Hz |
| `phi` Wankwinkel | `Roll` minus Einbauoffset | TP 2 Hz, trendbereinigt |
| `theta` Nickwinkel | `Pitch` minus Einbauoffset | TP 2 Hz, trendbereinigt |

## Schritt 2: Scheintreffer bekannter Signale verhindern
Wankwinkel ~ Querbeschleunigung, Nickwinkel ~ Längsbeschleunigung + Steigung. Ohne Gegenmaßnahme
"findet" die Suche nur `0x075`/`0x076` wieder.
- Jeden Anker per linearer Regression gegen bekannte CAN-Signale bereinigen
  (Lat/Long/Yaw, Geschwindigkeit, Radgeschwindigkeiten, Bremsdruck) und **gegen das Residuum** suchen.
- Ein Kandidat zählt nur, wenn er das Residuum erklärt, nicht bloß ein bekanntes Signal kopiert.

## Schritt 3: Grobsuche
`can_anchor_sweep.py` um eine Option erweitern, die die Handy-Anker aus Schritt 1/2 als Referenz nimmt.
- Suchraum: alle IDs, alle laut DBC freien Bits; bekannte Signale laufen als Gegenprobe mit.
- Ratenfilter: `a_z`/`p`/`q` nur IDs ≥ 20 Hz; Winkel auch in langsamen IDs ≥ 1 Hz
  (Leuchtweitenregulierung sendet vermutlich langsam).
- Trefferregel wie bestehend: r ≥ 0,8 in ≥ 3 Paaren, gleiches Vorzeichen, Steigung ±15 % über Logs.

## Schritt 4: Bitgenaue Bestimmung
`can_bitsearch.py` um eine externe Referenzreihe (Datei) und eine kleine Lag-Suche (±50 ms) erweitern;
liefert Startbit/Länge/Endianness/Sign/Scale/Offset. Plausibilität:
- `a_z`: Mittel ≈ 0 oder 1 g, klare Ausschläge an Bodenwellen, Radordnung sichtbar;
- `phi`: in stationären Kurven linear zu `a_lat` (einige °/g), Wankgradient konstant über Logs;
- `theta`: Eintauchen beim Bremsen; langfristig Steigung aus GPS-Höhe.

## Schritt 5: Gezielte Hypothesen (parallel)
1. **`0x075`/`0x076` freie Bytes zuerst**: sendet das RCM eine Z-Achse/Wankrate, dann am ehesten dort.
   Kostet fast nichts.
2. **`0x078`/`0x079` (ABS/DSC)**: eigener Sensorcluster.
3. **Leuchtweitenregulierung, bester Nick-Kandidat.** Die automatische LWR braucht einen
   Höhenstandssensor (typisch Hinterachse) → Karosserie-Nickwinkel gegen Straße.
   - **Einschalt-Referenzfahrt als Ereignisanker:** Licht-Ein-Zeitpunkte aus bekannten
     Lichtsignalen (`Headlight`/`LIGHT`, siehe `status/can-bus.md`) holen und mit
     `can_event_bit_diff.py` Felder suchen, die in den Sekunden danach eine Rampe hoch/runter zeigen
     (Soll-/Ist-Stellung der Aktoren). Unabhängig vom Handy, funktioniert auch in CAN-only-Logs.
   - Ein gefundenes Stellungsfeld ist nicht der Nickwinkel selbst, führt aber meist zur Botschaft
     mit dem Sensorwert (gleiche ID oder gleiches Steuergerät).
   - Achtung Bus: das Scheinwerfer-/Matrix-Steuergerät könnte auf dem **MS-CAN** statt HS-CAN hängen
     (`data/can/MX5ND_6thGenMazda_MSCAN.dbc` existiert). Der Pi loggt nur HS-CAN. Findet sich auf HS
     nichts, ist ein MS-CAN-Abgriff der nächste Schritt, nicht "Signal existiert nicht".
   - Viele LWR-Systeme regeln nur im Stand/bei konstanter Fahrt nach; das Sensorsignal selbst
     sollte trotzdem kontinuierlich laufen.
4. **Ruhetest (nur bei Bedarf, braucht Nutzer + Pi + Handy):** Zündung an, Motor aus, nacheinander an
   jede Ecke setzen bzw. in den Kofferraum lehnen, je ~10 s. Ändert Nick/Wank ohne jede Beschleunigung
   → trennt Höhensensor sauber von allen Beschleunigungsfeldern. Positionen mündlich/Zeit notieren.
5. **Fallback UDS:** Live-DIDs von RCM, ABS und Scheinwerfersteuergerät per `uds_did_sweep.py`.

## Schritt 6: Dokumentation
- Treffer als `_maybe` in die DBC, bis in ≥ 3 Paaren bestätigt.
- `status/can-open-fields.md`, `status/can-bus.md`, Logbuch.
- **Negativbefunde ausdrücklich festhalten** (Suchraum, Anzahl Paare, Schwelle), damit nicht
  erneut gesucht wird.

## Erwartung
- `a_z` und Wankrate: eher unwahrscheinlich (RCMs ohne Überschlagsensorik messen meist nur längs/quer).
- Nickwinkel über LWR-Höhensensor: realistischste Chance; Einschalt-Referenzfahrt ist der
  schnellste Einstieg.
- Aufwand liegt in Schritt 0-2, die Suche selbst ist automatisiert.
