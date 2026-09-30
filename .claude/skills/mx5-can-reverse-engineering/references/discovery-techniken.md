# Discovery-Modus — ungerichtete Signalsuche in ganzen Logs

Ziel: nicht „wo steckt Signal X?", sondern „was steckt überhaupt noch im Log, das wir
nicht kennen?". Daraus werden Hypothesen, die anschließend im gezielten Modus
(SKILL.md, ab Schritt 2) verifiziert werden. Alles läuft offline auf vorhandenen Logs.

Stand und bisherige Ausbeute: `docs/status/can-open-fields.md` (Rest-Budget,
neu identifiziert, offen) und `docs/plans/can-offline-ausbeute-plan.md` bzw.
`can-deep-search-plan.md`. Vor einer neuen Discovery-Runde dort nachsehen, welche
Techniken schon über den ganzen Bestand gelaufen sind.

## Werkzeuge je Technik

| Technik | Skript | Stand |
|---|---|---|
| D1 Inventur / Rest-Budget | `can_open_fields.py` | vorhanden, zuletzt 26.09. |
| D2 Feldtypen, Grenzen, Counter/CRC, Signedness | `can_field_segmentation.py` (READ) | vorhanden |
| D3 Fremd-DBC | `can_opendbc_crosscheck.py` | vorhanden (opendbc `mazda_2017`) |
| D4 Korrelationsnetz | `can_field_families.py` | vorhanden |
| D5 Erklärbarkeit | – | ad hoc |
| D6 Ereignis-Katalog | `can_rare_bits.py`, `can_event_bit_diff.py`, `can_natural_events.py` | vorhanden |
| D7 Multiplex | – (0x45B, 0x3D2 schon erkannt) | ad hoc |
| D8 DTW | – | ad hoc, nur Zusatzmetrik |
| D9 Clock-Skew | `can_timing.py` | [geplant] |
| D10 Kausalität, Soll/Ist | – | ad hoc |
| D11 Spektral/Kohärenz | – | ad hoc |
| D12 Symbolische Regression | – | ad hoc |
| D13 Log-Kontinuität | `can_find_clock_candidate.py` (Zähler), Kaltstart-Methode | teils vorhanden |
| D14 Externe Zeitreihen | – (Open-Meteo, Tankbelege schon genutzt) | ad hoc |
| D15 Init/Lampentest | `can_natural_events.py --event engine_start` | teils vorhanden |
| D16/D17 Steckbrief | `can_profile.py` | [geplant] |

Alle Skripte mit `.venv/bin/python scripts/<name>.py`, fast alle mit `--self-test`.
Schnelle Rohdaten über `can_offline_lab.py` (`frames`, `field`, `grid`, `.npz`-Cache).

Grundhaltung: Automatische Verfahren liefern **Hypothesen, keine Wahrheiten**.
Selbst aktuelle Systeme treffen bei der Bedeutungszuordnung nur rund zwei Drittel
(ByCAN: ~69 % Labeling-Genauigkeit). Nichts aus diesem Modus geht ohne Verifikation
als „plausibel" oder „bestätigt" in DBC oder Datalake. Die Techniken sind so
gewählt, dass sie sich **gegenseitig** stützen. Ein Kandidat, auf den drei
unabhängige Techniken zeigen, ist viel mehr wert als ein Kandidat mit einem sehr
hohen Einzelscore.

## Inhalt

**A Struktur — was ist ein Feld, wer sendet es?**
- D1 Bus-Inventur und „Karte der Unbekannten"
- D2 Feldtyp-Klassifikation (Unused / Switch / Dynamic / Verification)
- D7 Multiplex-Erkennung
- D9 Sender-Zuordnung über Timing (Clock-Skew, Burst-Muster)

**B Bedeutung aus Zusammenhängen**
- D4 Korrelationsnetz und funktionale Gruppen
- D5 Erklärbarkeitsanalyse (Kandidat ~ alle bekannten Signale)
- D10 Kausal- und Wirkketten, Soll/Ist-Paare
- D11 Spektral- und Kohärenzanalyse
- D12 Symbolische Regression (Formeln finden)
- D8 Zeitverzerrungs-Matching (DTW), nur mit Vorsicht

**C Zeitlicher Kontext**
- D6 Ereignis-Katalog für seltene Änderungen
- D13 Log-übergreifende Kontinuität (persistente Größen, Zähler)
- D15 Zündungs-/Init-Sequenz und Lampentest

**D Externe Offline-Quellen**
- D3 Wissenstransfer: Community-DBCs, Schwestermodelle
- D14 Externe Zeitreihen (Wetter, Tankbelege, Kalender)

**E Synthese**
- D16 Kandidaten-Steckbrief und Evidenz-Score
- D17 LLM-gestützte Hypothesenbildung aus Steckbriefen
- Aufwand/Nutzen-Übersicht, Pipeline, Kandidaten-Register

---

# A Struktur

## D1 Bus-Inventur und „Karte der Unbekannten"

Über alle Logs je CAN-ID erfassen:
- Zykluszeit (Median, Jitter), DLC, ob zyklisch oder ereignisgesteuert, und in
  welchen Zuständen die ID auftritt (nur Zündung, Motor läuft, Fahrt).
- Anteil der Bits: konstant / durch DBC abgedeckt / variabel, aber unbekannt.
- Aktivität der unbekannten Bits (Flip-Rate, Anzahl distinkter Werte).

Umgesetzt in `can_open_fields.py` (Ausgabe `results/can_open_fields.csv`): Felder aus
unbelegten, variierenden Bits, je Feld Anzahl Logs, Wertevielfalt, Änderungsrate,
Counter-/CRC-Verdacht. Zyklus: 99 von 101 IDs sind streng periodisch, praktisch nichts
ist ereignisgetrieben.

Ergebnis ist eine priorisierte Liste „variabel, aber unbekannt". Die ersten
Kandidaten sind schnelle IDs (10–20 ms) mit viel unbekannter Aktivität, denn dort
sitzen typischerweise Fahrdynamik- und Antriebsgrößen.

Nebenprodukt: Kopien bekannter Signale in anderen IDs, oft mit höherer Rate oder
feinerer Auflösung. Die sind für den Datalake wertvoll, auch wenn die Größe schon
bekannt ist.

## D2 Feldtyp-Klassifikation (READ, ergänzend ByCAN)

Im Projekt umgesetzt ist READ in `can_field_segmentation.py`: Feldgrenzen über die
Größenordnung der Bitkipprate, Klassen CONST/COUNTER/CRC/PHYSICAL/FLAG, plus ein
Signedness-Verdachtstest. Die ByCAN-Variante unten ist eine ad-hoc-Ergänzung, wenn READ
bei einer ID unsicher ist.

Erst auf Byte-Ebene, dann auf Bit-Ebene arbeiten. Merkmale je Byte bzw. Bitblock:
- Flip-Rate,
- Mittelwert,
- Anteil distinkter Werte (|distinkte Werte| / 2^Länge).

Bytes mit DBSCAN clustern; die Zahl der Signale muss dafür nicht bekannt sein.
Innerhalb der Byte-Cluster wird auf Bit-Ebene geschnitten. Clustergröße als
Default auf maximal 2 Bytes begrenzen, weil in OpenDBC über 98 % aller Signale
höchstens 16 Bit lang sind.

Typzuordnung über `θ = Flip-Rate × Distinkt-Anteil`:
- **Unused:** θ = 0 und Flip-Rate = 0.
- **Switch:** 0 < θ ≤ ε0.
- **Dynamic:** θ ≥ ε0 und Flip-Rate < 0,99.
- **Verification** (Counter/Checksumme): θ ≥ ε0 und Flip-Rate ≥ 0,99.

ε0 wird per Clustering bestimmt. Anschließend mit der CAN-D-Grenz- und
Signedness-Heuristik (SKILL.md Schritt 1) verfeinern.

Weiter geht es je nach Typ:
- Dynamic → B-Techniken.
- Switch → D6, D15.
- Verification → Counter-/CRC-Prüfung, danach aus allen weiteren Suchen ausschließen.

## D7 Multiplex-Erkennung

In gemultiplexten Frames bestimmt ein Multiplexer-Wert, welche Signale in den
übrigen Bits stehen. Ohne Aufteilung sehen solche IDs verrauscht oder
„checksummenartig" aus.

Schon erkannt: 0x45B und 0x3D2 (Offline-Ausbeute 26.09.).

Test: Die ID hat ein Feld mit wenigen Werten, das oft zyklisch rotiert
(0,1,2,…), und die Bit-Statistiken der übrigen Bytes unterscheiden sich stark je
nach dessen Wert. Dann die Frames nach Mux-Wert aufteilen und jeden Teil wie eine
eigene ID behandeln. Im DBC als `M`/`m<n>` eintragen.

## D9 Sender-Zuordnung über Timing (Clock-Skew, Burst-Muster)

CAN-Frames enthalten keinen Absender. Periodische Frames erben aber die
Uhrabweichung (Clock-Skew) des sendenden Steuergeräts. Die Forschung nutzt das zum
Fingerprinting (Cho & Shin, CIDS), und CANvas identifiziert so die sendenden
ECUs. Frames desselben Senders zeigen denselben Skew, fremde einen anderen. Für
uns heißt das: IDs nach Steuergerät gruppieren. Das liefert Kontext („diese
unbekannte ID kommt vom selben Gerät wie die Raddrehzahlen → ABS/DSC").

Offline-Verfahren je periodischer ID:
1. Nominalperiode T̂ schätzen (Median der Abstände).
2. Akkumulierten Offset `O(n) = (t_n − t_0) − n·T̂` gegen die Zeit auftragen und
   eine Gerade fitten. Die Steigung ist der relative Skew in ppm gegenüber der
   Logger-Uhr.
3. IDs mit gleicher Steigung (innerhalb der Unsicherheit) haben mit hoher
   Wahrscheinlichkeit denselben Sender. Die Gruppierung soll über mehrere Logs
   stabil sein.

Ergänzende Timing-Merkmale:
- **Burst-/Reihenfolge-Muster:** IDs, die fast immer direkt hintereinander im
  selben Zeitfenster erscheinen, kommen oft aus derselben Sende-Task.
- **Phasenkopplung:** gleiche Periode und konstanter Phasenversatz.
- **Weitergeleitete Frames:** Ein Gateway (beim ND laut Community z.B. das
  Kombiinstrument zwischen MS- und HS-CAN) prägt den weitergeleiteten Frames
  seinen eigenen Takt auf. Viele IDs mit gleichem Skew, aber inhaltlich
  gemischten Funktionen deuten auf ein Gateway hin.

Vorbehalte:
- Die Pi-Uhr lief bis 28.09. ohne RTC und springt bei NTP-Sync (Clock-Jumps im Log).
  Skew nur innerhalb sprungfreier Abschnitte schätzen (`can_offline_lab.grid()`
  schneidet Lücken > 2 s heraus).
- Klären, ob `candump`-Zeitstempel Hardware- oder Host-Zeitstempel sind.
  Host-Zeitstempel über USB haben Jitter im ms-Bereich. Bei Logs ab ~20–30 min
  ist der Skew trotzdem schätzbar, weil die Drift linear wächst, aber die
  Konfidenzintervalle müssen mitgerechnet werden.
- Ereignisgesteuerte IDs haben keinen verwertbaren Skew.

# B Bedeutung aus Zusammenhängen

## D4 Korrelationsnetz und funktionale Gruppen

Umgesetzt in `can_field_families.py` (10-Hz-Raster, Spearman, unbelegte Bytes/
Byte-Paare gegen einander und gegen alle Datalake-Kanäle desselben Logs, ohne
Lag-Suche und ohne Nulltest). Was darüber hinausgeht, ad hoc:

Alle Dynamic-Kandidaten und bekannten Signale auf ein gemeinsames Zeitraster
bringen (z.B. 20 ms). Dann paarweise Spearman mit kurzer Lag-Suche (±0,5 s)
rechnen.
- Graph bauen: eine Kante, wenn |ρ| über der Schwelle liegt und der
  Zirkularverschiebungs-Nulltest (SKILL.md Schritt 8) bestanden ist.
- Communities finden (Louvain/Leiden oder hierarchisches Clustering auf
  1 − |ρ|). Typische Gruppen: Raddrehzahlen, Lenkung, Motor/Last, Querdynamik,
  Temperaturen.
- Ein unbekannter Kandidat in einer Gruppe mit bekannten Signalen erbt eine
  Hypothese.
- Paare mit |ρ| > 0,995 sind Duplikate oder Kopien. Auflösung und Rate
  vergleichen, für den Datalake die beste Quelle wählen.
- Mit D9 kombinieren: Gruppe aus D4 und Sender aus D9 bestätigen sich
  gegenseitig.
- Stark korrelierte Kandidaten derselben ID sind oft ein falsch geschnittenes
  Signal. Dann die Grenzen neu prüfen.

## D5 Erklärbarkeitsanalyse (Kandidat ~ alle bekannten Signale)

- Für jeden Dynamic-Kandidaten ein Modell fitten:
  `Kandidat ~ f(bekannte Signale, abgeleitete Anker, Regime-Masken, Zeit seit
  Motorstart)`. Gradient Boosting genügt.
- **Train und Test auf verschiedenen Logs.** Test-R² zeigt, ob der Kandidat aus
  Bekanntem erklärbar ist; Permutation Importance zeigt, woraus.
- Beispiele:
  - hängt fast nur von „Zeit seit Start" und Last ab → Temperatur,
  - hängt von Drehzahl × Gaspedal ab → Moment oder Last,
  - hängt von a_y und v ab → Querdynamik.
- Billigere Ergänzung: Mutual Information je Paar. Die erfasst auch nichtlineare
  und nicht-monotone Beziehungen.

## D10 Kausal- und Wirkketten, Soll/Ist-Paare

Hier wird nicht gefragt, *ob* zwei Größen zusammenhängen, sondern *wer wen
treibt*. PicaCAN extrahiert Antriebsstrangsignale und ihre Bedeutung passiv über
physikalisch induzierte Kausalität, ohne Eingriffe ins Fahrzeug, und fand damit
u.a. Pedale und Motordrehzahl. Die zugrunde liegende Idee: Physik erzwingt
Reihenfolgen. Unsere eigene Umsetzung:

- **Bekannte Wirkketten des ND als Schablone:**
  - Gaspedal → Drosselklappe/Soll-Moment → Ist-Moment → Drehzahl → Hinterrad →
    Vorderrad (bei Traktion).
  - Bremspedal/-schalter → Bremsdruck → Verzögerung → Raddrehzahlen.
  - Lenkrad → Gierrate → Querbeschleunigung.
  Ein unbekanntes Feld wird dort eingeordnet, wo es in der Kette „passt":
  Vorlauf gegenüber den bekannten Nachfolgern, Nachlauf gegenüber den bekannten
  Vorgängern.
- **Richtung schätzen:**
  - Lag der maximalen Kreuzkorrelation (vorzeichenbehaftet),
  - Granger-Kausalität (lineare VAR-Modelle, Lag-Ordnung über AIC),
  - Transfer Entropy, falls nichtlinear.
  Nur innerhalb von Gültigkeitsmasken rechnen (z.B. eingekuppelt).
- **Soll/Ist-Paare (Regelkreis-Invarianten):** Zwei Felder mit gleicher Einheit
  und Skala, bei denen eines dem anderen mit Verzögerung erster Ordnung folgt
  (Ist ≈ Soll durch einen Tiefpass). Typisch sind Soll-/Ist-Moment,
  Soll-/Ist-Leerlaufdrehzahl, Momentenanforderung DSC → Motor. Test: Tiefpass
  mit Zeitkonstante τ auf den Soll-Kandidaten anwenden und τ so optimieren,
  dass der Fehler zum Ist-Kandidaten minimal wird. Ein kleiner Fehler bei
  plausiblem τ (10–500 ms) ist ein starkes Indiz.
  Wo Soll und Ist *auseinanderlaufen*, greift typischerweise eine Regelung ein
  (DSC, Drehzahlbegrenzer, Schubabschaltung). Diese Stellen als Ereignisse an D6
  übergeben.

Vorsicht: Granger ist keine echte Kausalität. Gemeinsame Ursachen und
unterschiedliche Sende-Raten erzeugen Scheinrichtungen. Deshalb auf einem
gemeinsamen Raster rechnen und Latenzen durch Zykluszeiten (D1) einkalkulieren.

## D11 Spektral- und Kohärenzanalyse

- **Periodische Inhalte finden:** Leistungsspektrum je Kandidat. Schmale Peaks
  deuten auf rhythmische Funktionen: Blinker (~1–2 Hz Rechteck, Oberwellen),
  Scheibenwischer-Intervalle, Lüfter-/Pumpentakt.
- **Drehzahlgekoppelte Frequenzen:** Anteile, die mit Radfrequenz (v/U) oder
  Motorordnung (n/60 × k) mitlaufen, lassen sich mit Ordnungsanalyse sichtbar
  machen (Spektrogramm über Drehzahl statt über Zeit). Achtung Nyquist: Eine
  10-ms-ID reicht nur bis 50 Hz.
- **Kohärenz statt Korrelation:** Die Magnitude-Squared Coherence zwischen
  Kandidat und bekanntem Signal ist unempfindlich gegen konstantes Lag und zeigt,
  in *welchem* Frequenzband sie zusammenhängen. Beispiel: Fahrwerksgröße und
  Raddrehzahl-Schwankung kohärent im Bereich 1–3 Hz (Aufbaueigenfrequenz).
- Die Phase der Kohärenz liefert zusätzlich die Laufzeit (Brücke zu D10).

## D12 Symbolische Regression (Formeln finden)

Wenn D5 sagt „erklärbar aus Drehzahl und v", sucht die symbolische Regression
(z.B. PySR) die **Formel**, etwa `Kandidat ≈ c · n_mot / v` → Gangindex, oder
`Kandidat ≈ c1·v² + c2` → Luftwiderstandsanteil.
- Nur mit wenigen, von D5 vorausgewählten Eingängen (≤ 4) und einfachen
  Operatoren (+, −, ×, ÷, Quadrat) arbeiten, sonst entstehen Kunstformeln.
- Auf Log A fitten, auf Log B prüfen.
- Eine Formel mit physikalisch sinnvollen Konstanten (z.B. Übersetzungen aus
  dem Fahrleistungsmodell) ist ein starkes Indiz. Beliebige Konstanten mit vielen
  Nachkommastellen sind es nicht.

## D8 Zeitverzerrungs-Matching (DTW), nur mit Vorsicht

ByCAN setzt DTW ein, weil OBD-Antworten kein festes Raten-Verhältnis zu CAN-Frames
haben. Nur als Zusatzmetrik verwenden, mit schmalem Warping-Fenster
(Sakoe-Chiba, ±0,5 s). Freies DTW biegt fast jede Kurve passend. Primär bleibt
Interpolation plus Lag-Suche (SKILL.md Schritt 3).

# C Zeitlicher Kontext

## D6 Ereignis-Katalog für seltene Änderungen

Vorhanden:
- `can_rare_bits.py`: je (ID, Bit, Zustand) jede Episode mit Fahrkontext über alle Logs
  (`results/can_rare_bit_episodes.csv`, `…_summary.csv`). Findet auch kurze Flags
  (so gefunden: TCS 0x211 Bit 40).
- `can_event_bit_diff.py`: Ereignisfenster gegen Baseline je Bit.
- `can_natural_events.py`: feste Ereignisliste gegen gleichartige Kontrolle.

Lehre aus dem ABS-Fund: gegen **gleichartige** Situationen ohne das Merkmal
vergleichen (Rest derselben Bremsung), nicht gegen das ganze Log.

Für jedes Switch-Feld über alle Logs jede Wertänderung protokollieren, mit:
Zeitstempel, alter/neuer Wert, v, Drehzahl, Gang-Cluster, Bremsmaske, Zeit seit
Zündung, und welche anderen Switch-Felder sich im selben ±0,5-s-Fenster ändern.
- Zustandsautomat je Feld: Werte, Übergänge, Verweildauern.
- Kontingenz gegen Regime-Masken (Anker-Referenz, Abschnitt 8).
- **Assoziationsregeln** über Ereignisse (z.B. „Feld X wird 1 → innerhalb von
  200 ms wird Feld Y 1", mit Support/Confidence/Lift). Das findet Abläufe wie
  Schaltvorgänge, Motorstart oder Regeleingriffe.
- Gleichzeitig schaltende Felder in verschiedenen IDs deuten auf dieselbe Funktion
  in mehreren Steuergeräten hin. Das ist Plausibilisierung, keine unabhängige
  Bestätigung.
- Ergebnis als Tabelle an den Nutzer. Er erkennt Ereignisse aus seinen Fahrten
  oft wieder; solche Aussagen sind zulässige Offline-Anker. Für Ort/Uhrzeit die
  korrigierte Startzeit (`build_datalake.log_start_epoch()`) und die GPS-Spur
  mitliefern, sonst passen seine Angaben nicht zum Log.

## D13 Log-übergreifende Kontinuität (persistente Größen, Zähler)

Die Logs sind eine Folge von Zündungszyklen. Wie sich ein Feld **über Log-Grenzen**
verhält, verrät viel:
- **Wert am Anfang von Log N+1 ≈ Wert am Ende von Log N:** Die Größe ist
  persistent oder physikalisch träge. Kandidaten: Kilometerstand, Tankfüllstand,
  Betriebsstunden, gelernte Adaptionswerte, Kühlmitteltemperatur bei kurzer Pause.
- **Kontinuität mit Abklingen je nach Pausendauer:** Temperaturen. Die
  Abkühlkurve über die Standzeit zwischen den Logs (aus den Log-Zeitstempeln)
  unterscheidet Kühlmittel, Öl und Umgebung.
- **Reset auf festen Wert bei jedem Start:** Tripzähler, Sitzungszähler,
  Init-Werte.
- **Monoton steigend über viele Logs:** Gesamtzähler. Die Steigung gegen die
  Strecke (∫v) ergibt z.B. den Kilometerstand samt Skala. Die Steigung gegen
  Verbrauchsgrößen ergibt den Kraftstoffzähler.
- **Sprung nach oben im Stand:** Tankfüllstand und Tankvorgang (Brücke zu D14).
- **Kaltstart-Methode** (im Projekt bewährt): Werte beim Start nach langer Standzeit
  gegen Ansaugluft/Wetter. So kalibriert: Außen-, Batterie- und RCM-Temperatur.
- Zähler mit variabler Rate: `can_find_clock_candidate.py`.
- Achtung: Die Reihenfolge der Logs nicht aus den Dateinamen ableiten, einige waren
  falsch datiert (`data/can_clock_offsets.json`).

## D15 Zündungs-/Init-Sequenz und Lampentest

Die ersten Sekunden nach Zündung-ein sind eine natürliche, sich wiederholende
„Testsequenz", die in jedem Log gratis mitkommt:
- **Lampentest:** Beispiel `DSC_Indicator_maybe` (0x415): Wert 114 = Lampentest
  2,5 s nach Zündung, 6 = Eingriff, 98 = Abstellen. Viele Warnleuchten (ABS, DSC, Airbag, Motor, Öl, Batterie …)
  gehen beim Einschalten kurz an und dann aus. Bits, die in *jedem* Log nur in
  diesem Fenster gesetzt sind, sind starke Kandidaten für Warnleuchten- bzw.
  Statusbits. Das ist gerade für den ABS/DSC-Status wertvoll, weil der sonst
  kaum Ereignisse liefert.
- **Boot-Reihenfolge:** Welche IDs erscheinen in welcher Reihenfolge? Das
  gruppiert IDs nach Steuergerät (Brücke zu D9).
- **Default-/Sentinel-Werte:** Felder, die vor dem ersten gültigen Messwert einen
  festen Code senden (z.B. 0xFF…), liefern den Sentinel für Schritt 7 und
  verraten, dass hier ein Messwert steht.
- **Motorstart:** Batteriespannung fällt beim Anlassen ein und steigt danach über
  den Ruhewert (Lichtmaschine). Das ist ein Anker für Spannungsfelder. Die
  Drehzahl springt, Öldruck- und Ladestatus-Bits wechseln.
- Dasselbe gilt symmetrisch für die Abschaltsequenz (Nachlauf von Lüfter/Pumpe).

# D Externe Offline-Quellen

## D3 Wissenstransfer: Community-DBCs, Schwestermodelle

Vorhanden: `can_opendbc_crosscheck.py` gegen `data/can/external/opendbc_mazda_2017.dbc`
(CX-5/Mazda3 ab MY2017, 84 von 102 IDs identisch mit unseren). Regeln für Fremd-DBCs
(Lizenz, nicht blind übernehmen): `data/can/external/README.md`. Unsere Master-DBC
basiert selbst auf berumiya/CAN_DBC_6thGenMazda.

CANmatch nutzt, dass Frames und Steuergeräte über Modelle hinweg wiederverwendet
werden. Mögliche Quellen für den ND (Stand der Recherche, vor Nutzung prüfen):
- Die Community-DBC für ND1/ND2 (berumiya/CAN_DBC_6thGenMazda) ist ausdrücklich
  als wachsendes Gemeinschaftsprojekt mit Zwischenständen angelegt. Neue Versionen
  regelmäßig gegen die eigene Master-DBC diffen.
- Die RaceChrono-DIY-Liste für den ND (getestet an einem 2019er RF) nennt u.a.
  Tankfüllstand, Kühlmitteltemperatur, Kupplungspedal und Gang.
- SkyActiv-Nachrichtendatenbank (majbthrd/MazdaCANbus, `skyactiv.kcd`, ursprünglich
  Mazda 6) und Mazda3-Sammlungen. Laut Community teilen sie einige IDs mit dem ND.
- OpenDBC (Mazda-Dateien).

Vorgehen: Fremddefinition probeweise auf die eigenen Frames anwenden und einen
Plausibilitätsscore berechnen (Glätte, Wertebereich, Counter korrekt, Checksumme
stimmt). Treffer landen als Kandidat „aus <Quelle>" im Register und werden danach
wie jedes andere Signal verifiziert. Dieselbe ID kann bei anderem Modell oder
Baujahr etwas anderes bedeuten. Eine Fremd-DBC ist nie selbst ein Beleg.

## D14 Externe Zeitreihen (offline)

Die Log-Zeitstempel erlauben den Abgleich mit Daten außerhalb des Autos:
- **Wetter:** Lufttemperatur am Ort (bewährt: Open-Meteo, 15-min-Werte; so wurde
  `AmbientTemp` = raw/4 °C an sechs Morgenstarts auf RMSE 0,5 K bestätigt) gegen
  Kandidaten für die Außentemperatur. Nur bei Fahrt
  vergleichen, weil der Sensor im Stand durch Motorwärme verfälscht wird.
  Temperaturverlauf über viele Logs und Jahreszeiten liefert Skala und Offset.
- **Tankbelege / eigene Notizen:** getankte Liter und Datum gegen den Sprung eines
  Füllstandskandidaten (D13). Die Literzahl liefert die Skala (so kalibriert:
  `FuelConsumption_Counter`, Voll-bis-Voll 16.→26.09.).
- **Kalender / Uhrzeit:** Licht-/Dämmerungsfelder gegen Sonnenauf- und
  -untergang am Datum; Uhrzeitfelder gegen den Log-Zeitstempel.
- **GPS und Handy-IMU** (GPX + `.dlg`, im Datalake an die CAN-Logs gehängt): Höhe
  gegen Luftdruck- und Steigungskandidaten, Kurs gegen Gierrate, IMU gegen
  Vertikal/Wanken/Nicken (Anker-Referenz A12, `can_phone_imu_sweep.py`).
- Alle externen Zeitreihen brauchen die korrigierte Log-Startzeit
  (`build_datalake.log_start_epoch()`).

# E Synthese

## D16 Kandidaten-Steckbrief und Evidenz-Score

Für jeden Kandidaten einen kompakten Steckbrief erzeugen. Er ist das zentrale
Artefakt des Discovery-Modus:

```
ID 0x___  Bits __–__  (endian, signed)   Typ: Dynamic      Sender-Gruppe: G3 (D9)
Rate: 20 ms   Wertebereich roh: ___–___   Sentinel: 0x___ (nur bei Init)
Korrelationsgruppe (D4): Lenkung  | beste Partner: SteeringAngle ρ=0.97 (Lag +10 ms)
Erklärbarkeit (D5): Test-R² 0.93, wichtigste: YawRate, v
Kausalität (D10): folgt SteeringAngle mit ~10 ms, führt YawRate
Spektrum/Kohärenz (D11): kohärent 0–2 Hz mit Lenkwinkel
Kontinuität (D13): Reset je Start      Init/Lampentest (D15): nein
Community-DBC (D3): kein Eintrag
Hypothese: Lenkwinkelgeschwindigkeit     Evidenz-Score: 3 unabhängige Hinweise
```

Evidenz-Score = Anzahl **unabhängiger** Techniken, die dieselbe Hypothese stützen.
D4, D5 und D12 beruhen auf denselben Korrelationen und zählen zusammen nur einmal.
D9, D10, D13, D15, D3 und D14 zählen jeweils eigenständig. Priorisiert wird nach
Evidenz-Score × Nutzen für das Projekt (Fahrleistungsmodell, Rundenzeit,
Fahrdynamik).

## D17 LLM-gestützte Hypothesenbildung aus Steckbriefen

Steckbriefe sind kompakt genug, um sie Claude gesammelt vorzulegen: „Welche
Fahrzeuggröße passt zu diesem Profil, und mit welchem Anker/Test ließe sich das
prüfen?" Das nutzt Fahrzeug-Domänenwissen für die Namensgebung.
- Nur als Hypothesen-Generator. Die LLM-Antwort ist keine Evidenz und zählt nicht
  im Evidenz-Score.
- Jede vorgeschlagene Hypothese muss einen konkreten, offline ausführbaren Test
  mitbringen (Anker, Maske, erwartetes Vorzeichen/Skala). Hypothesen ohne Test
  werden verworfen.

## Aufwand/Nutzen-Übersicht

| Technik | Aufwand | Braucht Referenz? | Typischer Ertrag |
|---|---|---|---|
| D1 Inventur | gering | nein | Priorisierung, Duplikate |
| D2 Feldtypen | mittel | nein | Grenzen, Counter/CRC raus |
| D3 Community-DBCs | gering | nein | viele Kandidaten auf einmal |
| D15 Init/Lampentest | gering | nein | Warnleuchten-/Statusbits, Sentinels |
| D13 Log-Kontinuität | gering | nein | Zähler, Füllstand, Temperaturen |
| D9 Clock-Skew | mittel | nein | Sender-Gruppen als Kontext |
| D4 Korrelationsnetz | mittel | bekannte Signale | funktionale Gruppen |
| D6 Ereignis-Katalog | mittel | Nutzer sichtet | Schalter, Abläufe |
| D5 Erklärbarkeit | mittel | bekannte Signale | Hypothesen für Dynamic-Felder |
| D14 Externe Daten | mittel | externe Daten | Außentemp., Tank, Licht |
| D10 Kausalität/Soll-Ist | hoch | bekannte Signale | Moment-/Regelgrößen, Eingriffe |
| D11 Spektral/Kohärenz | mittel | teils | rhythmische Funktionen, Bandzuordnung |
| D12 Symb. Regression | hoch | D5-Auswahl | Formeln, Skalen |
| D7 Multiplex | gering | nein | „verrauschte" IDs erschließen |

Empfehlung: zuerst die billigen, referenzfreien Techniken (D1, D2, D3, D13, D15,
D7), dann D9 und D4 bis D6, zuletzt D10 bis D12 gezielt für die spannendsten
Kandidaten.

## Pipeline

1. D1 Inventur (über den ganzen Bestand; bei neuen Logs aktualisieren).
2. D7 Multiplex-Check für auffällige IDs.
3. D2 Feldtypen + CAN-D-Grenzen/Signedness + Counter/CRC (SKILL.md Schritt 1).
4. D3 Community-DBC-Abgleich.
5. D15 Init/Lampentest und D13 Log-Kontinuität (referenzfrei, alle Logs).
6. D9 Sender-Gruppen.
7. D4 Korrelationsnetz → D5 Erklärbarkeit → bei Bedarf D10/D11/D12.
8. D6 Ereignis-Katalog → Nutzer sichtet. D14 für passende Kandidaten.
9. D16 Steckbriefe + Evidenz-Score → optional D17 → Register.
10. Beste Hypothesen → gezielter Modus (Anker, Kalibrierung, Nulltest, Cross-Log).
11. Register (`can-open-fields.md`)/DBC aktualisieren → Datalake und Logbuch
    (SKILL.md Schritt 11).

## Kandidaten-Register

Das Register ist `docs/status/can-open-fields.md` (in-place gepflegt):
- **Teil A** neu identifiziert: Signal, ID/Bits, Formel, Beleg, Status.
- **Teil B** offen: beobachtetes Verhalten, Hypothese, was zur Klärung fehlt.
- **Teil C** Fahrzeugtests (pflegt der Nutzer, dieser Skill ergänzt dort nichts
  ungefragt).

Discovery-Hypothesen gehen erst nach Teil B, nicht direkt in die Master-DBC. Je
Eintrag mitschreiben: ID/Bits/Endianness/Signedness, Typ, Hypothese, Quellen
(Dxx-Liste), Evidenz-Score, beste Referenz mit Score, Nulltest, geprüfte Logs.
Verworfene Hypothesen bleiben mit Grund stehen (bzw. im Logbuch), damit sie nicht
erneut untersucht werden.

Status wie in der DBC: offen (nur Register) → `_maybe`/`_related` (in der DBC) →
ohne Suffix = bestätigt (SKILL.md Schritte 9–10). Rest-Budget-Zahlen im Kopf von
`can-open-fields.md` nach jeder Runde mit `can_open_fields.py` aktualisieren.

## Quellen

- Lin et al., *ByCAN*, arXiv:2408.09265 (2024): Byte-/Bit-Features, DBSCAN, DTW.
- Buscemi et al., *CANMatch*, IEEE TVT 2021; *Multiplexed CAN Frames*, VehicleSec 2024.
- Kulandaivel et al., *CANvas*, USENIX Security 2019: Sender-Zuordnung per
  Clock-Offset-Tracking.
- Cho & Shin, *CIDS* (Clock-Skew-Fingerprinting von ECUs), USENIX Security 2016.
- Ruan et al., *PicaCAN*, IEEE TMC 2025: Semantik über physikalisch induzierte
  Kausalität (Methodendetails nicht im Volltext geprüft; D10 ist eine eigene
  Umsetzung der Idee).
- Verma et al., *CAN-D*, IEEE TVT 2021; Young et al. 2020 (ID-Clustering).
- Community: berumiya/CAN_DBC_6thGenMazda, timurrrr/RaceChronoDiyBleDevice
  (`mazda_mx5_nd.md`), majbthrd/MazdaCANbus, iDoka/awesome-automotive-can-id.
