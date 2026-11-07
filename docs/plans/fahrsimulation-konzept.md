# Konzept: möglichst echte Simulation einer MX-5-Fahrt (2026-11-07)

Ziel: Eine simulierte Fahrt ist von einer echten kaum zu unterscheiden, in den Zeitreihen, in den
Auswertungen und auf dem Dash. Grundlage sind nur die vorhandenen Daten, ohne neue Messfahrten.
Noch ist nichts gebaut. Die Zahlen unten stammen aus einer Machbarkeitsprobe am Datalake, Herleitung
im Logbuch `logs/projekt-stand.md`, „Fahrsimulation: Machbarkeitsprobe“.

## Der Kern in fünf Punkten

1. **Fahrer im geschlossenen Kreis statt Abspielen der Pedale.** Gemessene Pedale durch die Physik zu
   schicken, trägt nicht: Das Tempo läuft weg, schon nach 60 s um typisch 9 km/h. Echte Fahrer gleichen
   das laufend aus, also braucht die Simulation einen Fahrer, der einem Sollwert folgt.
2. **Die Strecke wird vorgegeben, nicht gekoppelt.** Aus Lenkwinkel und Tempo weicht die Position nach
   60 s um 104 m ab (Median). Der Ort kommt aus der Route, der Lenkwinkel wird daraus zurückgerechnet.
3. **Physik für die Grobform, echte Schnipsel für die Feinform.** Schaltungen und Anfahrten werden nicht
   hergeleitet, sondern aus den aufgezeichneten Vorgängen gezogen und angepasst.
4. **Ausgabe als echtes candump-Log.** Vorteil: Dash, Vcan-Simulation und alle Auswertungen laufen ohne
   Umbau darauf, und Simulation und Realität lassen sich mit denselben Werkzeugen vergleichen.
5. **Die Messlatte ist die Streuung echter Fahrten.** Dieselbe Straße fährt sich jedes Mal anders. Die
   Simulation gilt als gut genug, wenn sie von einer echten Fahrt nicht weiter weg liegt als zwei echte
   Fahrten voneinander. Exakter geht es nicht, denn der Verkehr steht in keinem Log.

## Datenlage

| Bestand | Umfang |
|---|---:|
| CAN-Fahrten | 119 Logs, rund 2930 km, 45 h, je Log bis 69 Kanäle bei bis zu 100 Hz |
| davon mit GPS | 53 Logs, rund 1730 km |
| Handy-Logs (OBD, GPS, IMU), überlappen teils mit CAN | 111 Logs, rund 3740 km |
| Ereignisse | 1570 Kurven, 1028 Bremsungen, 249 ausgewertete Hochschaltungen |
| Umfeld | 154 DGM-Kacheln plus Brücken, Windarchiv, Tankbuch, Massen je Log, Tempolimit- und Spurkrümmung aus der Kamera |

Das Rohmaterial ist da. Es fehlen Verkehr, Fahrbahn-Oberfläche und Fahrten am Reifenlimit (siehe „Grenzen“).

## Was die Probe gezeigt hat

94 CAN-Logs ab 5 min, je zur Hälfte Lern- und Prüfdaten, im 10-Hz-Raster. Die Trennung lief abwechselnd
nach Log, nicht nach Tag. Die Werte sind also Richtwerte, Stufe 0 misst sie sauber nach.

| Teilmodell | Ergebnis | Urteil |
|---|---|---|
| Drehzahl aus Tempo und Gang (Kupplung zu) | Fehler p90 12 min⁻¹ (Gang 1: 83, Gang 6: 8) | sehr gut |
| Gierrate aus Lenkwinkel und Tempo | r = 0,98, RMSE 0,8 °/s (Modell aus denselben Logs gefittet, LOO laut Status 1,2 °/s) | sehr gut |
| Verbrauch aus Moment und Drehzahl (Willans-Gerade, ohne Schub) | R² 0,87, RMSE 0,46 g/s (nur im Fit, nicht kreuzvalidiert) | gut |
| Kühlwasser beim Kaltstart (ein Knoten, Prüfdaten, n = 27) | RMSE Median 3,7 °C, p90 7,8 °C (Fit noch grob, ein Term mit falschem Vorzeichen) | Größenordnung passt, Modell neu ansetzen |
| Gang aus dem Tempo allein | 82 % Treffer, nur 7 % mehr als einen Gang daneben | brauchbarer Startwert |
| Beschleunigung, einstufig (Antriebsphase) | Streuung 0,19 m/s² | gut, aber siehe nächste Zeile |
| **Tempo, freier Lauf aus gemessenem Moment und Bremsdruck** | 60-s-Fenster: Endfehler p50 8,9 km/h, p90 27 km/h; ganze Fahrt: RMSE 18 km/h | **trägt nicht** |
| dito, mit gelerntem Restmoment und Bremskennlinie | 60 s: p50 6,8, p90 18 km/h; ganze Fahrt: RMSE 14 km/h | besser, trägt weiter nicht |
| **Position, Koppelnavigation aus dem Lenkmodell** | 60 s (1335 m Weg): p50 104 m, p90 719 m. Mit gemessener Gierrate: 27 / 89 m | **trägt nicht** |

Warum das Tempo wegläuft: 1 % Vollmoment (2 N·m) macht im 4. Gang 0,024 m/s², nach 60 s also 5 km/h.
Bei konstanter Fahrt heben sich große Kräfte fast auf, kleine Reste summieren sich. Das ist keine
Schwäche des Modells im engeren Sinn. Die gleiche Rechnung gilt für jedes offene Abspielen.

Zwei Befunde fließen direkt in die Modelle der Simulation ein:

- **Momentkanal mal Volllastkurve ist noch nicht die Kraft am Rad.** Median der Abweichung als
  Motormoment: −20 N·m bei gemeldetem Moment nahe 0, ±4 N·m im Mittelfeld, +30 N·m über 160 N·m.
  Steigung und Wind sind hier nicht herausgerechnet.
- **Die Bremse ist nicht proportional.** 1 bar ≈ 380 N, 12 bar ≈ 1780 N, 23 bar ≈ 3340 N. Eine Gerade
  erklärt nur R² 0,65.

## Aufbau

```
 Route                  Fahrer                 Fahrzeug                 Ausgabe
 s -> Ort, Krümmung,    Sollwert v(s),         Antrieb, Bremse,         Kanäle wie im Datalake
 Steigung, Limit,  -->  Pedale, Gang,     -->  Quer, Thermik,      -->  (100 Hz, echte Raten)
 Haltepunkte            Lenkung  <-- v, a --   Verbrauch, Elektrik      -> candump-Log
                           ^                                               |
              Fahrtprofil, Schnipsel                               Dash, Pipeline, Vergleich
```

Bewusst keine neue Modellwelt: Alles Vorhandene wird importiert, nichts kopiert. Vorschlag sind drei
Dateien, `drive_sim.py` (Route, Fahrer, Fahrzeug), `drive_sim_can.py` (candump-Ausgabe) und
`drive_sim_score.py` (Vergleich). Die Rundenzeit-Simulation bleibt unberührt, sie beantwortet eine andere
Frage (Bestzeit statt typische Fahrt).

### Fahrzeug

| Baustein | Grundlage | Zu tun |
|---|---|---|
| Moment | `torque_nm`, Teillastkennfeld (`partial_load_model.py`), Luftdichte | Restmoment-Tabelle ergänzen, dabei Steigung und Wind herausrechnen |
| Pedal zu Drosselklappe | Mediane je (APP, Drehzahl), Verfahren wie am 02.10. (`datalake_channels.py`) | Tabelle aus den Logs neu bilden |
| Längsdynamik | `effective_mass`, CdA, Crr, Steigung (`get_elevation_along_track`), Wind (`wind_at`) | zusammenführen |
| Schub | `ActualEnginePercentTorque_CAN` negativ, `FuelCut_CAN` | Moment je Drehzahl fitten, statt der gangunabhängigen Tabelle aus `engine_braking_analysis.py` |
| Bremse | `BrakePressure_CAN`, 1028 Ereignisse | Kennlinie F(p) neu, Grenze je Reifensatz |
| Kupplung, Schalten, Anfahren | `shift_traction_gap_analysis.py`, Launch 26.09. | Schnipsel-Bibliothek, siehe Fahrer |
| Quer | Lenkmodell (k1, k2) rückwärts: Lenkwinkel aus Krümmung und Tempo | Verzögerung der Gierantwort ergänzen |
| Räder | `WheelSpeed_CAN_1–4` | Tempo plus 2,9-%-Rahmen, Kurveninnen-Differenz, gemessenes Rauschen |
| Verbrauch | Willans-Gerade, 0x420 (4985 Schritte/l) | kreuzvalidieren, Zähler und Tankbuch fortschreiben |
| Gemisch, Luft | MAF, MAP, Lambda-Regel (APP > 90 % → ca. 0,85) | Kennfelder je Drehzahl und Last |
| Thermik | Kühlwasser wie in der Probe | Öl und Ansaugluft mit gleichem Ansatz, Katalysator grob |
| Elektrik, i-stop, TPMS | Spannungen, 0x4DB, `iStop_Stopped_CAN` | einfacher Zustandsautomat, sonst Verläufe aus Schnipseln |

Die Fahrzeugmasse kommt wie immer aus Tankbuch und Beifahrersitz. Die Geschwindigkeit bleibt im
`VehicleSpeed`-Rahmen, in dem das Modell kalibriert ist. Nur für den Ort entlang der Route wird mit dem
Faktor 0,971 auf die wahre Strecke umgerechnet.

### Fahrer

Der Fahrer ist der schwierigste Teil, denn er bestimmt den Eindruck der ganzen Fahrt.

- **Fahrtprofil statt Einzelparameter.** Kurventempo, Bremshärte, Schaltdrehzahl und Pedaleinsatz hängen
  innerhalb einer Fahrt zusammen. Pro Simulation wird deshalb das Kennwerte-Paket einer echten Fahrt
  gezogen (Bootstrap), kein Satz unabhängiger Zufallszahlen. So gibt es den ruhigen Arbeitsweg und die
  zügige Runde, ohne dass jemand sie definieren muss.
- **Sollwert v(s):** das Minimum aus freiem Wunschtempo (Tempolimit mal persönlichem Faktor, auf bekannten
  Straßen das Tempo der früheren Fahrten), Kurventempo v(R) aus den 1570 Kurven und Haltepunkten.
- **Vorausschau:** Der Bremsbeginn folgt aus der Distanz zum nächsten Sollwert und der Verzögerung des
  Fahrtprofils (real p50 0,15 g, p95 0,29 g).
- **Regler:** Vorsteuerung aus der Fahrzeugkraft plus PI-Anteil, Reaktionszeit und Pedalrate aus den
  gemessenen Pedalverläufen. Damit hebt der Regler auch die Modellreste auf, die den freien Lauf ruinieren.
- **Gangwahl:** Gang aus Tempo und Last aus der gemessenen Verteilung, mit Schaltdrehzahl aus dem Fahrtprofil.
- **Schnipsel:** Eine Schaltung (Pedal, Kupplung, Drehzahlabfall, Zwischengas) kommt als echter
  Verlauf aus den Logs, ausgewählt nach Gangpaar und Last und auf das aktuelle Tempo skaliert. Auch die
  Anfahrt wird so gezogen, das fehlende Launch-Modell ist dafür also kein Hindernis.
- **Verkehr:** Halte- und Bremsstellen je Straßenzelle aus den früheren Fahrten (Häufigkeit, Dauer). Sonst
  zufällige Ereignisse mit gemessener Rate je Straßentyp. Verkehr wird also nur statistisch nachgebildet,
  nicht vorhergesagt.

### Route

Stufe 1 und 3 nehmen die GPS-Spur einer echten Fahrt: Krümmung aus der geglätteten Spur, Steigung aus dem
DGM, Tempolimit aus `SpeedLimitSign_CAN`. Ob 1-Hz-GPS für die Krümmung reicht, prüft Stufe 1 gegen
`YawRate_CAN`. Eine beliebige neue Route aus OSM kommt erst mit Stufe 4, die Abfrage dafür gibt es im
Projekt schon (`fetch_osm_bridges`, Spreewaldring).

Bekannte Straßen sind begrenzt: Nur 35 % der GPS-Kilometer liegen auf 100-m-Zellen mit mindestens 3 Fahrten,
23 % auf Zellen mit mindestens 10 (rund 28 km Straße). Der Fahrer muss also auch ohne Streckengedächtnis
funktionieren, dort greift der Rückfall auf Limit und v(R).

### CAN-Ausgabe

Im Probelog tragen 103 IDs rund 2900 Frames/s. Alle IDs stehen im DBC, aber nur mit 242 Signalen, viele
Bytes sind ungedeutet. Frames frei zu erzeugen hieße daher, diese Bytes zu erfinden.

Der Vorschlag ist ein **Trägerlog**: Eine echte Fahrt liefert Raten und alle unbekannten Bytes, nur die
modellierten Signale werden per DBC überschrieben. UDS-Antworten (Öl, Klopfen, Lambda) laufen genauso.

**Trennung von echten Daten, zwingend:** Pipeline und Dash-Liste greifen nach `candump-*`. Synthetische
Logs heißen deshalb `simdump-*` und liegen in einem eigenen Ordner (Pi: `canlogs/sim/`), die Sim-Liste
des Dash bekommt diesen Ordner zusätzlich. So kann ein Simulationslog nie im Datalake, in einem Modellfit
oder im Reverse Engineering landen. Vergleichsläufe bekommen einen eigenen temporären Datalake.

## Stufen und Prüfkriterien

Die Schwellen sind Vorschläge und werden in Stufe 0 nachgeschärft. Jede Stufe liefert für sich etwas
Brauchbares, nach jeder lässt sich aufhören.

**Stufe 0: Prüfstand (klein).** Feste Prüffahrten, getrennt nach Tag und in Lernfahrten nie enthalten. Darauf
`drive_sim_score.py`: Zeitreihen nach Strecke ausgerichtet, Verteilungen (Tempo, Längs- und Querbeschleunigung,
Drehzahl, Gänge, Pedal, g-g-Diagramm) und Kennzahlen je km (Schaltungen, Halte, Verbrauch). Dazu die
**Messlatte:** echte Fahrt gegen echte Fahrt auf denselben Straßen, je Richtung getrennt. Die Probe nennt
hier 15,6 km/h Tempostreuung je Zelle (Median), allerdings noch richtungsgemischt, also eher zu hoch.

**Stufe 1: Fahrzeug-Kopie (mittel).** Der Regler folgt dem echten v(s) derselben Fahrt, Gänge wie gemessen. Das
prüft das Fahrzeug ohne die Unsicherheit des Fahrermodells.
Bestanden, wenn gegen die echte Fahrt gilt: Moment RMSE unter 20 N·m, Drehzahl p90 unter 50 min⁻¹,
Gierrate unter 2 °/s, Verbrauch je Fahrt ±3 %, Kühlwasser unter 5 °C.

**Stufe 2: CAN-Ausgabe (mittel).** Stufe-1-Fahrt als `simdump`-Log. Bestanden, wenn die Dekodierung die
Werte zurückliefert (Round-Trip-Test), das Dash auf `vcan0` ohne Sonderfall läuft (Pi, vorhandener
Sim-Knopf) und die Auswertungsskripte im temporären Datalake ohne Änderung durchlaufen.

**Stufe 3: Eigener Fahrer (groß).** Nur Route und Fahrtprofil vorgegeben, je Prüffahrt mehrere Läufe.
Bestanden, wenn die echte Fahrt innerhalb der simulierten Streuung liegt (Fahrzeit, Verbrauch, Schaltungen
und Halte je km) und der Abstand zur echten Fahrt nicht über der Messlatte aus Stufe 0 liegt. Zusatzprobe:
Lassen sich echte und simulierte Ausschnitte in den Auswertungsbildern auseinanderhalten?

**Stufe 4: Neue Situationen (optional).** Beliebige OSM-Route, andere Masse, anderes Fahrtprofil,
Spreewaldring. Hier wird nur noch extrapoliert, die Ergebnisse sind Abschätzungen.

## Grenzen

- **Verkehr und Ampeln** stehen nicht in den Daten. Eine bestimmte Fahrt lässt sich daher nicht vorhersagen,
  nur eine plausible Fahrt derselben Art erzeugen.
- **Grenzbereich bleibt außen vor.** Es gibt 4 ABS-Bremsungen und einen Übersteuerfall, alle auf NS-R2, auf
  den Winterreifen keine belastbare Grenze. Die Simulation deckelt auf die gemessene Hülle. Schleudern,
  TCS und ABS werden höchstens als Ereignis nachgespielt, nicht berechnet.
- **Kein Wanken, Nicken, keine Federung:** kein CAN-Signal dafür, nur alte Handy-IMU-Daten. Vertikale
  Schwingung fehlt also, die Radordnung ließe sich ergänzen.
- **Schwache Ränder bleiben schwach:** Volllast unter 4000 und über 7000 min⁻¹, Gang 6 (gemessen/Modell
  rund 1,14), CdA und η nur gekoppelt.
- **Reifensätze:** Parameter stammen überwiegend von den NS-R2, die aktuelle Prüfung läuft auf Winterreifen.
  Der Regler verdeckt solche Fehler teilweise. Deshalb zählen in Stufe 1 Moment und Verbrauch, nicht das Tempo.
- **Spreewaldring:** keine eigene Fahrt dort, also nicht prüfbar. Höhendaten sind seit 07.11. da, die
  Strecke ist fast eben (1,4 m Spanne).

## Entscheidungen

| Frage | Empfehlung | Warum |
|---|---|---|
| Wofür vor allem? (Dash und Pipeline testen, Modelle prüfen, Was-wäre-wenn, später selbst fahren) | Modelle prüfen und Dash testen, also Stufen 0 bis 2 zuerst | Größter Nutzen bei kleinstem Risiko, Stufe 3 entscheidet sich danach an echten Zahlen |
| Tempo-Rahmen (offener Punkt 9 im Performance-Status) | für die Simulation nicht umstellen | Kein Neufit nötig, Faktor nur bei Route und GPS-Kanal |
| Welcher Reifensatz? | beide als eigene Parametersätze, geprüft zuerst auf Winterreifen | Das ist das aktuelle Auto, NS-R2 hat dafür die meisten Daten |
| Dürfen Modellbefunde (Restmoment, Bremse) in die übrigen Modelle zurück? | zunächst nur in der Simulation | Regel im Status: Diagnosen ändern Parameter nicht automatisch |
