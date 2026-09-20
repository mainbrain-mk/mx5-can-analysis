"""
Datalake-Aufbau: alle Logs (aktuelle .dlg + historische CSVs) in eine
gemeinsame DuckDB-Datenbank ueberfuehren (MX-5 Projekt)

Zweck: bisher hatte jedes Analyseskript seine eigene, fest verdrahtete
Vorstellung von Kanalnamen (aus den .dlg-PidNames). Die vom Nutzer
bereitgestellten historischen Logs (`data/raw_csv/`, 21 Dateien, zwei
Namenskonventionen: "YYYY-MM-DD HHMMSS.csv" und "CSVLog_YYYYMMDD_HHMMSS.csv")
haben TEILWEISE andere Spaltennamen fuer dieselben Groessen (deutsch vs.
englisch, abgekuerzt vs. ausgeschrieben) UND teils andere Einheiten
(Bremsdruck in bar statt kPa, GPS-Hoehe/-Genauigkeit in ft statt m).
Dieses Skript normalisiert alles auf ein gemeinsames Kanal-Vokabular
(im Wesentlichen die schon in den .dlg-Dateien verwendeten PidNames,
siehe Docstrings der anderen Skripte) und eine gemeinsame Einheit pro
Kanal, und schreibt das Ergebnis in ein Langformat (long/tidy) in
`data/datalake.duckdb`.

WARUM LANGFORMAT (log_id, channel, t, value) statt einer breiten Tabelle:
die Kanaele unterscheiden sich stark zwischen Logs (manche haben nur
5 Kanaele, andere >60) - ein breites Schema mit einer Spalte pro Kanal
waere extrem duennbesetzt (viele NULLs) und muesste bei jedem neuen,
bisher unbekannten Kanal per Schema-Migration erweitert werden. Long-
Format ist dafuer robust: ein neuer Kanal ist einfach ein neuer Wert in
der `channel`-Spalte, keine Schema-Aenderung noetig.

WICHTIGE DESIGNENTSCHEIDUNGEN (vom Nutzer bestaetigt bzw. hier
dokumentiert):
  - `Brake Fluid Pressure Sensor` / `Brake Fluid Line Hydraulic Pressure
    (Raw Value)` / `BFP_PRE_MZ`: identische Messgroesse (Nutzer
    bestaetigt), aber ACHTUNG unterschiedliche Einheit (bar in den CSVs,
    kPa in den .dlg-Dateien) - Kanonisch: kPa, bar wird x100 umgerechnet.
  - `Air fuel ratio` / `Actual (AFR)` / `AFR_MZ`: identisch (Nutzer
    bestaetigt) - ABER: einige fruehe CSVs (2026-08-20 bis 08-25) haben
    BEIDE Rohspalten gleichzeitig im Header (zwei parallel konfigurierte
    PIDs fuer dieselbe Groesse waehrend einer Uebergangsphase der PID-
    Config), und die zweite ist dann jeweils strukturell tot (konstant/
    kaum Werte-Varianz - z.B. `Actual (AFR)` in 2026-08-25 081538 nur 3
    Werte {0, 0.5, 1} statt echter Lambda-Werte 0.8-2.0). Betrifft auch
    `EngineRPM` (`Motordrehzahl` vs. `Engine Revolutions Per Minute`),
    `VehicleSpeed` (`Fahrzeuggeschwindigkeit` vs. `Vehicle Speed`) und
    `BFP_PRE_MZ` (`Brake Fluid Pressure Sensor` vs. `...Raw Value`).
    `ingest_csv()` behaelt pro Log+Kanal nur die Rohspalte mit den
    meisten unterschiedlichen Werten (die informativere/echte), die
    andere faellt unter UNMAPPED statt beide blind zusammenzufuehren.
  - `Tatsaechlicher Gangstatus des Getriebes` / `Transmission Actual Gear
    Status` / `TM_GEST`: der reale Gang-Index (0-6). URSPRUENGLICH wurde
    auch `Unterstuetzter tatsaechlicher Gangstatus des Getriebes` hierauf
    gemappt (vom Nutzer als "identisch" bestaetigt) - eine Pruefung der
    Datenbank widerlegt das aber: in JEDEM betroffenen Log verhaelt sich
    diese Spalte wie eine binaere "PID unterstuetzt"-Flagge (fast immer
    konstant 0 kurz nach Logstart, danach konstant 3 - unabhaengig von
    Fahrzustand/Gangwechseln), waehrend `Tatsaechlicher...` mit der
    Fahrt mitschwankt (0-6). Die zwei Rohspalten wurden bislang per
    pd.concat blind zusammengefuehrt -> zwei widerspruechliche
    Zeitreihen unter demselben Kanalnamen. Fix: `Unterstuetzter...`
    NICHT mehr auf TM_GEST mappen (landet jetzt unter UNMAPPED, siehe
    unten) - `Tatsaechlicher...` bleibt alleinige Quelle, auch wenn die
    in einzelnen Logs (2026-08-20 164326, 08-22 141753/154521) selbst
    konstant/defekt ist (siehe mx5_kinematic_outlier_log-Memo).
  - `Getriebe Tatsaechliches Uebersetzungsverhaeltnis` (ein numerisches
    Verhaeltnis wie 2.035, KEIN Gang-Index) wird NICHT mit TM_GEST
    zusammengelegt (semantisch verschieden: Verhaeltnis vs. Gang-Nummer)
    - eigener neuer Kanal `TransmissionActualGearRatio`. NICHT vom
      Nutzer explizit bestaetigt - bei Bedarf pruefen/korrigieren.
  - GPS `Hoehe`/`Horz Genauigkeit`: in den .dlg-Dateien ueber
    PidMetadataEntry als Meter bestaetigt, in den CSVs explizit "(ft)"
    - Kanonisch: Meter, ft wird x0.3048 umgerechnet.
  - Alle sonstigen Drucksensoren (Tire Pressure, Fuel Rail Pressure...)
    ebenfalls kanonisch in kPa gefuehrt (Konsistenz), auch wenn dafuer
    kein .dlg-Praezedenzfall existiert.
  - `2026-08-25 170729.csv` in `data/raw_csv/` ist NACHWEISLICH dieselbe
    Fahrt wie `data/raw/2026-08-25 170729.dlg` (identische Dauer ~2080s,
    identische Hoechstgeschwindigkeit 219 km/h) - wird beim Import
    uebersprungen (Duplikat), die .dlg-Version ist ohnehin schon voll
    aufbereitet (Vibrationsanalyse, Achsenkalibrierung etc.).
  - Zeitbasis: .dlg nutzt .NET-Ticks (absolut), die CSVs eine
    "# StartTime = MM.DD.YYYY HH:MM:SS.ffff AM/PM"-Kommentarzeile +
    "Time (sec)"-Spalte (elapsed). Beides wird auf `t_elapsed_s`
    (Sekunden seit Logstart) UND `timestamp_local` (naive lokale Zeit,
    KEINE Zeitzonenumrechnung, da beide Quellen vermutlich Geraetezeit
    ohne Zeitzoneninfo sind) vereinheitlicht.

Schema in `data/datalake.duckdb`:
  - Tabelle `logs`: log_id, source_file, source_format, start_time_local,
    duration_s, n_measurements, content_fingerprint, schema_version
  - Tabelle `measurements`: log_id, channel, channel_original, unit,
    t_elapsed_s, timestamp_local, value

INKREMENTELLER BAU (seit 2026-09-20, vorher DROP+CREATE bei jedem Lauf):
bei < 30 Logs war ein voller Neubau unproblematisch, bei inzwischen > 130
Logs (>100 Mio Messwerte, 15 GB Rohdaten) dauert er >6 Minuten und ist
bereits einmal in den Timeout der naechtlichen Pipeline gelaufen (siehe
run_daily_pipeline.py). Jeder Lauf bestimmt stattdessen die Ziel-Menge an
log_ids (billiger Dateisystem-Scan, wie vorher), vergleicht sie gegen die
in `logs.content_fingerprint`/`logs.schema_version` gespeicherten Werte und
laedt nur neue/geaenderte Logs (mtime+size-Fingerabdruck geaendert, oder
SCHEMA_VERSION wurde hochgezaehlt) tatsaechlich neu ein.

WICHTIG - das Entfernen-Aequivalent ist kein Optimierungsdetail, sondern
der eigentliche Grund, warum das frueher nicht inkrementell ging: der
Pi hat keine RTC, CAN-Logs werden deshalb gelegentlich nachtraeglich per
Kreuzkorrelation umbenannt (siehe docs/logs/can-bus-status.md, Abschnitt
"Folgefund: Umbenennen allein hat den Datalake nie mitkorrigiert" - ein
bereits real aufgetretener Bug, bei dem ein umbenanntes Log bis zum
naechsten vollen Rebuild mit falschem Zeitstempel in der DB stand). Jeder
Lauf entfernt deshalb IMMER zuerst alle log_ids aus der DB, die nicht mehr
in der aktuellen Ziel-Menge sind (Datei umbenannt/geloescht, oder
nachtraeglich als Duplikat erkannt) - unabhaengig davon, ob sonst etwas
Neues da ist. Das ist der strukturelle Ersatz fuer den alten "einfach
alles wegwerfen und neu bauen"-Schutz.

`--full` erzwingt einen kompletten Re-Ingest aller Logs (z.B. direkt nach
einer Aenderung an NAME_ALIASES/CAN_SIGNAL_MAP/Einheiten-Umrechnung, statt
SCHEMA_VERSION hochzuzaehlen). Rohdateien werden nie veraendert.

Aufruf: python build_datalake.py [--full]
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import warnings
import xml.etree.ElementTree as ET
import zoneinfo
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd

RAW_DLG_DIR = "data/raw"
RAW_CSV_DIR = "data/raw_csv"
CAN_DIR = "data/can"
DB_PATH = "data/datalake.duckdb"
RESULTS_DIR = "results"
LOCAL_TZ = zoneinfo.ZoneInfo("Europe/Berlin")

# Hochzaehlen bei jeder Aenderung an NAME_ALIASES/CAN_SIGNAL_MAP/Einheiten-
# Umrechnung/Vorzeichenkorrekturen etc. - erzwingt beim naechsten Lauf einen
# Re-Ingest ALLER Logs (sonst bleiben schon eingelesene Logs unbemerkt mit
# der alten Mapping-Logik in der DB stehen, siehe Docstring oben).
SCHEMA_VERSION = "2"

MEASUREMENT_COLUMNS = ["log_id", "source_file", "source_format", "channel",
                        "channel_original", "unit", "t_elapsed_s", "timestamp_local", "value"]
LOG_COLUMNS = ["log_id", "source_file", "source_format", "start_time_local",
               "duration_s", "n_measurements", "content_fingerprint", "schema_version"]

# Bekannte CAN-Log <-> GPS-Track-Paare (begleitender Track derselben Fahrt, siehe
# docs/logs/can-bus-status.md). Neues Paar hier ergaenzen, sobald eine weitere Fahrt mit
# CAN-Log + begleitendem GPS-Track vorliegt.
CAN_GPS_PAIRS = [
    ("candump-2026-09-11_202803.log", "20260911-202812.gpx"),
    ("candump-2026-09-12_150619.log", "20260912-150636.gpx"),
    ("candump-2026-09-12_155117.log", "20260912-155117.gpx"),  # Rueckweg, keine GPX vorhanden - CAN-only
    # Y-Splitter-Kalibrierfahrt (OBD-Fusion + CAN gleichzeitig, siehe
    # docs/logs/can-bus-status.md); GPS kommt hier vom Handy ueber OBD-Fusion
    # (eigener .dlg-log_id "2026-09-12 211851"), kein separater GPX-Track.
    ("candump-2026-09-12_211833.log", "20260912-211833.gpx"),  # keine GPX vorhanden - CAN-only
]

# Automatisch vom Pi geladene CAN-Logs (siehe run_daily_pipeline.py,
# sync_can_logs_from_pi): {can_name: gpx_name oder ""}. Ergaenzt obige
# handkuratierte Liste, ohne sie zu veraendern - ein Eintrag hier gilt nur,
# wenn can_name nicht bereits in CAN_GPS_PAIRS steht.
CAN_GPS_PAIRS_OVERRIDE_PATH = "data/can_gps_pairs.json"


def _all_can_gps_pairs():
    pairs = list(CAN_GPS_PAIRS)
    if os.path.exists(CAN_GPS_PAIRS_OVERRIDE_PATH):
        known = {c for c, _ in pairs}
        with open(CAN_GPS_PAIRS_OVERRIDE_PATH, encoding="utf-8") as f:
            for can_name, gpx_name in json.load(f).items():
                if can_name not in known:
                    pairs.append((can_name, gpx_name))
    return [(c, g) for c, g in pairs if c not in KNOWN_CAN_LOG_DUPLICATES]

# CAN-Signal (cantools-Name, aus der *_decoded.csv von can_log_parser.py) ->
# (kanonischer Datalake-Kanalname, Ziel-Einheit). Nur "sichere" (unmarkierte)
# DBC-Signale mit eindeutiger physikalischer Entsprechung zu einem bereits
# bestehenden OBD-Kanal werden dorthin gemapped (Drehzahl/Speed/Gaspedal/
# Kuehlwasser-/Ansauglufttemperatur). Alles andere bleibt unter eigenem, klar
# als "_CAN" gekennzeichnetem Namen - u.a. weil Clutch_Pedal_Position_raw
# (0-199) nicht referenzgemessen ist und BrakePressure einen bekannten, hier
# nicht korrigierten Rollover-Bug hat (siehe
# data/can/CAN_unbekannte_signale_bericht_2026-09-11.md) - beide duerfen also
# NICHT unbesehen mit den gleichnamigen OBD-Kanaelen (CPP_PER_MZ/BFP_PRE_MZ)
# gleichgesetzt werden.
CAN_SIGNAL_MAP = {
    "EngineRPM": ("EngineRPM", "rpm"),
    "VehicleSpeed": ("VehicleSpeed", "km/h"),
    "APP_Accelerator_Pedal_Position": ("APP", "%"),
    "CoolantTemp": ("EngineCoolantTemp", "°C"),
    "IAT_Sensor_No1": ("IntakeAirTemperature", "°C"),
    "BrakePressure": ("BrakePressure_CAN", "bar"),
    "Steering_Wheel_Absolute_Angle": ("SteeringAngle_CAN", "deg"),
    # VehicleSpeed_Display: siehe Dual-Column-Kommentar in ingest_can() -
    # BO_606 HS_IC, ganzzahlig gerundete Tacho-Anzeige (~4% ueber der echten
    # Geschwindigkeit), bewusst getrennt von der echten "VehicleSpeed".
    "VehicleSpeed_Display": ("DisplaySpeed_CAN", "km/h"),
    "Clutch_Pedal_Position_raw": ("ClutchPosition_CAN_raw", ""),
    "Longitudinal_Acc_Raw": ("LongitudinalAcc_CAN", "G"),
    "Lateral_Acc_Raw": ("LateralAcc_CAN", "G"),
    "YawRate_Raw": ("YawRate_CAN", "deg/s"),
    "MT_Gear_Actual": ("Gear_CAN", ""),
    "WheelSpeed_1": ("WheelSpeed_CAN_1", "km/h"),
    "WheelSpeed_2": ("WheelSpeed_CAN_2", "km/h"),
    "WheelSpeed_3": ("WheelSpeed_CAN_3", "km/h"),
    "WheelSpeed_4": ("WheelSpeed_CAN_4", "km/h"),
    "KeyState": ("KeyState_CAN", ""),
    # C001_ODO (0x40A): exakt gegen den Tacho verifiziert (siehe
    # docs/logs/can-bus-status.md), daher direkt auf den bestehenden OBD-Kanal
    # gemappt statt eigenen "_CAN"-Namen zu bekommen (wie EngineRPM/VehicleSpeed/
    # APP/CoolantTemp/IAT oben) - im Gegensatz zu Clutch/BrakePressure gibt es
    # hier keine bekannte Kalibrierungs-/Rollover-Unsicherheit.
    "C001_ODO": ("VehicleOdometerReading", "km"),
    # MAP_Manifold_absolute_pressure_sensor (0x0FD): kein "_maybe"/"_related",
    # plausibler Bereich (15-104 kPa, naehert sich bei Volllast dem unabhaengig
    # stabilen BARO-Wert ~101,6 kPa an) - aber kein bestehender OBD-MAP-Kanal
    # zum draufmappen vorhanden, daher eigener "_CAN"-Name wie Brake/Steering/Accel.
    "MAP_Manifold_absolute_pressure_sensor": ("MAP_CAN", "kPa"),
    # BARO_Barometric_pressure (0x166): kein "_maybe", plausibler und
    # tagesaktuell konsistenter Bereich (siehe docs/logs/can-bus-status.md).
    "BARO_Barometric_pressure": ("BarometricPressure_CAN", "kPa"),
    # DSC_Status (0x415): KORREKTUR (2026-09-13, Nutzer) - zeigt nur, ob das
    # System eingeschaltet/aktiv ist, NICHT einen laufenden Regeleingriff.
    # Fruehere Annahme hier war falsch (unbelegte Vermutung aufgrund des
    # Namens, nie gegen ein echtes Eingriffsereignis getestet - CAN-Logging
    # gibt es erst seit 2026-09-11, kein bestaetigter Oversteer-Fall bisher
    # mit CAN-Daten). Fuer Eingriffserkennung weiterhin ETC_ACT/Drehmoment-
    # Einbruch nutzen (siehe mx5_steering_angle-Memory), nicht dieses Signal.
    # DSC_OFF_Switch (0x09E) ist der separate, tatsaechliche Fahrer-Schalter -
    # bisher in allen Logs konstant "Not_Pressed", nicht ausgewertet.
    "DSC_Status": ("DSC_Status_CAN", ""),
    # MT_Gear_Status: siehe _derive_gear_status() weiter unten - kein direktes
    # DBC-Signal, sondern aus MT_Gear_Position + MT_Gear_Select (beide 0x165,
    # selber Zeitstempel) kombiniert, weil Position fuer Rohwert 19 die
    # Doppelbedeutung "N/1st" hat (Select disambiguiert Neutral vs. 1. Gang).
    "MT_Gear_Status": ("MT_Gear_Status", ""),
    # Fuel_Tank (0x09E): Skala per Y-Splitter-Log (2026-09-12 211851, OBD+CAN
    # gleichzeitig) gegen FLI referenzgemessen (r=0.995 nach Glaettung):
    # FLI% ~ 2.486 * raw - 0.02, siehe docs/logs/can-bus-status.md. Bisher nur eine
    # Kalibrierfahrt - Rohwert bleibt hier unkonvertiert (wie Clutch_Pedal_
    # Position_raw), Umrechnung erst anwenden wenn ueber mehrere Fahrten
    # bestaetigt.
    "Fuel_Tank": ("FuelTank_CAN_raw", ""),
    # TPMS (0x728, HS_IC_TPMS_Response, aktiver UDS-Request per tpms_poller.py,
    # kein periodischer Broadcast - siehe mx5_tpms-Memory): DBC dekodiert
    # bereits korrekt skaliert (Druck in bar, Temp mit -50 Offset trotz
    # "_maybe"-Suffix, gegen den Y-Splitter-Log gegen OBD-Referenzwerte
    # bestaetigt). Tire3=hinten links/Tire4=hinten rechts bestaetigt (siehe
    # Memory), Tire1/Tire2=Vorderachse aber Reihenfolge NOCH NICHT bestaetigt -
    # deshalb bei der Rohnummerierung 1-4 belassen statt VL/VR/HL/HR zu
    # erfinden. Nur in Logs vorhanden, in denen der Pi beim Fahren lief
    # (seit 2026-09-11, sporadisch je nach Session).
    "Tire1_Pressure": ("TirePressure_CAN_Tire1", "bar"),
    "Tire2_Pressure": ("TirePressure_CAN_Tire2", "bar"),
    "Tire3_Pressure": ("TirePressure_CAN_Tire3", "bar"),
    "Tire4_Pressure": ("TirePressure_CAN_Tire4", "bar"),
    # Ereignis-Flags aus der Fahrdynamik (2026-09-15). ABS_Active ist der lange gesuchte
    # ABS-Eingriffsindikator (0x211 HS_ABS, 50 Hz); FuelCut die Schubabschaltung
    # (0x0FD) - beide sind fuer Brems- bzw. Schleppmomentanalysen direkt relevant.
    "ABS_Active": ("ABS_Active_CAN", ""),
    "FuelCut": ("FuelCut_CAN", ""),
    # OBD-Kanaele (kein DBC-Signal, sondern Diagnose-Antworten - siehe OBD_CHANNELS in
    # can_log_parser.py). OilTemp_OBD ist der wichtigste: die Motoroeltemperatur wird nicht
    # gebroadcastet, seit 2026-09-15 pollt tpms_poller.py sie alle 10s selbst. Aeltere Logs
    # haben sie daher nur sporadisch oder gar nicht.
    "OilTemp_OBD": ("OilTemp_CAN", "°C"),
    "MassAirFlow_OBD": ("MassAirFlow_CAN", "g/s"),
    "LambdaCommanded_OBD": ("LambdaCommanded_CAN", ""),
    "TimingAdvance_OBD": ("TimingAdvance_CAN", "°"),
    "EnginePercentTorque_OBD": ("EnginePercentTorque_CAN", "%"),
    "KnockRetard_OBD": ("KnockRetard_CAN", "°"),
    "Tire1_Temp_maybe": ("TireTemp_CAN_Tire1", "°C"),
    "Tire2_Temp_maybe": ("TireTemp_CAN_Tire2", "°C"),
    "Tire3_Temp_maybe": ("TireTemp_CAN_Tire3", "°C"),
    "Tire4_Temp_maybe": ("TireTemp_CAN_Tire4", "°C"),
}
GPX_NS = {"g": "http://www.topografix.com/GPX/1/0"}
TICKS_OFFSET = 621355968000000000  # .NET-Ticks -> Unix-Referenz

# Bekannte Duplikate: CSV-Datei ist dieselbe Fahrt wie eine bereits
# vorhandene .dlg-Datei (per Stichprobe verifiziert, siehe Docstring).
KNOWN_CSV_DUPLICATES_OF_DLG = {
    "2026-08-25 170729.csv",
}

# Bekannte Duplikate unter den CAN-Logs: candump-2026-09-11_201950.log ist,
# trotz des irrefuehrenden Dateinamens, dieselbe Fahrt wie das bereits
# kuratierte candump-2026-09-12_150619.log - Ursache ist der in
# docs/logs/can-bus-status.md dokumentierte Clock-Jump (Dateiname kommt von der
# noch nicht NTP-synchronisierten Boot-Uhr, der reale Fahrtabschnitt springt
# mitten in der Datei auf 2026-09-12 15:06-15:12 - exakt das Zeitfenster von
# candump-2026-09-12_150619.log). Verifiziert per Wertevergleich (2026-09-14):
# EngineRPM in beiden Logs ist auf die Mikrosekunde identisch im
# ueberlappenden Fenster. Durch den automatischen Pi-Sync (run_daily_
# pipeline.py) waere sie sonst als "neuer" Log erneut eingelesen worden -
# gleiche Ursache/gleiches Muster wie KNOWN_CSV_DUPLICATES_OF_DLG oben.
KNOWN_CAN_LOG_DUPLICATES = {
    "candump-2026-09-11_201950.log",
}

# --- Einheiten-Umrechnung (source_unit -> canonical_unit: Faktor) ---
UNIT_CONVERSIONS = {
    ("bar", "kPa"): 100.0,
    ("ft", "m"): 0.3048,
}

# --- kanonische Einheit pro Kanal (nur wo von der Quelleinheit abweichend
#     oder zur Dokumentation) ---
CANONICAL_UNIT = {
    "BFP_PRE_MZ": "kPa",
    "Höhe": "m",
    "Horz Genauigkeit": "m",
    "TirePressureWheel1": "kPa", "TirePressureWheel2": "kPa",
    "TirePressureWheel3": "kPa", "TirePressureWheel4": "kPa",
    "FuelRailPressureCommandedA": "kPa", "FuelRailPressureA": "kPa",
}

# --- Name (ohne Einheit) -> kanonischer Kanalname ---
# Deckt alle 65 in data/raw_csv/*.csv beobachteten Spalten ab (siehe
# Docstring). Neue, bisher unbekannte .dlg-PidNames brauchen HIER keinen
# Eintrag (Identitaets-Mapping fuer .dlg, siehe ingest_dlg()).
NAME_ALIASES = {
    "Accel X": "AccelerationX", "Accel Y": "AccelerationY", "Accel Z": "AccelerationZ",
    "Beschleunigung (Grav) X": "AccelerationWithGravityX",
    "Beschleunigung (Grav) Y": "AccelerationWithGravityY",
    "Beschleunigung (Grav) Z": "AccelerationWithGravityZ",
    "Rollen": "Roll", "Pech": "Pitch",
    "Magnetometer X": "MagnetometerX", "Magnetometer Y": "MagnetometerY",
    "Magnetometer Z": "MagnetometerZ",
    "Rotationsrate X": "RotationRateX", "Rotationsrate Y": "RotationRateY",
    "Rotationsrate Z": "RotationRateZ",
    "Fahrzeuggeschwindigkeit": "VehicleSpeed", "Vehicle Speed": "VehicleSpeed",
    "Kilometerstand des Fahrzeugs": "VehicleOdometerReading",
    "Gesamter Kraftstoffverbrauch": "TotalFuelEconomy",
    "Kraftstoffpreis": "FuelRate",
    "Gesamtes CO2": "TotalCO2",
    "CO2-Fluss": "CO2Flow",
    "Sofortiger Kraftstoffverbrauch": "InstantFuelEconomy",
    "Sofortige CO2-Rate": "InstantCO2Rate",
    "Durchfluss des Luftmassenmessers": "MassAirFlowRate",
    "Gesteuertes Äquivalenzverhältnis von Kraftstoff zu Luft": "CommandEquivalenceRatio",
    "Motorkühlmitteltemperatur": "EngineCoolantTemp",
    "Motordrehzahl": "EngineRPM", "Engine Revolutions Per Minute": "EngineRPM",
    "Zündungszeitpunktverstellung für Zylinder #1": "TimingAdvance",
    "Temperatur der Ansaugluft": "IntakeAirTemperature",
    "Tatsächlicher Motor - Drehmoment in Prozent": "ActualEnginePercentTorque",
    "Actual Engine - Percent Torque": "ActualEnginePercentTorque",
    "Accelerator Pedal Position": "APP",
    "Air fuel ratio": "AFR_MZ", "Actual (AFR)": "AFR_MZ",
    "Brake Fluid Pressure Sensor": "BFP_PRE_MZ",
    "Brake Fluid Line Hydraulic Pressure (Raw Value)": "BFP_PRE_MZ",
    "Clutch Pedal Position": "CPP_PER_MZ",
    "Electronic Throttle Control Actual": "ETC_ACT",
    "Transmission Actual Gear Status": "TM_GEST",
    "Tatsächlicher Gangstatus des Getriebes": "TM_GEST",
    # "Unterstützter tatsächlicher Gangstatus des Getriebes" bewusst NICHT
    # gemappt - ist ein "PID unterstuetzt"-Flag (konstant 0/3), keine
    # echte Gangnummer, siehe Docstring oben.
    "Breite": "Breite", "Länge": "Länge", "Höhe": "Höhe", "Lager": "Lager",
    "GPS-Geschwindigkeit": "GPS-Geschwindigkeit",
    "Horz Genauigkeit": "Horz Genauigkeit",
    "Knocking Retard": "KnockingRetard",
    "actual valve timing": "ActualValveTiming",
    "Getriebe Tatsächliches Übersetzungsverhältnis": "TransmissionActualGearRatio",
    "Battery Estimated State Of Charge": "BatteryStateOfCharge",
    "Engine Oil Temperature - Estimated": "EngineOilTemp",
    "Fuel Level": "FuelLevel",
    "Tire Pressure (Wheel Unit 1)": "TirePressureWheel1",
    "Tire Pressure (Wheel Unit 2)": "TirePressureWheel2",
    "Tire Pressure (Wheel Unit 3)": "TirePressureWheel3",
    "Tire Pressure (Wheel Unit 4)": "TirePressureWheel4",
    "Katalysatortemperatur (Bank 1  Sensor 1)": "CatalystTemp",
    "Spannung des Steuermoduls": "ControlModuleVoltage",
    "Unterstützung von Daten des Kraftstoffdruckregelsystems": "FuelPressureControlSupported",
    "Befohlener Kraftstoffverteilerrohrdruck A": "FuelRailPressureCommandedA",
    "Druck des Kraftstoffverteilerrohrs A": "FuelRailPressureA",
    "Temperatur des Kraftstoffverteilers A": "FuelRailTemperatureA",
}

HEADER_RE = re.compile(r"^(.*?)(?:\s*\(([^()]*)\))?$")

# Sonderfaelle, bei denen die letzte Klammer KEINE Einheit ist, sondern
# Teil des Namens (generische "letzte Klammer = Einheit"-Heuristik
# schlaegt hier fehl) - direktes Mapping des kompletten Rohnamens.
RAW_HEADER_OVERRIDES = {
    "Actual (AFR)": ("AFR_MZ", ""),
}


def parse_header_cell(raw):
    """'Brake Fluid Line Hydraulic Pressure (Raw Value) (bar)' ->
    ('Brake Fluid Line Hydraulic Pressure (Raw Value)', 'bar')."""
    raw = raw.strip()
    m = HEADER_RE.match(raw)
    name, unit = m.group(1).strip(), (m.group(2) or "").strip()
    return name, unit


def convert_value(value, source_unit, canonical_unit):
    if not source_unit or not canonical_unit or source_unit == canonical_unit:
        return value
    factor = UNIT_CONVERSIONS.get((source_unit, canonical_unit))
    if factor is None:
        return value  # keine bekannte Umrechnung noetig/verfuegbar
    return value * factor


def _fingerprint(paths):
    """mtime+size ueber alle Dateien, die zu einem Log gehoeren (bei CAN-Logs
    auch die _decoded.csv, da can_log_parser.py sie bei DBC-Fixes neu
    erzeugt). Aendert sich einer davon, gilt das Log als geaendert."""
    parts = []
    for p in paths:
        st = os.stat(p)
        parts.append(f"{os.path.basename(p)}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def ingest_dlg(path):
    log_id = os.path.splitext(os.path.basename(path))[0]
    conn = sqlite3.connect(path)
    df = pd.read_sql_query("""
        SELECT pde.Time AS raw_time, pme.PidName AS channel, pde.Value AS value
        FROM PidDataEntry pde
        LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
        ORDER BY pde.Time ASC
    """, conn)
    conn.close()
    df["timestamp_local"] = pd.to_datetime((df["raw_time"] - TICKS_OFFSET) / 10, unit="us")
    t0 = df["timestamp_local"].min()
    df["t_elapsed_s"] = (df["timestamp_local"] - t0).dt.total_seconds()
    df["channel_original"] = df["channel"]
    df["unit"] = df["channel"].map(CANONICAL_UNIT)
    df["log_id"] = log_id
    df["source_file"] = os.path.basename(path)
    df["source_format"] = "dlg"
    return df[["log_id", "source_file", "source_format", "channel", "channel_original",
               "unit", "t_elapsed_s", "timestamp_local", "value"]]


def ingest_csv(path):
    log_id = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8-sig") as f:
        start_line = f.readline().strip()
        header_line = f.readline()
    m = re.search(r"StartTime\s*=\s*(.+)", start_line)
    start_dt = pd.to_datetime(m.group(1).strip(), format="%m.%d.%Y %I:%M:%S.%f %p") if m else None

    raw_cols = [c.strip() for c in header_line.strip().split(",")]
    parsed = [parse_header_cell(c) for c in raw_cols]

    df = pd.read_csv(path, encoding="utf-8-sig", skiprows=1)
    df.columns = raw_cols
    time_col = raw_cols[0]  # "Time (sec)"

    unmapped = set()
    records = []
    for raw_col, (name, unit) in zip(raw_cols[1:], parsed[1:]):
        if raw_col in RAW_HEADER_OVERRIDES:
            canonical, unit = RAW_HEADER_OVERRIDES[raw_col]
        else:
            canonical = NAME_ALIASES.get(name)
            if canonical is None:
                unmapped.add(raw_col)
                canonical = f"UNMAPPED:{name}"
        target_unit = CANONICAL_UNIT.get(canonical, unit)
        series = pd.to_numeric(df[raw_col], errors="coerce")
        valid = series.notna()
        if not valid.any():
            continue
        sub = pd.DataFrame({
            "t_elapsed_s": df.loc[valid, time_col].astype(float),
            "value": convert_value(series[valid].astype(float), unit, target_unit),
        })
        sub["channel"] = canonical
        sub["channel_original"] = raw_col
        sub["unit"] = target_unit
        records.append(sub)

    if unmapped:
        warnings.warn(f"{os.path.basename(path)}: unbekannte Spalten (nicht gemappt): {sorted(unmapped)}")

    # Manche fruehen CSVs haben zwei Rohspalten, die per NAME_ALIASES auf
    # denselben Kanal zeigen (zwei parallel konfigurierte PIDs fuer dieselbe
    # Groesse), die sich am selben Zeitstempel widersprechen. Pd.concat
    # wuerde beide blind zusammenfuehren -> zwei widerspruechliche
    # Zeitreihen unter einem Kanalnamen. Pro Kanal nur die Rohspalte mit
    # den meisten unterschiedlichen Werten behalten (die informativere/
    # echte - die andere ist typischerweise ein toter/konstanter PID-Slot),
    # der Rest wird UNMAPPED statt verworfen (bleibt sichtbar/pruefbar).
    by_channel = {}
    for sub in records:
        by_channel.setdefault(sub["channel"].iat[0], []).append(sub)
    records = []
    for canonical, group in by_channel.items():
        if canonical.startswith("UNMAPPED:") or len(group) == 1:
            records.extend(group)
            continue
        group.sort(key=lambda s: s["value"].nunique(), reverse=True)
        best, rest = group[0], group[1:]
        for sub in rest:
            raw_col = sub["channel_original"].iat[0]
            warnings.warn(f"{os.path.basename(path)}: {canonical} hat mehrere Rohspalten - "
                           f"behalte '{best['channel_original'].iat[0]}' ({best['value'].nunique()} "
                           f"unterschiedliche Werte), verwerfe '{raw_col}' ({sub['value'].nunique()} "
                           f"unterschiedliche Werte, wirkt defekt/konstant)")
            sub = sub.copy()
            sub["channel"] = f"UNMAPPED:{raw_col}"
            records.append(sub)
        records.append(best)

    out = pd.concat(records, ignore_index=True) if records else pd.DataFrame(
        columns=["t_elapsed_s", "value", "channel", "channel_original", "unit"])
    out["timestamp_local"] = (start_dt + pd.to_timedelta(out["t_elapsed_s"], unit="s")
                               if start_dt is not None else pd.NaT)
    out["log_id"] = log_id
    out["source_file"] = os.path.basename(path)
    out["source_format"] = "csv"
    return out[["log_id", "source_file", "source_format", "channel", "channel_original",
                "unit", "t_elapsed_s", "timestamp_local", "value"]]


def log_start_epoch(can_log_path):
    """Erste candump-Zeitzeile lesen ('(1789151283.217727) can0 ...') - candump
    schreibt Unix-Epoch (UTC), NTP-synchronisiert ueber den Pi - direkt mit den
    ebenfalls UTC-basierten GPX-Zeitstempeln vergleichbar, kein manueller
    Offset noetig. None, falls die Datei keine einzige candump-Zeile enthaelt
    (z.B. eine Session, die sofort nach Start wieder endete - kommt seit dem
    automatischen Pi-Sync in run_daily_pipeline.py vor, siehe docs/logs/can-bus-status.md).

    ABER: der Pi hat keine RTC. Faehrt er ohne NTP los, sind Dateiname UND
    Frame-Zeitstempel gleichermassen falsch (bisher 3x passiert, siehe
    docs/logs/can-bus-status.md). Die Korrektur besteht im Projekt darin, die Datei
    auf die per Kreuzkorrelation ermittelte wahre Startzeit umzubenennen - der
    Dateiname ist damit die verlaesslichere Quelle als die Frames. Weichen
    beide um mehr als eine Minute voneinander ab, gewinnt deshalb der Name.
    Ohne das blieb der Datalake nach so einer Umbenennung still falsch
    (candump-2026-09-14_163711/173057 standen dort bis 2026-09-15 unter dem
    alten 13.09.-Zeitstempel)."""
    with open(can_log_path) as f:
        first_line = f.readline()
    try:
        frame_epoch = float(first_line.split(")", 1)[0].strip("("))
    except ValueError:
        return None
    m = re.search(r"candump-(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})",
                  os.path.basename(can_log_path))
    if not m:
        return frame_epoch
    name_epoch = datetime(*(int(g) for g in m.groups()), tzinfo=LOCAL_TZ).timestamp()
    if abs(name_epoch - frame_epoch) <= 60:
        return frame_epoch
    print(f"  Hinweis: {os.path.basename(can_log_path)} - Frame-Zeitstempel weichen "
          f"{(name_epoch - frame_epoch) / 3600:+.2f}h vom Dateinamen ab (Pi-Uhr ohne NTP); "
          f"Dateiname gewinnt.")
    return name_epoch


def _derive_gear_status(raw_decoded):
    """MT_Gear_Position (0x165) hat fuer Rohwert 19 die Doppelbedeutung "N/1st"
    (DBC-Choices: 19->N/1st, 11->2nd, 7->3rd, 6->4th, 5->5th, 3->6th).
    MT_Gear_Select (dieselbe Botschaft, also exakt derselbe Zeitstempel)
    disambiguiert das (1=InGear, 2=Neutral). Rohwerte ausserhalb der
    Choices-Tabelle (Schalthebel zwischen zwei Gassen waehrend eines
    Gangwechsels, ~4% der Samples) werden ebenfalls als N gewertet - naeher an
    "kein Gang eingelegt" als an irgendeinem der sechs Gaenge. Numerisch
    codiert (0=N, 1-6=Gang), analog zum KeyState-Pattern oben."""
    pos = raw_decoded[raw_decoded["signal"] == "MT_Gear_Position"][["t", "value"]].rename(columns={"value": "pos"})
    sel = raw_decoded[raw_decoded["signal"] == "MT_Gear_Select"][["t", "value"]].rename(columns={"value": "sel"})
    m = pd.merge(pos, sel, on="t", how="inner")
    if m.empty:
        # pos/sel sind leer -> "value" wurde beim rename() weg-projiziert,
        # ist im leeren m also gar nicht erst vorhanden (kein Tippfehler,
        # erst durch sehr kurze CAN-Logs ohne Gang-Frames aufgefallen, seit
        # dem automatischen Pi-Sync in run_daily_pipeline.py).
        return pd.DataFrame(columns=["t", "signal", "value"])
    gear_by_position = {"2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6}

    def resolve(row):
        if row.sel == "Neutral":
            return 0
        if row.pos == "N/1st":
            return 1
        return gear_by_position.get(row.pos, 0)

    m["value"] = m.apply(resolve, axis=1)
    m["signal"] = "MT_Gear_Status"
    return m[["t", "signal", "value"]]


def ingest_can(can_log_path, decoded_csv_path, t0_epoch):
    """CAN-Log in den Datalake einlesen. Nutzt die von can_log_parser.py bereits
    erzeugte *_decoded.csv (Long-Format t/can_id/message/signal/value) statt
    erneut >1 Mio Rohframes zu dekodieren - die CSV wird ohnehin schon fuers
    CAN-Reverse-Engineering gepflegt (siehe docs/logs/can-bus-status.md)."""
    log_id = os.path.splitext(os.path.basename(can_log_path))[0]
    t0_local = datetime.fromtimestamp(t0_epoch, tz=LOCAL_TZ).replace(tzinfo=None)

    raw_decoded = pd.read_csv(decoded_csv_path)
    # DBC-Dual-Column-Bug (analog ingest_csv()s NAME_ALIASES-Fund, siehe
    # mx5_build_datalake_dual_column_bug-Memory): das Signal "VehicleSpeed"
    # existiert ZWEIMAL in der DBC - BO_514 HS_PCM (echte, feinaufgeloeste
    # Fahrzeuggeschwindigkeit, 0.01 km/h/count, seit jeher der genutzte Kanal)
    # UND BO_606 HS_IC (auf ganze km/h gerundete TACHO-ANZEIGE, systematisch
    # ~4% hoeher - vgl. docs/logs/can-bus-status.md "0x25E ... Anzeige-
    # Geschwindigkeit"). Ohne Trennung landen beide unter demselben
    # Signalnamen im Datalake und werden beim Interpolieren wild
    # durcheinandergemischt (auffaellig geworden als 227/236-Sprung-Artefakt
    # in einer Kurvenanalyse, 2026-09-13). HS_IC-Variante vor dem Mapping
    # umbenennen, damit sie ihren eigenen Kanal bekommt statt die echte
    # VehicleSpeed zu verfaelschen.
    is_display_speed = (raw_decoded["signal"] == "VehicleSpeed") & (raw_decoded["message"] == "HS_IC")
    raw_decoded.loc[is_display_speed, "signal"] = "VehicleSpeed_Display"
    gear_status = _derive_gear_status(raw_decoded)
    decoded = pd.concat([raw_decoded, gear_status], ignore_index=True)
    decoded = decoded[decoded["signal"].isin(CAN_SIGNAL_MAP)].copy()
    # can_log_parser.py dekodiert mit cantools-Default decode_choices=True, d.h.
    # Signale mit VAL_-Tabelle (KeyState, DSC_Status) kommen als Enum-String
    # statt Zahl - wuerde sonst beim Zusammenfuehren mit den rein numerischen OBD-Logs
    # die gesamte "value"-Spalte in der Datenbank zu VARCHAR machen (inkl. dann
    # falscher lexikographischer statt numerischer MIN/MAX ueber ALLE Logs!).
    # Direkt auf den DBC-Rohcode zurueckmappen, alles andere zur Sicherheit hart
    # numerisch erzwingen (nicht-numerische Ausreisser rausfiltern).
    decoded["value"] = decoded["value"].replace(
        {"OFF": 0, "ACC": 1, "ON": 2, "START": 3, "Off": 0, "On": 1})
    decoded["value"] = pd.to_numeric(decoded["value"], errors="coerce")
    decoded = decoded.dropna(subset=["value"])
    # WheelSpeed_1..4 (HS_ABS): raw 0xFFFF ("Sensor ungueltig", klassischer CAN-
    # Sentinelwert) dekodiert nach der DBC-Formel (raw*0.01-100) zu genau 555,35
    # km/h - physikalisch unmoeglich. Selten (<0,02% der Samples in diesem Log),
    # aber eindeutig kein Messwert -> rausfiltern statt falsche Extremwerte in
    # den Datalake zu schreiben.
    is_wheel_speed = decoded["signal"].str.startswith("WheelSpeed_")
    decoded = decoded[~(is_wheel_speed & (decoded["value"] > 300))]
    # Lateral_Acc_Raw/YawRate_Raw (RCM, 0x75/0x76): DBC nutzt SAE-Konvention
    # (+ = links), waehrend im Rest dieses Projekts (steering_lateral_model.py,
    # grip_estimation.py, corner_event_analysis.py) durchgaengig + = Rechtskurve
    # gilt. Per Y-Splitter-Log (candump-2026-09-12_211833 + 2026-09-12 211851.dlg,
    # gleichzeitige CAN+OBD-Aufzeichnung, siehe can_lateral_validation.py)
    # eindeutig bestaetigt: beide Kanaele muessen negiert werden, um mit dem
    # bereits validierten OBD-Lenkwinkelmodell uebereinzustimmen (r=0.91/0.95
    # nach Vorzeichenkorrektur, slope~1.0/0.78). Hier korrigiert, damit der
    # Datalake ueberall dieselbe Vorzeichenkonvention hat.
    is_sign_flip = decoded["signal"].isin(["Lateral_Acc_Raw", "YawRate_Raw"])
    decoded.loc[is_sign_flip, "value"] = -decoded.loc[is_sign_flip, "value"]
    mapped = decoded["signal"].map(CAN_SIGNAL_MAP)
    decoded["channel"] = mapped.map(lambda x: x[0])
    decoded["unit"] = mapped.map(lambda x: x[1])
    decoded["channel_original"] = decoded["signal"]
    decoded["t_elapsed_s"] = decoded["t"]
    decoded["timestamp_local"] = t0_local + pd.to_timedelta(decoded["t_elapsed_s"], unit="s")
    decoded["log_id"] = log_id
    decoded["source_file"] = os.path.basename(can_log_path)
    decoded["source_format"] = "can"
    return decoded[["log_id", "source_file", "source_format", "channel", "channel_original",
                     "unit", "t_elapsed_s", "timestamp_local", "value"]]


def ingest_gps(gpx_path, log_id, t0_epoch):
    """Begleitenden GPS-Track (BasicAirData-GPX) unter demselben log_id wie das
    zugehoerige CAN-Log einlesen. Nur Felder uebernehmen, die die GPX tatsaechlich
    liefert (lat/lon/ele/speed) - kein Hoehen-/Genauigkeits-Ersatzwert erfunden."""
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    rows = []
    for trkpt in root.findall(".//g:trkpt", GPX_NS):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        ele_el = trkpt.find("g:ele", GPX_NS)
        time_el = trkpt.find("g:time", GPX_NS)
        speed_el = trkpt.find("g:speed", GPX_NS)
        t_utc = datetime.strptime(time_el.text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        rows.append((
            t_utc.timestamp() - t0_epoch, lat, lon,
            float(ele_el.text) if ele_el is not None else None,
            float(speed_el.text) * 3.6 if speed_el is not None else None,  # m/s -> km/h
        ))
    wide = pd.DataFrame(rows, columns=["t_elapsed_s", "Breite", "Länge", "Höhe", "GPS-Geschwindigkeit"])
    t0_local = datetime.fromtimestamp(t0_epoch, tz=LOCAL_TZ).replace(tzinfo=None)
    wide["timestamp_local"] = t0_local + pd.to_timedelta(wide["t_elapsed_s"], unit="s")

    long_parts = []
    for channel, unit in [("Breite", "deg"), ("Länge", "deg"), ("Höhe", "m"), ("GPS-Geschwindigkeit", "km/h")]:
        sub = wide[["t_elapsed_s", "timestamp_local", channel]].rename(columns={channel: "value"}).dropna()
        sub["channel"] = channel
        sub["channel_original"] = channel
        sub["unit"] = unit
        long_parts.append(sub)
    out = pd.concat(long_parts, ignore_index=True)
    out["log_id"] = log_id
    out["source_file"] = os.path.basename(gpx_path)
    out["source_format"] = "gps"
    return out[["log_id", "source_file", "source_format", "channel", "channel_original",
                "unit", "t_elapsed_s", "timestamp_local", "value"]]


def _build_can_target(can_name, gpx_name):
    """Bestimmt (log_id, Fingerabdruck-Pfade, ingest-Funktion) fuer ein
    CAN(+GPS)-Paar, oder None wenn das Log gar nicht als Ziel infrage kommt
    (Dateien fehlen / leere Session). Die eigentliche Dekodierung passiert
    erst beim Aufruf der ingest-Funktion (nur fuer tatsaechlich neue/
    geaenderte Logs), das Membership-Kriterium selbst ist billig (Dateien
    vorhanden? erste candump-Zeile lesbar?)."""
    can_path = os.path.join(CAN_DIR, can_name)
    decoded_csv_path = os.path.splitext(can_path)[0] + "_decoded.csv"
    gpx_path = os.path.join(CAN_DIR, gpx_name) if gpx_name else None
    if not (os.path.exists(can_path) and os.path.exists(decoded_csv_path)):
        print(f"CAN-Log oder *_decoded.csv fehlt, uebersprungen: {can_name}")
        return None
    log_id = os.path.splitext(can_name)[0]
    t0_epoch = log_start_epoch(can_path)
    if t0_epoch is None:
        print(f"CAN-Log ohne eine einzige candump-Zeile (leere Session), uebersprungen: {can_name}")
        return None
    has_gpx = bool(gpx_path and os.path.exists(gpx_path))
    paths = [can_path, decoded_csv_path] + ([gpx_path] if has_gpx else [])

    def ingest():
        print(f"lade (can): {can_name}")
        combined = ingest_can(can_path, decoded_csv_path, t0_epoch)
        source_file, source_format = can_name, "can"
        if has_gpx:
            print(f"lade (gps): {gpx_name}")
            gps_df = ingest_gps(gpx_path, log_id, t0_epoch)
            combined = pd.concat([combined, gps_df], ignore_index=True)
            source_file, source_format = f"{can_name} + {gpx_name}", "can+gps"
        else:
            print(f"kein begleitender GPS-Track gefunden fuer: {can_name}")
        if combined.empty:
            # kein Signal aus CAN_SIGNAL_MAP im Log enthalten (z.B. eine sehr
            # kurze Session mit nur wenigen, uninteressanten Frames) und kein
            # GPS-Track - nichts zum Einlesen. ponytail: so ein Log hat nie
            # einen logs-Eintrag (kein Fingerabdruck zum Vergleichen) und wird
            # dadurch bei jedem Lauf erneut dekodiert und verworfen - bei den
            # seltenen/kleinen Faellen, in denen das vorkommt, vernachlaessigbar.
            print(f"CAN-Log traegt keine bekannten Signale bei, uebersprungen: {can_name}")
            return None
        combined["log_id"] = log_id
        combined["source_file"] = source_file
        combined["source_format"] = source_format
        return combined

    return log_id, paths, ingest


def build_targets():
    """Bestimmt, welche log_ids im Datalake stehen SOLLEN (Ziel-Menge) -
    reiner Dateisystem-Scan + Ausschlusslisten, kein Parsen von Inhalten.
    Ergebnis: {log_id: {"paths": [...], "ingest": callable}}. `ingest()`
    liefert bei Aufruf das fertige DataFrame (oder None, wenn sich beim
    tatsaechlichen Einlesen herausstellt, dass nichts drin ist)."""
    targets = {}

    for path in sorted(glob.glob(f"{RAW_DLG_DIR}/*.dlg")):
        log_id = os.path.splitext(os.path.basename(path))[0]

        def ingest(path=path):
            print(f"lade (dlg): {os.path.basename(path)}")
            return ingest_dlg(path)

        targets[log_id] = {"paths": [path], "ingest": ingest}

    csv_files = sorted(glob.glob(f"{RAW_CSV_DIR}/*.csv"))
    for path in csv_files:
        if os.path.basename(path) in KNOWN_CSV_DUPLICATES_OF_DLG:
            continue
        log_id = os.path.splitext(os.path.basename(path))[0]

        def ingest(path=path):
            print(f"lade (csv): {os.path.basename(path)}")
            return ingest_csv(path)

        targets[log_id] = {"paths": [path], "ingest": ingest}

    skipped = set(os.path.basename(f) for f in csv_files) & KNOWN_CSV_DUPLICATES_OF_DLG
    for s in sorted(skipped):
        print(f"uebersprungen (Duplikat einer .dlg-Datei): {s}")
    for s in sorted(KNOWN_CAN_LOG_DUPLICATES):
        print(f"uebersprungen (Duplikat eines anderen CAN-Logs): {s}")

    for can_name, gpx_name in _all_can_gps_pairs():
        result = _build_can_target(can_name, gpx_name)
        if result is not None:
            log_id, paths, ingest = result
            targets[log_id] = {"paths": paths, "ingest": ingest}

    return targets


def _ensure_schema(con):
    """Legt logs/measurements neu (leer) an, falls sie fehlen ODER noch
    nicht das content_fingerprint/schema_version-Schema haben - einmalige
    Migration von der alten Full-Rebuild-Version: verhaelt sich beim
    naechsten run_build() wie ein kompletter Neubau (leere logs-Tabelle ->
    jede Ziel-log_id gilt als neu), danach inkrementell. Rohdateien bleiben
    die Quelle der Wahrheit, hier geht nichts verloren."""
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    has_new_schema = False
    if "logs" in tables:
        cols = {r[1] for r in con.execute("PRAGMA table_info('logs')").fetchall()}
        has_new_schema = {"content_fingerprint", "schema_version"} <= cols
    if not has_new_schema:
        con.execute("DROP TABLE IF EXISTS measurements")
        con.execute("DROP TABLE IF EXISTS logs")
        con.execute("""CREATE TABLE measurements (
            log_id VARCHAR, source_file VARCHAR, source_format VARCHAR,
            channel VARCHAR, channel_original VARCHAR, unit VARCHAR,
            t_elapsed_s DOUBLE, timestamp_local TIMESTAMP, value DOUBLE)""")
        con.execute("""CREATE TABLE logs (
            log_id VARCHAR, source_file VARCHAR, source_format VARCHAR,
            start_time_local TIMESTAMP, duration_s DOUBLE, n_measurements BIGINT,
            content_fingerprint VARCHAR, schema_version VARCHAR)""")


def run_build(force_full=False):
    targets = build_targets()
    fingerprints = {log_id: _fingerprint(t["paths"]) for log_id, t in targets.items()}

    con = duckdb.connect(DB_PATH)
    _ensure_schema(con)
    existing = {row[0]: (row[1], row[2]) for row in
                con.execute("SELECT log_id, content_fingerprint, schema_version FROM logs").fetchall()}

    to_remove = [log_id for log_id in existing if log_id not in targets]
    if force_full:
        to_ingest = sorted(targets)
    else:
        to_ingest = sorted(
            log_id for log_id in targets
            if existing.get(log_id) != (fingerprints[log_id], SCHEMA_VERSION))
    print(f"\nZiel-Logs: {len(targets)}  |  neu/geaendert: {len(to_ingest)}  |  "
          f"unveraendert: {len(targets) - len(to_ingest)}  |  zu entfernen: {len(to_remove)}")

    # IMMER zuerst entfernen, auch wenn to_ingest leer ist - das ist der
    # Schutz gegen umbenannte/geloeschte/nachtraeglich als Duplikat erkannte
    # Logs (siehe Docstring). Deckt auch to_ingest ab (Re-Ingest = erst
    # loeschen, dann neu einfuegen, sonst Dubletten).
    stale = sorted(set(to_remove) | set(to_ingest))
    if stale:
        placeholders = ",".join(["?"] * len(stale))
        con.execute(f"DELETE FROM measurements WHERE log_id IN ({placeholders})", stale)
        con.execute(f"DELETE FROM logs WHERE log_id IN ({placeholders})", stale)

    for log_id in to_ingest:
        df = targets[log_id]["ingest"]()
        if df is None or df.empty:
            continue
        con.register("_batch_measurements", df[MEASUREMENT_COLUMNS])
        con.execute("INSERT INTO measurements SELECT * FROM _batch_measurements")
        con.unregister("_batch_measurements")

        summary = _log_summary(df)
        summary["content_fingerprint"] = fingerprints[log_id]
        summary["schema_version"] = SCHEMA_VERSION
        con.register("_batch_log", pd.DataFrame([summary])[LOG_COLUMNS])
        con.execute("INSERT INTO logs SELECT * FROM _batch_log")
        con.unregister("_batch_log")

    n_logs, n_measurements = con.execute(
        "SELECT (SELECT COUNT(*) FROM logs), (SELECT COUNT(*) FROM measurements)").fetchone()
    n_unmapped = con.execute(
        "SELECT COUNT(*) FROM measurements WHERE channel LIKE 'UNMAPPED:%'").fetchone()[0]
    unmapped_overall = [r[0] for r in con.execute(
        "SELECT DISTINCT channel_original FROM measurements "
        "WHERE channel LIKE 'UNMAPPED:%' ORDER BY 1").fetchall()]
    unmapped_by_log = {row[0]: sorted(row[1]) for row in con.execute(
        "SELECT log_id, list(DISTINCT channel_original) FROM measurements "
        "WHERE channel LIKE 'UNMAPPED:%' GROUP BY log_id").fetchall()}
    channels_per_log = con.execute(
        "SELECT log_id, COUNT(DISTINCT channel) FROM measurements "
        "GROUP BY log_id ORDER BY log_id").fetchall()
    con.close()

    print(f"\n=== Datalake aktualisiert: {DB_PATH} ===")
    print(f"Logs: {n_logs}  |  Messwerte gesamt: {n_measurements:,}")
    print(f"Davon nicht zugeordnete (UNMAPPED) Messwerte: {n_unmapped:,}")
    if n_unmapped:
        print("Unbekannte Original-Spalten:", unmapped_overall)
    print("\nKanaele pro Log (Anzahl unterschiedlicher channel-Werte):")
    for log_id, n in channels_per_log:
        print(f"{log_id}  {n}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "datalake_build_summary.json"), "w", encoding="utf-8") as f:
        json.dump({
            "n_logs": n_logs,
            "n_measurements": int(n_measurements),
            "n_unmapped_channels": int(n_unmapped),
            "unmapped_channels_overall": unmapped_overall,
            "unmapped_channels_by_log": unmapped_by_log,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {RESULTS_DIR}/datalake_build_summary.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true",
                         help="alle Ziel-Logs neu einlesen, unabhaengig von Fingerabdruck/Schema-Version")
    args = parser.parse_args()
    run_build(force_full=args.full)


def _log_summary(df):
    valid_t = df["timestamp_local"].dropna()
    return {
        "log_id": df["log_id"].iloc[0],
        "source_file": df["source_file"].iloc[0],
        "source_format": df["source_format"].iloc[0],
        "start_time_local": valid_t.min() if len(valid_t) else pd.NaT,
        "duration_s": float(df["t_elapsed_s"].max() - df["t_elapsed_s"].min()) if len(df) else 0.0,
        "n_measurements": len(df),
    }


if __name__ == "__main__":
    main()
