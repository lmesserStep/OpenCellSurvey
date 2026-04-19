/**
 * survey.js — scan configuration form logic and live metric updates.
 * Loaded only on /survey.
 */

(function (OCS) {
  'use strict';

  const BANDS_API = '/api/bands';
  let bands = {};
  let running = false;

  // ── Fetch band database ──────────────────────────────────────────────────────
  async function loadBands() {
    try {
      const resp = await fetch(BANDS_API);
      bands = await resp.json();
    } catch (e) {
      console.warn('Could not load bands', e);
    }
  }

  // ── Band selection → populate freq info ──────────────────────────────────────
  function onBandChange() {
    const sel  = document.getElementById('band-select');
    const info = document.getElementById('band-info');
    if (!sel || !info) return;
    // API returns integer keys; sel.value is a string — coerce to match.
    const b = bands[parseInt(sel.value, 10)] || bands[sel.value];
    if (!b) { info.textContent = ''; return; }
    info.innerHTML = `
      DL ${b.dl_low}–${b.dl_high} MHz &nbsp;|&nbsp;
      EARFCN ${b.earfcn_dl_low}–${b.earfcn_dl_high}
    `;
  }

  // ── Frame type → show/hide bandwidth ────────────────────────────────────────
  function onFrameTypeChange() {
    const ft  = document.getElementById('frame-type');
    const bwG = document.getElementById('bw-group');
    if (!ft || !bwG) return;
    bwG.style.display = ft.value === 'TDD' ? '' : 'none';
    document.getElementById('bw-select').required = ft.value === 'TDD';
  }

  // ── Start survey ─────────────────────────────────────────────────────────────
  async function startSurvey() {
    const band         = parseInt(document.getElementById('band-select').value, 10);
    const pciVal       = document.getElementById('pci-input').value.trim();
    const mode         = document.getElementById('scan-mode').value;
    const frame_type   = document.getElementById('frame-type').value;
    const bw           = document.getElementById('bw-select')?.value;
    const binary_path  = document.getElementById('binary-path').value.trim();

    if (frame_type === 'TDD' && !bw) {
      OCS.toast.warning('Bandwidth is required for TDD mode.');
      return;
    }

    const body = {
      band,
      mode,
      frame_type,
      binary_path,
      pci:           pciVal ? parseInt(pciVal, 10) : null,
      bandwidth_mhz: frame_type === 'TDD' ? parseInt(bw, 10) : null,
    };

    try {
      const resp = await fetch('/api/survey/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json();
        OCS.toast.danger('Start failed: ' + (err.detail || resp.statusText));
        return;
      }
      const data = await resp.json();
      running = true;
      updateStartStopUI();
      OCS.toast.success(`Survey started — session ${data.session_id}`);
    } catch (e) {
      OCS.toast.danger('Network error: ' + e.message);
    }
  }

  // ── Stop survey ──────────────────────────────────────────────────────────────
  async function stopSurvey() {
    try {
      const resp = await fetch('/api/survey/stop', { method: 'POST' });
      if (!resp.ok) {
        const err = await resp.json();
        OCS.toast.danger('Stop failed: ' + (err.detail || resp.statusText));
        return;
      }
      running = false;
      updateStartStopUI();
      OCS.toast.success('Survey stopped.');
    } catch (e) {
      OCS.toast.danger('Network error: ' + e.message);
    }
  }

  function updateStartStopUI() {
    const startBtn = document.getElementById('btn-start');
    const stopBtn  = document.getElementById('btn-stop');
    const form     = document.getElementById('scan-form');
    if (!startBtn || !stopBtn) return;
    startBtn.disabled = running;
    stopBtn.disabled  = !running;
    if (form) {
      form.querySelectorAll('input,select').forEach(el => { el.disabled = running; });
      // keep stop always enabled
      stopBtn.disabled = !running;
    }
  }

  // ── Live metric updates ──────────────────────────────────────────────────────
  function updateMetrics(d) {
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    set('m-rsrp', d.rsrp_dbm != null ? d.rsrp_dbm.toFixed(1) : '—');
    set('m-rsrq', d.rsrq_db  != null ? d.rsrq_db.toFixed(1)  : '—');
    set('m-snr',  d.snr_db   != null ? d.snr_db.toFixed(1)   : '—');
    set('m-freq', d.freq_mhz != null ? d.freq_mhz.toFixed(2) : '—');
    set('m-pci',  d.pci      != null ? d.pci                  : '—');
    set('m-prb',  d.n_prb    != null ? d.n_prb                : '—');
    set('m-ports',d.ports    != null ? d.ports                : '—');
    set('m-band', d.band     != null ? d.band                 : '—');
    set('m-frame',d.frame_type || '—');

    // Colour RSRP card
    const card = document.getElementById('card-rsrp');
    if (card && d.rsrp_dbm != null) {
      card.classList.remove('good','warn','bad');
      card.classList.add(d.rsrp_dbm >= -80 ? 'good' : d.rsrp_dbm >= -100 ? 'warn' : 'bad');
    }
  }

  // ── Binary auto-detect ───────────────────────────────────────────────────────
  async function detectBinary() {
    const resp = await fetch('/api/binary/detect', { method: 'POST' });
    const data = await resp.json();
    if (data.found) {
      document.getElementById('binary-path').value = data.path;
      OCS.toast.success('Binary found: ' + data.path);
    } else {
      OCS.toast.warning('Binary not found in standard locations. Set path manually.');
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', async () => {
    await loadBands();

    const bandSel = document.getElementById('band-select');
    if (bandSel) { bandSel.addEventListener('change', onBandChange); onBandChange(); }

    const ft = document.getElementById('frame-type');
    if (ft) { ft.addEventListener('change', onFrameTypeChange); onFrameTypeChange(); }

    document.getElementById('btn-start')?.addEventListener('click', startSurvey);
    document.getElementById('btn-stop')?.addEventListener('click', stopSurvey);
    document.getElementById('btn-detect')?.addEventListener('click', detectBinary);

    // Pre-populate binary path from saved settings (overrides the hardcoded default).
    try {
      const s = await fetch('/api/settings').then(r => r.json());
      if (s.binary_path) {
        const pathEl = document.getElementById('binary-path');
        if (pathEl) pathEl.value = s.binary_path;
      }
    } catch (_) {}

    // Sync initial running state
    const statusResp = await fetch('/api/survey/status');
    const status = await statusResp.json();
    running = status.running;
    updateStartStopUI();

    // Live metrics
    OCS.ws.on('survey_data', updateMetrics);
    OCS.ws.on('status', (d) => {
      running = d.running;
      updateStartStopUI();
    });
  });

}(window.OCS = window.OCS || {}));
