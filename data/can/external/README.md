# Fremde DBC-Dateien (Referenz, nicht unsere Arbeit)

## `opendbc_mazda_2017.dbc`
Quelle: <https://github.com/commaai/opendbc>, `opendbc/dbc/mazda_2017.dbc`, MIT-Lizenz.
Gilt fuer Mazda CX-5 / Mazda3 ab MY2017 - **nicht** fuer den MX-5 ND. Uebernommen, weil
diese Fahrzeuge auf derselben SkyActiv-Busarchitektur sitzen: 84 von 102 CAN-IDs sind mit
unseren identisch, und bei 58 davon sind dort mehr Signale definiert als bei uns.

Beim Import minimal gepatcht, damit `cantools` die Datei laedt - **keine inhaltliche
Aenderung**:
- Message-/Signalnamen duerfen in DBC nicht mit einer Ziffer beginnen
  (`2017_5` -> `M2017_5`, `5_SEC_DISABLE_TIMER` -> `S5_SEC_DISABLE_TIMER`).
- `VAL_`-Wertetabellen ohne abschliessendes Semikolon entfernt (brauchen wir nicht).

**Nicht blind uebernehmen.** Jedes Signal daraus gehoert durch
`scripts/can_opendbc_crosscheck.py` gegen unsere eigenen Logs geprueft, bevor es in
`MX5ND_6thGenMazda_HSCAN_extended.dbc` wandert. Erfahrung aus der gitgc-Quelle (2026-09-15):
eine Fremdformel war goldrichtig (AmbientTemp), eine andere fuer unser Fahrzeug unbrauchbar.
