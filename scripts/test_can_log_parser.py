"""Selbsttest fuer parse_candump: abgeschnittene/defekte Zeilen duerfen den
ganzen Lauf nicht abbrechen (siehe candump-2026-09-15_171047.log - dort hatte
ein harter Stromverlust die letzte Zeile mitten im Frame abgeschnitten und
damit das komplette Log aus der automatischen Pipeline geworfen)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from can_log_parser import decode, load_db, parse_candump, passenger_occupied_seconds


def _frame(t, can_id, byte2):
    data = bytearray(8)
    data[2] = byte2
    return f"({t:.6f}) can0 {can_id:X}#{data.hex()}\n"


def test_passenger_occupied_seconds_settle_and_session_split():
    """Erste 80s je Session (settle_s) muessen ignoriert werden, und ein
    Log mit einer >60s-Luecke (zwei Zuendungszyklen in einer Datei, siehe
    candump-2026-09-11_201950.log) muss an der Luecke in zwei Sessions
    gesplittet werden statt die Luecke selbst als Zeit mitzuzaehlen."""
    lines = [
        # Session 1 (t0=0): Frame-Abstaende bewusst < gap_s (60s), sonst wuerde
        # schon eine normale Frame-Luecke innerhalb der Fahrt faelschlich als
        # Session-Ende erkannt.
        _frame(0, 0x340, 0x00),    # vor dem Settle-Fenster, muss ignoriert werden
        _frame(55, 0x340, 0x00),   # ebenfalls noch vor dem Settle-Fenster (toff=55<80)
        _frame(85, 0x340, 0x01),   # toff=85, Bit0=1 (belegt)
        _frame(95, 0x340, 0x01),
        _frame(105, 0x340, 0x00),  # Bit0=0 (leer)
        _frame(115, 0x340, 0x00),
        # 65s Luecke (> gap_s) -> neue Session, toff wieder ab 0
        _frame(180, 0x340, 0x00),  # vor dem Settle-Fenster der 2. Session
        _frame(230, 0x340, 0x00),  # toff=50, noch vor dem Settle-Fenster
        _frame(270, 0x340, 0x01),  # toff=90, belegt
        _frame(285, 0x340, 0x01),  # toff=105, belegt
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.writelines(lines)
        path = f.name
    try:
        occupied_s, total_s = passenger_occupied_seconds(path)
        assert abs(occupied_s - 50.0) < 0.01, f"erwartet 50.0s belegt, bekommen {occupied_s}"
        assert abs(total_s - 70.0) < 0.01, f"erwartet 70.0s gesamt, bekommen {total_s}"
    finally:
        os.unlink(path)


def test_passenger_occupied_seconds_no_0x340():
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(_frame(0, 0x165, 0x00))
        path = f.name
    try:
        assert passenger_occupied_seconds(path) == (0.0, 0.0)
    finally:
        os.unlink(path)


def test_truncated_last_line():
    content = (
        "(1789457252.659110) can0 165#46801E80080013DF\n"
        "(1789457252.659332) can0 166#00317BC00E630000\n"
        "(1789457252.659586) can0 076"          # abgeschnitten, kein "#", kein "\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        df = parse_candump(path)
        assert len(df) == 2, f"erwartet 2 gute Zeilen, bekommen {len(df)}"
        assert df.can_id.tolist() == [0x165, 0x166]
        assert df.data.iloc[0] == bytes.fromhex("46801E80080013DF")
    finally:
        os.unlink(path)


def test_vectorized_decode_matches_cantools():
    # decode() liest Bits per numpy (30.10.2026) - muss fuer jedes Signal der echten DBC exakt
    # liefern, was cantools Frame fuer Frame liefert: Wert, Typ, Enum-Name, Reihenfolge, auch
    # bei zu kurzen Frames (allow_truncated) und bei unbekannten IDs.
    db = load_db()
    rng = np.random.default_rng(0)
    rows = []
    for msg in db.messages:
        for _ in range(40):
            n = msg.length if rng.random() < 0.8 else int(rng.integers(0, msg.length + 1))
            rows.append((float(len(rows)), msg.frame_id, rng.integers(0, 256, n, dtype=np.uint8).tobytes()))
    rows.append((float(len(rows)), 0x7FF, b"\x01"))  # nicht im DBC
    order = rng.permutation(len(rows))
    df = pd.DataFrame([rows[i] for i in order], columns=["t", "can_id", "data"])
    got, undecoded = decode(df, db)
    by_id = {m.frame_id: m for m in db.messages}
    want = []
    for t, can_id, data in df.itertuples(index=False):
        if can_id in by_id:
            try:
                decoded = by_id[can_id].decode(data, allow_truncated=True)
            except Exception:
                continue
            want += [(t, can_id, by_id[can_id].name, sig, val) for sig, val in decoded.items()]
    assert undecoded == {0x7FF}
    got_rows = list(got.itertuples(index=False))
    assert len(got_rows) == len(want), (len(got_rows), len(want))
    for (t, c, m, s, v, txt), w in zip(got_rows, want):
        assert (t, c, m, s) == w[:4], w
        if isinstance(w[4], (int, float)):
            assert v == float(w[4]) and pd.isna(txt), w  # Zahl exakt, kein Text
        else:
            assert np.isnan(v) and txt == str(w[4]), w   # Enum-Name als Text


def test_parquet_roundtrip_keeps_order():
    # read_decoded mit langer Signalliste: DuckDB macht daraus einen Join, der parallel ungeordnet
    # liefert - ohne ORDER BY file_row_number kam die Reihenfolge durcheinander (31.10.).
    from can_log_parser import decoded_path, read_decoded, write_decoded
    n = 400_000
    df = pd.DataFrame({"t": np.arange(n) * 1e-3, "can_id": 1, "message": pd.Categorical(["M"] * n),
                       "signal": pd.Categorical([f"S{i % 50}" for i in range(n)]),
                       "value": np.arange(n, dtype=float), "value_text": pd.Categorical([None] * n)})
    tmpdir = tempfile.mkdtemp()
    try:
        log = os.path.join(tmpdir, "x.log")
        write_decoded(df, decoded_path(log))
        back = read_decoded(log, signals=[f"S{i}" for i in range(0, 50, 2)])
        assert (np.diff(back["value"].to_numpy()) > 0).all(), "Reihenfolge verloren"
        assert len(back) == n // 2 and set(back.columns) == set(df.columns)
    finally:
        import shutil
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    test_vectorized_decode_matches_cantools()
    test_parquet_roundtrip_keeps_order()
    test_truncated_last_line()
    test_passenger_occupied_seconds_settle_and_session_split()
    test_passenger_occupied_seconds_no_0x340()
    print("OK")
