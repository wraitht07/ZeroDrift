from datetime import datetime, timedelta, timezone
from src.backend.sanity_gate import validate_telemetry_window


def test_valid_window_passes():
    rows = [
        {"elapsed_min": 0, "chamber_id": "CH-01_SEAFOOD", "temp_c": -21.0},
        {"elapsed_min": 1, "chamber_id": "CH-01_SEAFOOD", "temp_c": -20.8},
        {"elapsed_min": 2, "chamber_id": "CH-01_SEAFOOD", "temp_c": -20.5},
    ]
    res = validate_telemetry_window("CH-01_SEAFOOD", rows)
    assert res["passed"] is True
    assert res["reason"] == "OK"


def test_flatline_sensor_rejected():
    rows = [
        {"elapsed_min": i, "chamber_id": "CH-01_SEAFOOD", "temp_c": -20.0}
        for i in range(6)
    ]
    res = validate_telemetry_window("CH-01_SEAFOOD", rows)
    assert res["passed"] is False
    assert res["reason"] == "SENSOR_FLATLINE"


def test_blackout_null_telemetry_rejected():
    rows = [
        {"elapsed_min": 0, "chamber_id": "CH-02_VACCINES", "temp_c": None},
        {"elapsed_min": 1, "chamber_id": "CH-02_VACCINES", "temp_c": None},
    ]
    res = validate_telemetry_window("CH-02_VACCINES", rows)
    assert res["passed"] is False
    assert res["reason"] == "BLACKOUT_OR_NULL_TELEMETRY"


def test_stale_packet_rejected():
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=25)).isoformat()
    rows = [
        {
            "elapsed_min": 0,
            "chamber_id": "CH-03_HORTICULTURE",
            "temp_c": 6.5,
            "timestamp": old_ts,
        }
    ]
    res = validate_telemetry_window("CH-03_HORTICULTURE", rows)
    assert res["passed"] is False
    assert res["reason"] == "STALE_TELEMETRY_PACKET"
