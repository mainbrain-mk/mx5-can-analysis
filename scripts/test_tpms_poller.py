"""Test: TPMS-Request-Framing und Response-Decoding, ohne echten CAN-Bus."""
import sys

sys.path.insert(0, "scripts")
from tpms_poller import build_request, decode_response, PIDS, PCM_PIDS


def test_build_request_frames_did_correctly():
    assert build_request(0x2A05) == [0x03, 0x22, 0x2A, 0x05, 0x00, 0x00, 0x00, 0x00]
    assert build_request(0x2A0D) == [0x03, 0x22, 0x2A, 0x0D, 0x00, 0x00, 0x00, 0x00]


def test_decode_response_extracts_raw_value():
    # Tire1-Druck, raw=200 -> passt zur Formel (200*1373/1000)/100 = 2.746 bar
    data = bytes([0x04, 0x62, 0x2A, 0x05, 200, 0x00, 0x00, 0x00])
    raw = decode_response(0x2A05, data)
    assert raw == 200
    name, n_bytes, formula = PIDS[0x2A05]
    assert n_bytes == 1
    assert round(formula(raw), 3) == 2.746


def test_decode_response_rejects_wrong_did():
    # Antwort auf eine ANDERE PID darf nicht als Treffer durchgehen
    data = bytes([0x04, 0x62, 0x2A, 0x06, 200, 0x00, 0x00, 0x00])
    assert decode_response(0x2A05, data) is None


def test_decode_response_rejects_negative_response():
    # UDS Negative Response: SID 0x7F statt 0x62 (z.B. PID nicht unterstuetzt)
    data = bytes([0x03, 0x7F, 0x22, 0x31, 0x00, 0x00, 0x00, 0x00])
    assert decode_response(0x2A05, data) is None


def test_decode_response_rejects_short_frame():
    assert decode_response(0x2A05, bytes([0x02, 0x62, 0x2A])) is None


def test_temp_formula():
    name, n_bytes, formula = PIDS[0x2A0A]
    assert formula(50) == 0  # 0 Grad Referenzpunkt der Formel
    assert formula(30) == -20


def test_oil_temp_group():
    """Zweite Pollgruppe (PCM 0x7E0): 2-Byte-Antwort, eigene Formel."""
    name, n_bytes, formula = PCM_PIDS[0x1310]
    assert (name, n_bytes) == ("OilTemp_C", 2)
    # echte Antwort aus candump-2026-09-12_211833: raw 7053 -> 30,53 Grad
    data = bytes([0x04, 0x62, 0x13, 0x10, 0x1B, 0x8D, 0x00, 0x00])
    raw = decode_response(0x1310, data, n_bytes)
    assert raw == 7053
    assert round(formula(raw), 2) == 30.53


def test_oil_temp_ignores_foreign_response():
    """0x7E0 teilt sich den Header mit dem Handy-OBD-Adapter - eine Antwort auf eine
    fremde DID darf NICHT als Oeltemperatur durchgehen (sonst landen z.B. AFR-Rohwerte
    als Temperatur im Datalake)."""
    foreign = bytes([0x04, 0x62, 0xDA, 0x85, 0x7F, 0x00, 0x00, 0x00])
    assert decode_response(0x1310, foreign, 2) is None


def test_single_byte_decode_unchanged():
    """Default n_bytes=1 - alte Aufrufer ohne das neue Argument bleiben korrekt."""
    data = bytes([0x04, 0x62, 0x2A, 0x05, 200, 0x00, 0x00, 0x00])
    assert decode_response(0x2A05, data) == 200


if __name__ == "__main__":
    test_build_request_frames_did_correctly()
    test_decode_response_extracts_raw_value()
    test_decode_response_rejects_wrong_did()
    test_decode_response_rejects_negative_response()
    test_decode_response_rejects_short_frame()
    test_temp_formula()
    test_oil_temp_group()
    test_oil_temp_ignores_foreign_response()
    test_single_byte_decode_unchanged()
    print("alle Tests ok")
    print("OK")
