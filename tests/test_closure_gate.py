import pytest
from src.backend.closure_gate import enforce_and_close_ticket, verify_ticket_closure
from src.backend.memory import reset_runtime_state, upsert_alert


def test_gate3_rejects_fewer_than_3_ticks():
    ticks = [{"temp_c": -20.0}, {"temp_c": -20.5}]
    res = verify_ticket_closure("CH-01_SEAFOOD", ticks)
    assert res["verified"] is False
    assert res["reason"] == "NEED_3_STABLE_TICKS"


def test_gate3_rejects_rising_or_out_of_band_ticks():
    rising = [{"temp_c": -21.0}, {"temp_c": -20.0}, {"temp_c": -19.0}]
    res_rise = verify_ticket_closure("CH-01_SEAFOOD", rising)
    assert res_rise["verified"] is False
    assert res_rise["reason"] == "TEMP_STILL_RISING"

    out_band = [{"temp_c": -14.0}, {"temp_c": -15.0}, {"temp_c": -16.0}]
    res_band = verify_ticket_closure("CH-01_SEAFOOD", out_band)
    assert res_band["verified"] is False
    assert res_band["reason"] == "TEMP_OUTSIDE_SAFE_BAND"


def test_gate3_approves_3_stable_ticks_and_blocks_tier3():
    reset_runtime_state(seed_alerts=[])
    upsert_alert({
        "incident_id": "INC-T2",
        "chamber_id": "CH-01_SEAFOOD",
        "tier": "TIER_2_HITL",
        "root_cause_code": "STUCK_DEFROST_HEATER",
        "recommended_action": "Isolate stuck heater.",
    })
    stable = [{"temp_c": -19.0}, {"temp_c": -20.0}, {"temp_c": -20.5}]
    closed = enforce_and_close_ticket("INC-T2", "Ravi", "Fixed relay", stable)
    assert closed["closed"] is True
    assert closed["status"] == "CLOSED_VERIFIED"

    upsert_alert({
        "incident_id": "INC-T3",
        "chamber_id": "CH-01_SEAFOOD",
        "tier": "TIER_3_QUARANTINE",
        "root_cause_code": "BATCH_QUARANTINE_SEAL",
        "recommended_action": "Quarantine batch.",
    })
    with pytest.raises(PermissionError):
        enforce_and_close_ticket("INC-T3", "Ravi", "Override", stable)
