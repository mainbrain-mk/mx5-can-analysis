# Plan: Handy-GPS kabellos auf dem Pi mitloggen (2026-11-06)

Ziel: GPS-Daten des Handys (Android) landen während der Fahrt per Bluetooth auf dem Auto-Pi, ohne
neue Hardware und ohne den Umweg über den Drive-Ordner.

## Datenfluss

```
Handy: NMEA-App (Vordergrunddienst, Bildschirm darf aus sein)
   │  Bluetooth Classic SPP/RFCOMM, ca. 0,3–1,5 kB/s
   ▼
Pi: gps-logger.service, verbindet sich selbst immer wieder neu
   │  jede Zeile: Empfangszeit (Pi-Uhr) + NMEA-Satz
   ▼
Stick: canlogs/gps-JJJJ-MM-TT_hhmmss.nmea
   │  bisheriger Abholweg, muss die neue Datei mitnehmen
   ▼
build_datalake.py: ingest_nmea() → Breite, Länge, Höhe, GPS-Geschwindigkeit
```

## Entscheidungen

1. **Der Pi baut die Verbindung auf, das Handy wartet.** Der Pi bleibt unsichtbar, die
   Wiederverbindung liegt bei uns. Dass die App wartet, ist aus ihrer Store-Beschreibung geschlossen
   und wird in Phase 0 geprüft.
2. **Eigener Dienst, unabhängig von `can_backend` und Dash.** Ein Bluetooth-Problem darf weder
   CAN-Logging noch Anzeige stören.
3. **Rohdaten statt GPX.** Empfangszeit und GPS-Zeit stehen in jeder Zeile, Fehler bleiben später
   nachrechenbar. Rund 5 MB/h, kein Packen nötig.
4. **Datalake-Kanäle bleiben gleich.** Kurs, HDOP und Satellitenzahl bleiben vorerst nur in der
   Rohdatei.

App-Kandidat: GPS NMEA Tether (Standard-SPP-UUID, Vordergrunddienst, 1/2/5 Hz, Roh-NMEA-Durchreichung,
keine Internet-Berechtigung; klein: ab 1.000 Downloads, 13 Bewertungen). Ausweichoption: Share GPS.

## Stolperstellen

- **Pairing:** `/var/lib/bluetooth` liegt im flüchtigen Overlay; die Schlüssel müssen einmal per
  `overlayroot-chroot` in die Basisschicht.
- **Adapter geteilt mit `bbq.service` (BLE):** Datenmenge unkritisch; Parallelbetrieb wird in Phase 0
  gemessen.
- **Höhe:** GPX ist als „Altitudes = Raw“ markiert, vermutlich Ellipsoidhöhe. NMEA-GGA liefert Höhe
  über dem Meeresspiegel plus Geoid-Abstand (hier gut 40 m). Ohne Korrektur sind alte und neue Werte
  nicht vergleichbar.
- **Zeitbasis:** GPS-Zeit ist Referenz, die Empfangszeit misst die Verzögerung. Versatz je Log bleibt
  einstellbar, geschätzt aus GPS- gegen CAN-Geschwindigkeit.
- **Datenrate:** Bisherige GPX haben etwa 1 Hz. Ob die 5 Hz der App echte Fixes sind, wird gemessen.
- **Eingabedaten:** Prüfsumme kontrollieren, Sätze ohne gültigen Fix verwerfen, abgeschnittene Zeilen
  nach Stromausfall tolerieren.

## Verworfene Alternativen

| Variante | Warum nicht |
|---|---|
| Handy-Hotspot + TCP | Mehr Akku, WLAN-Wechsel Zuhause/Auto. Plan B. |
| Bluetooth-Tethering (PAN) | Muss am Handy eingeschaltet werden, viel Netzwerktechnik für wenige Bytes. |
| Eigene Android-App | Nur falls keine fertige App taugt oder die Handy-IMU mit soll. |
| GPX wie bisher | Läuft und wird automatisch zugeordnet, kommt aber über den Drive-Ordner und zeigt keinen Live-Zustand. |
| USB-GPS-Empfänger | Kein Handy, aber einziger sicherer Weg zu echten 10 Hz oder mehr. |

## Dash-Anzeige

Die Fußzeile hat schon den Platzhalter `GPS  -- (kein Empfaenger)` (`StatusBar` in `dash_gui.py`).
Er zeigt künftig den Zustand des Loggers, damit ein Ausfall während der Fahrt auffällt:

| Zustand | Text | Farbe |
|---|---|---|
| Keine Verbindung, kein Log läuft | `GPS  --` | grau |
| Keine Verbindung, Log läuft | `GPS  keine Verbindung` | rot |
| Verbunden, aber seit über 3 s kein neuer Fix | `GPS  kein Fix` | Warnfarbe |
| Fix, HDOP über 2,0 | `GPS  9 Sat  HDOP 2.4` | Warnfarbe |
| Fix, HDOP bis 2,0 | `GPS  21 Sat  HDOP 0.6` | grün |

Die App sendet ohne Fix nichts oder wiederholt bei ausgeschaltetem Standort alle 5 s den alten Fix mit
Status „gültig“ (gemessen 06.11.). „Kein Fix“ heißt deshalb: seit über 3 s kein GGA mit neuer GPS-Zeit.
Das Einlesen in Phase 2 muss Sätze mit bereits bekannter GPS-Zeit verwerfen. Die HDOP-Schwelle ist ein Startwert und wird nach der Testfahrt nachgestellt.

Datenweg: Der Logger ersetzt etwa einmal pro Sekunde atomar eine kleine Statusdatei in `/tmp`
(verbunden, Satelliten, HDOP, Zeitpunkt in `time.monotonic()`), wie schon `SIM_TRIGGER_PATH` dort liegt.
Das Dash liest sie im Takt der Fußzeile; fehlt sie oder ist sie zu alt, gilt „keine Verbindung“.
`can_backend.py` bleibt unberührt, der Logger hängt nicht vom Dash ab. Live-Position oder
GPS-Geschwindigkeit im Dash sind nicht Teil dieses Plans.

## Phasen

**Phase 0: Funktionstest ohne eigenen Code.** App installieren, koppeln, Daten per `rfcomm` ansehen,
zuerst im Demo-Modus. Erfolgreich, wenn die Verbindung einen Pi-Neustart übersteht, Daten neben dem
laufenden `bbq.service` fließen und die echte Fix-Rate bekannt ist.

**Phase 1: Logger und Dash-Feld auf dem Pi.** Dienst mit kleinem Parser-Selbsttest, Dash-Feld mit
Test der Zustände in `test_dash_gui.py`, Deploy per `git push car HEAD:deployed`. Erfolgreich, wenn
eine Trennung von selbst behoben wird, nichts außer der Lücke verloren geht und das Dash jeden Zustand
korrekt zeigt.

**Phase 2: Testfahrt und Datalake.** BasicAirData läuft als Referenz parallel, NMEA-Einleser kommt
dazu. Erfolgreich, wenn der Track zur GPX passt, sich die bekannte Abweichung von 2,9 % zwischen
`VehicleSpeed` und GPS wieder zeigt und die Verbindung die ganze Fahrt hält.

## Ergebnisse Phase 0 (06.11.2026)

Handy OnePlus Nord CE5, App GPS NMEA Tether, Pi 4 mit Kernel 6.12.109. Herleitung im Logbuch
(`logs/can-bus-status.md`, Abschnitt vom 06.11.).

- **Verbindung:** Der Pi verbindet sich in 0,7–2,3 s, Prüfsummen zu 100 % gültig.
- **Rate:** 1 Hz, auch mit eingestellten 5 Hz. Ob der Chip mehr kann, ist offen.
- **Bildschirm aus:** 60 s lückenlos, ganze Fahrt noch offen.
- **Datenmenge:** etwa 1,5 kB/s, also rund 5 MB/h, davon der größte Teil GSV.
- **Ohne Fix sendet die App nichts**, die Verbindung steht trotzdem.
- **Kanal wechselt:** Nach jeder Trennung neue RFCOMM-Nummer (9 bis 15 gesehen), also vor jedem
  Verbinden per SDP nach `0x1101` fragen. Der Dienst steht nicht in der öffentlichen Browse-Liste,
  `sdptool browse` findet ihn nicht.
- **Wiederkehr dauert rund 30 s:** Erst wird kurz der alte Kanal gemeldet und die Verbindung abgewiesen,
  dann ist der Dienst etwa 24 s weg.
- **Neustart bestanden:** Das Pairing überlebt, der App-Dienst bleibt ohne Eingriff am Handy erreichbar,
  auch unter Kernel 6.12.115.
- **Offen für Phase 1:** Verhalten am Kanal-Ende (30), Parallelbetrieb mit verbundenem Thermometer
  (bisher nur dessen Suchlauf).

## Annahmen

- Für Fahrleistung und Streckenzuordnung reicht das Handy-GPS.
- Ein Tipp auf die App vor jeder Fahrt ist in Ordnung, Automatik erst bei Bedarf.
- Nur GPS, die Handy-IMU läuft nicht über NMEA.
