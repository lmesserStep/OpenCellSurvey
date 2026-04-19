/**
 * app.js — global survey status management, utilities, toasts.
 */

(function (OCS) {
  'use strict';

  // ── Unit conversion ──────────────────────────────────────────────────────────
  OCS.units = {
    current: 'meters',

    init() {
      fetch('/api/settings')
        .then(r => r.json())
        .then(s => { this.current = s.units || 'meters'; })
        .catch(() => {});
    },

    metersToDisplay(m) {
      return this.current === 'feet' ? (m * 3.28084).toFixed(1) + ' ft' : m.toFixed(1) + ' m';
    },

    label() { return this.current === 'feet' ? 'ft' : 'm'; },
  };

  // ── Toast notifications ──────────────────────────────────────────────────────
  OCS.toast = {
    _container: null,

    _ensure() {
      if (this._container) return;
      this._container = document.createElement('div');
      this._container.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;';
      document.body.appendChild(this._container);
    },

    show(message, type = 'info', duration = 3500) {
      this._ensure();
      const colors = { success: '#28a745', danger: '#dc3545', warning: '#ffc107', info: '#2f5668' };
      const el = document.createElement('div');
      el.style.cssText = `
        background:${colors[type]||colors.info};color:#fff;
        padding:10px 16px;border-radius:6px;font-size:.85rem;
        box-shadow:0 2px 8px rgba(0,0,0,.2);min-width:220px;
        animation:fadeInRight .2s ease;
      `;
      el.textContent = message;
      this._container.appendChild(el);
      setTimeout(() => {
        el.style.opacity = '0';
        el.style.transition = 'opacity .3s';
        setTimeout(() => el.remove(), 350);
      }, duration);
    },

    success(msg) { this.show(msg, 'success'); },
    danger(msg)  { this.show(msg, 'danger'); },
    warning(msg) { this.show(msg, 'warning'); },
    info(msg)    { this.show(msg, 'info'); },
  };

  // ── Survey status badge ──────────────────────────────────────────────────────
  OCS.surveyStatus = {
    _badge: null,

    init() {
      this._badge = document.getElementById('survey-status-badge');
      OCS.ws.on('status', (data) => this._update(data));
    },

    _update(data) {
      if (!this._badge) return;
      if (data.running) {
        this._badge.className = 'survey-badge running';
        this._badge.innerHTML = '<i class="bi bi-record-circle-fill text-danger"></i> Scanning';
      } else {
        this._badge.className = 'survey-badge';
        this._badge.innerHTML = '<i class="bi bi-circle-fill text-secondary"></i> Idle';
      }
    },
  };

  // ── RSRP signal quality helper (LTE industry-standard thresholds) ───────────
  OCS.signalQuality = {
    // Excellent >= -80 | Good -80 to -90 | Fair -90 to -100 | Poor < -100
    rsrpClass(v) {
      if (v >= -80)  return 'sig-excellent';
      if (v >= -90)  return 'sig-good';
      if (v >= -100) return 'sig-fair';
      return 'sig-poor';
    },
    rsrpLabel(v) {
      if (v >= -80)  return 'Excellent';
      if (v >= -90)  return 'Good';
      if (v >= -100) return 'Fair';
      return 'Poor';
    },
    rsrpColor(v) {
      if (v >= -80)  return '#007bff';   // Excellent — blue
      if (v >= -90)  return '#28a745';   // Good      — green
      if (v >= -100) return '#ffc107';   // Fair      — yellow
      return '#dc3545';                   // Poor      — red
    },
    // Metric-card CSS class for live RSRP card colouring
    rsrpCardClass(v) {
      if (v >= -80)  return 'excellent';
      if (v >= -90)  return 'good';
      if (v >= -100) return 'warn';
      return 'bad';
    },
    // Normalise RSRP (-120 worst → -60 best) to 0…1
    rsrpNorm(v) { return Math.max(0, Math.min(1, (v + 120) / 60)); },
  };

  // ── DOM ready init ───────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    OCS.units.init();
    OCS.surveyStatus.init();
  });

}(window.OCS = window.OCS || {}));
