"""Ergebnis-Cache je Log fuer die Modellskripte (02.10.2026).

Die Modellskripte rechneten bei jedem Lauf ALLE Logs neu (Volllast 17 min, Vmax 11 min, ...),
obwohl ein neues Log nur sein eigenes Ergebnis beisteuert. cached() rechnet ein Log nur neu, wenn
sich etwas geaendert hat, das sein Ergebnis beeinflusst:
  - das Log selbst (content_fingerprint + schema_version aus der Datalake-Tabelle `logs`),
  - der Code (Inhalt der uebergebenen deps-Dateien, z.B. das Skript selbst + datalake_channels.py),
  - die Abdeckung durch CAN-Logs (dlg-Samples dort verwirft datalake_channels.load_channel),
  - log-spezifische Eingaben (`extra`, z.B. die Masse aus data/log_mass_overrides.json).
Die Zusammenfassung ueber alle Logs bleibt in den Skripten und ist billig.

Ablage: data/cache/<name>/<log_id>.pkl (nicht im Repo). Komplett neu rechnen: Ordner loeschen.
"""
import functools
import hashlib
import os
import pickle

from datalake_channels import can_covered_intervals

CACHE_DIR = "data/cache"


@functools.lru_cache(maxsize=None)
def _file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _key(con, log_id, deps, extra):
    row = con.execute("SELECT content_fingerprint, schema_version FROM logs WHERE log_id = ?",
                      [log_id]).fetchone()
    coverage = [(round(a, 3), round(b, 3), c) for a, b, c in can_covered_intervals(con).get(log_id, [])]
    raw = repr((row, [(d, _file_hash(d)) for d in sorted(deps)], coverage, extra))
    return hashlib.sha256(raw.encode()).hexdigest()


@functools.lru_cache(maxsize=None)
def dir_inventory(*dirs):
    """Hash der Dateinamen in dirs (rekursiv) - fuer `extra`, wenn Ergebnisse von manuell
    nachgeladenen Dateien abhaengen (z.B. Hoehendaten-Kacheln: neue Kachel -> neu rechnen)."""
    names = sorted(os.path.join(root, f) for d in dirs for root, _, files in os.walk(d) for f in files)
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def cached(con, name, log_id, compute, deps, extra=None):
    """Ergebnis von compute() fuer log_id - aus dem Cache, solange der Schluessel passt."""
    key = _key(con, log_id, deps, extra)
    path = os.path.join(CACHE_DIR, name, log_id.replace("/", "_") + ".pkl")
    try:
        with open(path, "rb") as f:
            stored_key, value = pickle.load(f)
        if stored_key == key:
            return value
    except (OSError, EOFError, pickle.UnpicklingError):
        pass
    value = compute()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump((key, value), f)
    os.replace(tmp, path)  # atomar: ein Abbruch hinterlaesst keinen halben Eintrag
    return value


if __name__ == "__main__":
    import shutil
    import tempfile
    import duckdb
    tmp = tempfile.mkdtemp()
    CACHE_DIR = os.path.join(tmp, "cache")
    dep = os.path.join(tmp, "dep.py")
    open(dep, "w").write("a = 1\n")
    con = duckdb.connect()
    con.execute("CREATE TABLE logs (log_id VARCHAR, source_format VARCHAR, start_time_local TIMESTAMP, "
                "duration_s DOUBLE, content_fingerprint VARCHAR, schema_version VARCHAR)")
    con.execute("INSERT INTO logs VALUES ('x', 'can', '2026-10-02 12:00:00', 10, 'fp1', '10')")
    calls = []
    compute = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert cached(con, "t", "x", compute, [dep]) == 1
    assert cached(con, "t", "x", compute, [dep]) == 1 and len(calls) == 1  # Treffer
    assert cached(con, "t", "x", compute, [dep], extra=1180.0) == 2         # andere Masse
    con.execute("UPDATE logs SET content_fingerprint = 'fp2'")
    assert cached(con, "t", "x", compute, [dep], extra=1180.0) == 3         # Log geaendert
    open(dep, "w").write("a = 2\n")
    _file_hash.cache_clear()
    assert cached(con, "t", "x", compute, [dep], extra=1180.0) == 4         # Code geaendert
    shutil.rmtree(tmp)
    print("per_log_cache: Selbsttest OK")
