from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "data" / "sops" / "chamber_profiles.json"


class TelemetryTick(BaseModel):
    elapsed_min: float = 0.0
    chamber_id: str = "CH-01_SEAFOOD"
    temp_c: Optional[float] = None
    rh_pct: Optional[float] = None
    nh3_ppm: float = 0.0
    defrost_relay: int = 0
    door_switch: int = 0
    grid_v: float = 415.0
    phase_imbalance_pct: float = 0.0
    solar_kw: float = 0.0
    grid_outage_sec: float = 0.0
    dg_status: str = "OFF"
    dg_load_kw: float = 0.0
    dg_fuel_liters: Optional[float] = None
    timestamp: Optional[str] = None


class SanityResult(BaseModel):
    passed: bool
    reason: str
    chamber_id: str
    valid_tick_count: int = 0
    missing_tick_count: int = 0
    std_dev: Optional[float] = None
    flags: List[str] = Field(default_factory=list)


def load_profiles() -> Dict[str, Any]:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def normalize_chamber(chamber_id: str, chambers: Dict[str, Any]) -> Optional[str]:
    cid = chamber_id.strip().upper()
    if cid in chambers:
        return cid
    return next((k for k in chambers if k.startswith(cid)), None)


def validate_telemetry_window(
    chamber_id: str,
    rows: List[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    profiles = load_profiles()
    sg = profiles["sanity_gate"]
    ch_key = normalize_chamber(chamber_id, profiles["chambers"])
    if not ch_key:
        return SanityResult(
            passed=False, reason="UNKNOWN_CHAMBER_ID", chamber_id=chamber_id
        ).model_dump()

    if not rows:
        return SanityResult(
            passed=False, reason="EMPTY_TELEMETRY_WINDOW", chamber_id=ch_key
        ).model_dump()

    cleaned = []
    defaults = TelemetryTick().model_dump()
    for r in rows:
        rr = dict(r)
        for k, dv in defaults.items():
            if k in rr and rr[k] is None and dv is not None:
                rr[k] = dv
        cleaned.append(rr)
    ticks = [TelemetryTick(**rr) for rr in cleaned]
    valid_temps = [
        t.temp_c for t in ticks
        if t.temp_c is not None and not math.isnan(t.temp_c)
    ]
    missing_count = len(ticks) - len(valid_temps)

    if not valid_temps:
        return SanityResult(
            passed=False,
            reason="BLACKOUT_OR_NULL_TELEMETRY",
            chamber_id=ch_key,
            missing_tick_count=missing_count,
            flags=["ZERO_FABRICATION_ABSTAIN"],
        ).model_dump()

    latest_ts = ticks[-1].timestamp
    if latest_ts:
        try:
            pkt_time = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
            ref_time = now_utc or datetime.now(timezone.utc)
            age_sec = (ref_time - pkt_time).total_seconds()
            if age_sec > sg["max_packet_age_sec"]:
                return SanityResult(
                    passed=False,
                    reason="STALE_TELEMETRY_PACKET",
                    chamber_id=ch_key,
                    valid_tick_count=len(valid_temps),
                    missing_tick_count=missing_count,
                    flags=[f"PACKET_AGE_{int(age_sec)}S"],
                ).model_dump()
        except ValueError:
            pass

    flat_n = int(sg["flatline_window_ticks"])
    std_dev: Optional[float] = None
    if len(valid_temps) >= flat_n:
        tail = valid_temps[-flat_n:]
        avg = sum(tail) / flat_n
        std_dev = round(math.sqrt(sum((x - avg) ** 2 for x in tail) / flat_n), 6)
        if std_dev < sg["flatline_min_std_dev"]:
            return SanityResult(
                passed=False,
                reason="SENSOR_FLATLINE",
                chamber_id=ch_key,
                valid_tick_count=len(valid_temps),
                missing_tick_count=missing_count,
                std_dev=std_dev,
                flags=["FROZEN_PROBE_ABSTAIN"],
            ).model_dump()

    flags = [f"PARTIAL_GAP_{missing_count}"] if missing_count > 0 else []
    return SanityResult(
        passed=True,
        reason="OK",
        chamber_id=ch_key,
        valid_tick_count=len(valid_temps),
        missing_tick_count=missing_count,
        std_dev=std_dev,
        flags=flags,
    ).model_dump()
