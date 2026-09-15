"""Selbsttest fuer parse_candump: abgeschnittene/defekte Zeilen duerfen den
ganzen Lauf nicht abbrechen (siehe candump-2026-09-15_171047.log - dort hatte
ein harter Stromverlust die letzte Zeile mitten im Frame abgeschnitten und
damit das komplette Log aus der automatischen Pipeline geworfen)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from can_log_parser import parse_candump


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


if __name__ == "__main__":
    test_truncated_last_line()
    print("OK")
