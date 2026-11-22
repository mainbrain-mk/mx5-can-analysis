# PCM-Bits vollständig zuordnen: Konzept

**Stand 2026-11-22 abends (Konzept v3; A1/A2 erledigt, Strang B gebaut und getestet, nicht deployt, Stufe −1).** Verlauf: [`logs/can-bus-status.md`](../logs/can-bus-status.md) „PCM-Bits vollständig zuordnen: Start (2026-11-22)“. Ziel: jedes Bit in den Botschaften des
Motorsteuergeräts (PCM) auf dem HS-CAN ist zugeordnet und belegt, oder nachweislich als Zähler,
Prüfsumme, Konstante oder Reserve eingeordnet. Ergebnisse gehen fortlaufend in
[`logs/can-bus-status.md`](../logs/can-bus-status.md) (Abschnitte „PCM-Bits …“), der Ist-Stand in
[`status/can-open-fields.md`](../status/can-open-fields.md) und in die DBC.

**Randbedingungen (Nutzer, 22.11.):**
- Der Pi arbeitet im Auto **ohne Kontakt**: kein SSH, kein Eingriff, keine Handgriffe des Fahrers.
- **Nichts Bestehendes geht verloren**: Dash, Logging, TPMS, Öl, Lambda/ETC/MAF/Klopfen,
  Handy-Dongle. *(Nachtrag 22.11.: Laut Nutzer hängt seit einiger Zeit kein OBD-Adapter mehr am Bus, der Pi fragt alles selbst ab. Der Fremd-Tester-Schutz bleibt als Absicherung, z. B. gegen einen Werkstatt-Tester; eine fremde Anfrage in einem Log ist damit ein Befund, kein Normalfall.)*
- Die Aufgaben sind in Häppchen geteilt, die sich **in den Fahrten eines Tages im Hintergrund**
  abarbeiten und bei jeder Fahrt dort weitermachen, wo die letzte aufgehört hat.
- Die Auswertung läuft in der täglichen Aufgabe **„process-new-mx5-logs“**. Dort ist auch der
  Ort für Rückfragen und Zwischenergebnisse an den Nutzer.
- **Zeit ist da. Genauigkeit und Gründlichkeit gehen vor Schnelligkeit.** Keine Zuordnung ohne
  Bestätigung an Daten, die bei der Hypothesenbildung noch nicht vorlagen.

## Ausgangslage (Inventur 22.11., `can_open_fields.py` neu gerechnet, 132 Logs)

24 IDs tragen in der DBC den Absender `HS_PCM`, 23 davon kommen auf dem Bus vor (0x040 nie). **A1 (22.11.):** alle 23
laufen nachweislich auf dem PCM-Quarz, auch 0x4D4/0x4D9/0x4DA/0x4FE; keine weitere ID. Unbelegte, variierende Bits
vor A2 (in ≥ 10 % der Logs):

| ID | offen | Charakter | ID | offen | Charakter |
|---|---|---|---|---|---|
| 0x0FD | 11 | Flags, 4-Bit-Zähler | 0x420 | 9 | Flags |
| 0x130 | 9 | i-stop-Zustände | 0x42B | 32 | Zustandsbytes (≈ 0x4FA) |
| 0x165 | 13 | Gang/Kupplung, Byte7 Wert | 0x4D4 | 34 | Eco-/i-DM-Werte (Absender zweifelhaft) |
| 0x166 | 2 | Zustand | 0x4D9 | 5 | G-Wechsel-Bewertung (Absender zweifelhaft) |
| 0x167 | 10 | Zähler, Zustand | 0x4DA | 48 | Kat-Modell? |
| 0x200 | 12 | Lastgröße | 0x4FA | 26 | Temperatur-/Lastkomp., Prüfsumme |
| 0x202 | 8 | Byte7 Wert | 0x4FE | 48 | Verlaufstabelle (Absender zweifelhaft) |
| 0x20A | 20 | Gemisch/Schub | 0x21F | 39 | MAP-ähnlich, 25-Bit-Wert |

Summe **~326 offene Bits**, dazu ~15 Signale mit `_maybe`/`_related`. 0x040, 0x0FE, 0x4D1,
0x4D5–7, 0x4DC und 0x4F0 haben keine offenen variierenden Bits mehr. Auch die schon „bestätigten“
PCM-Signale werden einmal gegen die neuen Anker gegengeprüft. Ein Widerspruch dort wäre ein Befund.

## Beweisregeln (gelten für jede Zuordnung)

Das Projekt hat schon mehrfach Scheinbefunde gesehen: Öltemperatur gegen Kilometerstand (zwei
monoton steigende Größen), vertauschte Anker im Sweep, Zeitbasis-Fehler, signed/unsigned. Daraus
folgt:

1. **Stufen:** *Kandidat* (Rechner findet etwas) → *Hypothese* (von mir geprüft, mit Datum und
   konkreter Vorhersage ins Hypothesenregister eingetragen) → *bestätigt* (Vorhersage trifft an
   **Holdout-Logs** an ≥ 3 Tagen, darunter ein Kaltstart). Holdout = Logs, die für die Herleitung
   nicht benutzt wurden. Ist ein Befund aus einer Suche über viele Logs entstanden (Korrelations-
   Sweeps), zählen nur Logs, die **nach** dem Eintragsdatum aufgezeichnet wurden. Eine Hypothese, die an neuen Daten scheitert, bleibt mit Begründung im Register
   stehen und wird nicht still verworfen.
2. **Exakt vor Korrelation:** PCM-Broadcasts sind oft bitgleiche Kopien interner Größen. Zuerst
   wird gegen Rohwert-Gleichheit nach Zeitabgleich getestet (Anteil identischer Werte). Eine
   Regression mit r zählt nur, wenn es keine exakte Abbildung gibt, und dann mit Skala/Offset,
   die zu einer runden Auflösung passen.
3. **Gegen Scheinkorrelation:** Korrelation auch auf den **Differenzen** (Änderungen), nicht nur
   auf den Pegeln. Trends und Fahrzeit als Störgrößen herausnehmen. Der Versatz (Lag) muss über
   die Logs stabil sein. Bei ~300 Feldern × einigen hundert DIDs entstehen zigtausend Vergleiche,
   daher reicht ein guter Median nicht: der Treffer muss in **jedem** Log mit genug Variation tragen.
4. **Bitbreite und Wertebereich** des Feldes müssen zum Anker passen (kein 8-Bit-Feld für eine
   Größe, die im Anker 12 Bit Auflösung zeigt). Vorzeichen, Endianness und Überlauf explizit prüfen.
5. **Physikalisch plausibel:** Einheit, Größenordnung und Verhalten in Grenzfällen (Leerlauf,
   Schub, Volllast, Kaltstart, i-stop) müssen passen.
6. **Abtastung:** Jede Anker-DID wird mit ≥ 1 Hz gepollt, sonst sind schnelle Felder nicht
   prüfbar. Für langsame Größen reicht weniger.

## Abschlusskriterium je Bit

Jedes Bit landet in genau einer Klasse, mit Beleg im Logbuch:

1. **Messwert**: Formel gegen einen unabhängigen Anker, bestätigt nach Regel 1.
2. **Zustand/Flag**: Auslöser benannt, P(Bit | Ereignis) und P(Bit | kein Ereignis), bestätigt
   nach Regel 1.
3. **Zähler/Prüfsumme**: Algorithmus bitgenau über **alle** Frames aller Logs nachgerechnet.
4. **Konstante/Reserve**: in allen Logs konstant.
5. **Rest, dokumentiert**: Verhalten beschrieben und begründet, warum ohne Werkstatt-Tool nicht
   belegbar. Ziel: Klasse 5 < 10 % der Bits. Lieber ehrlich Klasse 5 als eine schwache Zuordnung.

## Strang A: offline am PC (sofort, unabhängig vom Pi)

- **A1 Absender verifizieren**: Botschaften desselben Steuergeräts teilen Quarzdrift und
  Jitter. IDs nach Periodendrift gruppieren und die echte PCM-Menge festlegen. 0x4D4, 0x4D9 und
  0x4FE stammen vermutlich vom Kombiinstrument oder Infotainment. Ergebnis zuerst, weil es die
  Zielmenge festlegt.
- **A2 Zähler/Prüfsummen** (0x0FD, 0x167, 0x4FA u. a.): Zählerperiode bestimmen und Prüfsumme
  gegen Summe/XOR/CRC-8-Varianten testen, bitgenau über alle Frames oder verworfen.
- **A3 Natürliche Ereignisse**: Flags und Zustände gegen Kaltstart, i-stop, Schub/FuelCut,
  Volllast, Limiter, Kupplung, Lüfter, Tempomat, Tankstopp
  (`can_event_bit_diff.py`, `can_rare_bits.py`, `can_natural_events.py`).
- A-Befunde laufen nach Regel 1 ebenfalls zuerst als Hypothese ins Register. Die neuen Fahrten
  bestätigen sie dann nebenbei über die Pipeline.

## Strang B: autonom auf dem Pi (PCM-Diagnosewerte als Anker)

Namensvorschläge für Sweep-Treffer: [`pcm-community-dids.csv`](pcm-community-dids.csv) (OBDb + Torque-Liste, nur
Kandidaten, Übernahme erst nach den Beweisregeln).

Die PCM-Broadcasts sind fast immer Kopien interner Größen, die das PCM auch per Diagnose
ausgibt. Der Pi fragt diese Größen ab, die Antworten landen automatisch im candump, und die
Pipeline legt sie gegen jedes offene Feld.

**Wo es läuft: im bestehenden `tpms_poller.py`, nicht als eigener Prozess.** Auf 0x7E0/0x7E8 darf
nur ein Tester des Pi sprechen, sonst vermischen sich die Antworten mit Öl/Lambda/Klopfen. Die
Kampagne wird ein Modul `pcm_campaign.py`, das der Poller in jeder schnellen Runde mit
**höchstens einer Anfrage** bedient.

**Schutz der bestehenden Funktion (fest eingebaut):**

| Regel | Wirkung |
|---|---|
| max. 1 Kampagnen-Anfrage je schnelle Runde, Obergrenze 15/s, Zeitanteil 10 %, Wartezeit 30 ms | Lambda/ETC/MAF/Klopfen behalten ≥ 85 % ihrer Rate (gemessen 41–46/s, das Dash braucht < 10). Integrationstest: 92 % (PCM antwortet mit NRC) bzw. 87 % (PCM schweigt); mit 15 % Zeitanteil waren es nur 84 % |
| Pause, solange ein fremder Tester (Handy) aktiv ist | der Dongle-Schutz vom 26.09. bleibt unverändert, das Handy bekommt seine volle Bandbreite |
| Pause bei Pedal > 80 % und in den ersten 30 s nach Motorstart | Volllast- und Limiter-Daten bleiben ungestört, Start und i-stop-Wiederstart unberührt |
| nur bei laufendem Motor (Drehzahl aus 0x202, Socket-Filter um 0x202 erweitert) | Zündung an, Motor aus: nichts |
| nur lesende Dienste 0x22 und 0x01, keine Sitzungswechsel, kein 0x2F/0x31/0x2E | keine Eingriffe ins Steuergerät |
| Fehler im Modul: try/except, nach 20 Fehlern aus bis zur nächsten Zündung | der Poller selbst läuft weiter wie heute |
| Notaus ohne Deploy: Datei `canlogs/pcm_campaign/STOP` oder `--no-campaign` | abschaltbar, sobald der Pi zu Hause erreichbar ist |
| Backend/Dash dekodieren 0x7E8 nur nach bekannter DID | neue DIDs werden dort ignoriert (geprüft: `can_backend.py` vergleicht die DID). Grenzfall Mehrrahmen-Antwort mit Länge 0x62 wird im vcan-Test abgedeckt |

**Fortschritt überlebt jede Fahrt:** Der Zustand liegt in `canlogs/pcm_campaign/state.json` auf
dem Stick, nicht im RAM-Overlay. Er wird atomar geschrieben (tmp + fsync + rename) nach jedem
Block bzw. alle 30 s. Wird während einer Anfrage ausgeschaltet, geht höchstens der letzte Block
verloren, und der wird beim nächsten Start wiederholt.

**Freigabestufe statt Automatik bis zum Ende.** Der Pi arbeitet nur bis zu der Stufe, die in
`pcm_campaign_config.json` (im Repo, per Deploy) freigegeben ist. Die nächste Stufe gebe ich erst
frei, wenn die Pipeline die vorige als sauber ausweist. Den Schritt von der Pilotstufe zum vollen
Betrieb stimme ich vorher mit dir ab.

| Stufe | Job | Inhalt | Umfang (Motorlaufzeit) |
|---|---|---|---|
| 0 Pilot | J0 + Probeblock | Mode-01-Inventar (0x00/0x20/…/0xE0, jede unterstützte PID einmal) und **ein** DID-Block (0x1300–0x13FF, dort liegt die bekannte Öltemperatur als Selbstkontrolle) | < 5 min, 1 Fahrt |
| 1 | J1 DID-Sweep | alle Mode-22-DIDs 0x0000–0xFFFF. Zuerst die Blöcke, in denen schon Treffer liegen (03xx, 04xx, 09xx, 28xx, DAxx, F4xx, 0Cxx), dann der Rest. Gespeichert werden Antwort/NRC, Länge und Rohwert; Mehrrahmen-Antworten bekommen ein Flow-Control-Frame | ~2,5 h (PCM antwortet mit NRC, ~7/s) bis ~6 h (PCM schweigt, ~3/s) Motorlaufzeit, also mehrere Fahrtage; der Pilot misst, welcher Fall gilt |
| 2 | J2 Anker-Rotation | alle positiv antwortenden DIDs in Gruppen, so klein, dass jede DID ≥ 1 Hz bekommt. Jede Gruppe läuft 15 min, dann die nächste, rundum. Was ausgemustert wird, entscheide ich am PC (nicht der Pi) und spiele es mit der Konfiguration auf | mehrere Umläufe, ≥ 3 Tage je DID |
| 3 | J3 Bestätigung | nur die DIDs aus dem Hypothesenregister, die die Bestätigung nach Regel 1 brauchen | bis alle Hypothesen entschieden sind |

Weniger Fahrt verschiebt nur den Kalender, der Pi macht beim nächsten Start einfach weiter. Ist eine
Stufe fertig und die nächste noch nicht freigegeben, macht die Kampagne Pause.

## Auswertung im Prozess „process-new-mx5-logs“

Die Aufgabe ruft heute nur `run_daily_pipeline.py` auf und gibt den Report weiter. Für die
Kampagne kommen zwei Teile dazu, sauber getrennt:

**1. Deterministisch in `run_daily_pipeline.py` (neuer Schritt „PCM-Kampagne“, nur bei neuen CAN-Logs):**
- `canlogs/pcm_campaign/state.json` mit vom Pi holen (Sync um den Unterordner erweitern), nach
  `data/can/pcm_campaign/` versioniert je Tag.
- **Gesundheitsprüfung** je neuem Log, als Befund im Report (⚠️ bei Verletzung):
  - Raten von Lambda/ETC/MAF/Klopfen/Öl gegen den Median der 10 Fahrten davor (Warnung unter 85 %).
  - Handy-Anfragen/s und Antwortquote des Handys gegen vorher.
  - candump-Lücken und Poller-Fehler gegen vorher.
  - Kampagnen-Anfragen/s, Pausenanteile (Handy/Volllast/Start), Fehlerzähler.
- **Fortschritt:** Stufe, Anteil erledigt, Restschätzung in Motorstunden.
- **Kandidatensuche** (`pcm_campaign_eval.py`): neue Antworten aus dem candump dekodieren und
  jedes offene PCM-Feld gegen jede Anker-DID testen, nach Regel 2–4 (Gleichheit, Differenzen,
  Lag-Stabilität, Bitbreite). Ergebnis: `results/pcm_campaign/candidates_<datum>.json`, im Report
  nur eine Zeile (Anzahl neuer Kandidaten, Anzahl Hypothesen-Bestätigungen/-Widersprüche).
- **Hypothesenprüfung:** jede Hypothese im Register hat eine maschinenprüfbare Vorhersage
  (Feld, Formel, Toleranz bzw. Ereignis und Trefferquote). Die Pipeline prüft sie an jedem neuen
  Log und zählt Tage/Treffer/Verstöße mit.

**2. Mit mir in der Aufgabe (zusätzlicher Abschnitt in deren `SKILL.md`):** Nur wenn der
PCM-Abschnitt im Report etwas Neues meldet, übernehme ich in diesem Lauf:
- **Kandidaten prüfen:** jeden neuen Kandidaten von Hand gegen die Beweisregeln (Plots,
  Grenzfälle, Gegenprobe). Was trägt, kommt mit Vorhersage ins Register, was nicht trägt, mit
  Grund in die Verwurfliste. Lieber über mehrere Läufe verteilt als hastig.
- **Logbuch:** je Lauf mit Ergebnis ein datierter Abschnitt „PCM-Bits …“ in
  `logs/can-bus-status.md`: was untersucht wurde, was belegt ist, was verworfen wurde. Ändert
  sich ein Stand, auch `status/can-open-fields.md` und, erst bei *bestätigt*, die DBC.
- **Rückfragen an dich**, wenn nur du sie beantworten kannst. Am Ende der Antwort, gebündelt und
  einzeln beantwortbar. Beispiele: „Lief am 24.11. um 17:42 die Klimaanlage?“, „War die Fahrt am
  25.11. mit Beifahrer?“, „Stufe 1 freigeben?“. Deine Antwort in derselben Sitzung arbeite ich ein.
  Ohne Antwort bleibt die betroffene Hypothese offen, nichts wird angenommen.
- **Zwischenstand:** einmal pro Woche bzw. bei jedem Stufenwechsel eine kurze Bilanz (Bits je
  Klasse, Fortschritt je ID), sonst nur, wenn es etwas Neues gibt.
- **Grenzen in der Aufgabe:** keine Änderungen an der Pipeline selbst (wie bisher), kein Deploy
  auf den Pi ohne Freigabe. Neue Freigabestufen schlage ich in der Aufgabe vor und setze sie
  erst nach deinem Ja um.

Damit jeder Lauf ohne Gedächtnis weitermachen kann, liegt der Arbeitsstand in Dateien:
dieses Konzept, `results/pcm_campaign/hypotheses.json` (Register mit Datum, Vorhersage, Stand),
`results/pcm_campaign/rejected.json` (verworfene Kandidaten mit Grund) und das Logbuch.

## Vor dem ersten Deploy (zu Hause, ohne Auto)

- `pcm_campaign.py --self-test`: Zustand speichern/laden, Abbruch mitten im Block, Freigabestufe,
  Rotation, Notaus-Datei.
- Ersatz-PCM auf `vcan0`: ein kleines Antwortskript (NRC 0x31, positive Ein- und
  Mehrrahmen-Antworten, 0x78 responsePending, Schweigen, eine Mehrrahmen-Antwort der Länge
  0x62). Poller und Kampagne laufen dagegen. Geprüft wird, dass Öl/Lambda/Klopfen mit
  unveränderten Werten und ≥ 85 % Rate ankommen, das Backend nichts Falsches dekodiert und das
  simulierte Handy (fremde Anfragen auf 0x7E0) die Kampagne sofort pausiert.
- Ein echtes Log über `canplayer` in `vcan0` mit Kampagne und Backend: Dash-Werte gleich wie
  ohne Kampagne.
- Die Pipeline-Teile laufen gegen ein künstliches Log mit eingebauten DIDs, deren Zuordnung
  bekannt ist. Erwartet wird, dass die Kandidatensuche sie findet und eine eingebaute
  Scheinkorrelation (zwei monotone Größen) **nicht** als Kandidat meldet.
- Auf dem Pi zu Hause: Testlauf gegen `vcan0`, danach `git status` leer, Dienste ohne Fehler,
  Laufzeitstatus in `status/pi-runtime-state.md` nachgetragen.

## Umsetzungsstand (22.11.)

| Teil | Stand |
|---|---|
| A1 Absender | erledigt, 23 PCM-IDs bestätigt (Register H-A1-*) |
| A2 Zähler/Prüfsummen | erledigt: 4 Prüfsummen, 6 Zähler (Schritt 0/1/2 im Rechentakt), 0x4FA-Multiplex + Zustandsbit; in der DBC (Register H-A2-*) |
| A3 natürliche Ereignisse | offen |
| `pcm_campaign.py` + Poller | gebaut, Selbst- und Integrationstest grün, Konfiguration Stufe −1 (aus) |
| `vcan0`-Test auf dem Pi | offen (Pi war im Auto) |
| Pipeline-Schritt `pcm_campaign_eval.py` | gebaut, an echten Logs gelaufen; Vergleichsbasis steht |
| Kandidatensuche (Felder gegen DIDs) | offen, vor Freigabe Stufe 2 |
| Aufgabe „process-new-mx5-logs“ | Schritt 5 ergänzt |
| Pilot-Deploy | 22.11. abends nach Freigabe des Nutzers: `0cce4fe` auf dem Pi, Stufe 0 aktiv; vorher Integrationstest auf dem Pi (vcan9) bestanden |
| Pilotfahrt | 22.11. 19:55, fertig nach 72 s; Gesundheit sauber; PCM antwortet unbekannten DIDs mit NRC 0x31, ~13,5/s, Stufe 1 also ~80 min Motorlauf; Kandidaten 0xA4 (Ist-Gang) und 0xA6 (km) |
| Stufe 1 | 22.11. spät nach Freigabe des Nutzers deployt (`a58710b`), beginnt bei DID 0x0300 |

## Bewusst weggelassen

- **Aktive Ansteuerung** (UDS 0x2F, Lüfter/Klima schalten): nicht nötig, solche Zustände kommen
  in normalen Fahrten von selbst vor, und ohne Fahrerkontakt wäre es nicht vertretbar.
- **Andere Steuergeräte** (DSC/EPS): nicht Teil dieses Ziels. Das Modul kann später einfach
  einen anderen Header bekommen.
