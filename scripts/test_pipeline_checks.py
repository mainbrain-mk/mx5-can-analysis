"""Minimaler Selbsttest fuer pipeline_checks.py - synthetische Fixtures,
prueft dass jede Schwellwert-Regel bei einem klaren Verletzungsfall
tatsaechlich ein Finding auswirft und bei einem unauffaelligen Fall keins.
Kein Framework, kein pytest noetig: `python scripts/test_pipeline_checks.py`.
"""
import json
import os
import shutil
import tempfile

import pipeline_checks as pc


def with_results_dir(fixtures):
    """Kontextmanager-Ersatz: legt fixtures (name -> obj) in ein Temp-
    results/-Verzeichnis, patcht pc.RESULTS_DIR/DERIVED_DIR dorthin."""
    tmp = tempfile.mkdtemp()
    for name, obj in fixtures.items():
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    return tmp


def cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


def test_vibration():
    tmp = with_results_dir({
        "derived/2026-01-01 000000_vibration_summary.json": {
            "axes": {"AccelerationX": {"resonance_peak_hz": 6.7}}
        }
    })
    pc.DERIVED_DIR, orig = os.path.join(tmp, "derived"), pc.DERIVED_DIR
    findings = pc.check_vibration(["2026-01-01 000000"])
    pc.DERIVED_DIR = orig
    cleanup(tmp)
    assert len(findings) == 1 and findings[0]["category"] == "vibration", findings
    assert pc.check_vibration(["nonexistent-log"]) == []


def test_brake_axis():
    data = [
        {"file": "2026-01-01 000000.dlg", "horizontal_axes": ["X", "Z"],
         "theta_r": 0.5, "forward_direction_deg": 60.0, "vertical_axis_defaulted": False},
        {"file": "2026-01-02 000000.dlg", "horizontal_axes": ["X", "Y"],
         "theta_r": 0.9, "forward_direction_deg": 60.0, "vertical_axis_defaulted": False},
    ]
    tmp = with_results_dir({"brake_event_summary.json": data})
    pc.RESULTS_DIR, orig = tmp, pc.RESULTS_DIR
    findings = pc.check_brake_axis(["2026-01-01 000000", "2026-01-02 000000"])
    pc.RESULTS_DIR = orig
    cleanup(tmp)
    categories = {f["category"] for f in findings}
    assert "brake_axis" in categories
    assert any("R=0.50" in f["message"] for f in findings), findings
    assert any("Halterung geaendert" in f["message"] for f in findings), findings

    tmp2 = with_results_dir({"brake_event_summary.json": [{
        "file": "2026-02-01 000000.dlg", "horizontal_axes": ["X", "Z"],
        "theta_r": 0.95, "forward_direction_deg": 60.0, "vertical_axis_defaulted": False,
    }]})
    pc.RESULTS_DIR, orig = tmp2, pc.RESULTS_DIR
    findings_ok = pc.check_brake_axis(["2026-02-01 000000"])
    pc.RESULTS_DIR = orig
    cleanup(tmp2)
    assert findings_ok == [], findings_ok


def test_corner_events():
    data = [{"file": "2026-01-01 000000.dlg", "events": [
        {"t_start": 1.0, "t_end": 2.0, "a_lat_mean_g": 0.1, "a_lat_peak_g": 0.5},
        {"t_start": 3.0, "t_end": 4.0, "a_lat_mean_g": 0.4, "a_lat_peak_g": 0.5},
    ]}]
    tmp = with_results_dir({"corner_event_summary.json": data})
    pc.RESULTS_DIR, orig = tmp, pc.RESULTS_DIR
    findings = pc.check_corner_events(["2026-01-01 000000"])
    pc.RESULTS_DIR = orig
    cleanup(tmp)
    assert len(findings) == 1, findings


def test_unmapped_channels():
    tmp = with_results_dir({"datalake_build_summary.json": {
        "n_unmapped_channels": 3,
        "unmapped_channels_overall": ["Foo"],
        "unmapped_channels_by_log": {"2026-01-01 000000": ["Foo"]},
    }})
    pc.RESULTS_DIR, orig = tmp, pc.RESULTS_DIR
    findings = pc.check_unmapped_channels(["2026-01-01 000000"])
    pc.RESULTS_DIR = orig
    cleanup(tmp)
    assert len(findings) == 2, findings

    tmp2 = with_results_dir({"datalake_build_summary.json": {
        "n_unmapped_channels": 0, "unmapped_channels_overall": [], "unmapped_channels_by_log": {},
    }})
    pc.RESULTS_DIR, orig = tmp2, pc.RESULTS_DIR
    assert pc.check_unmapped_channels(["2026-01-01 000000"]) == []
    pc.RESULTS_DIR = orig
    cleanup(tmp2)


def test_drivetrain_ratio():
    data = [{"file": "2026-01-01 000000.dlg", "a_measured_ms2": 1.0, "a_model_ms2": 2.0}]
    tmp = with_results_dir({"drivetrain_model_validation_summary.json": data})
    pc.RESULTS_DIR, orig = tmp, pc.RESULTS_DIR
    findings = pc.check_drivetrain_ratio(["2026-01-01 000000"])
    pc.RESULTS_DIR = orig
    cleanup(tmp)
    assert len(findings) == 1, findings

    data_ok = [{"file": "2026-01-01 000000.dlg", "a_measured_ms2": 0.94, "a_model_ms2": 1.0}]
    tmp2 = with_results_dir({"drivetrain_model_validation_summary.json": data_ok})
    pc.RESULTS_DIR, orig = tmp2, pc.RESULTS_DIR
    assert pc.check_drivetrain_ratio(["2026-01-01 000000"]) == []
    pc.RESULTS_DIR = orig
    cleanup(tmp2)


def test_dgm_gaps():
    tmp = with_results_dir({"dgm_coverage_gaps.json": [
        {"log_id": "2026-01-01 000000", "t_start": 1.0, "t_end": 2.0}
    ]})
    pc.RESULTS_DIR, orig = tmp, pc.RESULTS_DIR
    findings = pc.check_dgm_gaps(["2026-01-01 000000"])
    findings_none = pc.check_dgm_gaps(["other-log"])
    pc.RESULTS_DIR = orig
    cleanup(tmp)
    assert len(findings) == 1 and findings_none == []


def test_new_coastdown_events():
    current = [{"log_id": "old", "t_start": 0, "t_end": 1, "duration_s": 1}] * 2
    current.append({"log_id": "2026-01-01 000000", "t_start": 5, "t_end": 10, "duration_s": 5})
    findings = pc.check_new_coastdown_events(previous_event_count=2, current_summary=current)
    assert len(findings) == 1, findings
    assert pc.check_new_coastdown_events(previous_event_count=3, current_summary=current) == []


def test_steering_model_shift():
    tmp = with_results_dir({
        "steering_lateral_model_summary.json": {"k": [0.02, -0.0001], "r2": 0.90, "calibration_points": [1, 2]},
    })
    ref_path = os.path.join(tmp, "steering_model_reference.json")
    with open(ref_path, "w", encoding="utf-8") as f:
        json.dump({"k1": 0.023, "r2": 0.95, "n": 2}, f)
    pc.RESULTS_DIR, orig_r = tmp, pc.RESULTS_DIR
    pc.STEERING_REFERENCE_PATH, orig_p = ref_path, pc.STEERING_REFERENCE_PATH
    findings = pc.check_steering_model_shift()
    pc.RESULTS_DIR, pc.STEERING_REFERENCE_PATH = orig_r, orig_p
    cleanup(tmp)
    assert len(findings) == 2, findings  # R² und k1 beide deutlich verschoben


def test_script_errors():
    findings = pc.check_script_errors([("build_datalake.py", "exit code 1")])
    assert len(findings) == 1 and findings[0]["category"] == "script_error"
    assert pc.check_script_errors([]) == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")
