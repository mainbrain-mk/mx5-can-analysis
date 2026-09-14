import os
import sys
import pandas as pd
import sqlite3

RAW_DIR = "data/raw"
DERIVED_DIR = "data/derived"

DB_FILE = "2026-08-28 143138.dlg"
if len(sys.argv) > 1:
    DB_FILE = sys.argv[1]
DB_BASENAME = os.path.basename(DB_FILE)

conn = sqlite3.connect(os.path.join(RAW_DIR, DB_BASENAME))

# 1. Daten inklusive Einheiten und Kategorien laden
query = """
SELECT 
    pde.Time AS raw_time,
    pme.PidName AS sensor_name,
    pme.MetricUnitName AS unit,
    pme.UnitCategory AS unit_category,
    pme.PidType AS pid_type,
    pde.Value AS value
FROM PidDataEntry pde
LEFT JOIN PidMetadataEntry pme ON pde.UniqueId = pme.UniqueId
ORDER BY pde.Time ASC;
"""

df = pd.read_sql_query(query, conn)
conn.close()

# 2. Sensornamen mit Einheiten zusammensetzen
df["unit_clean"] = df["unit"].fillna("").astype(str).str.strip()
df["full_sensor_name"] = df.apply(
    lambda r: f"{r['sensor_name']} ({r['unit_clean']})" if r["unit_clean"] else r["sensor_name"],
    axis=1
)

# 3. .NET Ticks in Zeitstempel umwandeln
ticks_offset = 621355968000000000
df["datetime"] = pd.to_datetime((df["raw_time"] - ticks_offset) / 10, unit="us")

start_time = df["datetime"].min()
df["elapsed_sec"] = (df["datetime"] - start_time).dt.total_seconds()

# 4. Spaltenreihenfolge festlegen (GPS an 2. Stelle)
sensor_meta = df[["full_sensor_name", "pid_type", "unit_category"]].drop_duplicates().dropna()
sensor_meta["is_gps"] = sensor_meta["pid_type"].apply(lambda x: 0 if x == 10 else 1)
sensor_meta = sensor_meta.sort_values(by=["is_gps", "pid_type", "unit_category", "full_sensor_name"])

sorted_sensor_names = sensor_meta["full_sensor_name"].tolist()

# 5. Pivot-Tabelle erstellen (OHNE Lücken aufzufüllen)
df_pivot = df.pivot_table(
    index=["datetime", "elapsed_sec"],
    columns="full_sensor_name",
    values="value"
).reset_index()

# 6. Finale Spaltenanordnung
base_cols = ["datetime", "elapsed_sec"]
ordered_cols = base_cols + [col for col in sorted_sensor_names if col in df_pivot.columns]
df_final = df_pivot[ordered_cols]

# 7. CSV Export (na_rep='' stellt sicher, dass fehlende Werte als leere Felder exportiert werden)
output_csv = os.path.join(DERIVED_DIR, DB_BASENAME.replace(".dlg", "_categorized.csv"))
df_final.to_csv(output_csv, index=False, na_rep="")

print("Export erfolgreich gespeichert unter:", output_csv)

# Load the current log file to inspect IMU columns deeply
df = pd.read_csv(output_csv)

imu_cols = [
    'AccelerationX (m/s²)', 'AccelerationY (m/s²)', 'AccelerationZ (m/s²)',
    'AccelerationWithGravityX (m/s²)', 'AccelerationWithGravityY (m/s²)', 'AccelerationWithGravityZ (m/s²)',
    'RotationRateX (deg/s)', 'RotationRateY (deg/s)', 'RotationRateZ (deg/s)',
    'Pitch (deg)', 'Roll (deg)'
]

imu_df = df.dropna(subset=['AccelerationX (m/s²)'])[['datetime', 'elapsed_sec'] + imu_cols].copy()
imu_df['datetime'] = pd.to_datetime(imu_df['datetime'])

# Inspect sampling intervals for IMU
dt_imu = imu_df['datetime'].diff().dt.total_seconds()

print("IMU Data count:", len(imu_df))
print("Sampling interval stats (sec):")
print(dt_imu.describe())
print("\nFirst 5 IMU samples:")
print(imu_df.head())