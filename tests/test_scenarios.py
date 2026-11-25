from fastapi.testclient import TestClient
from src.backend.agent.react_agent import run_react_agent
from src.backend.main import app
from src.backend.memory import reset_runtime_state

client = TestClient(app)


def test_all_four_scenarios_end_to_end():
    reset_runtime_state(seed_alerts=[])

    r1 = client.post("/api/scenarios/loading_surge")
    assert r1.status_code == 200
    t1 = r1.json()["ticket"]
    assert t1["tier"] == "TIER_1_AUTONOMOUS"
    assert t1["root_cause_code"] == "LOADING_SURGE"

    r2 = client.post("/api/scenarios/stuck_defrost")
    assert r2.status_code == 200
    t2 = r2.json()["ticket"]
    assert t2["tier"] == "TIER_2_HITL"
    assert t2["root_cause_code"] == "STUCK_DEFROST_HEATER"

    r3 = client.post("/api/scenarios/solar_eb_outage")
    assert r3.status_code == 200
    t3 = r3.json()["ticket"]
    assert t3["tier"] == "TIER_2_HITL"
    assert t3["root_cause_code"] == "DG_CRANK_FAIL"

    r4 = client.post("/api/scenarios/unknown_leak")
    assert r4.status_code == 200
    t4 = r4.json()["ticket"]
    assert t4["tier"] == "TIER_2_HITL"
    assert t4["root_cause_code"] == "UNKNOWN_THERMAL_LEAK"


def test_overreach_penalty_on_critical_write_and_turn_limit():
    window = [{"elapsed_min": 0, "chamber_id": "CH-01_SEAFOOD", "temp_c": -20.0}]
    power = {"grid_v": 415.0}

    def rogue_llm(_msgs, _schemas):
        return {"tool": "write_plc", "args": {"coil": 1}}

    res = run_react_agent("INC-ROGUE", "CH-01_SEAFOOD", window, power, llm_fn=rogue_llm)
    assert res["guardrail_violation_flag"] is True
    assert res["confidence"] == 0.35
    assert res["status"] == "ESCALATED_HITL"

    def looping_llm(_msgs, _schemas):
        return {"tool": "lookup_sop", "args": {"query": "AUTO-01"}}

    res_loop = run_react_agent("INC-LOOP", "CH-01_SEAFOOD", window, power, llm_fn=looping_llm)
    assert res_loop["guardrail_violation_flag"] is True
    assert res_loop["violation_reason"] == "MAX_3_TURNS_EXCEEDED"
