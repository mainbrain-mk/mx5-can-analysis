# Mazda MX-5 ND RF G184: aktueller Kenntnisstand

**Dokumentstand:** 29.08.2026  
**Zweck:** Portierbare technische Zusammenfassung des im Agenten dokumentierten Modell- und IMU-Kenntnisstands.  
**Abgrenzung:** Das aktive Fahrleistungsmodell ist mit Stand 21.08.2026 dokumentiert. Die IMU-Diagnosemethode und die Schwingungsanalyse wurden bis 27.08.2026 fortgeschrieben. Diagnosebefunde ändern Fahrzeugparameter nicht automatisch.

## 1. Kurzfazit

- Das Fahrleistungsmodell verwendet einen dynamischen Radradius von **0,2985 m**, eine Achsübersetzung von **2,866**, eine Referenzmasse von **1180,705 kg**, **CdA = 0,647 m²**, einen gekoppelten Antriebswirkungsgrad von **0,93**, **Crr = 0,013** und **ρ = 1,18 kg/m³**.
- Für maximale Fahrleistung gelten gangindividuelle ATTACK-Zugkraftunterbrechungen von **0,15 / 0,15 / 0,17 / 0,23 / 0,25 s** für 1→2 bis 5→6. Der frühere Pauschalwert **0,41 s** bleibt Diagnosewert, ist aber nicht mehr Default.
- Fahrer-WOT wird nicht allein über eine offene Drosselklappe erkannt. **APP > 90 %** kennzeichnet den Bereich oberhalb der haptischen Pedalraste; APP, ETC, Lambda, MAF, Drehzahl, Geschwindigkeit und Kupplung sind gemeinsam zu prüfen.
- Die Volllastkurve ist im Bereich **4250 bis 6750 min⁻¹** inter-log plausibilisiert. Niedrige Drehzahlen sowie Bereiche oberhalb 7000 min⁻¹ bleiben schwächer abgesichert.
- Die IMU-Methode **MX5_IMU_METHOD_V1.0** ist eine Diagnosemethode. Gierachse und Vertikalrichtung sind hoch plausibilisiert; die absolute Längs- und Querskalierung bleibt diagnostisch.
- Für die Halterungsresonanz ist **20,629 Hz** nur ein fahrtspezifisch zu prüfender Startkandidat. Aktuell ist **kein Notch-Filter freigegeben**. Die Logs vom 27.08.2026 lieferten keine geeigneten stabilen Segmente und wurden als **NOT_TESTABLE** eingestuft.

## 2. Statuslogik

| Status | Bedeutung im aktuellen Arbeitsstand |
|---|---|
| VALIDiert / aktive Primärevidenz | Für das jeweilige Modell- oder Diagnoseziel aktiv verwendbar |
| Plausibilisiert | Durch Daten gestützt, aber nicht zwingend unabhängig identifiziert |
| Modellannahme | Arbeitswert ohne vollständige empirische Trennung |
| Diagnostisch | Für Sichtung und Vergleich nutzbar, nicht für automatische Parameteränderung |
| Interpoliert | Zwischenwert ohne ausreichende direkte Messstützung |
| NOT_TESTABLE | Datensatz enthält keine ausreichenden Prüfsegmente; weder Bestätigung noch Widerlegung |

## 3. Aktive Fahrzeugparameter

| Parameter | Symbol | Wert | Einheit | Status / Hinweis |
|---|---:|---:|---|---|
| Referenzmasse | m | 1180,705 | kg | verwendet; einzelne Logs dürfen eigene Massenannahmen tragen |
| Dynamischer Radradius | r_dyn | 0,2985 | m | aktiv, datenbasiert plausibilisiert |
| Historischer Radradius | r_dyn,alt | 0,2997 | m | abgelöst, als Referenz erhalten |
| Reifen-Referenzradius | r_ref | 0,2997 | m | verwendet |
| Achsantrieb | i_final | 2,866 | 1 | validiert |
| Antriebswirkungsgrad | η | 0,93 | 1 | gekoppelt plausibilisiert, nicht unabhängig identifiziert |
| Luftwiderstandsfläche | CdA | 0,647 | m² | gekoppelt plausibilisiert, nicht unabhängig identifiziert |
| Referenz-Luftdichte | ρ | 1,18 | kg/m³ | Modellannahme |
| Rollwiderstandsbeiwert | Crr | 0,013 | 1 | Modellannahme |
| Erdbeschleunigung | g | 9,81 | m/s² | Konstante |
| Drosselklappe vollständig offen | ETC_open | ca. 86 | ° | validiert, aber kein alleiniger WOT-Nachweis |
| Haptische Pedalraste | APP_raste | 90 | % | aktive Nutzerdefinition |

## 4. Antriebsstrang und Übersetzungen

| Gang | Gangübersetzung | Gesamtübersetzung mit i_final = 2,866 | Status |
|---:|---:|---:|---|
| 1 | 5,087 | 14,579342 | validiert |
| 2 | 2,991 | 8,572206 | validiert |
| 3 | 2,035 | 5,832310 | validiert |
| 4 | 1,594 | 4,568404 | validiert |
| 5 | 1,286 | 3,685676 | validiert |
| 6 | 1,000 | 2,866000 | validiert |

### Schaltmodell

| Wechsel | ATTACK-Zugkraftunterbrechung | Verwendung |
|---|---:|---|
| 1→2 | 0,15 s | Default für maximale Fahrleistung |
| 2→3 | 0,15 s | Default für maximale Fahrleistung |
| 3→4 | 0,17 s | Default für maximale Fahrleistung |
| 4→5 | 0,23 s | Default für maximale Fahrleistung |
| 5→6 | 0,25 s | Default für maximale Fahrleistung |

Weitere Diagnosegrößen:

- Pauschale effektive Momentunterbrechung: **0,41 s**, als Default abgelöst.
- Vollständiger Schalttransient: **ca. 0,62 s**. Nicht zusätzlich zu 0,41 s addieren.
- Median Kupplungsaktivität: **0,49 s**.
- Median Nachstabilisierung: **0,11 s**.
- Gang-0-Dauer, Kupplungsdauer, mechanischer Gesamttransient und Zugkraftunterbrechung sind nicht gleichzusetzen.

## 5. Motor-, Last- und WOT-Modell

### Arbeitsregeln

1. **APP < 90 %:** Lambda liegt typischerweise nahe 1,00.
2. **APP > 90 %:** In passenden thermisch stabilen Lastzuständen erfolgt Anreicherung in Richtung Lambda ca. 0,85.
3. **ETC ≈ 86°:** Bedeutet offene Drosselklappe, nicht automatisch Fahrer-WOT.
4. **WOT-Klassifikation:** APP, ETC, Lambda, MAF, Drehzahl, Geschwindigkeit und Kupplungszustand gemeinsam bewerten.
5. Warmlauf, Übergänge, Schub und besondere Regelzustände müssen separat gefiltert werden.

### Volllast-Drehmomentkurve

| Drehzahl [min⁻¹] | Drehmoment [N·m] | Einordnung |
|---:|---:|---|
| 1000 | 115 | Prüfbedarf |
| 1500 | 140 | Prüfbedarf |
| 2000 | 165 | Prüfbedarf |
| 2500 | 185 | Prüfbedarf |
| 3000 | 198 | Prüfbedarf |
| 3500 | 203 | Prüfbedarf |
| 4000 | 205 | Randzone |
| 4500 | 204 | inter-log plausibilisiert |
| 5000 | 202 | inter-log plausibilisiert |
| 5500 | 198 | inter-log plausibilisiert |
| 6000 | 193 | inter-log plausibilisiert |
| 6500 | 189 | inter-log plausibilisiert |
| 7000 | 184 | Randzone |
| 7200 | 178 | schwach abgesichert |
| 7400 | 172 | schwach abgesichert |
| 7500 | 169 | schwach abgesichert |

Das Teillastkennfeld bleibt numerisch unverändert. Die **ETC-60°-Zeile ist ausdrücklich interpoliert**; stabile APP/ETC/Lambda/MAF-Segmente zur direkten Validierung fehlen.

## 6. Fahrleistungs- und Hochgeschwindigkeitsmodell

- CdA und Wirkungsgrad sind nur **gekoppelt** plausibilisiert. Eine unabhängige Identifikation beider Größen steht aus.
- Eine 100–200-km/h-Auswertung darf nicht als primäre Kalibrierreferenz dienen, wenn GPS/OBD zeitlich versetzt sind oder ein Grenzpunkt in einer Schaltung liegt.
- Ein Messwert von **222 km/h** wurde im Modellstand als plausibel eingeordnet, weil zusammenhängende Phasen über 215 km/h vorlagen.
- Historische Simulationsergebnisse von **0–100 km/h 6,29 s**, **100–200 km/h 19,95 s**, **0–200 km/h 26,24 s** und **Vmax 229,9 km/h** sind Entwicklungshistorie, keine aktuellen Messwerte und kein automatisches Kalibrierziel.

## 7. IMU-Methode MX5_IMU_METHOD_V1.0

### Zeitraster und Altersgrenzen

| Größe | Wert |
|---|---:|
| Ausgaberaster | 50 Hz |
| Zeitschritt | 0,02 s |
| Beschleunigung maximal alt | 0,05 s |
| Rotation maximal alt | 0,35 s |
| OBD-Kontext maximal alt | 0,65 s |
| GPS-Kontext maximal alt | 1,60 s |

Das 50-Hz-Raster ist eine gemeinsame Ausgabetaktung. Daraus darf keine native 50-Hz-Messrate für OBD oder GPS abgeleitet werden.

### Achstransformation Smartphone → Fahrzeug

```text
a_long = +0.954017155*AccelerationX
         -0.007262847*AccelerationY
         +0.299664011*AccelerationZ

a_lat  = -0.299740966*AccelerationX
         -0.031697045*AccelerationY
         +0.953493918*AccelerationZ

a_vert = -0.002573383*AccelerationX
         +0.999471134*AccelerationY
         +0.032416497*AccelerationZ

yaw_right = -RotationRateY
```

Einordnung:

- `yaw_right`: hoch plausibilisiert gegen GPS-Kursrate.
- Vertikalachse: hoch plausibilisiert über Gravitationsrichtung, nahe Smartphone +Y.
- `a_long` und `a_lat`: diagnostisch; absolute Skalierung noch nicht validiert.
- `a_lat_LP4` darf nicht als Haftgrenze verwendet werden.
- `a_long_LP4` darf keine automatische Drehmoment- oder Massenanpassung auslösen.

### Filterpipeline

| Kanal | Filter | Parameter | Freigabe |
|---|---|---|---|
| Beschleunigung, Fahrdynamik | Butterworth-Tiefpass | 4. Ordnung, 4 Hz, nullphasig offline | aktiv |
| Gier | Butterworth-Tiefpass | 4. Ordnung, 2 Hz, nullphasig offline | aktiv |
| Halterungsresonanz | IIR-Notch | f0 fahrtspezifisch, Startkandidat 20,629 Hz, Q=15 | nur bei CONFIRMED oder SHIFTED |

Rohdaten und Residuen sind für Rückverfolgbarkeit und Qualitätskontrolle aufzubewahren. Residuen sind nicht als Fahrzeugdynamik zu interpretieren.

## 8. Resonanz- und Schwingungsanalyse

### Prüfverfahren

Geeignete 2-s-Referenzfenster erfordern:

- Geschwindigkeit ≥ 20 km/h
- Betrag des Geschwindigkeitstrends ≤ 0,5 km/h/s
- Geschwindigkeitsspanne ≤ 2 km/h
- Betrag der GPS-Gierrate ≤ 1,5 °/s
- konstanten Gang

Zusammenhängende Segmente müssen mindestens 8 s dauern. Danach wird je Achse eine Welch-PSD berechnet und das Band **19,0 bis 22,5 Hz** geprüft.

### Aktueller Befund

- Legacy-Log 25.08.2026: 8 stabile Segmente, 20,629 Hz als Referenzkandidat.
- Logs 26.08.2026: 15 stabile Segmente, 74 überlappende 4-s-Fenster und 30 Filterversuche. Arbeitsklassifikation: 7 `WEAK_OR_BROAD`, 7 `MODERATE_CANDIDATE`, 1 `STRONG_CANDIDATE`.
- Trotz einzelner Kandidaten ist kein Notch freigegeben; produktive Störsubtraktion bleibt gesperrt.
- Log 27.08.2026 08:16:20: 0 stabile Segmente, Status `NOT_TESTABLE`.
- Log 27.08.2026 17:03:39: 0 stabile Segmente, Status `NOT_TESTABLE`.
- Die Methode blieb unverändert; es erfolgte keine Fahrzeugparameteränderung.

## 9. Kurven- und Ereignislogik

| Klasse | Primärregel | Zusatzbedingung / Hinweis |
|---|---|---|
| Geradeaus | `abs(yaw_right_LP2) < 2 °/s` | GPS-Kursrate plausibel; 2-s-Fenster |
| Rechtskurve | `yaw_right_LP2 > 3 °/s` | GPS-Kursrate > 0 |
| Linkskurve | `yaw_right_LP2 < -3 °/s` | GPS-Kursrate < 0 |
| Zügige Kurve | `abs(a_lat_ref) ≥ 0,12 g` | alternativ v ≥ 80 km/h und abs(GPS-Gier) ≥ 4 °/s |
| Gerade Beschleunigung | `a_long_LP4 > 0,35 m/s²` | abs(yaw) < 2 °/s, Speed steigend, Bremse niedrig |
| Gerade Verzögerung | `a_long_LP4 < -0,35 m/s²` | abs(yaw) < 2 °/s; Schub, Bremse und Schaltung trennen |

Querbeschleunigungsreferenz:

```text
a_lat_ref = (GPS_speed_kmh / 3.6) * radians(GPS_yaw_rate_deg_s)
```

GPS-Ereignisse sollen nur bei horizontaler Ungenauigkeit ≤ 20 m verwendet werden.

## 10. Qualitäts- und Freigabestufen

| Stufe | Mindestanforderung | Zulässige Nutzung |
|---|---|---|
| CANDIDATE | ≥ 1 gültiges 2-s-Fenster | Sichtung |
| PLAUSIBLE | ≥ 2 gleichgerichtete Fenster | qualitativer Vergleich |
| USABLE | ≥ 3 Fenster und Dauer ≥ 6 s | Kurvenvergleich |
| CALIBRATION_REFERENCE | langes sauberes Ereignis, hohe Gierkorrelation, gute GPS-Genauigkeit, Wiederholung in weiterem Log | Methodenvalidierung nach manueller Freigabe |
| REJECTED | unplausibel, Datenlücke oder schlechte Referenz | keine Modellwirkung |

Auch `USABLE` erlaubt keine automatische Fahrzeugparameteränderung.

## 11. Bekannte Daten, Annahmen und Grenzen

### Bekannte bzw. aktiv verwendete Daten

- Gang- und Achsübersetzungen.
- Gangindividuelle ATTACK-Schaltzeiten für maximale Fahrleistung.
- APP-Pedalraste bei 90 % als Nutzerdefinition.
- Gier-Vorzeichen und Vertikalrichtung der IMU.
- Methodische Altersgrenzen, Filterparameter und Ereignisregeln.

### Modellannahmen oder gekoppelte Größen

- Luftdichte 1,18 kg/m³.
- Rollwiderstand 0,013.
- CdA 0,647 m² und η 0,93 sind gekoppelt, nicht unabhängig identifiziert.
- Historischer Launch: μ_eff = 0,88, Traktionsfaktor im ersten Gang 0,90 und gemittelte Launch-Beschleunigung 5,54 m/s² gelten als starke historische Evidenz, sind aber nicht aus der aktuellen IMU-Methode neu bestätigt.
- Kupplungs-Anfahrmodell mit Exponent p = 0,5 bleibt Annahme; historischer Reibpunkt 54 % ist als Evidenz dokumentiert.

### Harte methodische Grenzen

- Keine automatische Fahrzeugparameteränderung aus IMU-Diagnosen.
- Keine künstliche native 50-Hz-Rate für OBD oder GPS behaupten.
- Kein Notch ohne erneute Resonanzprüfung pro Log.
- Keine Haftgrenze aus `a_lat_LP4`.
- Keine Drehmoment- oder Massenanpassung aus `a_long_LP4` ohne gezielte Kalibrierfahrt.
- Physikalisch unplausible 20-ms-Spitzen verwerfen bzw. über geeignete 2-s-Fenster aggregieren.

## 12. Priorisierte offene Punkte

1. **CdA und Wirkungsgrad entkoppeln**  
   Benötigt kontrollierte Hochgeschwindigkeits- oder Ausrollmessungen mit belastbaren Umwelt- und Streckendaten.

2. **Absolute IMU-Längs- und Querskalierung validieren**  
   Benötigt gezielte Geradeaus-Beschleunigungs-/Bremsfahrten sowie wiederholbare Kurven mit guter GPS-Referenz.

3. **Halterungsresonanz reproduzierbar bestätigen oder verwerfen**  
   Benötigt mehrere stabile Geradeaussegmente pro Fahrt und Wiederholung in weiteren Logs. Bis dahin kein Notch.

4. **Teillastkennfeld, insbesondere ETC = 60°, direkt messen**  
   Benötigt thermisch stabile Segmente mit APP, ETC, Lambda, MAF, Drehzahl und klarer Kupplungs-/Schaltabgrenzung.

5. **Volllastkurve an den Rändern absichern**  
   Benötigt saubere WOT-Fenster unter 4000 min⁻¹ und oberhalb 7000 min⁻¹.

6. **Multi-Rate-Synchronisation weiter absichern**  
   GPS-, OBD- und IMU-Signale kanalweise auf Zeitversatz, Altersgrenzen und Schaltgrenzpunkte prüfen.

7. **Aktuellen Modellstand und IMU-Master konsolidieren**  
   Die Fahrzeugmodell-Datei und die beiden v1.1-Schwingungsfortschreibungen sollten in einem eindeutigen, versionierten Master mit Changelog zusammengeführt werden.

## 13. Empfohlene Verwendung außerhalb des Agenten

- Dieses Dokument als lesbare Einstiegsebene nutzen.
- Für Berechnungen weiterhin die numerischen Masterdateien verwenden.
- Jede Änderung mit Datum, Ausgangswert, neuem Wert, Begründung, erwarteter Wirkung und Validierungsnachweis dokumentieren.
- Diagnoseausgaben, Annahmen und aktive Modellparameter strikt getrennt halten.
- Primärlogs unverändert archivieren; Ableitungen und Filterausgaben versionieren.

## 14. Quellen und Rückverfolgbarkeit

Diese Markdown-Datei wurde aus folgenden internen Arbeitsständen konsolidiert:

1. `MX5_Modellstand.xlsx`, aktives Fahrzeug- und Fahrleistungsmodell, Referenz-ID `turn1search52`.
2. `MX5_IMU_Auswertungsmethodik_v1.0.xlsx`, normative IMU-Diagnosemethode, Referenz-ID `turn1search54`.
3. `MX5_IMU_Schwingungsanalyse_Gesamtstand_v1.1 3.xlsx`, Fortschreibung für Log 27.08.2026 08:16:20, Referenz-ID `turn1search59`.
4. `MX5_IMU_Schwingungsanalyse_Gesamtstand_v1.1 4.xlsx`, Fortschreibung für Log 27.08.2026 17:03:39, Referenz-ID `turn1search60`.

> Hinweis: Die Referenz-IDs dienen der Herkunftsdokumentation im Agentenkontext. Außerhalb dieses Kontexts sind die Dateinamen die primären Such- und Archivschlüssel.
