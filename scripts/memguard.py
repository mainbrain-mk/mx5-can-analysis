"""RAM-Schutz fuer parallele Laeufe (Projektregel 24.11.: kein Swap).

Am 24.11. lief der Rechner voll (39 GB RAM + 7,7 GB Swap): die Pools starteten 6-7 Worker
nur nach Kernzahl, und jeder parst ein candump-Log (bis 860 MB) in mehrere GB Python-Listen.

    from memguard import bounded_map, connect
    for r in bounded_map(fn, items):   # wie ex.map: Reihenfolge bleibt
        ...
    con = connect()                    # duckdb.connect mit memory_limit

bounded_map startet einen weiteren Worker-Auftrag nur, wenn danach noch RESERVE_GB frei sind
(MemAvailable aus /proc/meminfo); sonst wartet er, bis ein laufender fertig ist. Der erste
Auftrag startet immer, damit nichts verhungert.

Harte Grenze zusaetzlich: grosse Laeufe ueber scripts/run_capped.sh starten.
"""
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

RESERVE_GB = float(os.environ.get("MEMGUARD_RESERVE_GB", 8))
# Ein neuer Worker braucht Sekunden, bis sein Speicher sichtbar ist; so lange nach jedem
# Start nicht erneut messen, sonst starten alle auf einmal gegen denselben freien Wert.
STAGGER_S = float(os.environ.get("MEMGUARD_STAGGER_S", 5))


def mem_available_gb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    raise RuntimeError("MemAvailable fehlt in /proc/meminfo")


def default_workers():
    return max(1, (os.cpu_count() or 2) - 2)


def bounded_map(fn, *iterables, workers=None, reserve_gb=None, stagger_s=None, _mem=mem_available_gb):
    """Wie ProcessPoolExecutor.map (geordnet, mehrere Iterables), aber RAM-gebremst.

    ponytail: Freigabe nur ueber MemAvailable + Wartezeit, keine Bedarfsschaetzung je Auftrag;
    reicht das nicht, Bedarf je Auftrag messen und vorab abziehen.
    """
    reserve_gb = RESERVE_GB if reserve_gb is None else reserve_gb
    stagger_s = STAGGER_S if stagger_s is None else stagger_s
    args = list(zip(*iterables))
    workers = min(workers or default_workers(), max(1, len(args)))
    if workers == 1:
        for a in args:
            yield fn(*a)
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs, nxt, last_start, held = [], 0, 0.0, False
        for i in range(len(args)):
            while True:
                running = [f for f in futs[i:] if not f.done()]
                if futs[i:] and futs[i].done():
                    break
                can_start = nxt < len(args) and len(running) < workers
                if can_start and running and time.monotonic() - last_start < stagger_s:
                    wait(running, timeout=stagger_s - (time.monotonic() - last_start),
                         return_when=FIRST_COMPLETED)
                    continue
                if can_start and (not running or _mem() > reserve_gb):
                    futs.append(ex.submit(fn, *args[nxt]))
                    nxt += 1
                    last_start = time.monotonic()
                    held = False
                    continue
                if can_start and not held:
                    print(f"[memguard] nur {_mem():.1f} GB frei (< {reserve_gb:g}), "
                          f"warte mit Auftrag {nxt + 1}/{len(args)}", flush=True)
                    held = True
                wait(running, timeout=2.0, return_when=FIRST_COMPLETED)
            yield futs[i].result()


def connect(*a, memory_limit=os.environ.get("MEMGUARD_DUCKDB_LIMIT", "4GB"), **kw):
    """duckdb.connect mit Speichergrenze (Default sonst 80 % RAM *je Verbindung*);
    groessere Abfragen lagert DuckDB dann selbst auf Platte aus."""
    import duckdb
    con = duckdb.connect(*a, **kw)
    con.execute(f"SET memory_limit = '{memory_limit}'")
    return con


def _sq(x, y=0):
    time.sleep(0.05)
    return x * x + y


def self_test():
    assert 0 < mem_available_gb() < 4096
    assert list(bounded_map(_sq, range(6), workers=3, stagger_s=0)) == [0, 1, 4, 9, 16, 25]
    assert list(bounded_map(_sq, [1, 2], [10, 20], workers=2, stagger_s=0)) == [11, 24]
    assert list(bounded_map(_sq, [3], workers=4)) == [9]          # seriell, kein Pool
    # kein freier RAM: laeuft trotzdem durch, nur einer zur Zeit
    assert list(bounded_map(_sq, range(4), workers=4, reserve_gb=1e9, stagger_s=0)) == [0, 1, 4, 9]
    con = connect()
    assert con.execute("SELECT current_setting('memory_limit')").fetchone()[0].startswith("3.7")
    print("memguard self-test ok")


if __name__ == "__main__":
    self_test()
