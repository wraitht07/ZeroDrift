from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from src.backend.agent.tools import TOOL_REGISTRY, TOOL_SCHEMAS

ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = ROOT / "src" / "backend" / "agent" / "rules.md"
MAX_TURNS = 3
PENALTY = 0.40

WRITE_RE = re.compile(
    r"\b(write_plc|plc_write|modbus_write|write_register|set_coil|toggle_relay|trip_breaker|"
    r"switch_grid|dispatch_dg|start_dg|stop_dg|shed_relay|change_setpoint|actuate|grid_write)\b",
    re.I,
)
DOMAIN_RE = re.compile(
    r"\b(ch-0[123]|seafood|vaccine|horticulture|temp|defrost|door|nh3|grid|solar|dg|power|"
    r"fssai|mpeda|cdsco|nccd|cea|runway|drift|leak|blackout|flatline|tier_[123]|hitl|telemetry)\b",
    re.I,
)


def _penalize(inc_id: str, ch_id: str, reason: str, turns: int, trace: List[Dict[str, Any]], conf: float = 0.75) -> Dict[str, Any]:
    return {
        "incident_id": inc_id,
        "chamber_id": ch_id,
        "status": "ESCALATED_HITL",
        "tier": "TIER_2_HITL",
        "root_cause_code": "GUARDRAIL_VIOLATION",
        "guardrail_violation_flag": True,
        "violation_reason": reason,
        "confidence": round(max(0.0, conf - PENALTY), 2),
        "turns_used": turns,
        "thermal_runway_min": None,
        "recommended_action": f"BLOCKED ({reason}). HITL takeover required.",
        "closure_rule": "LOCKED_UNTIL_3_STABLE_TICKS",
        "maintenance_record_updated": False,
        "trace": trace,
    }


def _offline_run(inc_id: str, ch_id: str, window: List[Dict[str, Any]], power: Dict[str, Any], overrides: Optional[Dict[str, bool]]) -> Dict[str, Any]:
    t = TOOL_REGISTRY["inspect_telemetry"](chamber_id=ch_id, telemetry_window=window)
    trace = [{"turn": 1, "tool": "inspect_telemetry", "obs": t}]
    if t.get("abstain"):
        return {
            "incident_id": inc_id, "chamber_id": t.get("chamber_id", ch_id), "status": "ABSTAINED_SAFE",
            "tier": "TIER_3_QUARANTINE", "root_cause_code": f"ABSTAINED_{t.get('status')}",
            "guardrail_violation_flag": False, "confidence": 1.0, "turns_used": 1,
            "thermal_runway_min": None, "recommended_action": "Abstained: null/flatline sensor. Inspect probe.",
            "closure_rule": "QUARANTINE_CAPA_REQUIRED", "maintenance_record_updated": True, "trace": trace,
        }

    p = TOOL_REGISTRY["check_power_bus"](power_snapshot=power, facility_overrides=overrides)
    trace.append({"turn": 2, "tool": "check_power_bus", "obs": p})

    tf, pf, ch, rw = set(t.get("flags", [])), set(p.get("flags", [])), t["chamber_id"], t.get("thermal_runway_min")

    if "NH3_GT_50PPM" in tf:
        sop, tier, st, code, conf, cl, act = "UNSOLVABLE-01", "TIER_3_QUARANTINE", "QUARANTINED_LOCKED", "CATASTROPHIC_EVACUATION_LOCK", 0.99, "QUARANTINE_CAPA_REQUIRED", "NH3 > 50ppm: Evacuate & isolate power."
    elif "ABOVE_SPOILAGE_CLIFF" in tf:
        sop, tier, st, code, conf, cl, act = "UNSOLVABLE-02", "TIER_3_QUARANTINE", "QUARANTINED_LOCKED", "BATCH_QUARANTINE_SEAL", 0.98, "QUARANTINE_CAPA_REQUIRED", f"Cliff breached ({t['current_temp_c']}C). Quarantine ({t['regulator']})."
    elif "NO_DG_THERMAL_COAST_ONLY" in pf:
        sop, tier, st, code, conf, cl, act = "HUM-02", "TIER_2_HITL", "ESCALATED_HITL", "NO_DG_GRID_OUTAGE", 0.95, "LOCKED_UNTIL_3_STABLE_TICKS", f"Grid outage (no DG). Coast runway: {rw}m. Escalate."
    elif "DG_CRANK_FAIL_GT_30S" in pf:
        sop, tier, st, code, conf, cl, act = "HUM-02", "TIER_2_HITL", "ESCALATED_HITL", "DG_CRANK_FAIL", 0.96, "LOCKED_UNTIL_3_STABLE_TICKS", f"DG crank fail >30s. Runway: {rw}m. Dispatch tech."
    elif "DG_OVERLOAD_SHED_CH03" in pf:
        sop, tier, st, code, conf, cl, act = "HUM-02", "TIER_2_HITL", "ESCALATED_HITL", "DG_OVERLOAD_SHED_CH03", 0.93, "LOCKED_UNTIL_3_STABLE_TICKS", "DG >85%. Shed CH-03_HORTICULTURE; 25s stagger."
    elif "DEFROST_TIMEOUT" in tf:
        sop, tier, st, code, conf, cl, act = "HUM-01", "TIER_2_HITL", "ESCALATED_HITL", "STUCK_DEFROST_HEATER", 0.95, "LOCKED_UNTIL_3_STABLE_TICKS", f"Defrost >{t['defrost_max_min']}m. Isolate stuck heater."
    elif t["defrost_relay"] == 1 and t["defrost_active_min"] <= t["defrost_max_min"]:
        sop, tier, st, code, conf, cl, act = "AUTO-01", "TIER_1_AUTONOMOUS", "PENDING_HITL_REVIEW", "NORMAL_DEFROST_TRANSIENT", 0.94, "AUTO_CLOSED", "Normal defrost suppressed. Logged for HITL review."
    elif t["door_switch"] == 1 and t["door_open_min"] <= 15.0:
        sop, tier, st, code, conf, cl, act = "AUTO-02", "TIER_1_AUTONOMOUS", "PENDING_HITL_REVIEW", "LOADING_SURGE", 0.92, "AUTO_CLOSED", "Loading surge suppressed. Logged for HITL review."
    elif "SOLAR_ANTI_ISLANDING_OK" in pf and p["power_mode"] == "DG_ACTIVE":
        sop, tier, st, code, conf, cl, act = "AUTO-03", "TIER_1_AUTONOMOUS", "PENDING_HITL_REVIEW", "ANTI_ISLANDING_SHUTDOWN", 0.93, "AUTO_CLOSED", "CEA anti-islanding logged; DG active. HITL review."
    else:
        sop, tier, st, code, conf, cl, act = "HUM-03", "TIER_2_HITL", "ESCALATED_HITL", "UNKNOWN_THERMAL_LEAK", 0.86, "LOCKED_UNTIL_3_STABLE_TICKS", f"Drift {t['drift_c_per_min']}C/m. Runway: {rw}m. AMC check."

    s = TOOL_REGISTRY["lookup_sop"](query=sop, chamber_id=ch)
    trace.append({"turn": 3, "tool": "lookup_sop", "obs": {"rule": sop, "clauses": s.get("clauses", [])[:2]}})
    return {
        "incident_id": inc_id, "chamber_id": ch, "status": st, "tier": tier,
        "root_cause_code": code, "guardrail_violation_flag": False, "confidence": conf,
        "turns_used": 3, "thermal_runway_min": rw, "recommended_action": act,
        "closure_rule": cl, "maintenance_record_updated": True, "trace": trace,
    }


def run_react_agent(
    incident_id: str,
    chamber_id: str,
    telemetry_window: List[Dict[str, Any]],
    power_snapshot: Dict[str, Any],
    facility_overrides: Optional[Dict[str, bool]] = None,
    llm_fn: Optional[Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if WRITE_RE.search(json.dumps([telemetry_window, power_snapshot], default=str)):
        return _penalize(incident_id, chamber_id, "WRITE_IN_INPUT", 0, [])
    if llm_fn is None:
        return _offline_run(incident_id, chamber_id, telemetry_window, power_snapshot, facility_overrides)

    rules = RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else ""
    msgs: List[Dict[str, Any]] = [
        {"role": "system", "content": rules + "\nKeep recommended_action <= 15 words. JSON only."},
        {"role": "user", "content": json.dumps({"incident_id": incident_id, "chamber_id": chamber_id, "telemetry_window": telemetry_window[-6:], "power_snapshot": power_snapshot, "facility_overrides": facility_overrides}, separators=(",", ":"))},
    ]
    trace: List[Dict[str, Any]] = []

    for turn in range(1, MAX_TURNS + 2):
        if turn > MAX_TURNS:
            return _penalize(incident_id, chamber_id, "MAX_3_TURNS_EXCEEDED", turn - 1, trace)

        step = llm_fn(msgs, TOOL_SCHEMAS)
        raw_step = json.dumps(step, default=str)
        if WRITE_RE.search(raw_step):
            return _penalize(incident_id, chamber_id, "CRITICAL_WRITE_ATTEMPT", turn, trace)

        tool = step.get("tool")
        if tool:
            if tool not in TOOL_REGISTRY:
                return _penalize(incident_id, chamber_id, f"FORBIDDEN_TOOL:{tool}", turn, trace)
            obs = TOOL_REGISTRY[tool](**step.get("args", {}))
            trace.append({"turn": turn, "tool": tool, "obs": obs})
            msgs.extend([
                {"role": "assistant", "content": json.dumps(step, separators=(",", ":"))},
                {"role": "tool", "name": tool, "content": json.dumps(obs, separators=(",", ":"))},
            ])
            continue

        final = step.get("final", step)
        f_str = final if isinstance(final, str) else json.dumps(final)
        if WRITE_RE.search(f_str) or not DOMAIN_RE.search(f_str):
            return _penalize(incident_id, chamber_id, "OUT_OF_BOUNDS_OUTPUT", turn, trace)

        res = json.loads(final) if isinstance(final, str) else dict(final)
        tier = str(res.get("tier", "TIER_2_HITL")).upper()
        if tier != "TIER_1_AUTONOMOUS" and res.get("closure_rule") == "AUTO_CLOSED":
            return _penalize(incident_id, chamber_id, f"UNAUTHORIZED_SOLVE:{tier}", turn, trace, float(res.get("confidence", 0.75)))

        res["status"] = "PENDING_HITL_REVIEW" if tier == "TIER_1_AUTONOMOUS" else ("QUARANTINED_LOCKED" if tier == "TIER_3_QUARANTINE" else "ESCALATED_HITL")
        res.update({"turns_used": turn, "guardrail_violation_flag": False, "maintenance_record_updated": True, "trace": trace})
        return res

    return _penalize(incident_id, chamber_id, "MAX_3_TURNS_EXCEEDED", MAX_TURNS, trace)
