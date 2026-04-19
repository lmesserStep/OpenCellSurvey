/**
 * map.js — Leaflet map, grid-based aggregation, path tracking, live graph,
 *           survey stats, radio config display, zoom-adaptive rendering.
 * Loaded only on /map.
 */

(function (OCS) {
  'use strict';

  // ── State ─────────────────────────────────────────────────────────────────────
  let map, pathLine, myMarker, tileLayer;
  let gridCells      = {};   // gridKey → aggregated cell object
  let measureLayer   = null; // currently rendered measurement rectangles
  let towerMarkers   = {};   // tower id → L.marker
  let pathCoords     = [];   // [[lat,lng], …] for the path polyline
  let heatMetric     = 'rsrp';
  let indoorMode     = false;
  let floorplanLayer = null;
  let floorplanUrl   = null;   // URL of the loaded floor plan image
  let floorplanBounds = null;  // [[s,w],[n,e]] bounds the floor plan is pinned to
  let fpOverlayMode  = 'overlay'; // 'overlay' | 'indoor'
  let indoorPos      = null;      // { lat, lng } manual indoor position
  let simulationMode      = false;
  let simulationLayer     = null;
  let simulationPoints    = [];
  let simulationGridSizeM = 20;  // metres — matches the request grid_size_meters
  let hasInitialView = false;
  let rsrpHistory    = [];   // { y: dBm } — last 100 samples for live chart
  let rsrpChart      = null;
  let lastMeasurement = null;  // most-recent RF data from the binary (for indoor REC)

  // Grid cell size — ~5 m (outdoor default; overridden for indoor)
  const GRID_DEG = 0.000045;

  /**
   * Returns the grid cell size in degrees for the current mode.
   * Outdoor: fixed 5 m (~GRID_DEG).
   * Indoor: 1/15 of the floor-plan's larger dimension so the plan is divided
   *         into ~15 zones — coarse enough that a single tap fills a room.
   */
  function currentGridDeg() {
    if (indoorMode && floorplanBounds) {
      const latSpan = floorplanBounds[1][0] - floorplanBounds[0][0];
      const lonSpan = floorplanBounds[1][1] - floorplanBounds[0][1];
      return Math.max(latSpan, lonSpan) / 15;
    }
    return GRID_DEG;
  }

  // ── Grid aggregation ──────────────────────────────────────────────────────────

  function gridKey(lat, lng) {
    const g = currentGridDeg();
    return `${Math.floor(lat / g)},${Math.floor(lng / g)}`;
  }

  /**
   * Add a raw measurement point into the grid cell it belongs to.
   * Returns the updated cell.
   */
  function addToGrid(d) {
    const g   = currentGridDeg();
    const key = gridKey(d.latitude, d.longitude);
    if (!gridCells[key]) {
      // Use grid-cell centre so the rendered square is centred on the cell,
      // not biased toward whichever point happened to land there first.
      const ci = Math.floor(d.latitude  / g);
      const cj = Math.floor(d.longitude / g);
      gridCells[key] = {
        lat      : ci * g + g / 2,
        lon      : cj * g + g / 2,
        rsrp_sum : 0, rsrq_sum : 0, snr_sum : 0,
        count    : 0,
        // radio config from first point; updated to latest
        pci       : d.pci,
        band      : d.band,
        earfcn    : d.earfcn,
        prb       : d.n_prb,
        ports     : d.ports,
        frame_type: d.frame_type,
        timestamp : d.timestamp,
      };
    }
    const cell = gridCells[key];
    if (d.rsrp_dbm != null) cell.rsrp_sum += d.rsrp_dbm;
    if (d.rsrq_db  != null) cell.rsrq_sum += d.rsrq_db;
    if (d.snr_db   != null) cell.snr_sum  += d.snr_db;
    // Rolling centroid
    cell.lat = (cell.lat * cell.count + d.latitude)  / (cell.count + 1);
    cell.lon = (cell.lon * cell.count + d.longitude) / (cell.count + 1);
    cell.count++;
    // Keep radio config current
    cell.pci        = d.pci;
    cell.band       = d.band;
    cell.earfcn     = d.earfcn;
    cell.prb        = d.n_prb;
    cell.ports      = d.ports;
    cell.frame_type = d.frame_type;
    return cell;
  }

  function cellAvg(cell, metric) {
    if (!cell.count) return null;
    if (metric === 'rsrp') return cell.rsrp_sum / cell.count;
    if (metric === 'rsrq') return cell.rsrq_sum / cell.count;
    if (metric === 'snr')  return cell.snr_sum  / cell.count;
    return null;
  }

  // ── Map init ──────────────────────────────────────────────────────────────────

  function initMap() {
    map = L.map('main-map', { zoomControl: true });

    tileLayer = L.tileLayer('/api/tiles/{z}/{x}/{y}.png', {
      attribution : '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom     : 19,
      crossOrigin : true,
      errorTileUrl: 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==',
    }).addTo(map);

    // Survey path — orange, clearly visible
    pathLine = L.polyline([], { color: '#f05a28', weight: 3, opacity: 0.85 }).addTo(map);

    map.on('contextmenu', onMapRightClick);

    // Indoor click-to-place-and-record: sets position marker and immediately
    // records an RF measurement there (no need to press REC separately).
    // Works on SVG overlays where imageOverlay.on('click') is unreliable.
    map.on('click', (e) => {
      if (!indoorMode) return;
      indoorPos = { lat: e.latlng.lat, lng: e.latlng.lng };
      placeIndoorMarker(indoorPos.lat, indoorPos.lng);
      indoorRecord();   // auto-record; indoorRecord() shows its own feedback toast
    });

    // Re-render squares whenever zoom changes so sizes stay readable
    map.on('zoomend', () => {
      if (simulationMode) renderSimulatedCoverage();
      else                renderMeasurements();
    });
  }

  // ── GPS tracking ──────────────────────────────────────────────────────────────

  document.addEventListener('ocs:gps', (evt) => {
    const { latitude: lat, longitude: lng } = evt.detail;
    if (!map) return;
    // In indoor-only mode the GPS position is irrelevant — the user places their
    // position manually by clicking the floor plan, so don't move the GPS dot.
    if (fpOverlayMode === 'indoor') return;
    if (!myMarker) {
      myMarker = L.circleMarker([lat, lng], {
        radius: 8, color: '#f05a28', fillColor: '#f05a28', fillOpacity: 1, weight: 2,
      }).bindPopup('You are here').addTo(map);
    } else {
      myMarker.setLatLng([lat, lng]);
    }
    const followEl = document.getElementById('follow-gps');
    if (!hasInitialView) {
      map.setView([lat, lng], 16);
      hasInitialView = true;
    } else if (followEl?.checked) {
      map.setView([lat, lng]);
    }
  });

  // ── Process one measurement point (live or historical) ────────────────────────

  function processPoint(d) {
    if (!d.latitude || !d.longitude) return;

    // Add to grid
    addToGrid(d);

    // Extend path
    pathCoords.push([d.latitude, d.longitude]);
    pathLine.setLatLngs(pathCoords);

    // Feed live chart
    if (d.rsrp_dbm != null) {
      rsrpHistory.push({ ts: d.timestamp || new Date().toISOString(), y: d.rsrp_dbm });
      if (rsrpHistory.length > 100) rsrpHistory.shift();
      updateChart();
    }
  }

  // ── Live WebSocket data ───────────────────────────────────────────────────────

  OCS.ws.on('survey_data', (d) => {
    lastMeasurement = d;   // keep latest RF data for indoor REC
    processPoint(d);
    renderMeasurements();
    updateRadioDisplay(d);
    updateStats();
    // Update live signal mini cards
    updateLiveSignal(d);
  });

  function updateLiveSignal(d) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('mm-rsrp', d.rsrp_dbm != null ? d.rsrp_dbm.toFixed(1) : '—');
    set('mm-rsrq', d.rsrq_db  != null ? d.rsrq_db.toFixed(1)  : '—');
    set('mm-snr',  d.snr_db   != null ? d.snr_db.toFixed(1)   : '—');
    const card = document.getElementById('card-rsrp-map');
    if (card && d.rsrp_dbm != null) {
      card.classList.remove('excellent', 'good', 'warn', 'bad');
      card.classList.add(OCS.signalQuality.rsrpCardClass(d.rsrp_dbm));
    }
  }

  // ── Zoom-adaptive square size ─────────────────────────────────────────────────
  // zoom 13 → ~10 m squares  |  zoom 17 → ~2 m squares

  function squareSizeDeg() {
    // Indoor: fill each grid zone so a single tap covers the whole room area
    if (indoorMode && floorplanBounds) return currentGridDeg();
    // Outdoor: zoom-adaptive (zoom 13→~10 m, zoom 17→~2 m)
    if (!map) return GRID_DEG * 2;
    const zoom   = map.getZoom();
    const size_m = Math.max(1.5, 5 * Math.pow(2, Math.max(0, 16 - zoom)));
    return size_m / 111000;
  }

  // ── Render grid-aggregated measurement squares ────────────────────────────────

  function renderMeasurements() {
    if (simulationMode) return;
    if (measureLayer) map.removeLayer(measureLayer);
    measureLayer = L.featureGroup().addTo(map);

    const sz = squareSizeDeg();

    Object.values(gridCells).forEach(cell => {
      const val = cellAvg(cell, heatMetric);
      if (val == null) return;

      const color = metricColor(val, heatMetric);
      const bounds = [
        [cell.lat - sz / 2, cell.lon - sz / 2],
        [cell.lat + sz / 2, cell.lon + sz / 2],
      ];

      const rsrp = cell.count ? (cell.rsrp_sum / cell.count).toFixed(1) : '—';
      const rsrq = cell.count ? (cell.rsrq_sum / cell.count).toFixed(1) : '—';
      const snr  = cell.count ? (cell.snr_sum  / cell.count).toFixed(1) : '—';

      const tip = `
        <div style="font-family:monospace;font-size:.82rem;line-height:1.65">
          <b>PCI ${cell.pci ?? '—'}</b> &nbsp;&bull;&nbsp; Band ${cell.band ?? '—'}<br>
          EARFCN: ${cell.earfcn ?? '—'}
          <hr style="margin:4px 0;border-color:#ddd">
          RSRP: <b>${rsrp} dBm</b><br>
          RSRQ: ${rsrq} dB &nbsp;&bull;&nbsp; SNR: ${snr} dB
          <hr style="margin:4px 0;border-color:#ddd">
          Samples: <b>${cell.count}</b>
        </div>`;

      L.rectangle(bounds, {
        color, weight: 0.5, fillColor: color, fillOpacity: 0.82,
      })
        .bindTooltip(tip, { sticky: true, opacity: 0.97 })
        .bindPopup(tip)
        .addTo(measureLayer);
    });
  }

  // ── Metric → colour mapping ───────────────────────────────────────────────────

  function metricColor(val, metric) {
    if (metric === 'rsrp') return OCS.signalQuality.rsrpColor(val);
    if (metric === 'rsrq') {
      if (val >= -5)  return '#007bff';
      if (val >= -10) return '#28a745';
      if (val >= -15) return '#ffc107';
      return '#dc3545';
    }
    if (metric === 'snr') {
      if (val >= 20)  return '#007bff';
      if (val >= 10)  return '#28a745';
      if (val >= 0)   return '#ffc107';
      return '#dc3545';
    }
    return '#888';
  }

  // ── Survey statistics ─────────────────────────────────────────────────────────

  function haversineM(lat1, lon1, lat2, lon2) {
    const R    = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a    = Math.sin(dLat / 2) ** 2
               + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180)
               * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function updateStats() {
    const cells  = Object.values(gridCells);
    const total  = cells.reduce((s, c) => s + c.count, 0);

    // Path distance
    let distM = 0;
    for (let i = 1; i < pathCoords.length; i++) {
      distM += haversineM(
        pathCoords[i - 1][0], pathCoords[i - 1][1],
        pathCoords[i][0],     pathCoords[i][1],
      );
    }
    const distMiles = (distM * 0.000621371).toFixed(2);

    // RSRP aggregates
    const rsrpVals = cells.filter(c => c.count > 0).map(c => c.rsrp_sum / c.count);
    const avg   = rsrpVals.length ? (rsrpVals.reduce((s, v) => s + v, 0) / rsrpVals.length).toFixed(1) : null;
    const best  = rsrpVals.length ? Math.max(...rsrpVals).toFixed(1) : null;
    const worst = rsrpVals.length ? Math.min(...rsrpVals).toFixed(1) : null;

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('stat-samples',   total);
    set('stat-distance',  distMiles + ' mi');
    set('stat-avg-rsrp',  avg   != null ? avg   + ' dBm' : '—');
    set('stat-best-rsrp', best  != null ? best  + ' dBm' : '—');
    set('stat-worst-rsrp',worst != null ? worst + ' dBm' : '—');
  }

  // ── Radio / cell configuration display ───────────────────────────────────────

  function updateRadioDisplay(d) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v ?? '—'; };
    set('cell-band',   d.band);
    set('cell-earfcn', d.earfcn);
    set('cell-pci',    d.pci);
    set('cell-freq',   d.freq_mhz ? d.freq_mhz + ' MHz' : null);
    set('cell-prb',    d.n_prb);
    set('cell-ports',  d.ports);
    set('cell-frame',  d.frame_type);
  }

  // ── Live RSRP chart (Chart.js) ────────────────────────────────────────────────

  function initChart() {
    const canvas = document.getElementById('rsrp-chart');
    if (!canvas || !window.Chart) return;

    rsrpChart = new Chart(canvas, {
      type: 'line',
      data: {
        labels  : [],
        datasets: [{
          label          : 'RSRP',
          data           : [],
          borderColor    : '#f05a28',
          backgroundColor: 'rgba(240,90,40,0.12)',
          borderWidth    : 1.5,
          pointRadius    : 0,
          tension        : 0.3,
          fill           : true,
        }],
      },
      options: {
        animation     : false,
        responsive    : true,
        maintainAspectRatio: false,
        plugins       : { legend: { display: false } },
        scales: {
          x: {
            ticks: { display: false },
            grid : { display: false },
          },
          y: {
            min  : -130,
            max  : -40,
            ticks: {
              font    : { size: 9 },
              callback: v => v + ' dB',
              stepSize: 20,
            },
            grid : { color: '#f0f0f0' },
          },
        },
      },
    });
  }

  function updateChart() {
    if (!rsrpChart) return;
    rsrpChart.data.labels              = rsrpHistory.map((_, i) => i);
    rsrpChart.data.datasets[0].data   = rsrpHistory.map(p => p.y);
    rsrpChart.update('none');
  }

  // ── Simulated coverage ────────────────────────────────────────────────────────

  function renderSimulatedCoverage() {
    if (simulationLayer) map.removeLayer(simulationLayer);
    simulationLayer = L.featureGroup().addTo(map);
    // Use the simulation grid size (full cell width) so squares tile with no gaps
    const sz = simulationGridSizeM / 111000;
    simulationPoints.forEach(pt => {
      const color  = OCS.signalQuality.rsrpColor(pt.rsrp_dbm);
      const bounds = [
        [pt.lat - sz / 2, pt.lon - sz / 2],
        [pt.lat + sz / 2, pt.lon + sz / 2],
      ];
      const rsrpLabel = OCS.signalQuality.rsrpLabel(pt.rsrp_dbm);
      const tip = `
        <div style="font-family:monospace;font-size:.82rem;line-height:1.65">
          RSRP: <b>${pt.rsrp_dbm} dBm</b> (${rsrpLabel})<br>
          Distance: ${pt.distance_km} km
        </div>`;
      L.rectangle(bounds, { color, weight: 0.5, fillColor: color, fillOpacity: 0.78 })
       .bindTooltip(tip, { sticky: true, opacity: 0.97 })
       .bindPopup(tip)
       .addTo(simulationLayer);
    });
  }

  function setHeatmapMode(mode) {
    const towerDiv = document.getElementById('tower-select-div');
    if (mode === 'simulated') {
      simulationMode = true;
      if (measureLayer) { map.removeLayer(measureLayer); measureLayer = null; }
      if (towerDiv) towerDiv.style.display = '';
      updateTowerSelectDropdown();
    } else {
      simulationMode = false;
      if (simulationLayer) { map.removeLayer(simulationLayer); simulationLayer = null; }
      if (towerDiv) towerDiv.style.display = 'none';
      renderMeasurements();
    }
  }

  async function simulateTowerCoverage(towerId) {
    if (!towerId) {
      if (simulationLayer) map.removeLayer(simulationLayer);
      simulationLayer = null;
      return;
    }
    const gridSizeM = 20;
    try {
      const resp = await fetch('/api/propagation/simulate', {
        method : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body   : JSON.stringify({ tower_id: parseInt(towerId), grid_size_meters: gridSizeM, max_distance_km: 5 }),
      });
      if (!resp.ok) { OCS.toast.danger('Simulation failed: ' + resp.statusText); return; }
      const data = await resp.json();
      simulationPoints    = data.points;
      simulationGridSizeM = gridSizeM;
      renderSimulatedCoverage();
      OCS.toast.success(`Simulated ${data.points.length} coverage points`);
    } catch (e) {
      OCS.toast.danger('Error: ' + e.message);
    }
  }

  function updateTowerSelectDropdown() {
    const sel = document.getElementById('tower-select');
    if (!sel) return;
    fetch('/api/towers').then(r => r.json()).then(towers => {
      sel.innerHTML = '<option value="">— Choose tower —</option>';
      towers.forEach(t => {
        sel.insertAdjacentHTML('beforeend', `<option value="${t.id}">${t.name}</option>`);
      });
    });
  }

  // ── Load historical session ───────────────────────────────────────────────────

  async function loadSession(sessionId) {
    // Clear state
    gridCells   = {};
    pathCoords  = [];
    rsrpHistory = [];
    if (measureLayer) { map.removeLayer(measureLayer); measureLayer = null; }
    pathLine.setLatLngs([]);
    if (rsrpChart) rsrpChart.data.datasets[0].data = [];

    const infoCard = document.getElementById('session-info-card');

    if (!sessionId) {
      if (infoCard) infoCard.style.display = 'none';
      updateStats();
      return;
    }

    const resp = await fetch(`/api/sessions/${sessionId}/points`);
    const pts  = await resp.json();

    // Remove any stale circle markers from a previous session
    map.eachLayer(layer => {
      if (layer instanceof L.CircleMarker && layer !== myMarker) map.removeLayer(layer);
    });

    pts.forEach(d => processPoint(d));

    renderMeasurements();
    updateStats();
    updateChart();

    if (pathCoords.length > 0) {
      map.fitBounds(L.latLngBounds(pathCoords));
      hasInitialView = true;
    }

    // Session info card
    const sessResp = await fetch('/api/sessions');
    const sessions = await sessResp.json();
    const session  = sessions.find(s => s.id === sessionId);
    if (session && infoCard) {
      document.getElementById('session-name-display').textContent = session.name || '(Unnamed)';
      const start = session.started_at ? session.started_at.slice(0, 19).replace('T', ' ') : '';
      const end   = session.ended_at   ? session.ended_at.slice(0, 19).replace('T', ' ')   : '';
      document.getElementById('session-time-display').textContent = start + (end ? ' → ' + end : '');
      infoCard.style.display = '';
    }

    OCS.toast.success(`Loaded ${pts.length} points`);
  }

  // ── Towers ────────────────────────────────────────────────────────────────────

  async function loadTowers() {
    const resp   = await fetch('/api/towers');
    const towers = await resp.json();
    towers.forEach(renderTower);
  }

  function renderTower(t) {
    if (!t.latitude || !t.longitude) return;
    const icon = L.divIcon({
      html     : `<div style="color:#f05a28;font-size:22px;line-height:1"><i class="bi bi-broadcast"></i></div>`,
      className: '',
      iconSize : [22, 22], iconAnchor: [11, 22],
    });
    towerMarkers[t.id] = L.marker([t.latitude, t.longitude], { icon })
      .bindPopup(towerPopupHtml(t))
      .addTo(map);
  }

  function towerPopupHtml(t) {
    return `
      <div class="tower-popup">
        <strong>${t.name}</strong><br>
        PCI: ${t.pci ?? '—'} &nbsp;|&nbsp; Band: ${t.band ?? '—'}<br>
        Azimuth: ${t.azimuth ?? '—'}° &nbsp;|&nbsp; Height: ${t.height_m ?? '—'} m<br>
        EIRP: ${t.eirp_w != null ? t.eirp_w + ' W' : '—'}<br>
        ${t.notes ? `<em>${t.notes}</em>` : ''}
      </div>`;
  }

  function removeTowerMarker(id) {
    if (towerMarkers[id]) { map.removeLayer(towerMarkers[id]); delete towerMarkers[id]; }
  }

  // ── Right-click tower placement ───────────────────────────────────────────────

  function onMapRightClick(evt) {
    const { lat, lng } = evt.latlng;
    document.getElementById('tower-lat').value = lat.toFixed(6);
    document.getElementById('tower-lng').value = lng.toFixed(6);
    new bootstrap.Modal(document.getElementById('towerModal')).show();
  }

  async function saveTower() {
    const body = {
      name    : document.getElementById('tower-name').value.trim(),
      pci     : parseInt(document.getElementById('tower-pci').value)    || null,
      band    : parseInt(document.getElementById('tower-band').value)   || null,
      azimuth : parseFloat(document.getElementById('tower-az').value)   || null,
      height_m: parseFloat(document.getElementById('tower-ht').value)   || null,
      eirp_w  : parseFloat(document.getElementById('tower-eirp').value) || null,
      notes   : document.getElementById('tower-notes').value.trim()     || null,
      latitude : parseFloat(document.getElementById('tower-lat').value),
      longitude: parseFloat(document.getElementById('tower-lng').value),
    };
    if (!body.name) { OCS.toast.warning('Tower name is required'); return; }
    const resp = await fetch('/api/towers', {
      method : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    renderTower(await resp.json());
    bootstrap.Modal.getInstance(document.getElementById('towerModal')).hide();
    OCS.toast.success('Tower saved');
  }

  // ── Indoor / floor plan mode ──────────────────────────────────────────────────

  function applyFloorplanOverlayMode(mode) {
    fpOverlayMode = mode;
    if (!floorplanLayer) return;
    if (mode === 'indoor') {
      // Hide tile layer — show floorplan on blank background
      if (tileLayer) map.removeLayer(tileLayer);
      map.getContainer().style.background = '#f0f0f0';
      // Hide GPS dot; user must place position manually
      if (myMarker) myMarker.setOpacity(0);
      OCS.toast.info('Indoor Only: tap the floor plan to set your starting position before recording.');
    } else {
      // Restore tile layer
      if (tileLayer && !map.hasLayer(tileLayer)) tileLayer.addTo(map);
      map.getContainer().style.background = '';
      if (myMarker) myMarker.setOpacity(1);
    }
  }

  function loadFloorplan(url, bounds) {
    if (floorplanLayer) map.removeLayer(floorplanLayer);
    // crossOrigin lets leaflet-image capture the overlay without tainting the canvas
    floorplanLayer  = L.imageOverlay(url, bounds, { opacity: 0.85, crossOrigin: true }).addTo(map);
    floorplanUrl    = url;
    floorplanBounds = [
      [bounds[0][0], bounds[0][1]],
      [bounds[1][0], bounds[1][1]],
    ];
    map.fitBounds(bounds);
    indoorMode = true;
    // Click-to-place is handled by the global map.on('click') handler in initMap()
    applyFloorplanOverlayMode(fpOverlayMode);
  }

  // ── Indoor marker ─────────────────────────────────────────────────────────────

  let indoorMarker = null;

  function placeIndoorMarker(lat, lng) {
    if (indoorMarker) map.removeLayer(indoorMarker);
    indoorMarker = L.circleMarker([lat, lng], {
      radius: 9, color: '#f05a28', fillColor: '#f05a28', fillOpacity: 0.9, weight: 2,
    }).bindPopup('Indoor position').addTo(map);
    indoorPos = { lat, lng };
  }

  // ── Manual indoor navigation ──────────────────────────────────────────────────

  function getNavStep() {
    const el = document.getElementById('nav-step');
    return el ? (parseFloat(el.value) || 2) : 2;
  }

  // degrees per metre (approx)
  const M_TO_LAT = 1 / 111000;
  const mToLon   = (lat) => 1 / (111000 * Math.cos(lat * Math.PI / 180));

  function moveIndoorPos(dlat, dlon) {
    if (!floorplanLayer) { OCS.toast.warning('Load a floor plan first'); return; }
    if (!indoorPos) {
      // Default to floor plan center
      const b = floorplanLayer.getBounds();
      indoorPos = { lat: b.getCenter().lat, lng: b.getCenter().lng };
    }
    const newLat = indoorPos.lat + dlat;
    const newLng = indoorPos.lng + dlon;
    placeIndoorMarker(newLat, newLng);
  }

  function indoorRecord() {
    if (!indoorPos) {
      OCS.toast.warning('Set position first (arrows or tap floor plan)');
      return;
    }
    if (!lastMeasurement) {
      OCS.toast.warning('No RF signal data yet — wait for the scanner to produce measurements');
      return;
    }

    // Build the point from the latest RF measurement + current indoor position
    const point = {
      latitude  : indoorPos.lat,
      longitude : indoorPos.lng,
      band      : lastMeasurement.band,
      earfcn    : lastMeasurement.earfcn,
      freq_mhz  : lastMeasurement.freq_mhz,
      pci       : lastMeasurement.pci,
      n_prb     : lastMeasurement.n_prb,
      ports     : lastMeasurement.ports,
      rsrp_dbm  : lastMeasurement.rsrp_dbm,
      rsrq_db   : lastMeasurement.rsrq_db,
      snr_db    : lastMeasurement.snr_db,
      cfo_hz    : lastMeasurement.cfo_hz,
      frame_type: lastMeasurement.frame_type,
    };

    fetch('/api/survey/point', {
      method : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body   : JSON.stringify(point),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          OCS.toast.success(
            `Indoor point recorded · RSRP ${point.rsrp_dbm?.toFixed(1)} dBm ` +
            `@ (${indoorPos.lat.toFixed(5)}, ${indoorPos.lng.toFixed(5)})`
          );
        } else {
          OCS.toast.warning(data.error || 'No active session — start a survey first');
        }
      })
      .catch(() => OCS.toast.danger('Failed to record indoor point'));

    // Also update server GPS state so the binary's next sample gets this position too
    fetch('/api/gps/update', {
      method : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body   : JSON.stringify({ latitude: indoorPos.lat, longitude: indoorPos.lng }),
    }).catch(() => {});
  }

  // ── Heatmap metric toggle ─────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-heat-metric]').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-heat-metric]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        heatMetric = btn.dataset.heatMetric;
        renderMeasurements();
      });
    });

    // Session selector
    const sel = document.getElementById('session-select');
    if (sel) sel.addEventListener('change', () => loadSession(sel.value));

    // Tower modal
    document.getElementById('btn-save-tower')?.addEventListener('click', saveTower);

    // Floor plan load
    const fpSelect = document.getElementById('fp-select');
    const fpSetBtn = document.getElementById('fp-set-bounds');
    if (fpSelect) {
      fetch('/api/floorplans').then(r => r.json()).then(list => {
        list.forEach(f => {
          const opt = document.createElement('option');
          opt.value = f.url; opt.textContent = f.filename;
          fpSelect.appendChild(opt);
        });
      });
    }
    if (fpSetBtn) {
      fpSetBtn.addEventListener('click', () => {
        const url = fpSelect?.value;
        if (!url) { OCS.toast.warning('Select a floor plan first'); return; }
        const center = map.getCenter();
        loadFloorplan(url, [
          [center.lat - 0.001, center.lng - 0.002],
          [center.lat + 0.001, center.lng + 0.002],
        ]);
        OCS.toast.info('Floor plan loaded.');
      });
    }
    const fpForm = document.getElementById('fp-upload-form');
    if (fpForm) {
      fpForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const file = document.getElementById('fp-file').files[0];
        if (!file) return;
        const fd = new FormData(); fd.append('file', file);
        const resp = await fetch('/api/floorplan/upload', { method: 'POST', body: fd });
        if (resp.ok) {
          const data = await resp.json();
          OCS.toast.success(`Uploaded: ${data.filename}`);
          const opt = document.createElement('option'); opt.value = data.url; opt.textContent = data.filename;
          fpSelect?.appendChild(opt);
        } else { OCS.toast.danger('Upload failed'); }
      });
    }

    // Floor plan overlay mode toggle
    document.querySelectorAll('input[name="fp-mode"]').forEach(radio => {
      radio.addEventListener('change', () => applyFloorplanOverlayMode(radio.value));
    });

    // Indoor navigation arrow buttons
    document.getElementById('nav-fwd')?.addEventListener('click', () => {
      const step = getNavStep();
      if (!indoorPos) { moveIndoorPos(0, 0); }
      moveIndoorPos(step * M_TO_LAT, 0);
    });
    document.getElementById('nav-back')?.addEventListener('click', () => {
      const step = getNavStep();
      if (!indoorPos) { moveIndoorPos(0, 0); }
      moveIndoorPos(-step * M_TO_LAT, 0);
    });
    document.getElementById('nav-left')?.addEventListener('click', () => {
      const step = getNavStep();
      if (!indoorPos) { moveIndoorPos(0, 0); }
      const lat = indoorPos?.lat || map.getCenter().lat;
      moveIndoorPos(0, -step * mToLon(lat));
    });
    document.getElementById('nav-right')?.addEventListener('click', () => {
      const step = getNavStep();
      if (!indoorPos) { moveIndoorPos(0, 0); }
      const lat = indoorPos?.lat || map.getCenter().lat;
      moveIndoorPos(0, step * mToLon(lat));
    });
    document.getElementById('nav-record')?.addEventListener('click', indoorRecord);
  });

  // ── Boot ──────────────────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadTowers();
    initChart();
  });

  // ── Public API ────────────────────────────────────────────────────────────────

  /** Returns the live Leaflet map instance (used by leaflet-image for capture). */
  function getMap() { return map; }

  /**
   * Returns the current survey-mode descriptor so the start-survey request and
   * export handler can lock the session to the correct mode.
   *
   *   MAP_SURVEY     — outdoor GPS survey (default)
   *   INDOOR_OVERLAY — floor plan overlaid on the map
   *   INDOOR_ONLY    — floor plan replaces the map entirely
   */
  function getSurveyModeInfo() {
    let surveyMode = 'MAP_SURVEY';
    if (indoorMode) {
      surveyMode = fpOverlayMode === 'indoor' ? 'INDOOR_ONLY' : 'INDOOR_OVERLAY';
    }
    return {
      surveyMode,
      floorplanUrl   : floorplanUrl,
      floorplanBounds: floorplanBounds,
    };
  }

  // Expose for towers page and map.html inline scripts
  OCS.map = { removeTowerMarker, renderTower, getMap, getSurveyModeInfo };
  window.setHeatmapMode        = setHeatmapMode;
  window.simulateTowerCoverage  = simulateTowerCoverage;

}(window.OCS = window.OCS || {}));
