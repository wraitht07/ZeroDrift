# ZeroDrift Standard Operating Procedures (3-Tier Autonomy & Escalation)

## Core Scope & Hardware Posture
* **Lightweight Post-Alarm Supervisor (`0% PLC Write`):** ZeroDrift never dispatches inverters, pumps, gensets, or PLC setpoints. It reads existing Modbus/logger telemetry (`<= 3` ReAct hops), eliminates false alarms, calculates thermal runway, and enforces tamper-evident CAPA recovery.
* **Uneven SME Infrastructure Handling:** Always check `facility_capabilities` (`has_solar`, `has_dg`, `has_tes`) before diagnosing power events. Small cold rooms without a DG rely solely on thermal inertia/TES during grid cuts.

---

## TIER 1: Low-Risk Autonomous Tasks (Agent Solves, Maintains & Acts)
* **Strict Boundary:** Software-only actions (alarm suppression, compliance log maintenance, preventive reminders, and memory pruning). Auto-closure is permitted **only** in Tier 1.
* **AUTO-01 Normal Defrost Suppression:** If `defrost_relay == 1` within the chamber's `defrost_max_duration_min` (`8 min` Pharma, `12 min` Seafood, `15 min` Horticulture) AND temperature spike `<= defrost_max_spike_c`, autonomously suppress the alarm, log `NORMAL_DEFROST_TRANSIENT` to `alerts.json`, and auto-close.
* **AUTO-02 Loading Surge Suppression:** If `door_switch == 1` and temp rise `>= +0.4°C/min` with RH spike `> +5%` for `<= 15 min`, autonomously tag `LOADING_SURGE` and suppress false compressor fault alerts.
* **AUTO-03 Solar Anti-Islanding Verification (CEA Reg 11(6)(e) / IS 16169):**
  * When `grid_v == 0` and `solar_kw` drops to `0 kW` within `<= 2s`, autonomously log `ANTI_ISLANDING_SHUTDOWN` (normal inverter safety, not a solar inverter fault).
  * **If `has_dg == true`:** Monitor the `30s` ATS crank window (`ats_switchover_grace_sec`).
  * **If `has_dg == false`:** Immediately compute pure thermal coasting runway (`(T_cliff - T_current) / drift_rate`) and escalate under `HUM-02`.
* **AUTO-04 Routine Maintenance & Log Hygiene:**
  * Auto-archive verified compliance slices and prune `agent_memory.json` to the 20 most recent Gate-3-verified resolutions.
  * If `has_dg == true` and `dg_fuel_hrs` is between `4.0` and `6.0 hrs` (at `~20.5 L/hr` Kirloskar 125 kVA 75% load burn), emit a non-urgent `PREVENTIVE_FUEL_REMINDER` to the plant manager.

---

## TIER 2: Human-Solvable Edge Cases (First Escalation — HITL Required)
* **Rule:** Agent diagnoses root cause within `<= 3` tool hops, drafts a 1-click AMC/Operator brief, and **MUST escalate** (`ESCALATED_HITL`). Ticket stays `LOCKED` until Gate 3 verifies **3 consecutive stable ticks inside the safe band with `dT/dt <= 0`**.
* **HUM-01 Stuck Defrost Heater:** `defrost_relay == 1` exceeding the chamber's `defrost_max_duration_min` (or temp spike `> defrost_max_spike_c`) -> Escalate to AMC (`STUCK_DEFROST_HEATER`).
* **HUM-02 Grid Outage / DG Crank Fail / Overload:**
  * **No-DG Site (`has_dg == false`):** `grid_v == 0` -> Escalate immediately to Plant Operator with exact thermal coasting runway (`NO_DG_GRID_OUTAGE`).
  * **DG Site (`has_dg == true`):** `grid_v == 0` for `> 30s` with `dg_status == OFF` -> Escalate (`DG_CRANK_FAIL`).
  * **DG Overload (`dg_load_pct > 85%` of `100 kW` prime cap):** Recommend shedding `CH-03_HORTICULTURE` (`SHED_CH03_HORTICULTURE`) and `25s` staggered compressor restarts (`compressor_stagger_sec`) to prevent locked-rotor inrush trips; never shed `CH-02_VACCINES` or `CH-01_SEAFOOD`.
  * **Grid Brownout / Phase Imbalance:** `grid_v < 370V` or `> 455V`, or `phase_imbalance_pct > 3.0%` -> Escalate (`GRID_POWER_QUALITY_FAULT`).
* **HUM-03 Recoverable Thermal Drift / Refrigerant Leak:** Steady upward drift (`> max_drift_c_per_hr`) with healthy power and healthy cooling-water loop while still above `irreversible_spoilage_c` -> Escalate to AMC (`UNKNOWN_THERMAL_LEAK`, note NCCD `GWP < 1500` compliance).
* **HUM-04 Condenser / Cooling-Water Pump Trip:** Compressor trip or high head pressure accompanied by `condenser_pump_status == 0` (water circulation dry-run / VFD fault) -> Escalate to AMC (`CONDENSER_WATER_LOOP_FAULT`) instead of misdiagnosing a refrigerant leak.
* **AGENT OVERREACH PENALTY:** Attempting autonomous physical recovery, proposing a PLC/inverter/pump write, guessing missing data, or failing to escalate a Tier 2/3 case within 3 hops -> Apply **`PENALTY: -0.40 CONFIDENCE + GUARDRAIL_VIOLATION_FLAG`** and force `ESCALATED_HITL`.

---

## TIER 3: Unsolvable Edge Cases (Quarantine & Mandatory CAPA Lock)
* **Rule:** When physical safety, irreversible biological/food damage, or zero-energy isolation occurs, normal recovery is impossible. Both AI and operators are blocked from normal closure; raw telemetry is sealed in `data/archive/` for FSSAI/MPEDA/CDSCO audit.
* **UNSOLVABLE-01 Toxic Ammonia / Refrigerant Rupture (`NH3 > 50 ppm`):**
  * *State:* `CATASTROPHIC_EVACUATION_LOCK` (Hardware safety kills power; block remote restart; log emergency evacuation event).
* **UNSOLVABLE-02 Irreversible Cold-Chain Spoilage (FSSAI Schedule 4 / MPEDA / CDSCO):**
  * Triggers on: Vaccine freeze (`< 0.0°C`) or heat excursion (`> +12.0°C`), Seafood thaw (`> -12.0°C` for `> 30 min`), OR Horticulture/perishable food remaining in the **FSSAI Bacterial Danger Zone (`+5.0°C to +60.0°C`) for `> 120 min`**.
  * *State:* `BATCH_QUARANTINE_SEAL` (Re-cooling cannot restore spoiled cargo; block normal closure to prevent illegal shipment and mandate FSSAI Schedule 4 CAPA).
* **UNSOLVABLE-03 Total Blackout + Zero Backup (`grid_v == 0`, `solar_kw == 0`, and `has_dg == false` or `dg_fuel == 0`):**
  * *State:* `ABSTAINED_BLACKOUT_GAP` (Log last known thermal runway before telemetry drop; zero data interpolation or backfilling).
