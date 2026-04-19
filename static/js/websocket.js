/**
 * websocket.js — persistent WebSocket client with auto-reconnect.
 *
 * Provides:
 *   OCS.ws.send(type, data)
 *   OCS.ws.on(eventType, handler)
 *   OCS.ws.off(eventType, handler)
 */

(function (OCS) {
  'use strict';

  const RECONNECT_MS  = 3000;
  // Use wss:// when the page is served over HTTPS (required for TLS deployments).
  const WS_SCHEME     = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const WS_URL        = `${WS_SCHEME}//${location.host}/ws`;

  let socket   = null;
  let handlers = {};   // { eventType: [fn, ...] }

  function _dispatch(type, data) {
    (handlers[type] || []).forEach(fn => { try { fn(data); } catch (e) { console.error(e); } });
    (handlers['*']  || []).forEach(fn => { try { fn(type, data); } catch (e) { console.error(e); } });
  }

  function _setStatus(state) {
    const el  = document.getElementById('ws-status');
    const dot = el?.querySelector('.dot');
    const lbl = el?.querySelector('span:last-child') || el;
    if (!el) return;
    dot?.classList.remove('dot-green', 'dot-red', 'dot-orange', 'dot-grey');
    if (state === 'connected')    { dot?.classList.add('dot-green');  if (lbl !== dot) lbl.textContent = 'Connected'; }
    if (state === 'disconnected') { dot?.classList.add('dot-red');    if (lbl !== dot) lbl.textContent = 'Disconnected'; }
    if (state === 'connecting')   { dot?.classList.add('dot-orange'); if (lbl !== dot) lbl.textContent = 'Connecting…'; }
  }

  function connect() {
    _setStatus('connecting');
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
      _setStatus('connected');
      _dispatch('_connected', {});
      // Start sending browser GPS
      OCS.gps.startWatching();
    };

    socket.onclose = () => {
      _setStatus('disconnected');
      _dispatch('_disconnected', {});
      OCS.gps.stopWatching();
      setTimeout(connect, RECONNECT_MS);
    };

    socket.onerror = () => {
      _setStatus('disconnected');
    };

    socket.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        _dispatch(msg.type, msg.data);
      } catch (e) {
        console.warn('WS parse error', e);
      }
    };
  }

  OCS.ws = {
    send(type, data) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type, data }));
      }
    },
    on(type, fn)  { (handlers[type] = handlers[type] || []).push(fn); },
    off(type, fn) { handlers[type] = (handlers[type] || []).filter(h => h !== fn); },
  };

  // Auto-connect when DOM is ready
  document.addEventListener('DOMContentLoaded', connect);

}(window.OCS = window.OCS || {}));


/* ── GPS helper ─────────────────────────────────────────────────────────────── */
(function (OCS) {
  let watchId = null;

  OCS.gps = {
    startWatching() {
      if (!navigator.geolocation || watchId !== null) return;
      watchId = navigator.geolocation.watchPosition(
        (pos) => {
          const { latitude, longitude, accuracy } = pos.coords;
          OCS.ws.send('gps', { latitude, longitude, accuracy });
          OCS.ws._dispatch && void 0; // internal only
          // fire local listeners too
          const evt = new CustomEvent('ocs:gps', { detail: { latitude, longitude, accuracy } });
          document.dispatchEvent(evt);
        },
        (err) => console.warn('GPS error', err),
        { enableHighAccuracy: true, maximumAge: 2000, timeout: 10000 },
      );
    },
    stopWatching() {
      if (watchId !== null) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
      }
    },
  };

}(window.OCS = window.OCS || {}));
