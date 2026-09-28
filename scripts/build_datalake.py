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
die SCHEMA_VERSIONS des jeweiligen Quellformats wurde hochgezaehlt)
tatsaechlich neu ein.

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
SCHEMA_VERSIONS hochzuzaehlen). Rohdateien werden nie veraendert.

`--verify` schreibt nichts, sondern liest alle als unveraendert geltenden Logs
neu ein und vergleicht sie zeilengenau (inkl. Reihenfolge je Kanal) mit der
DB - Nachweis nach Aenderungen am Ingest-Code, bzw. Fund einer vergessenen
SCHEMA_VERSIONS-Erhoehung. Siehe run_verify().

LAUFZEIT (2026-09-26): ingest_dlg() ohne SQLite-JOIN/-Sortierung, CAN-CSV mit
usecols/Categoricals, _derive_gear_status() vektorisiert, INSERT ueber
_insert_measurements() (Konstanten als Parameter, Strings per Code-Tabelle).
Jeder Lauf gibt pro Log und pro Format Lese-/Schreibzeit aus (auch in
results/datalake_build_summary.json). Herleitung/Messwerte:
docs/logs/projekt-stand.md, "build_datalake.py: Laufzeitoptimierung".

Aufruf: python build_datalake.py [--full | --verify]
"""
import argparse
import functools
import glob
import json
import os
import re
import sqlite3
import sys
import time
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
# Re-Ingest der betroffenen Logs (sonst bleiben schon eingelesene Logs
# unbemerkt mit der alten Mapping-Logik in der DB stehen, siehe Docstring oben).
# Getrennt pro Quellformat (seit 2026-09-26): bis dahin gab es EINE Version,
# und alle 7 Erhoehungen 1->8 (20.-26.09.) betrafen nur den CAN-Pfad, liessen
# aber jedes Mal auch alle .dlg/CSV-Logs neu einlesen. Nur das Format
# hochzaehlen, dessen ingest-Pfad sich geaendert hat:
#   "dlg" -> ingest_dlg(), "csv" -> ingest_csv()/NAME_ALIASES/RAW_HEADER_OVERRIDES,
#   "can" -> ingest_can()/ingest_gps()/CAN_SIGNAL_MAP/CAN_SENTINELS/FUEL_*
#   (CAN+GPS-Paare laufen als ein Log unter "can").
# CANONICAL_UNIT/UNIT_CONVERSIONS wirken auf dlg UND csv -> dann beide.
# Alle starten bei "8" (= der letzten gemeinsamen Version), damit die
# Umstellung selbst keinen Re-Ingest ausloest.
SCHEMA_VERSIONS = {"dlg": "8", "csv": "8", "can": "9"}  # can 9: dlg-GPS an CAN-Logs (27.09.)

# Was die ingest_*()-Funktionen liefern. Tabelle `measurements` = log_id,
# source_file, source_format + diese Spalten; die ersten drei sind pro Log
# konstant und kommen erst beim INSERT als Parameter dazu (siehe
# _insert_measurements()), statt als Millionen gleicher Python-Strings durch
# pandas und die DuckDB-Konvertierung zu laufen.
INGEST_COLUMNS = ["channel", "channel_original", "unit", "t_elapsed_s", "timestamp_local", "value"]
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


# No-RTC-Versaetze, die erst nachtraeglich per Kreuzkorrelation auffielen (28.09.:
# can_phone_imu_sweep.py, Handy-IMU gegen CAN): {can_name: Sekunden, um die der wahre Start
# NACH der Zeit im Dateinamen liegt}. Statt Umbenennen, weil das auch auf dem Pi passieren
# muesste (sonst holt sync_can_logs_from_pi das alte Log erneut). Siehe log_start_epoch().
CAN_CLOCK_OFFSETS_PATH = "data/can_clock_offsets.json"


def _can_clock_offsets():
    if not os.path.exists(CAN_CLOCK_OFFSETS_PATH):
        return {}
    with open(CAN_CLOCK_OFFSETS_PATH, encoding="utf-8") as f:
        return json.load(f)

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
    # Memory), Tire1=vorne links/Tire2=vorne rechts seit 2026-09-26 bestaetigt
    # (Nutzer fuellte VR mit weniger Druck, temperaturbereinigte Differenz
    # Tire1-Tire2 sprang von 0,027 auf 0,085 bar). Kanalnamen bleiben 1-4. Nur in Logs vorhanden, in denen der Pi beim Fahren lief
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
    # Beifahrer-Gurtschloss/-Belegung (0x340 HS_RCM, 2026-09-20). Buckled ist der
    # bestaetigte Gurtschloss-Schalter; Occupied_maybe der vermutete, vom Gurt
    # unabhaengige Gewichts-/Belegungscode (Herleitung + offene Punkte: DBC-
    # Kommentar bei BO_832 sowie docs/logs/can-bus-status.md 2026-09-20). Fuer die
    # 75kg-Beifahrer-Massenkorrektur siehe run_daily_pipeline.py::compute_mass().
    "PassengerSeatbelt_Buckled": ("PassengerSeatbelt_Buckled_CAN", ""),
    "PassengerSeatOccupied_maybe": ("PassengerSeatOccupied_CAN", ""),
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
    "ThrottlePosition_OBD": ("ThrottlePosition_CAN", "%"),
    "Tire1_Temp_maybe": ("TireTemp_CAN_Tire1", "°C"),
    "Tire2_Temp_maybe": ("TireTemp_CAN_Tire2", "°C"),
    "Tire3_Temp_maybe": ("TireTemp_CAN_Tire3", "°C"),
    "Tire4_Temp_maybe": ("TireTemp_CAN_Tire4", "°C"),
    # Offline-Ausbeute 2026-09-26 (docs/plans/can-offline-ausbeute-plan.md, Herleitungen in
    # docs/logs/can-bus-status.md und in den CM_-Kommentaren der DBC). "_maybe" = Deutung/Skala
    # noch nicht am Fahrzeug bestaetigt, siehe docs/status/can-open-fields.md.
    "BatteryVoltage_OBD": ("BatteryVoltage_CAN", "V"),
    "DCDC_Voltage": ("SystemVoltage_CAN", "V"),
    "iELOOP_CapVoltage_maybe": ("iELOOP_CapVoltage_CAN", "V"),
    "DCDC_State_maybe": ("DCDC_State_CAN", ""),
    "BattSensor_Temp_maybe": ("BatteryTemp_CAN", "°C"),
    "AmbientTemp": ("AmbientTemp_CAN", "°C"),
    "SteeringRate_Abs_maybe": ("SteeringRateAbs_CAN", "deg/s"),
    "TCS_Active_maybe": ("TCS_Active_CAN", ""),
    "TCS_RequestActive_maybe": ("TCS_RequestActive_CAN", ""),
    "TCS_TorqueRequest_maybe": ("TCS_TorqueRequest_CAN_raw", ""),
    "HighDecel_maybe": ("HighDecel_CAN", ""),
    "DSC_Indicator_maybe": ("DSC_Indicator_CAN", ""),
    "ReverseGear": ("ReverseGear_CAN", ""),
    "BrakeSwitch_PCM": ("BrakeSwitch_CAN", ""),
    "EngineRunning": ("EngineRunning_CAN", ""),
    "iStop_EngineStopped": ("iStop_Stopped_CAN", ""),
    "TSR_SpeedLimit": ("SpeedLimitSign_CAN", "km/h"),
    "LaneCurvature_maybe": ("LaneCurvature_CAN", "1/km"),
    # FuelConsumption_Counter wird nicht roh uebernommen, sondern in ingest_can() zu
    # FuelRate_CAN (g/s) differenziert - siehe _derive_fuel_rate().
    "FuelConsumption_Counter": ("FuelRate_CAN", "g/s"),
    # Fahrzeugtests 26.09. (C3/C7/C12, docs/logs/can-bus-status.md "Fahrtag 26.09."):
    # Bordcomputer-Durchschnittsverbrauch, Fahrergurt, Notbremssignal.
    "AvgFuelConsumption": ("AvgFuelConsumption_CAN", "l/100km"),
    "DriverSeatbelt_Buckled": ("DriverSeatbelt_Buckled_CAN", ""),
    "EmergencyStopSignal_maybe": ("EmergencyStopSignal_CAN", ""),
    # Zusatz-PIDs aus Fahrzeugtest C9 (tpms_poller.py langsame Gruppe seit 26.09.)
    "CatalystTemp_OBD": ("CatalystTemp_CAN", "°C"),
    "LambdaMeasured_OBD": ("LambdaMeasured_CAN", ""),
    "FuelLevel_OBD": ("FuelLevel_CAN", "%"),
}
# Byte2 von 0x420: 5025 Schritte je Bordcomputer-Liter (26.09., zwei Logs 5024/5026); der Bordcomputer
# zaehlt aber 4,0 % zu wenig (Voll-bis-Voll 16.09.->26.09.: 34,4 l getankt gegen 33,09 l angezeigt),
# also 4834 Schritte je echtem Liter; bei 0,745 kg/l ~6,49 Schritte/g. Vorher 7,03 aus OBD-MAF/Lambda.
FUEL_COUNTS_PER_G = 4834.0 / 745.0
FUEL_RATE_WINDOW_S = 2.0           # Zaehler zaehlt nur ~1 Schritt/s im Leerlauf -> ueber ein Fenster mitteln
# Init-/Ungueltig-Werte, die sonst als echte Messwerte im Datalake landen (2026-09-26 gesehen:
# Batterietemperatur 255-40=215, Aussentemperatur Rohwert 0 (Init, seit 0,25/LSB = 0,0), Spannungen 0 in den ersten Frames).
CAN_SENTINELS = {"BattSensor_Temp_maybe": 215.0, "AmbientTemp": 0.0, "DCDC_Voltage": 0.0,
                 "iELOOP_CapVoltage_maybe": 0.0, "AvgFuelConsumption": 655.34}
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
    """Laufzeit (2026-09-26): frueher JOIN auf PidMetadataEntry + ORDER BY Time
    direkt in SQLite - das liess SQLite sortieren und erzeugte pro Messwert
    einen eigenen PidName-String. Jetzt nur die (~60) Metadaten-Zeilen als
    Dict, die Datenzeilen ohne JOIN/Sortierung, Kanalnamen als Categorical per
    UniqueId, Zeitsortierung per stabilem argsort (gleiche Zeitstempel bleiben
    in Datei-Reihenfolge). Etwa halbe Lesezeit bei identischem Ergebnis."""
    conn = sqlite3.connect(path)
    try:
        meta = conn.execute("SELECT UniqueId, PidName FROM PidMetadataEntry").fetchall()
        df = pd.read_sql_query(
            "SELECT Time AS raw_time, UniqueId AS uid, Value AS value FROM PidDataEntry", conn)
    finally:
        conn.close()
    names = dict(meta)
    if len(names) != len(meta):
        # Der fruehere LEFT JOIN haette hier jeden Messwert vervielfacht.
        warnings.warn(f"{os.path.basename(path)}: PidMetadataEntry hat doppelte UniqueIds - "
                      f"es gilt jeweils der letzte PidName")
    if not df["raw_time"].is_monotonic_increasing:
        df = df.iloc[np.argsort(df["raw_time"].to_numpy(), kind="stable")].reset_index(drop=True)
    df["timestamp_local"] = pd.to_datetime((df["raw_time"] - TICKS_OFFSET) / 10, unit="us")
    t0 = df["timestamp_local"].min()
    df["t_elapsed_s"] = (df["timestamp_local"] - t0).dt.total_seconds()
    df["channel"] = df["uid"].map(names).astype("category")
    df["channel_original"] = df["channel"]
    df["unit"] = df["channel"].map(CANONICAL_UNIT)
    return df[INGEST_COLUMNS]


def ingest_csv(path):
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
    return out[INGEST_COLUMNS]


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
    alten 13.09.-Zeitstempel).

    Steht das Log in CAN_CLOCK_OFFSETS_PATH, gilt Dateiname + Versatz (28.09.)."""
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
    offset = _can_clock_offsets().get(os.path.basename(can_log_path))
    if offset is not None:
        print(f"  Hinweis: {os.path.basename(can_log_path)} - Uhr-Override {offset:+.1f} s "
              f"auf den Dateinamen ({CAN_CLOCK_OFFSETS_PATH}).")
        return name_epoch + offset
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
    gear_by_position = {"N/1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6}
    # vektorisiert statt m.apply(..., axis=1) (~30x schneller, ~2 s je Fahrstunde gespart)
    gear = m["pos"].map(gear_by_position).fillna(0).astype(np.int64)
    m["value"] = np.where(m["sel"] == "Neutral", 0, gear)
    m["signal"] = "MT_Gear_Status"
    return m[["t", "signal", "value"]]


def _derive_fuel_rate(decoded):
    """FuelConsumption_Counter (0x420 Byte2, umlaufender 8-Bit-Zaehler der Einspritzmenge) ->
    Kraftstoffstrom in g/s, gemittelt ueber FUEL_RATE_WINDOW_S. Einzelne Spruenge > 20 Schritte
    je Frame (physikalisch max. ~9) werden als ungueltig verworfen."""
    sel = decoded["signal"] == "FuelConsumption_Counter"
    c = decoded[sel].sort_values("t")
    if len(c) < 3:
        return decoded[~sel]
    raw = pd.to_numeric(c["value"], errors="coerce").to_numpy()
    t = c["t"].to_numpy()
    inc = np.diff(raw) % 256
    inc[(inc > 20) | (np.diff(t) > 1.0)] = 0
    cum = np.concatenate([[0.0], np.cumsum(inc)])
    j = np.searchsorted(t, t - FUEL_RATE_WINDOW_S)
    span = t - t[j]
    ok = span >= 0.5 * FUEL_RATE_WINDOW_S
    rate = pd.DataFrame({"t": t[ok], "signal": "FuelConsumption_Counter",
                         "value": (cum[ok] - cum[j[ok]]) / span[ok] / FUEL_COUNTS_PER_G})
    # gleicher dtype wie decoded (Categorical, siehe ingest_can()) - sonst macht concat
    # daraus eine object-Spalte und alle folgenden Filter werden String-Vergleiche
    rate["signal"] = rate["signal"].astype(decoded["signal"].dtype)
    return pd.concat([decoded[~sel], rate], ignore_index=True)


def ingest_can(can_log_path, decoded_csv_path, t0_epoch):
    """CAN-Log in den Datalake einlesen. Nutzt die von can_log_parser.py bereits
    erzeugte *_decoded.csv (Long-Format t/can_id/message/signal/value) statt
    erneut >1 Mio Rohframes zu dekodieren - die CSV wird ohnehin schon fuers
    CAN-Reverse-Engineering gepflegt (siehe docs/logs/can-bus-status.md)."""
    t0_local = datetime.fromtimestamp(t0_epoch, tz=LOCAL_TZ).replace(tzinfo=None)

    # can_id wird nicht gebraucht; message/signal als Categorical statt Millionen
    # Python-Strings (etwa halbe Lesezeit, und alle Signal-Filter unten werden
    # Ganzzahl- statt String-Vergleiche). Die beiden hier erst entstehenden
    # Signalnamen muessen vorab als Kategorie existieren.
    # Kein usecols: pandas 3.0.5 wirft damit IndexError, sobald die gemischte
    # value-Spalte eine DtypeWarning ausloest (grosse CSVs, z.B. candump-2026-09-19_163755).
    raw_decoded = pd.read_csv(decoded_csv_path,
                              dtype={"message": "category", "signal": "category"}
                              ).drop(columns="can_id")
    new_signals = [s for s in ("VehicleSpeed_Display", "MT_Gear_Status")
                   if s not in raw_decoded["signal"].cat.categories]
    raw_decoded["signal"] = raw_decoded["signal"].cat.add_categories(new_signals)
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
    gear_status["signal"] = gear_status["signal"].astype(raw_decoded["signal"].dtype)
    # erst filtern, dann anhaengen (gleiche Zeilen/Reihenfolge wie concat+Filter,
    # aber ohne die ungemappten Signale nochmal zu kopieren)
    decoded = pd.concat([raw_decoded[raw_decoded["signal"].isin(CAN_SIGNAL_MAP)], gear_status],
                        ignore_index=True)
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
    sentinel = decoded["signal"].map(CAN_SENTINELS).astype(float)  # NaN fuer Signale ohne Sentinel
    decoded = decoded[~(np.abs(decoded["value"] - sentinel) < 1e-6)]
    decoded = _derive_fuel_rate(decoded)
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
    decoded["channel"] = decoded["signal"].map({k: v[0] for k, v in CAN_SIGNAL_MAP.items()})
    decoded["unit"] = decoded["signal"].map({k: v[1] for k, v in CAN_SIGNAL_MAP.items()})
    decoded["channel_original"] = decoded["signal"]
    decoded["t_elapsed_s"] = decoded["t"]
    decoded["timestamp_local"] = t0_local + pd.to_timedelta(decoded["t_elapsed_s"], unit="s")
    return decoded[INGEST_COLUMNS]


def ingest_gps(gpx_path, t0_epoch):
    """Begleitenden GPS-Track (BasicAirData-GPX) einlesen (landet unter demselben
    log_id wie das zugehoerige CAN-Log, siehe _build_can_target()). Nur Felder
    uebernehmen, die die GPX tatsaechlich liefert (lat/lon/ele/speed) - kein
    Hoehen-/Genauigkeits-Ersatzwert erfunden."""
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
    return pd.concat(long_parts, ignore_index=True)[INGEST_COLUMNS]


DLG_GPS_MIN_OVERLAP_S = 60  # sonst Sekunden-Ueberlappung am Rand einer Nachbarfahrt (17.09. 084511)
# Per CAN-Speed/GPS-Speed-Kreuzkorrelation (27.09.) NICHT deckungsgleich -> kein dlg-GPS anhaengen,
# sonst laege die Position falsch zur Fahrt. Alle uebrigen Paare: Rest-Offset <= 2 s.
# 14_163711/14_173057/18_165510 standen hier bis 28.09. wegen Fehldatierung, jetzt per
# CAN_CLOCK_OFFSETS_PATH korrigiert.
DLG_GPS_EXCLUDE = {
    "candump-2026-09-18_170350.log",  # kein passender Offset, vermutlich auch No-RTC (ueberlappt 171150)
    "candump-2026-09-26_154000.log",  # Datierung nur geschaetzt, kein Offset passt (RMSE 26,8 km/h)
}
DLG_GPS_CHANNELS = {"Breite": "deg", "Länge": "deg", "Höhe": "m", "GPS-Geschwindigkeit": "km/h"}  # wie ingest_gps()


@functools.lru_cache(maxsize=None)
def _dlg_gps_span(dlg_path):
    """(erste, letzte) GPS-Zeit einer dlg als UTC-Epoch, None ohne GPS. Gecacht: wird fuer
    jedes CAN-Log gegen alle dlg geprueft."""
    conn = sqlite3.connect(dlg_path)
    try:
        row = conn.execute("""SELECT min(Time), max(Time) FROM PidDataEntry WHERE UniqueId IN
            (SELECT UniqueId FROM PidMetadataEntry WHERE PidName = 'GPS-Geschwindigkeit')""").fetchone()
    finally:
        conn.close()
    return None if row[0] is None else tuple((t - TICKS_OFFSET) / 1e7 for t in row)


def _can_log_duration_s(can_path):
    """Letzte minus erste candump-Zeit (nur Kopfzeile + letzte 4 KB gelesen)."""
    stamps = []
    with open(can_path, "rb") as f:
        head = f.readline()
        f.seek(max(0, os.path.getsize(can_path) - 4096))
        for line in [head, *f.read().splitlines()]:
            try:
                stamps.append(float(line.split(b")", 1)[0].strip(b"(")))
            except ValueError:
                pass  # abgeschnittene Zeile
    # Muell am Ende eines per Stromverlust abgeschnittenen Logs (candump-2026-09-16_090826) parst
    # sonst als Zeit in ferner Zukunft -> nur Zeiten binnen eines Tages nach Start zaehlen
    ok = [t for t in stamps if 0 <= t - stamps[0] <= 86400] if stamps else []
    return max(ok) - stamps[0] if ok else 0.0


def _find_overlapping_dlg(can_path, t0_epoch):
    """dlg mit der laengsten GPS-Zeitueberlappung zum CAN-Log, sonst None. Fuer CAN-Logs ohne
    GPX: das GPS steckt dann in der Handy-dlg (27.09.: 112539/113615 in dlg 112512, per
    Speed-Kreuzkorrelation +-1 s deckungsgleich)."""
    t1 = t0_epoch + _can_log_duration_s(can_path)
    best, best_overlap = None, DLG_GPS_MIN_OVERLAP_S
    for dlg_path in sorted(glob.glob(f"{RAW_DLG_DIR}/*.dlg")):
        span = _dlg_gps_span(dlg_path)
        overlap = min(t1, span[1]) - max(t0_epoch, span[0]) if span else 0
        if overlap > best_overlap:
            best, best_overlap = dlg_path, overlap
    return best


def ingest_dlg_gps(dlg_path, t0_epoch):
    """GPS-Kanaele einer dlg auf die Zeitachse des CAN-Logs umgerechnet (Gegenstueck zu
    ingest_gps()). ingest_dlg()'s timestamp_local ist in Wahrheit UTC (bekanntes Mislabeling),
    hier daher als UTC-Epoch gelesen."""
    df = ingest_dlg(dlg_path)
    df = df[df["channel"].isin(DLG_GPS_CHANNELS)].copy()
    df["channel"] = df["channel"].astype(str)
    df["channel_original"] = df["channel_original"].astype(str)
    df["unit"] = df["channel"].map(DLG_GPS_CHANNELS)  # dlg-Ingest laesst die GPS-Einheiten leer
    epoch = (df["timestamp_local"] - pd.Timestamp("1970-01-01")).dt.total_seconds()
    df["t_elapsed_s"] = epoch - t0_epoch
    t0_local = datetime.fromtimestamp(t0_epoch, tz=LOCAL_TZ).replace(tzinfo=None)
    df["timestamp_local"] = t0_local + pd.to_timedelta(df["t_elapsed_s"], unit="s")
    return df[INGEST_COLUMNS]


def _build_can_target(can_name, gpx_name):
    """Bestimmt (log_id, Ziel-Eintrag wie in build_targets()) fuer ein
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
    dlg_path = None if has_gpx or can_name in DLG_GPS_EXCLUDE else _find_overlapping_dlg(can_path, t0_epoch)
    gps_path = gpx_path if has_gpx else dlg_path
    paths = [can_path, decoded_csv_path] + ([gps_path] if gps_path else [])
    if can_name in _can_clock_offsets():  # Versatz geaendert -> Fingerabdruck geaendert -> Re-Ingest
        paths.append(CAN_CLOCK_OFFSETS_PATH)

    def ingest():
        print(f"lade (can): {can_name}")
        combined = ingest_can(can_path, decoded_csv_path, t0_epoch)
        if gps_path:
            print(f"lade (gps): {os.path.basename(gps_path)}")
            gps_df = ingest_gps(gpx_path, t0_epoch) if has_gpx else ingest_dlg_gps(dlg_path, t0_epoch)
            if not combined.empty:  # ein Track kann mehrere CAN-Logs abdecken (27.09.) -> nur der eigene Zeitraum
                gps_df = gps_df[gps_df["t_elapsed_s"].between(0, combined["t_elapsed_s"].max())]
            combined = pd.concat([combined, gps_df], ignore_index=True)
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
        return combined

    source_file, source_format = (f"{can_name} + {os.path.basename(gps_path)}", "can+gps") if gps_path else (can_name, "can")
    return log_id, {"paths": paths, "ingest": ingest, "format": "can",
                    "source_file": source_file, "source_format": source_format}


def build_targets():
    """Bestimmt, welche log_ids im Datalake stehen SOLLEN (Ziel-Menge) -
    reiner Dateisystem-Scan + Ausschlusslisten, kein Parsen von Inhalten.
    Ergebnis: {log_id: {"paths": [...], "ingest": callable, "format": Schluessel
    in SCHEMA_VERSIONS, "source_file": ..., "source_format": ...}}. `ingest()`
    liefert bei Aufruf das DataFrame mit INGEST_COLUMNS (oder None, wenn sich
    beim tatsaechlichen Einlesen herausstellt, dass nichts drin ist)."""
    targets = {}

    for path in sorted(glob.glob(f"{RAW_DLG_DIR}/*.dlg")):
        log_id = os.path.splitext(os.path.basename(path))[0]

        def ingest(path=path):
            print(f"lade (dlg): {os.path.basename(path)}")
            return ingest_dlg(path)

        targets[log_id] = {"paths": [path], "ingest": ingest, "format": "dlg",
                           "source_file": os.path.basename(path), "source_format": "dlg"}

    csv_files = sorted(glob.glob(f"{RAW_CSV_DIR}/*.csv"))
    for path in csv_files:
        if os.path.basename(path) in KNOWN_CSV_DUPLICATES_OF_DLG:
            continue
        log_id = os.path.splitext(os.path.basename(path))[0]

        def ingest(path=path):
            print(f"lade (csv): {os.path.basename(path)}")
            return ingest_csv(path)

        targets[log_id] = {"paths": [path], "ingest": ingest, "format": "csv",
                           "source_file": os.path.basename(path), "source_format": "csv"}

    skipped = set(os.path.basename(f) for f in csv_files) & KNOWN_CSV_DUPLICATES_OF_DLG
    for s in sorted(skipped):
        print(f"uebersprungen (Duplikat einer .dlg-Datei): {s}")
    for s in sorted(KNOWN_CAN_LOG_DUPLICATES):
        print(f"uebersprungen (Duplikat eines anderen CAN-Logs): {s}")

    for can_name, gpx_name in _all_can_gps_pairs():
        result = _build_can_target(can_name, gpx_name)
        if result is not None:
            log_id, target = result
            targets[log_id] = target

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


def _insert_measurements(con, df, log_id, source_file, source_format, table="measurements"):
    """Fuegt die INGEST_COLUMNS eines Logs in `table` ein. Laufzeit (2026-09-26):
    frueher wurden alle 9 Spalten als DataFrame registriert, davon 6 Text-
    spalten - DuckDB muss dabei jeden Python-String einzeln konvertieren, das
    war der teuerste Einzelschritt des ganzen Baus (~9 s je 5 Mio Messwerte,
    mit pyarrow-Strings sogar ~20 s). Jetzt:
      - log_id/source_file/source_format als SQL-Parameter (pro Log konstant),
      - channel/channel_original/unit als Ganzzahl-Code + kleine Nachschlage-
        tabelle (ein paar Dutzend Zeilen), per JOIN in DuckDB aufgeloest,
    zusammen ~3x schneller. Das ORDER BY auf die Zeilennummer ist PFLICHT:
    ein Hash-JOIN gibt die Zeilen sonst in beliebiger Reihenfolge aus (per
    Test bestaetigt), und etliche Leser-Skripte fragen Zeitreihen ohne ORDER BY
    ab und verlassen sich auf die Einfuege-Reihenfolge."""
    key_cols = ["channel", "channel_original", "unit"]
    codes = np.zeros(len(df), dtype=np.int64)
    for col in key_cols:
        col_codes, uniques = pd.factorize(df[col], use_na_sentinel=False)
        codes = codes * max(len(uniques), 1) + col_codes
    codes, uniques = pd.factorize(codes)
    # beliebiges Vorkommen je Code reicht, alle Zeilen eines Codes sind in key_cols gleich
    first = np.empty(len(uniques), dtype=np.int64)
    first[codes] = np.arange(len(df))
    lut = df[key_cols].iloc[first].astype(object).reset_index(drop=True)
    lut = lut.where(lut.notna(), None)
    lut["code"] = np.arange(len(lut))

    batch = df[["t_elapsed_s", "timestamp_local", "value"]].reset_index(drop=True)
    batch["code"] = codes
    batch["rn"] = np.arange(len(batch))
    con.register("_ins_batch", batch)
    con.register("_ins_lut", lut)
    try:
        con.execute(f"""INSERT INTO {table}
            SELECT ?, ?, ?, l.channel, l.channel_original, l.unit,
                   b.t_elapsed_s, b.timestamp_local, b.value
            FROM _ins_batch b JOIN _ins_lut l USING (code)
            ORDER BY b.rn""", [log_id, source_file, source_format])
    finally:
        con.unregister("_ins_batch")
        con.unregister("_ins_lut")


def run_build(force_full=False):
    t_run = time.perf_counter()
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
            if existing.get(log_id) != (fingerprints[log_id], SCHEMA_VERSIONS[targets[log_id]["format"]]))
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

    timing = {}  # format -> {"logs", "measurements", "read_s", "write_s"}
    for log_id in to_ingest:
        target = targets[log_id]
        t_start = time.perf_counter()
        df = target["ingest"]()
        read_s = time.perf_counter() - t_start
        if df is None or df.empty:
            continue
        t_start = time.perf_counter()
        _insert_measurements(con, df, log_id, target["source_file"], target["source_format"])

        summary = _log_summary(df, log_id, target["source_file"], target["source_format"])
        summary["content_fingerprint"] = fingerprints[log_id]
        summary["schema_version"] = SCHEMA_VERSIONS[target["format"]]
        con.register("_batch_log", pd.DataFrame([summary])[LOG_COLUMNS])
        con.execute("INSERT INTO logs SELECT * FROM _batch_log")
        con.unregister("_batch_log")
        write_s = time.perf_counter() - t_start
        print(f"  {len(df):,} Messwerte | lesen {read_s:.1f}s | schreiben {write_s:.1f}s")
        acc = timing.setdefault(target["format"], {"logs": 0, "measurements": 0, "read_s": 0.0, "write_s": 0.0})
        acc["logs"] += 1
        acc["measurements"] += len(df)
        acc["read_s"] += read_s
        acc["write_s"] += write_s

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

    total_s = time.perf_counter() - t_run
    print(f"\n=== Datalake aktualisiert: {DB_PATH} ===")
    print(f"Laufzeit gesamt: {total_s:.1f}s")
    for fmt, acc in sorted(timing.items()):
        print(f"  {fmt}: {acc['logs']} Logs, {acc['measurements']:,} Messwerte | "
              f"lesen {acc['read_s']:.1f}s | schreiben {acc['write_s']:.1f}s")
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
            "runtime_s": round(total_s, 1),
            "ingest_timing": {fmt: {k: round(v, 1) if isinstance(v, float) else v for k, v in acc.items()}
                              for fmt, acc in timing.items()},
        }, f, indent=2, ensure_ascii=False)
    print(f"\nDetails: {RESULTS_DIR}/datalake_build_summary.json")


# Pro (Log, Kanal) die komplette Zeitreihe in Einfuege-Reihenfolge - vergleicht damit
# Werte UND die Reihenfolge, auf die sich Leser ohne ORDER BY verlassen.
_VERIFY_SEQ_SQL = """SELECT log_id, source_file, source_format, channel, channel_original, unit,
       list((t_elapsed_s, timestamp_local, value) ORDER BY rowid) AS seq
FROM {table} WHERE log_id = ? GROUP BY ALL"""


def run_verify():
    """Liest jedes Log, das laut Fingerabdruck/SCHEMA_VERSIONS als "unveraendert"
    gilt, neu ein (nur in eine TEMP-Tabelle, DB read-only geoeffnet) und
    vergleicht es mit dem, was in der DB steht. Zweck: (1) Nachweis, dass eine
    Aenderung am Ingest-Code (z.B. die Laufzeitoptimierung vom 2026-09-26)
    dieselben Zeilen erzeugt wie der Code, der die DB gebaut hat; (2) findet
    eine vergessene SCHEMA_VERSIONS-Erhoehung (Mapping geaendert, Version
    nicht). Dauert so lange wie ein --full-Lauf. Rueckgabe: Anzahl
    abweichender Logs (Exit-Code 1, wenn > 0)."""
    targets = build_targets()
    if not os.path.exists(DB_PATH):
        print(f"{DB_PATH} existiert nicht, nichts zu pruefen")
        return 0
    # read_only: garantiert nichts zu schreiben (TEMP-Tabellen gehen trotzdem) und
    # sperrt parallel laufende Leser-Skripte nicht aus
    con = duckdb.connect(DB_PATH, read_only=True)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if not {"logs", "measurements"} <= tables:
        print(f"{DB_PATH}: keine Datalake-Tabellen, nichts zu pruefen")
        con.close()
        return 0
    existing = {row[0]: (row[1], row[2]) for row in
                con.execute("SELECT log_id, content_fingerprint, schema_version FROM logs").fetchall()}
    con.execute("CREATE TEMP TABLE _verify AS SELECT * FROM measurements LIMIT 0")

    n_ok, not_checked, bad = 0, [], []
    for log_id in sorted(targets):
        target = targets[log_id]
        if existing.get(log_id) != (_fingerprint(target["paths"]), SCHEMA_VERSIONS[target["format"]]):
            not_checked.append(log_id)  # neu/geaendert - der naechste Lauf liest es ohnehin neu ein
            continue
        df = target["ingest"]()
        con.execute("DELETE FROM _verify")
        summary = {"start_time_local": pd.NaT, "duration_s": 0.0, "n_measurements": 0}
        if df is not None and not df.empty:
            _insert_measurements(con, df, log_id, target["source_file"], target["source_format"],
                                 table="_verify")
            summary = _log_summary(df, log_id, target["source_file"], target["source_format"])
        n_diff = con.execute(f"""
            WITH a AS ({_VERIFY_SEQ_SQL.format(table="measurements")}),
                 b AS ({_VERIFY_SEQ_SQL.format(table="_verify")})
            SELECT (SELECT count(*) FROM (FROM a EXCEPT ALL FROM b))
                 + (SELECT count(*) FROM (FROM b EXCEPT ALL FROM a))""", [log_id, log_id]).fetchone()[0]
        con.register("_verify_log", pd.DataFrame([summary]))
        summary_diff = con.execute("""
            SELECT count(*) FROM (
                SELECT start_time_local, duration_s, n_measurements FROM logs WHERE log_id = ?
                EXCEPT ALL
                SELECT start_time_local::TIMESTAMP, duration_s::DOUBLE, n_measurements::BIGINT FROM _verify_log)""",
                                   [log_id]).fetchone()[0]
        con.unregister("_verify_log")
        if n_diff or summary_diff:
            bad.append(log_id)
            print(f"  ABWEICHUNG: {log_id} ({n_diff} Kanal-Zeitreihen verschieden, "
                  f"logs-Eintrag {'verschieden' if summary_diff else 'gleich'})")
        else:
            n_ok += 1
            print("  identisch")
    con.close()

    print(f"\n=== Verify: {n_ok} identisch, {len(bad)} abweichend, "
          f"{len(not_checked)} nicht geprueft (neu/geaendert) ===")
    if bad:
        print("Abweichend:", bad)
        print("Entweder Ingest-Code-Regression, oder Mapping geaendert ohne SCHEMA_VERSIONS "
              "hochzuzaehlen.")
    return len(bad)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true",
                      help="alle Ziel-Logs neu einlesen, unabhaengig von Fingerabdruck/Schema-Version")
    mode.add_argument("--verify", action="store_true",
                      help="unveraenderte Logs neu einlesen und mit der DB vergleichen, ohne zu schreiben")
    args = parser.parse_args()
    if args.verify:
        sys.exit(1 if run_verify() else 0)
    run_build(force_full=args.full)


def _log_summary(df, log_id, source_file, source_format):
    valid_t = df["timestamp_local"].dropna()
    return {
        "log_id": log_id,
        "source_file": source_file,
        "source_format": source_format,
        "start_time_local": valid_t.min() if len(valid_t) else pd.NaT,
        "duration_s": float(df["t_elapsed_s"].max() - df["t_elapsed_s"].min()) if len(df) else 0.0,
        "n_measurements": len(df),
    }


if __name__ == "__main__":
    main()
