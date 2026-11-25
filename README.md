# ZeroDrift

> **Read-only (`0% PLC Write`) bounded agentic post-alarm supervisor for Indian hybrid-solar cold storages.**  
> Built by **Team KESTREL** (Tagore Engineering College) for **K! Hacks 4.0 (CEG Tech Forum, Anna University)** - *Agentic AI & Intelligent Systems* track.

ZeroDrift owns the **post-alarm recovery gap**: it diagnoses thermal/power incidents, drafts evidence-backed human action briefs, and locks ticket closure until recovery is physically verified. It never writes to PLCs, inverters, DG relays, or setpoints.

---

## Why ZeroDrift

India operates **8,815+ cold storages** and loses **₹1.53 Lakh Cr annually** in post-harvest/temperature-sensitive cargo (NABCONS/MoFPI 2022). Existing SCADA/loggers raise alarms; they do not govern the chaotic 2-4 hours after the alarm.

ZeroDrift is designed for Indian field reality:

- **415V 3-phase grid instability** - brownouts, surges, phase imbalance.
- **Solar anti-islanding** - CEA Reg 11(6)(e) / IS 16169 forces PV to `0 kW` within `<= 2s` on grid loss; not an inverter fault.
- **Uneven SME hardware** - some sites have DG, some do not.
- **DG bottlenecks** - 125 kVA / 100 kW, `~20.5 L/hr`, ATS crank window, overload/load-shed logic.
- **Alarm fatigue** - defrost/door/loading transients vs real faults.

---

## What ZeroDrift Does

| Capability | Behavior |
|---|---|
| **Gate 1 - Sanity** | Rejects stale/flatlined/null/blackout telemetry; abstains with zero fabrication. |
| **Gate 2 - Bounded ReAct** | Max **3 tool hops**: `inspect_telemetry`, `check_power_bus`, `lookup_sop`, `search_verified_memory`. |
| **Gate 3 - Closure Lock** | Tier 2 tickets stay locked until **3 consecutive in-band, non-rising** recovery ticks are verified. |
| **Guardrails** | `0% PLC write`, `-0.40` confidence penalty on overreach, forced HITL escalation, no Tier 3 auto-close. |
| **Audit** | SHA-256 raw telemetry slice, IST audit trail, Gate-3-verified episodic memory (capped at 20). |
| **PWA Frontend** | Dark industrial command center with live schematic strip, rule/physics math, 3-hop trace, audit ledger, and Gate 3 sign-off. |

## What ZeroDrift Does NOT Do

- **Never writes to PLC/registers/relays/setpoints/DG controls.**
- **Never fabricates missing telemetry.** Missing/flatlined/blackout data => abstain.
- **Never auto-closes physical Tier 2/3 faults.**
- **Never unlocks Tier 3 quarantine/CAPA cases.**

---

## 3-Tier Autonomy

| Tier | Examples | Closure |
|---|---|---|
| **Tier 1 Autonomous** | Normal defrost transient, loading surge, CEA anti-islanding log, preventive reminders | Auto-logged/reviewable; no physical actuation |
| **Tier 2 HITL** | Stuck defrost heater, DG crank fail, no-DG grid outage, overload shed advisory, unknown thermal leak | **LOCKED_UNTIL_3_STABLE_TICKS** |
| **Tier 3 Quarantine** | NH3 toxic leak, irreversible spoilage/FSSAI danger-zone breach, total blackout + zero backup | **QUARANTINE_CAPA_REQUIRED** |

---

## Repository Structure

```text
.
├── README.md
├── pyproject.toml
├── uv.lock
├── data/
│   ├── scenarios/                 # loading_surge, stuck_defrost, solar_eb_outage, unknown_leak
│   └── sops/
│       ├── chamber_profiles.json  # chamber physics, power thresholds, gate policy
│       └── cold_chain_sop.md      # Tier 1/2/3 SOP rules
├── src/
│   ├── backend/
│   │   ├── main.py                # FastAPI API, scenario/upload/close/timeout/reset
│   │   ├── sanity_gate.py         # Gate 1 telemetry validation / abstention
│   │   ├── closure_gate.py        # Gate 3 3-tick verification + close enforcement
│   │   ├── memory.py              # alerts, verified memory, SHA-256 archive
│   │   └── agent/
│   │       ├── react_agent.py     # bounded 3-hop ReAct + guardrail penalty
│   │       ├── tools.py           # read-only diagnostic tools
│   │       └── rules.md           # agent constitution
│   └── frontend/
│       ├── index.html             # PWA shell
│       ├── styles.css             # calm industrial dark UI
│       ├── app.js                 # vanilla JS app
│       ├── manifest.json          # PWA manifest
│       └── sw.js                  # service worker (bypasses /api/*)
└── tests/
    ├── test_closure_gate.py
    ├── test_sanity_gate.py
    └── test_scenarios.py
```

> Note: local runtime artifacts (`data/runtime/`, `data/archive/`) and local env/OS files are intentionally not tracked.

---

## Quickstart

```bash
# Install deps
uv sync --extra dev

# Run tests
uv run pytest -v

# Start API + PWA
uv run uvicorn src.backend.main:app --reload --port 8000
```

Open: <http://127.0.0.1:8000>

---

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/state` | Alerts, verified memory, profiles, server IST time |
| `POST` | `/api/scenarios/{scenario_name}` | Run scenario (`loading_surge`, `stuck_defrost`, `solar_eb_outage`, `unknown_leak`, `guardrail_breach`) |
| `POST` | `/api/upload` | Upload CSV/PDF/DOCX/log file |
| `POST` | `/api/tickets/{incident_id}/close` | Gate 3 close with supervisor + 3 recovery ticks |
| `POST` | `/api/tickets/{incident_id}/timeout` | Simulate no-human response -> quarantine seal |
| `POST` | `/api/reset` | Reset runtime alerts (preserves verified memory behavior as implemented) |

---

## Verification & Test Results

### Pytest (repo tests)

Command:

```bash
uv run pytest -v
```

Result:

```text
9 passed, 1 warning in 0.09s
```

Coverage by suite:
- `tests/test_closure_gate.py` - 3 passed (Gate 3 reject/approve/quarantine block)
- `tests/test_sanity_gate.py` - 4 passed (valid, flatline, blackout/null, stale packet)
- `tests/test_scenarios.py` - 2 passed (4 end-to-end scenarios + overreach penalty/turn limit)

Warning: existing `StarletteDeprecationWarning` from `fastapi.testclient` / `httpx` (non-blocking).

### Additional rigorous checks (outside `tests/`)

- `uv run python -m compileall -q src tests` - **OK**
- Module import smoke (`pkgutil.walk_packages` over `src.*`, 8 modules) - **OK**
- Circular/import-order smoke (3 subprocess import orders) - **OK**
- `uv pip check` - **OK** (`All installed packages are compatible`)
- Dependency imports (`fastapi`, `uvicorn`, `pydantic`, `multipart`, `pypdf`, `docx`) - **OK**
- Ad-hoc FastAPI `TestClient` smoke - **ALL_ADHOC_OK**
  - scenarios including `guardrail_breach`
  - timeout/no-human seal
  - Gate 3 bad close blocked (409), good close verified (200)
  - CSV upload
  - static assets served (`/`, `/static/styles.css`, `/static/app.js`)

### Stress / abuse simulation

In-process stress run (FastAPI `TestClient`):

| Workload | Result |
|---|---:|
| 10,000 manual/doc uploads (`/api/upload`, PDF path) | ~175.9s; **9994 alerts persisted** (6 fewer than requests; no 5xx observed, cause not fully isolated) |
| 1,000 rapid scenario runs | **1000/1000 OK** (~8.2s) |
| 500 `GET /api/state` spam | **500/500 OK** |
| 200 `POST /api/reset` spam | **200/200 OK** |
| 100 quarantine-close attempts | **100/100 expected 409** |
| Malformed/edge inputs | Expected 4xx/409/200-safe handling; **1 exception before fix** |

Aggregate: **11704 ok, 8 expected 4xx, 101 expected 409, 0 unexpected 5xx, 1 exception (fixed)**.

---

## Bugs Found & Fixed During Hardening

1. **Asset path mismatch (frontend 404)** - `index.html` referenced `styles.css`/`app.js` at root while FastAPI mounted static under `/static`. Fixed by using `/static/...`.
2. **Incident ID collision under rapid runs** - second-resolution IDs caused alert overwrites during rapid/tour scenarios. Fixed with UUID suffix (`INC-HHMMSS-XXXX` / `DOC-HHMMSS-XXXX`).
3. **Malformed CSV numeric validation 500** - `None` numeric fields (e.g., `nh3_ppm`) caused Pydantic validation error in Gate 1. Fixed by coercing `None` to `TelemetryTick` defaults before validation.

Known non-blocking items:
- `StarletteDeprecationWarning` in testclient stack.
- 10k doc upload persistence count anomaly (9994/10000) observed once; no 5xx, needs focused follow-up if production persistence guarantees are required.

---

## PWA / Frontend Notes

The frontend is a Scribely-polish-inspired **dark industrial** command center:

- Live schematic strip (CH-01/02/03 + Grid/Solar/DG)
- Field event simulator + Judge Tour
- 3-Gate stepper (Sanity -> Bounded ReAct -> 3-Tick Lock)
- Time-to-Cliff runway, confidence, IST detection, supervisor
- Deterministic rule/physics math breakdown
- Collapsible 3-hop ReAct trace (plain-English takeaway + raw JSON)
- IST audit ledger + SHA-256 frozen slice
- MaintainX-style Gate 3 sign-off with stable/rising tick helpers
- PWA install prompt, manifest, service worker (`/api/*` bypassed)

---

## Safety & Compliance Posture

- **0% PLC write** by design.
- Human-in-the-loop for physical repairs and Tier 2 closure.
- Tier 3 quarantine/CAPA lock for irreversible/toxic/blackout cases.
- Auditability through SHA-256 archived raw slices and IST audit trail.
- Standards referenced in SOP/profiles: FSSAI Schedule 4, MPEDA/EIC, CDSCO/WHO-GDP, NCCD 2025, CEA Reg 11(6)(e) / IS 16169.

---

## Team KESTREL

- **Tarun R** - Agentic AI & Systems Lead (CSE III, Tagore Engineering College)
- **Selva Maruthi P** - Backend & Data Engineer
- **Mohamed Uvaish S** - Testing & SOP Engineer
- **Yogesh P** - Frontend & UI Engineer
