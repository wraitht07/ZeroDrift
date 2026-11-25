from __future__ import annotations
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT / "data" / "runtime"
ARCHIVE_DIR = ROOT / "data" / "archive"
ALERTS_PATH = RUNTIME_DIR / "alerts.json"
MEMORY_PATH = RUNTIME_DIR / "agent_memory.json"
MAX_MEMORY_ENTRIES = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def load_alerts() -> List[Dict[str, Any]]:
    if not ALERTS_PATH.exists():
        return []
    raw = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else raw.get("alerts", [])


def save_alerts(alerts: List[Dict[str, Any]]) -> None:
    _ensure_dirs()
    ALERTS_PATH.write_text(json.dumps({"alerts": alerts}, indent=2), encoding="utf-8")


def upsert_alert(record: Dict[str, Any]) -> Dict[str, Any]:
    alerts = load_alerts()
    inc_id = record["incident_id"]
    record.setdefault("updated_at", _now_iso())
    for i, a in enumerate(alerts):
        if a.get("incident_id") == inc_id:
            alerts[i] = {**a, **record}
            save_alerts(alerts)
            return alerts[i]
    record.setdefault("created_at", _now_iso())
    alerts.insert(0, record)
    save_alerts(alerts)
    return record


def get_alert(incident_id: str) -> Optional[Dict[str, Any]]:
    for a in load_alerts():
        if a.get("incident_id") == incident_id:
            return a
    return None


def load_verified_memory() -> List[Dict[str, Any]]:
    if not MEMORY_PATH.exists():
        return []
    raw = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else raw.get("entries", [])
    return [e for e in entries if e.get("verified_by_gate3") is True]


def append_verified_memory(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    if entry.get("verified_by_gate3") is not True:
        raise ValueError("Memory poisoning blocked: verified_by_gate3 must be True.")
    _ensure_dirs()
    entries = load_verified_memory()
    entry.setdefault("verified_at", _now_iso())
    entries = [e for e in entries if e.get("incident_id") != entry.get("incident_id")]
    entries.insert(0, entry)
    entries = entries[:MAX_MEMORY_ENTRIES]
    MEMORY_PATH.write_text(
        json.dumps({"max_entries": MAX_MEMORY_ENTRIES, "entries": entries}, indent=2),
        encoding="utf-8",
    )
    return entries


def archive_raw_slice(incident_id: str, rows: List[Dict[str, Any]]) -> Dict[str, str]:
    _ensure_dirs()
    safe_id = "".join(c for c in incident_id if c.isalnum() or c in ("-", "_")) or "INC-UNKNOWN"
    out_path = ARCHIVE_DIR / f"{safe_id}_raw_slice.csv"
    if rows:
        fieldnames = list(rows[0].keys())
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        out_path.write_text("", encoding="utf-8")
    sha256 = hashlib.sha256(out_path.read_bytes()).hexdigest()
    return {"archive_path": str(out_path.relative_to(ROOT)), "sha256": sha256}


def reset_runtime_state(seed_alerts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    _ensure_dirs()
    alerts = seed_alerts if seed_alerts is not None else []
    save_alerts(alerts)
    entries = load_verified_memory()[:MAX_MEMORY_ENTRIES]
    MEMORY_PATH.write_text(
        json.dumps({"max_entries": MAX_MEMORY_ENTRIES, "entries": entries}, indent=2),
        encoding="utf-8",
    )
    return {"alerts_count": len(alerts), "memory_count": len(entries)}
