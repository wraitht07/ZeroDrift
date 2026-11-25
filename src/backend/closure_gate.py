from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List
from src.backend.memory import append_verified_memory, get_alert, upsert_alert
from src.backend.sanity_gate import load_profiles, normalize_chamber


def verify_ticket_closure(
    chamber_id: str,
    recent_ticks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    profiles = load_profiles()
    cg = profiles["closure_gate"]
    req_ticks = int(cg["required_stable_ticks"])
    ch_key = normalize_chamber(chamber_id, profiles["chambers"])
    if not ch_key:
        return {"verified": False, "reason": "UNKNOWN_CHAMBER_ID"}

    ch = profiles["chambers"][ch_key]
    temps = [
        float(t["temp_c"]) for t in recent_ticks
        if t.get("temp_c") is not None
    ]
    if len(temps) < req_ticks:
        return {
            "verified": False,
            "reason": f"NEED_{req_ticks}_STABLE_TICKS",
            "chamber_id": ch_key,
        }

    tail = temps[-req_ticks:]
    in_band = all(ch["temp_min_c"] <= x <= ch["temp_max_c"] for x in tail)
    non_rising = all(tail[i] <= tail[i - 1] for i in range(1, len(tail)))

    if not in_band:
        return {
            "verified": False,
            "reason": "TEMP_OUTSIDE_SAFE_BAND",
            "chamber_id": ch_key,
            "checked_ticks": tail,
        }
    if cg.get("require_non_rising_slope", True) and not non_rising:
        return {
            "verified": False,
            "reason": "TEMP_STILL_RISING",
            "chamber_id": ch_key,
            "checked_ticks": tail,
        }

    return {
        "verified": True,
        "reason": "VERIFIED_3_STABLE_TICKS",
        "chamber_id": ch_key,
        "checked_ticks": tail,
    }


def enforce_and_close_ticket(
    incident_id: str,
    supervisor_name: str,
    resolution_notes: str = "",
    recent_ticks: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    ticket = get_alert(incident_id)
    if not ticket:
        raise KeyError(f"Incident {incident_id} not found.")

    tier = str(ticket.get("tier", "TIER_2_HITL")).upper()
    if tier == "TIER_3_QUARANTINE":
        raise PermissionError(
            "Gate 3 Blocked: Tier 3 Quarantine requires mandatory CAPA seal."
        )

    if tier != "TIER_1_AUTONOMOUS":
        gate3 = verify_ticket_closure(
            ticket.get("chamber_id", "CH-01_SEAFOOD"),
            recent_ticks or [],
        )
        if not gate3["verified"]:
            raise ValueError(f"Gate 3 Closure Locked: {gate3['reason']}")

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = upsert_alert({
        "incident_id": incident_id,
        "status": "CLOSED_VERIFIED",
        "closed": True,
        "closed_by": supervisor_name,
        "resolution_notes": resolution_notes,
        "closed_at": now_iso,
    })

    append_verified_memory({
        "incident_id": incident_id,
        "chamber_id": updated.get("chamber_id"),
        "root_cause_code": updated.get("root_cause_code"),
        "tier": tier,
        "recommended_action": updated.get("recommended_action"),
        "supervisor_name": supervisor_name,
        "verified_by_gate3": True,
    })
    return updated
