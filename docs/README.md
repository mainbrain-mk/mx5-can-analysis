# Doku-Übersicht: wo steht was

Diese Doku hat zwei Ebenen:

- **`status/`** — kurze, thematisch sortierte "aktueller Stand"-Dokumente. Bei
  jeder relevanten Änderung **in-place aktualisiert** (kein Datums-Snapshot).
  Erster Anlaufpunkt für "was wissen wir schon zu X".
- **`logs/`** — die vollständigen chronologischen Arbeitsprotokolle (Lab
  Notebook). Append-only, nichts wird nachträglich umgeschrieben. Enthalten
  die Herleitungen, Messwerte und verworfenen Hypothesen hinter jedem Stand.

Faustregel: **erst den Status lesen, bei Bedarf per Link/Suchbegriff ins Log
springen.** Umgekehrt beim Schreiben: neue Erkenntnisse gehen chronologisch
ans passende Log, und wenn sich dadurch der Ist-Stand ändert, wird der
zugehörige Status zusätzlich in-place nachgezogen.

## Themen-Landkarte

| Thema | Status (aktuell) | Logbuch (Verlauf/Herleitung) | Wichtigste Skripte |
|---|---|---|---|
| Fahrleistungsmodell (Antrieb, Traktion, Schaltzeiten, Fahrwiderstände) + IMU/Schwingung | [`status/performance-model.md`](status/performance-model.md) *(Stand 30.08./07.09.2026)* | [`logs/projekt-stand.md`](logs/projekt-stand.md) | `performance_simulation.py`, `drivetrain_model_validation.py`, `coastdown_analysis.py`, `partial_load_model.py`, `partial_throttle_calibration.py`, `top_speed_validation.py`, `vibration_analysis.py`, `imu_orientation.py` |
| Strecke, Höhendaten, Racing-Line, Rundenzeit-Simulation (Spreewaldring) | [`status/vehicle-dynamics-track.md`](status/vehicle-dynamics-track.md) *(Stand 18.09.2026)* | [`logs/projekt-stand.md`](logs/projekt-stand.md) (Abschnitte ab "Höhendaten", "Spreewaldring…") | `elevation_model.py`, `check_dgm_coverage_gaps.py`, `spreewaldring_track.py`, `spreewaldring_track_surface.py`, `spreewaldring_racing_line*.py`, `spreewaldring_lap_simulation.py`, `render_report.py` |
| Lenkwinkel, Kurvenradius, Quergrip, Kraftkreis, Bremsmodell, Schaltzeiten/Zugkraftunterbrechung, Tankstand | [`status/vehicle-dynamics-track.md`](status/vehicle-dynamics-track.md) *(Stand 18.09.2026)* | [`logs/projekt-stand.md`](logs/projekt-stand.md) (Abschnitte ab "Lenkwinkel-Kanal", "Quergrip…") | `steering_lateral_model.py`, `steering_offset_drift.py`, `steering_zero_offset.py`, `steering_radius_estimate.py`, `calibrate_axes.py`, `grip_estimation.py`, `corner_speed_model.py`, `corner_event_analysis.py`, `braking_model.py`, `brake_event_analysis.py`, `engine_braking_analysis.py`, `clutch_ride_detection.py`, `shift_traction_gap_analysis.py`, `shift_time_analysis.py` |
| CAN-Bus-Logging: Hardware/Pi-Infrastruktur, DBC-Pflege, Signal-Reverse-Engineering, TPMS, Renncockpit-GUI | [`status/can-bus.md`](status/can-bus.md) *(Stand 2026-09-16)* | [`logs/can-bus-status.md`](logs/can-bus-status.md) | `can_log_parser.py`, `can_byte_search.py`, `can_bitsearch.py`, `can_re_toolkit.py`, `can_field_segmentation.py`, `can_opendbc_crosscheck.py`, `obd_from_can.py`, `uds_did_sweep.py`, `tpms_poller.py`, `status_gui.py`, `dash_gui.py`, `session_logger.py` |
| CAN-Deep-Search-Projekt (referenzfreie Signalsuche in leeren Botschaften) | — (ist selbst ein Plan-Doc) | [`plans/can-deep-search-plan.md`](plans/can-deep-search-plan.md), verlinkt aus `logs/can-bus-status.md` | `can_field_segmentation.py`, `can_opendbc_crosscheck.py`, `can_event_bit_diff.py`, `can_find_native_counterpart.py`, `uds_did_sweep.py` |
| Pi-Hardware-Reproduzierbarkeit (Partitionierung, overlayroot, systemd-Units) | [`../scripts/pi-config/SETUP.md`](../scripts/pi-config/SETUP.md) | Details/Begründungen im Fließtext von `logs/can-bus-status.md` | `session_logger.py`, `status_gui.py` |
| Fremde DBC-Quellen (Lizenz/Herkunft/Übernahmeregeln) | [`../data/can/external/README.md`](../data/can/external/README.md) | — | `can_opendbc_crosscheck.py` |
| Datenpipeline / Datalake-Aufbau | *(kein eigenes Status-Doc, Infra-Teil von Performance- bzw. CAN-Status)* | `logs/projekt-stand.md` + `logs/can-bus-status.md` | `build_datalake.py`, `run_daily_pipeline.py`, `pipeline_checks.py`, `dlg_database.py` |

## Konventionen für neue Einträge

- Neue, datierte Erkenntnisse: unten ans passende `logs/*.md` anhängen, wie
  bisher (Überschrift mit Datum, chronologisch).
- Ändert sich dadurch der Ist-Stand zu einem Thema: den zugehörigen
  `status/*.md` unmittelbar mit aktualisieren (nicht erst bei der nächsten
  großen Aufräumaktion) — kurzer Verweis auf den neuen Log-Abschnitt genügt,
  keine Duplizierung der Herleitung.
- Neues Thema, das noch keinen Platz hat: Zeile in der Themen-Landkarte oben
  ergänzen, auch wenn das Status-Doc erstmal fehlt.
