"""Test: TPMS-Request-Framing und Response-Decoding, ohne echten CAN-Bus."""
import sys

sys.path.insert(0, "scripts")
from tpms_poller import build_request, decode_response, PIDS


def test_build_request_frames_did_correctly():
    assert build_request(0x2A05) == [0x03, 0x22, 0x2A, 0x05, 0x00, 0x00, 0x00, 0x00]
    assert build_request(0x2A0D) == [0x03, 0x22, 0x2A, 0x0D, 0x00, 0x00, 0x00, 0x00]


def test_decode_response_extracts_raw_value():
    # Tire1-Druck, raw=200 -> passt zur Formel (200*1373/1000)/100 = 2.746 bar
    data = bytes([0x04, 0x62, 0x2A, 0x05, 200, 0x00, 0x00, 0x00])
    raw = decode_response(0x2A05, data)
    assert raw == 200
    name, formula = PIDS[0x2A05]
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
    name, formula = PIDS[0x2A0A]
    assert formula(50) == 0  # 0 Grad Referenzpunkt der Formel
    assert formula(30) == -20


if __name__ == "__main__":
    test_build_request_frames_did_correctly()
    test_decode_response_extracts_raw_value()
    test_decode_response_rejects_wrong_did()
    test_decode_response_rejects_negative_response()
    test_decode_response_rejects_short_frame()
    test_temp_formula()
    print("OK")
