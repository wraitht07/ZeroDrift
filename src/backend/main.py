from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.backend.agent.react_agent import run_react_agent
from src.backend.closure_gate import enforce_and_close_ticket
from src.backend.memory import (
    archive_raw_slice,
    get_alert,
    load_alerts,
    load_verified_memory,
    reset_runtime_state,
    upsert_alert,
)
from src.backend.sanity_gate import load_profiles, validate_telemetry_window

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "data" / "scenarios"
STATIC_DIR = ROOT / "src" / "frontend"
IST = timezone(timedelta(hours=5, minutes=30))

NUM_COLS = {
    "elapsed_min", "temp_c", "rh_pct", "nh3_ppm", "defrost_relay",
    "door_switch", "grid_v", "phase_imbalance_pct", "solar_kw",
    "grid_outage_sec", "dg_load_kw", "dg_fuel_liters",
}

app = FastAPI(title="ZeroDrift PWA API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class CloseBody(BaseModel):
    supervisor_name: str = "Karthik R. (AMC / Shift Supervisor)"
    resolution_notes: str = ""
    recent_ticks: List[Dict[str, Any]] = []


def now_ist() -> str:
    return datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")


def to_num(val: Any) -> Optional[float]:
    if val is None or str(val).strip().lower() in {"", "null", "none", "nan", "na"}:
        return None
    try:
        num = float(str(val).strip())
        return None if math.isnan(num) else num
    except ValueError:
        return None


def parse_csv(text: str) -> List[Dict[str, Any]]:
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        row = {}
        for k, v in raw.items():
            if k:
                col = k.strip()
                row[col] = to_num(v) if col in NUM_COLS else (v.strip() if v else "")
        rows.append(row)
    return rows


def build_calc_breakdown(diag: Dict[str, Any]) -> List[str]:
    calcs = []
    for step in diag.get("trace", []):
        obs = step.get("obs", {})
        if step.get("tool") == "inspect_telemetry" and obs.get("status") == "OK":
            calcs.append(
                f"Thermal Drift: {obs['drift_c_per_min']}°C/min | "
                f"Runway = ({obs['irreversible_spoilage_c']}°C cliff - {obs['current_temp_c']}°C) "
                f"/ {obs['drift_c_per_min']} = {obs['thermal_runway_min']} min"
            )
            calcs.append(
                f"Defrost Active: {obs['defrost_active_min']}m (Max SOP: {obs['defrost_max_min']}m) | "
                f"Door Open: {obs['door_open_min']}m | NH3: {obs['nh3_ppm']} ppm"
            )
        elif step.get("tool") == "check_power_bus":
            calcs.append(
                f"Power Bus: Grid {obs['grid_v']}V | Solar {obs['solar_kw']}kW | "
                f"DG {obs['dg_status']} ({obs['dg_load_pct']}% load, Fuel {obs['dg_fuel_hrs']}h @ 20.5 L/hr)"
            )
    if diag.get("guardrail_violation_flag"):
        calcs.append("Penalty Math: Base 0.75 - 0.40 Overreach Penalty = 0.35 Confidence + Forced HITL")
    return calcs


def run_pipeline(source: str, rows: List[Dict[str, Any]], rogue: bool = False) -> Dict[str, Any]:
    if not rows:
        raise HTTPException(status_code=400, detail="Empty telemetry.")
    inc_id = f"INC-{datetime.now(IST).strftime('%H%M%S')}-{uuid4().hex[:4].upper()}"
    ch_id = str(rows[-1].get("chamber_id") or "CH-01_SEAFOOD")
    ts_ist = now_ist()
    arc = archive_raw_slice(inc_id, rows)
    g1 = validate_telemetry_window(ch_id, rows)
    last = rows[-1]
    pwr = {
        "grid_v": last.get("grid_v", 415.0),
        "phase_imbalance_pct": last.get("phase_imbalance_pct", 0.5),
        "solar_kw": last.get("solar_kw", 0.0),
        "grid_outage_sec": last.get("grid_outage_sec", 0.0),
        "dg_status": last.get("dg_status", "OFF"),
        "dg_load_kw": last.get("dg_load_kw", 0.0),
        "dg_fuel_liters": last.get("dg_fuel_liters", 150.0),
    }

    if rogue:
        diag = run_react_agent(
            inc_id, ch_id, rows, pwr,
            llm_fn=lambda _m, _s: {"tool": "write_plc", "args": {"coil": 1}},
        )
    elif not g1["passed"]:
        diag = {
            "incident_id": inc_id, "chamber_id": ch_id, "status": "ABSTAINED_SAFE",
            "tier": "TIER_3_QUARANTINE", "root_cause_code": g1["reason"],
            "guardrail_violation_flag": False, "confidence": 1.0, "turns_used": 0,
            "thermal_runway_min": None, "closure_rule": "QUARANTINE_CAPA_REQUIRED",
            "recommended_action": f"Gate 1 blocked ({g1['reason']}). Zero fabrication; inspect probe.",
            "maintenance_record_updated": True, "trace": [],
        }
    else:
        diag = run_react_agent(inc_id, ch_id, rows, pwr)

    sup = "Auto-Logged (HITL Review)" if diag.get("tier") == "TIER_1_AUTONOMOUS" else "Karthik R. (On-Duty)"
    audit = [{
        "time_ist": ts_ist,
        "event": diag.get("root_cause_code", "UNKNOWN"),
        "detail": diag.get("recommended_action", ""),
        "supervisor": sup,
    }]
    return upsert_alert({
        **diag,
        "source": source,
        "detected_at_ist": ts_ist,
        "supervisor_name": sup,
        "calc_breakdown": build_calc_breakdown(diag),
        "audit_trail": audit,
        "archive_path": arc["archive_path"],
        "archive_sha256": arc["sha256"],
        "latest_telemetry": last,
        "power_snapshot": pwr,
        "gate1": g1,
        "closed": False,
    })


@app.get("/api/state")
def api_state() -> Dict[str, Any]:
    return {
        "alerts": load_alerts(),
        "memory": load_verified_memory(),
        "profiles": load_profiles(),
        "server_time_ist": now_ist(),
    }


@app.post("/api/scenarios/{scenario_name}")
def api_scenario(scenario_name: str) -> Dict[str, Any]:
    name = scenario_name.replace(".csv", "").strip()
    if name == "guardrail_breach":
        path = SCENARIO_DIR / "stuck_defrost.csv"
        if not path.exists():
            raise HTTPException(status_code=404, detail="stuck_defrost scenario missing.")
        rows = parse_csv(path.read_text(encoding="utf-8"))
        return {"status": "ok", "ticket": run_pipeline("scenario:guardrail_breach", rows, rogue=True)}
    path = SCENARIO_DIR / f"{name}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Scenario {name} not found.")
    rows = parse_csv(path.read_text(encoding="utf-8"))
    return {"status": "ok", "ticket": run_pipeline(f"scenario:{name}", rows)}


@app.post("/api/tickets/{incident_id}/timeout")
def api_timeout(incident_id: str) -> Dict[str, Any]:
    ticket = get_alert(incident_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Incident not found.")
    ts_ist = now_ist()
    trail = list(ticket.get("audit_trail", []))
    trail.append({
        "time_ist": ts_ist,
        "event": "NO_HUMAN_ACK_QUARANTINE_SEAL",
        "detail": "No supervisor response before cliff. Held 0% PLC Write & sealed batch for CAPA.",
        "supervisor": "UNACKNOWLEDGED -> Plant Head Escalation",
    })
    updated = upsert_alert({
        "incident_id": incident_id,
        "status": "NO_HUMAN_ACK_SEALED",
        "tier": "TIER_3_QUARANTINE",
        "closure_rule": "QUARANTINE_CAPA_REQUIRED",
        "supervisor_name": "UNACKNOWLEDGED (Escalated to Plant Head)",
        "audit_trail": trail,
    })
    return {"status": "sealed", "ticket": updated}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    fname = file.filename or "upload.csv"
    text = (await file.read()).decode("utf-8", errors="ignore")
    if fname.lower().endswith(".csv") or "temp_c" in text.lower():
        lines = [ln for ln in text.splitlines() if ln.strip()]
        idx = next((i for i, ln in enumerate(lines) if "temp_c" in ln.lower()), 0)
        rows = parse_csv("\n".join(lines[idx:]))
        return {"status": "ok", "ticket": run_pipeline(f"upload:{fname}", rows)}
    inc_id = f"DOC-{datetime.now(IST).strftime('%H%M%S')}-{uuid4().hex[:4].upper()}"
    ts_ist = now_ist()
    ticket = upsert_alert({
        "incident_id": inc_id, "chamber_id": "CH-01_SEAFOOD",
        "status": "PENDING_HITL_REVIEW", "tier": "TIER_2_HITL",
        "root_cause_code": "UPLOADED_CAPA_DOCUMENT", "guardrail_violation_flag": False,
        "confidence": 0.9, "turns_used": 0, "thermal_runway_min": None,
        "detected_at_ist": ts_ist, "supervisor_name": "Karthik R. (On-Duty)",
        "calc_breakdown": ["Manual compliance document attached; awaiting 3 stable recovery ticks."],
        "audit_trail": [{"time_ist": ts_ist, "event": "DOC_UPLOADED", "detail": fname, "supervisor": "Karthik R."}],
        "recommended_action": f"Review {fname} and verify 3 cooling ticks.",
        "closure_rule": "LOCKED_UNTIL_3_STABLE_TICKS", "source": f"upload:{fname}", "closed": False,
    })
    return {"status": "ok", "ticket": ticket}


@app.post("/api/tickets/{incident_id}/close")
def api_close(incident_id: str, body: CloseBody) -> Dict[str, Any]:
    ticket = get_alert(incident_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Incident not found.")
    try:
        updated = enforce_and_close_ticket(
            incident_id, body.supervisor_name, body.resolution_notes, body.recent_ticks
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    ts_ist = now_ist()
    trail = list(ticket.get("audit_trail", []))
    trail.append({
        "time_ist": ts_ist,
        "event": "GATE3_VERIFIED_CLOSED",
        "detail": f"3 stable ticks verified. Saved to agent_memory.json. {body.resolution_notes}".strip(),
        "supervisor": body.supervisor_name,
    })
    updated = upsert_alert({
        "incident_id": incident_id,
        "supervisor_name": body.supervisor_name,
        "audit_trail": trail,
    })
    return {"status": "closed", "ticket": updated}


@app.post("/api/reset")
def api_reset() -> Dict[str, Any]:
    return {"status": "reset", **reset_runtime_state(seed_alerts=[])}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

    @app.get("/manifest.json")
    def manifest() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "manifest.json"), media_type="application/manifest+json")
