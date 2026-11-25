(() => {
  'use strict';
  const state = { alerts: [], memory: [], profiles: null, selected: null, deferredPrompt: null };
  const $ = (id) => document.getElementById(id);
  const qsa = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtTemp = (v) => (v == null || Number.isNaN(Number(v)) ? '--' : `${Number(v).toFixed(1)}°C`);
  const fmtRunway = (v) => (v == null || Number.isNaN(Number(v)) ? 'N/A' : `${Number(v).toFixed(1)} min`);
  const fmtPct = (v) => (v == null || Number.isNaN(Number(v)) ? '--' : `${Math.round(Number(v) * 100)}%`);
  const fmtNum = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(d));

  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { ...(opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(opts.headers || {}) } });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
    if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    return data;
  }

  // PWA
  function initPWA() {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => navigator.serviceWorker.register('/static/sw.js').catch(() => {}));
    }
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); state.deferredPrompt = e; $('installBtn').classList.remove('hidden'); });
    $('installBtn').addEventListener('click', async () => {
      if (!state.deferredPrompt) return;
      state.deferredPrompt.prompt();
      try { await state.deferredPrompt.userChoice; } catch {}
      state.deferredPrompt = null; $('installBtn').classList.add('hidden');
    });
    window.addEventListener('appinstalled', () => $('installBtn').classList.add('hidden'));
  }

  // Clock
  function tickClock() {
    const now = new Date();
    $('istClock').textContent = now.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) + ' IST';
  }

  // Tabs
  function initTabs() {
    qsa('.tab-btn').forEach((btn) => btn.addEventListener('click', () => setTab(btn.dataset.tab)));
    setTab('queue');
  }
  function setTab(tab) {
    qsa('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    qsa('.mobile-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tab));
  }

  // Toast
  let toastTimer;
  function showToast(msg, bad = false) {
    const t = $('toast');
    t.textContent = msg;
    t.className = `toast show${bad ? ' bad' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'toast'; }, 3400);
  }

  function getSelected() { return state.alerts.find((a) => a.incident_id === state.selected) || state.alerts[0] || null; }
  function chamberLabel(ch) { return ({ 'CH-01_SEAFOOD': 'CH-01 Seafood • MPEDA', 'CH-02_VACCINES': 'CH-02 Vaccines • CDSCO', 'CH-03_HORTICULTURE': 'CH-03 Horticulture • FSSAI' })[ch] || ch || '--'; }
  function tierPillClass(tier) { if (tier === 'TIER_1_AUTONOMOUS') return 'pill-ok'; if (tier === 'TIER_3_QUARANTINE') return 'pill-bad'; return 'pill-warn'; }
  function statusPillClass(t) { if (!t) return 'pill-neutral'; if (t.closed || t.status === 'CLOSED_VERIFIED') return 'pill-ok'; if (t.status === 'NO_HUMAN_ACK_SEALED' || t.tier === 'TIER_3_QUARANTINE' || t.status === 'ABSTAINED_SAFE') return 'pill-bad'; return 'pill-warn'; }
  function severity(t) { if (!t) return 'warn'; if (t.closed || t.status === 'CLOSED_VERIFIED' || t.tier === 'TIER_1_AUTONOMOUS') return 'ok'; if (t.status === 'NO_HUMAN_ACK_SEALED' || t.tier === 'TIER_3_QUARANTINE' || t.status === 'ABSTAINED_SAFE') return 'bad'; return 'warn'; }

  function renderSchematic() {
    const map = {
      'CH-01_SEAFOOD': { card: 'card-ch1', val: 'monCh1', badge: 'badgeCh1', def: '-21.2°C' },
      'CH-02_VACCINES': { card: 'card-ch2', val: 'monCh2', badge: 'badgeCh2', def: '+4.4°C' },
      'CH-03_HORTICULTURE': { card: 'card-ch3', val: 'monCh3', badge: 'badgeCh3', def: '+6.8°C' },
    };
    for (const m of Object.values(map)) {
      $(m.val).textContent = m.def;
      $(m.badge).textContent = 'Nominal'; $(m.badge).className = 'node-badge ok';
      $(m.card).classList.remove('alert');
    }
    $('monPwr').textContent = '415V • 68kW Solar • DG Standby';
    $('badgePwr').textContent = 'Grid normal'; $('badgePwr').className = 'node-badge ok';
    $('card-pwr').classList.remove('alert');

    const seen = new Set();
    for (const t of state.alerts) {
      const m = map[t.chamber_id];
      if (m && !seen.has(t.chamber_id)) {
        seen.add(t.chamber_id);
        $(m.val).textContent = fmtTemp(t.latest_telemetry?.temp_c);
        const door = Number(t.latest_telemetry?.door_switch) === 1;
        const defrost = Number(t.latest_telemetry?.defrost_relay) === 1;
        let badge = 'Nominal', cls = 'ok';
        if (t.closed || t.status === 'CLOSED_VERIFIED') { badge = 'Verified closed'; cls = 'ok'; }
        else if (t.status === 'NO_HUMAN_ACK_SEALED') { badge = 'Quarantine sealed'; cls = 'bad'; }
        else if (t.tier === 'TIER_3_QUARANTINE' || t.status === 'ABSTAINED_SAFE') { badge = 'Quarantine / Abstain'; cls = 'bad'; }
        else if (defrost) { badge = 'Defrost cycle'; cls = 'warn'; }
        else if (door) { badge = 'Door open'; cls = 'warn'; }
        else if (t.tier === 'TIER_2_HITL') { badge = 'Excursion'; cls = 'warn'; }
        else if (t.tier === 'TIER_1_AUTONOMOUS') { badge = 'Auto-suppressed'; cls = 'ok'; }
        $(m.badge).textContent = badge; $(m.badge).className = `node-badge ${cls}`;
        if (!t.closed && (t.tier === 'TIER_2_HITL' || t.tier === 'TIER_3_QUARANTINE')) $(m.card).classList.add('alert');
      }
      if (t.power_snapshot) {
        const p = t.power_snapshot;
        $('monPwr').textContent = `${fmtNum(p.grid_v,0)}V • ${fmtNum(p.solar_kw,0)}kW Solar • DG ${p.dg_status || 'OFF'}`;
        const pBad = Number(p.grid_v) === 0 || String(p.dg_status).toUpperCase() === 'FAULT' || Number(p.grid_outage_sec) > 30;
        $('badgePwr').textContent = pBad ? 'Power anomaly' : 'Grid normal';
        $('badgePwr').className = `node-badge ${pBad ? 'warn' : 'ok'}`;
        if (pBad && !t.closed) $('card-pwr').classList.add('alert');
      }
    }
  }

  function renderQueue() {
    $('alertCount').textContent = String(state.alerts.length);
    $('tabQueueBadge').textContent = String(state.alerts.length);
    const list = $('alertList');
    if (!state.alerts.length) { list.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-dim);font-size:13px">No active incidents. Run a scenario or Judge Tour.</div>`; return; }
    list.innerHTML = state.alerts.map((t) => {
      const active = t.incident_id === state.selected ? 'active' : '';
      const sev = severity(t);
      return `<button class="q-item ${active}" data-id="${esc(t.incident_id)}" type="button">
        <div class="q-top"><span class="q-id">${esc(t.incident_id)}</span><span class="pill ${tierPillClass(t.tier)}" style="font-size:10px;padding:3px 8px">${esc((t.tier||'').replace('TIER_','T'))}</span></div>
        <div class="q-title"><span class="sev-dot ${sev}"></span>${esc(t.root_cause_code || 'Incident')}</div>
        <div class="q-meta">${esc(t.chamber_id||'')} · ${esc(t.status||'')} · ${fmtPct(t.confidence)}</div>
      </button>`;
    }).join('');
    qsa('.q-item', list).forEach((btn) => btn.addEventListener('click', () => { state.selected = btn.dataset.id; setTab('agent'); render(); }));
  }

  function renderGates(t) {
    const g1ok = !!t.gate1?.passed;
    const g2ok = !t.guardrail_violation_flag;
    const g3ok = !!t.closed;
    const g3bad = t.status === 'NO_HUMAN_ACK_SEALED' || (t.tier === 'TIER_3_QUARANTINE' && !g1ok);
    $('g1Step').className = `gate ${g1ok ? 'ok' : 'bad'}`;
    $('g2Step').className = `gate ${g2ok ? 'ok' : 'bad'}`;
    $('g3Step').className = `gate ${g3ok ? 'ok' : g3bad ? 'bad' : 'warn'}`;
    $('g1Line').className = `gate-link ${g1ok ? 'ok' : 'bad'}`;
    $('g2Line').className = `gate-link ${g2ok ? (g3ok ? 'ok' : g3bad ? 'bad' : 'warn') : 'bad'}`;
    $('g1Text').textContent = g1ok ? 'Sanity passed' : (t.gate1?.reason || 'Blocked');
    $('g2Text').textContent = g2ok ? `${t.turns_used || 0}/3 hops bounded` : 'Guardrail penalty';
    $('g3Text').textContent = g3ok ? '3 ticks verified' : (t.status === 'NO_HUMAN_ACK_SEALED' ? 'Quarantine sealed' : (t.closure_rule || 'Recovery lock'));
  }

  function renderDisposition(t) {
    const sev = severity(t);
    const b = $('actionBanner');
    b.className = `disposition ${sev}`;
    let icon = '!', kicker = 'DISPOSITION', title = 'Awaiting diagnosis', text = t.recommended_action || '--';
    if (t.status === 'NO_HUMAN_ACK_SEALED') { icon = '⛔'; kicker = 'NO_HUMAN_ACK_SEALED'; title = 'Batch quarantined — no human acknowledgment'; text = t.recommended_action || 'No supervisor response before cliff. 0% PLC Write held. Batch sealed for CAPA.'; }
    else if (t.status === 'ABSTAINED_SAFE') { icon = '⛔'; kicker = 'ABSTAINED_SAFE'; title = 'Agent abstained — zero fabrication'; }
    else if (t.tier === 'TIER_3_QUARANTINE' && !t.closed) { icon = '⛔'; kicker = 'QUARANTINE / CAPA REQUIRED'; title = 'Quarantine disposition required'; }
    else if (t.guardrail_violation_flag) { icon = '⚠'; kicker = 'GUARDRAIL_VIOLATION · AGENT_FAILED_ESCALATED_HITL'; title = 'Guardrail breach — forced HITL escalation'; }
    else if (t.closed || t.status === 'CLOSED_VERIFIED') { icon = '✓'; kicker = 'RELEASE / VERIFIED CLOSED'; title = 'Recovery verified — safe to release'; }
    else if (t.tier === 'TIER_1_AUTONOMOUS') { icon = '✓'; kicker = 'AUTO-SUPPRESSED / LOGGED'; title = 'Low-risk event autonomously handled'; }
    else { icon = '!'; kicker = 'AGENT_FAILED_ESCALATED_HITL'; title = 'Human intervention required'; }
    $('dispIcon').textContent = icon; $('dispKicker').textContent = kicker; $('dispTitle').textContent = title; $('dispText').textContent = text;
    $('dispRight').textContent = t.archive_sha256 ? `SHA ${t.archive_sha256.slice(0, 12)}…` : '';
  }

  function traceTakeaway(step) {
    const obs = step.obs || {};
    if (step.tool === 'inspect_telemetry') { if (obs.status !== 'OK') return `${obs.status}: agent abstained from fabrication.`; return `Temp ${fmtTemp(obs.current_temp_c)}, drift ${Number(obs.drift_c_per_min).toFixed(4)}°C/min, runway ${obs.thermal_runway_min ?? 'N/A'} min.`; }
    if (step.tool === 'check_power_bus') return `Grid ${obs.grid_v}V, Solar ${obs.solar_kw}kW, DG ${obs.dg_status} at ${obs.dg_load_pct}% load.`;
    if (step.tool === 'lookup_sop') return `Matched SOP rule ${obs.rule || 'N/A'} for deterministic escalation.`;
    if (step.tool === 'search_verified_memory') return `Retrieved ${obs.matches?.length ?? 0} verified prior resolutions.`;
    return 'Tool observation recorded.';
  }

  function renderTrace(t) {
    const trace = t.trace || [];
    $('hopCount').textContent = String(trace.length);
    const list = $('traceList');
    if (!trace.length) { list.innerHTML = `<div style="padding:16px;color:var(--text-dim);font-size:13px">No ReAct tool calls recorded (Gate 1 abstain or deterministic path).</div>`; return; }
    list.innerHTML = trace.map((s, i) => `
      <div class="trace-step">
        <div class="trace-head" data-trace="${i}">
          <div class="trace-left"><div class="turn-n">${esc(s.turn)}</div><div style="min-width:0"><div class="trace-tool">Turn ${esc(s.turn)}: ${esc(s.tool)}</div><div class="trace-take">${esc(traceTakeaway(s))}</div></div></div>
          <button class="trace-toggle" type="button" data-toggle="${i}">Raw JSON</button>
        </div>
        <div class="trace-json hidden" id="traceJson${i}"><pre>${esc(JSON.stringify(s.obs, null, 2))}</pre></div>
      </div>`).join('');
    qsa('[data-toggle]', list).forEach((btn) => btn.addEventListener('click', (e) => { e.stopPropagation(); const el = $(`traceJson${btn.dataset.toggle}`); el.classList.toggle('hidden'); btn.textContent = el.classList.contains('hidden') ? 'Raw JSON' : 'Hide JSON'; }));
    qsa('.trace-head', list).forEach((h) => h.addEventListener('click', (e) => { if (e.target.closest('[data-toggle]')) return; h.querySelector('[data-toggle]')?.click(); }));
  }

  function renderAudit(t) {
    const rows = t.audit_trail || [];
    $('auditBody').innerHTML = rows.length ? rows.map((a) => `<tr><td class="mono" style="white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--text-muted)">${esc(a.time_ist || '--')}</td><td><div class="evt">${esc(a.event || '--')}</div><div class="evt-d">${esc(a.detail || '')}</div></td><td>${esc(a.supervisor || '--')}</td></tr>`).join('') : `<tr><td colspan="3" style="color:var(--text-dim);padding:20px">No audit events recorded.</td></tr>`;
    $('dArchive').textContent = t.archive_path || '--';
    $('dHash').textContent = t.archive_sha256 || '--';
  }

  function renderMemory() {
    const count = state.memory.length;
    $('memCount').textContent = `${count}/20`;
    $('tabMemBadge').textContent = `${count}/20`;
    const list = $('memList');
    if (!count) { list.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text-dim);font-size:13px">No verified resolutions yet. Close a Tier 2 ticket through Gate 3.</div>`; return; }
    list.innerHTML = state.memory.map((m) => `
      <div class="mem-item">
        <div class="mem-top"><span class="mem-id">${esc(m.incident_id)}</span><span class="pill pill-ok" style="font-size:10px;padding:3px 8px">VERIFIED</span></div>
        <div class="mem-cause">${esc(m.root_cause_code || '--')}</div>
        <div class="mem-meta">${esc(chamberLabel(m.chamber_id))}<br>${esc(m.supervisor_name || 'Supervisor')} · ${esc((m.verified_at || '').replace('T',' ').slice(0,19))}</div>
      </div>`).join('');
  }

  function renderDetail() {
    const t = getSelected();
    if (!t) { $('emptyState').classList.remove('hidden'); $('detailView').classList.add('hidden'); return; }
    state.selected = t.incident_id;
    $('emptyState').classList.add('hidden'); $('detailView').classList.remove('hidden');
    $('dId').textContent = t.incident_id;
    $('dTitle').textContent = t.root_cause_code || 'Incident';
    $('dChamber').textContent = `${chamberLabel(t.chamber_id)} • ${t.source || 'live'}`;
    $('dTier').textContent = t.tier || '--'; $('dTier').className = `pill ${tierPillClass(t.tier)}`;
    $('dStatus').textContent = t.status || '--'; $('dStatus').className = `pill ${statusPillClass(t)}`;
    renderGates(t);
    $('mRunway').textContent = fmtRunway(t.thermal_runway_min);
    $('runwayFoot').textContent = t.thermal_runway_min == null ? 'no active thermal runway' : 'to spoilage cliff';
    const rc = $('runwayCard'); rc.classList.remove('safe','danger');
    if (t.thermal_runway_min == null) rc.classList.add('safe'); else if (Number(t.thermal_runway_min) <= 15) rc.classList.add('danger');
    $('mConf').textContent = fmtPct(t.confidence);
    $('confFoot').textContent = t.guardrail_violation_flag ? 'penalized -0.40 by Gate 2' : 'bounded by guardrails';
    $('mTime').textContent = t.detected_at_ist || '--';
    $('mSup').textContent = t.supervisor_name || '--';
    $('supFoot').textContent = t.closed ? 'closure verified' : 'HITL accountable';
    renderDisposition(t);
    $('calcList').innerHTML = (t.calc_breakdown && t.calc_breakdown.length) ? t.calc_breakdown.map((l) => `<li>${esc(l)}</li>`).join('') : `<li>Standard SOP threshold evaluation. No thermal drift calculation available.</li>`;
    renderTrace(t); renderAudit(t);
    const showTimeout = !t.closed && t.tier === 'TIER_2_HITL' && t.status !== 'NO_HUMAN_ACK_SEALED';
    $('timeoutBtn').classList.toggle('hidden', !showTimeout);
    const showClose = !t.closed && t.tier !== 'TIER_3_QUARANTINE' && t.status !== 'NO_HUMAN_ACK_SEALED';
    $('closeBox').classList.toggle('hidden', !showClose);
    $('closeRulePill').textContent = t.closure_rule || 'LOCKED UNTIL 3 STABLE TICKS';
    $('closeMsg').textContent = ''; $('closeMsg').className = 'inline-msg';
  }

  function render() { renderSchematic(); renderQueue(); renderDetail(); renderMemory(); }

  async function refresh(selectId) {
    const data = await api('/api/state');
    state.alerts = data.alerts || [];
    state.memory = data.memory || [];
    state.profiles = data.profiles || state.profiles;
    if (selectId) state.selected = selectId;
    if (!state.selected && state.alerts.length) state.selected = state.alerts[0].incident_id;
    if (state.selected && !state.alerts.some((a) => a.incident_id === state.selected)) state.selected = state.alerts[0]?.incident_id || null;
    render();
  }

  async function runScenario(sc) {
    try { const r = await api(`/api/scenarios/${encodeURIComponent(sc)}`, { method: 'POST' }); await refresh(r.ticket?.incident_id); setTab('agent'); }
    catch (e) { showToast(e.message, true); }
  }

  async function runTour() {
    const btn = $('tourBtn'); btn.disabled = true; btn.textContent = 'Running tour…';
    try {
      await api('/api/reset', { method: 'POST' });
      for (const sc of ['loading_surge', 'stuck_defrost', 'solar_eb_outage', 'guardrail_breach', 'unknown_leak']) await api(`/api/scenarios/${sc}`, { method: 'POST' });
      await refresh();
      const target = state.alerts.find((a) => a.root_cause_code === 'UNKNOWN_THERMAL_LEAK') || state.alerts[0];
      if (target) { await api(`/api/tickets/${target.incident_id}/timeout`, { method: 'POST' }); await refresh(target.incident_id); }
      setTab('agent'); showToast('Judge tour complete — incidents, guardrail breach, and no-human quarantine sealed.');
    } catch (e) { showToast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = 'Run Judge Tour'; }
  }

  function initActions() {
    qsa('[data-sc]').forEach((btn) => btn.addEventListener('click', () => runScenario(btn.dataset.sc)));
    $('tourBtn').addEventListener('click', runTour);
    $('resetBtn').addEventListener('click', async () => { try { await api('/api/reset', { method: 'POST' }); state.selected = null; await refresh(); showToast('Runtime state reset.'); } catch (e) { showToast(e.message, true); } });
    $('timeoutBtn').addEventListener('click', async () => { if (!state.selected) return; try { await api(`/api/tickets/${state.selected}/timeout`, { method: 'POST' }); await refresh(state.selected); showToast('No-human response simulated. Batch sealed for CAPA.', true); } catch (e) { showToast(e.message, true); } });
    $('fileInput').addEventListener('change', async (e) => {
      const f = e.target.files?.[0]; if (!f) return;
      const fd = new FormData(); fd.append('file', f);
      try { const res = await fetch('/api/upload', { method: 'POST', body: fd }); const data = await res.json().catch(() => ({})); if (!res.ok) throw new Error(data.detail || 'Upload failed'); await refresh(data.ticket?.incident_id); setTab('agent'); showToast(`Uploaded ${f.name} — incident created.`); }
      catch (err) { showToast(err.message, true); }
      e.target.value = '';
    });
    $('fillStableBtn').addEventListener('click', () => { const t = getSelected(); const ch = t?.chamber_id; let ticks = [-19.0,-20.0,-20.5]; if (ch === 'CH-02_VACCINES') ticks = [5.0,4.5,4.2]; if (ch === 'CH-03_HORTICULTURE') ticks = [7.0,6.5,6.0]; $('ticksInput').value = ticks.map((v)=>v.toFixed(1)).join(', '); });
    $('fillBadBtn').addEventListener('click', () => { $('ticksInput').value = '-21.0, -20.0, -19.0'; });
    $('closeBtn').addEventListener('click', async () => {
      if (!state.selected) return;
      const msg = $('closeMsg'); msg.className = 'inline-msg'; msg.textContent = 'Verifying 3-tick recovery lock…';
      try {
        const raw = $('ticksInput').value.split(',').map((x) => Number(x.trim())).filter((x) => !Number.isNaN(x));
        const body = { supervisor_name: $('supInput').value || 'Supervisor', resolution_notes: $('noteInput').value || '', recent_ticks: raw.map((temp_c) => ({ temp_c })) };
        await api(`/api/tickets/${state.selected}/close`, { method: 'POST', body: JSON.stringify(body) });
        msg.className = 'inline-msg ok'; msg.textContent = 'Gate 3 verified. Ticket closed and resolution saved to verified memory.';
        await refresh(state.selected); showToast(`🧠 Saved to Verified Memory • Slot ${state.memory.length}/20`); setTab('agent');
      } catch (e) { msg.className = 'inline-msg bad'; msg.textContent = `BLOCKED: ${e.message}`; }
    });
  }

  function handleShortcuts() {
    const p = new URLSearchParams(window.location.search);
    if (p.get('action') === 'tour') runTour();
    if (p.get('action') === 'unknown_leak') runScenario('unknown_leak');
  }

  async function boot() {
    initPWA(); initTabs(); initActions(); tickClock(); setInterval(tickClock, 1000);
    try { await refresh(); } catch (e) { showToast(`Failed to load state: ${e.message}`, true); }
    handleShortcuts();
    setInterval(() => { refresh(state.selected).catch(() => {}); }, 15000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
