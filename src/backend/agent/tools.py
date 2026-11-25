from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILES_PATH = REPO_ROOT / "data" / "sops" / "chamber_profiles.json"
SOP_MD_PATH = REPO_ROOT / "data" / "sops" / "cold_chain_sop.md"
MEMORY_PATH = REPO_ROOT / "data" / "runtime" / "agent_memory.json"


def _load_profiles() -> Dict[str, Any]:
    with PROFILES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _norm_chamber(chamber_id: str, chambers: Dict[str, Any]) -> str:
    cid = chamber_id.strip().upper()
    if cid in chambers:
        return cid
    for k in chambers:
        if k.startswith(cid):
            return k
    raise ValueError(f"Unknown chamber_id: {chamber_id}")


def inspect_telemetry(chamber_id: str, telemetry_window: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not telemetry_window:
        return {"status": "NO_DATA", "abstain": True}

    profiles = _load_profiles()
    ch_key = _norm_chamber(chamber_id, profiles["chambers"])
    ch = profiles["chambers"][ch_key]
    sg = profiles["sanity_gate"]

    valid = [
        t for t in telemetry_window
        if t.get("temp_c") is not None and not (isinstance(t["temp_c"], float) and math.isnan(t["temp_c"]))
    ]
    missing = len(telemetry_window) - len(valid)
    if not valid:
        return {"status": "BLACKOUT_GAP", "chamber_id": ch_key, "abstain": True, "missing_ticks": missing}

    n_flat = sg["flatline_window_ticks"]
    if len(valid) >= n_flat:
        temps = [float(t["temp_c"]) for t in valid[-n_flat:]]
        mean_t = sum(temps) / n_flat
        std_dev = math.sqrt(sum((x - mean_t) ** 2 for x in temps) / n_flat)
        if std_dev < sg["flatline_min_std_dev"]:
            return {"status": "SENSOR_FLATLINE", "chamber_id": ch_key, "abstain": True, "std_dev": round(std_dev, 6)}

    latest = valid[-1]
    curr_t = float(latest["temp_c"])
    nh3 = float(latest.get("nh3_ppm", 0.0) or 0.0)
    defrost = int(latest.get("defrost_relay", 0) or 0)
    door = int(latest.get("door_switch", 0) or 0)

    drift_min = 0.0
    step_min = 1.0
    if len(valid) >= 2:
        dt = float(latest.get("elapsed_min", len(valid) - 1)) - float(valid[0].get("elapsed_min", 0.0))
        if dt <= 0:
            dt = float(len(valid) - 1)
        drift_min = (curr_t - float(valid[0]["temp_c"])) / dt
        s = float(valid[-1].get("elapsed_min", 1)) - float(valid[-2].get("elapsed_min", 0))
        if s > 0:
            step_min = s

    defrost_min = sum(step_min for t in valid if int(t.get("defrost_relay", 0) or 0) == 1)
    door_min = sum(step_min for t in valid if int(t.get("door_switch", 0) or 0) == 1)
    cliff = float(ch["irreversible_spoilage_c"])
    runway = round(max(0.0, (cliff - curr_t) / drift_min), 1) if drift_min > 0.001 else None

    flags: List[str] = []
    if nh3 > 50.0:
        flags.append("NH3_GT_50PPM")
    if curr_t > cliff:
        flags.append("ABOVE_SPOILAGE_CLIFF")
    if defrost == 1 and defrost_min > ch["defrost_max_duration_min"]:
        flags.append("DEFROST_TIMEOUT")
    if missing > 0:
        flags.append(f"MISSING_{missing}_TICKS")

    return {
        "status": "OK",
        "chamber_id": ch_key,
        "regulator": ch["regulator"],
        "triage_priority": ch["triage_priority"],
        "load_shed_allowed": ch["load_shed_allowed"],
        "current_temp_c": round(curr_t, 2),
        "safe_band_c": [ch["temp_min_c"], ch["temp_max_c"]],
        "irreversible_spoilage_c": cliff,
        "drift_c_per_min": round(drift_min, 4),
        "thermal_runway_min": runway,
        "defrost_relay": defrost,
        "defrost_active_min": round(defrost_min, 1),
        "defrost_max_min": ch["defrost_max_duration_min"],
        "door_switch": door,
        "door_open_min": round(door_min, 1),
        "nh3_ppm": nh3,
        "flags": flags,
        "abstain": False,
    }


def check_power_bus(power_snapshot: Dict[str, Any], facility_overrides: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    profiles = _load_profiles()
    caps = dict(profiles["facility_capabilities"])
    if facility_overrides:
        caps.update(facility_overrides)
    p = profiles["power_thresholds"]

    grid_v = float(power_snapshot.get("grid_v", 0.0) or 0.0)
    imbalance = float(power_snapshot.get("phase_imbalance_pct", 0.0) or 0.0)
    solar_kw = float(power_snapshot.get("solar_kw", 0.0) or 0.0)
    outage_sec = float(power_snapshot.get("grid_outage_sec", 0.0) or 0.0)
    dg_status = str(power_snapshot.get("dg_status", "OFF")).upper()
    dg_kw = float(power_snapshot.get("dg_load_kw", 0.0) or 0.0)
    dg_fuel = power_snapshot.get("dg_fuel_liters")

    dg_rated_kw = p["dg_rated_kva"] * p["dg_power_factor"]
    dg_pct = round((dg_kw / dg_rated_kw) * 100.0, 1) if dg_rated_kw > 0 else 0.0
    dg_hrs = round(float(dg_fuel) / p["dg_burn_rate_lph"], 2) if caps["has_dg"] and dg_fuel is not None else None

    flags: List[str] = []
    mode = "GRID_NORMAL"

    if grid_v == 0.0:
        if caps["has_solar"] and solar_kw == 0.0:
            flags.append("SOLAR_ANTI_ISLANDING_OK")
        if not caps["has_dg"]:
            mode = "NO_DG_OUTAGE"
            flags.append("NO_DG_THERMAL_COAST_ONLY")
        elif dg_status == "ON":
            mode = "DG_ACTIVE"
            if dg_pct > p["dg_load_shed_trigger_pct"]:
                flags.append("DG_OVERLOAD_SHED_CH03")
            if dg_hrs is not None and dg_hrs <= p["dg_fuel_reminder_hrs"]:
                flags.append("DG_FUEL_LOW")
        elif outage_sec <= p["ats_switchover_grace_sec"]:
            mode = "ATS_CRANK_WINDOW"
        else:
            mode = "DG_CRANK_FAIL"
            flags.append("DG_CRANK_FAIL_GT_30S")
    else:
        if grid_v < p["grid_voltage_min_v"] or grid_v > p["grid_voltage_max_v"]:
            mode = "GRID_VOLTAGE_FAULT"
            flags.append("GRID_V_OUT_OF_BAND")
        if imbalance > p["phase_imbalance_max_pct"]:
            flags.append("PHASE_IMBALANCE_GT_3PCT")

    return {
        "power_mode": mode,
        "facility_capabilities": caps,
        "grid_v": grid_v,
        "solar_kw": solar_kw,
        "dg_status": dg_status if caps["has_dg"] else "NONE",
        "dg_load_pct": dg_pct if caps["has_dg"] else 0.0,
        "dg_fuel_hrs": dg_hrs,
        "flags": flags,
    }


def lookup_sop(query: str, chamber_id: Optional[str] = None) -> Dict[str, Any]:
    profiles = _load_profiles()
    lines = SOP_MD_PATH.read_text(encoding="utf-8").splitlines()
    kws = [w.lower() for w in query.replace("_", " ").split() if len(w) > 2]
    matched = [ln.strip() for ln in lines if any(k in ln.lower() for k in kws)]
    spec = None
    if chamber_id:
        try:
            k = _norm_chamber(chamber_id, profiles["chambers"])
            spec = {k: profiles["chambers"][k]}
        except ValueError:
            spec = None
    return {
        "autonomy_policy": profiles["autonomy_policy"],
        "closure_gate": profiles["closure_gate"],
        "chamber_spec": spec,
        "clauses": matched[:12] if matched else lines[:20],
    }


def search_verified_memory(chamber_id: Optional[str] = None, symptom_keywords: Optional[str] = None) -> Dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {"matches": []}
    raw = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else raw.get("entries", [])
    cid = chamber_id.strip().upper() if chamber_id else None
    kws = [w.lower() for w in (symptom_keywords or "").replace("_", " ").split() if len(w) > 2]

    out = []
    for e in entries:
        if e.get("verified_by_gate3") is False:
            continue
        ech = str(e.get("chamber_id", "")).upper()
        if cid and not (ech.startswith(cid) or cid.startswith(ech)):
            continue
        if not kws or any(k in json.dumps(e).lower() for k in kws):
            out.append(e)
    return {"matches": out[:5]}


TOOL_REGISTRY = {
    "inspect_telemetry": inspect_telemetry,
    "check_power_bus": check_power_bus,
    "lookup_sop": lookup_sop,
    "search_verified_memory": search_verified_memory,
}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "inspect_telemetry",
        "description": "Read-only thermal slope, runway, defrost/door duration, and NH3 check.",
        "parameters": {
            "type": "object",
            "properties": {
                "chamber_id": {"type": "string"},
                "telemetry_window": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["chamber_id", "telemetry_window"],
        },
    },
    {
        "name": "check_power_bus",
        "description": "Read-only 415V grid, CEA solar anti-islanding, and DG status/fuel check.",
        "parameters": {
            "type": "object",
            "properties": {
                "power_snapshot": {"type": "object"},
                "facility_overrides": {"type": "object"},
            },
            "required": ["power_snapshot"],
        },
    },
    {
        "name": "lookup_sop",
        "description": "Fetch Tier 1/2/3 SOP rules and chamber limits.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "chamber_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_verified_memory",
        "description": "Search Gate-3-verified episodic memory (max 20 entries).",
        "parameters": {
            "type": "object",
            "properties": {
                "chamber_id": {"type": "string"},
                "symptom_keywords": {"type": "string"},
            },
        },
    },
]
