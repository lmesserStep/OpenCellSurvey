import base64
import io
import json
import math
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config as cfg


# ── statistics ─────────────────────────────────────────────────────────────────

def _stats(values: List[float]) -> Dict[str, Any]:
    v = [x for x in values if x is not None]
    if not v:
        return {"min": "—", "max": "—", "avg": "—"}
    return {
        "min": round(min(v), 2),
        "max": round(max(v), 2),
        "avg": round(sum(v) / len(v), 2),
    }


def compute_stats(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "rsrp":  _stats([p.get("rsrp_dbm") for p in points]),
        "rsrq":  _stats([p.get("rsrq_db")  for p in points]),
        "snr":   _stats([p.get("snr_db")    for p in points]),
        "total": len(points),
    }


# ── Haversine distance (metres) ────────────────────────────────────────────────

def _haversine_m(lat1, lon1, lat2, lon2):
    import math
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _survey_distance_miles(points):
    geo = [p for p in points if p.get("latitude") and p.get("longitude")]
    if len(geo) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(geo)):
        total += _haversine_m(geo[i-1]["latitude"], geo[i-1]["longitude"],
                              geo[i]["latitude"],   geo[i]["longitude"])
    return round(total * 0.000621371, 2)


# ── OSM tile stitching (server-side, Pillow required) ─────────────────────────

def _fetch_osm_tile_map(
    points: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[Dict[str, float]]]:
    """
    Fetch OSM tiles, stitch into one PNG, return (data_url, geo_bounds).
    Returns (None, None) if Pillow is missing, no GPS points, or tiles fail.
    geo_bounds = {min_lat, max_lat, min_lon, max_lon} of the stitched image.
    """
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None, None

    geo = [p for p in points if p.get("latitude") and p.get("longitude")]
    if not geo:
        return None, None

    lats = [p["latitude"] for p in geo]
    lons = [p["longitude"] for p in geo]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    pad    = 0.20
    lat_r  = (max_lat - min_lat) or 0.002
    lon_r  = (max_lon - min_lon) or 0.002
    min_lat -= lat_r * pad;  max_lat += lat_r * pad
    min_lon -= lon_r * pad;  max_lon += lon_r * pad

    def _lon_x(lon, z): return int((lon + 180) / 360 * (2 ** z))
    def _lat_y(lat, z):
        lr = math.radians(lat)
        return int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * (2 ** z))
    def _x_lon(x, z): return x / (2 ** z) * 360 - 180
    def _y_lat(y, z):
        n = math.pi - 2 * math.pi * y / (2 ** z)
        return math.degrees(math.atan(math.sinh(n)))

    MAX_TILES = 25
    zoom = 14
    for z in range(18, 11, -1):
        x0 = _lon_x(min_lon, z);  x1 = _lon_x(max_lon, z)
        y0 = _lat_y(max_lat, z);  y1 = _lat_y(min_lat, z)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= MAX_TILES:
            zoom = z
            break

    x0 = _lon_x(min_lon, zoom);  x1 = _lon_x(max_lon, zoom)
    y0 = _lat_y(max_lat, zoom);  y1 = _lat_y(min_lat, zoom)
    nx, ny = x1 - x0 + 1, y1 - y0 + 1

    TILE = 256
    stitched = Image.new("RGB", (nx * TILE, ny * TILE), (221, 228, 234))
    hdrs = {"User-Agent": "OpenCellSurvey/1.0 (report-generator; offline-export)"}

    for xi in range(nx):
        for yi in range(ny):
            url = f"https://tile.openstreetmap.org/{zoom}/{x0 + xi}/{y0 + yi}.png"
            try:
                req = urllib.request.Request(url, headers=hdrs)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    tile = Image.open(io.BytesIO(resp.read())).convert("RGB")
                    stitched.paste(tile, (xi * TILE, yi * TILE))
            except Exception:
                pass  # leave grey placeholder for failed tiles

    buf = io.BytesIO()
    stitched.save(buf, "PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    bounds = {
        "min_lat": _y_lat(y1 + 1, zoom),
        "max_lat": _y_lat(y0,     zoom),
        "min_lon": _x_lon(x0,     zoom),
        "max_lon": _x_lon(x1 + 1, zoom),
    }
    return data_url, bounds


# ── Static canvas map (self-contained — no CDN tile dependency) ───────────────
#
# Outdoor mode:  grey topo-style background + subtle grid lines + scale bar.
# Indoor mode:   floor-plan image as background (loaded from session config URL)
#                so the heatmap cells are projected onto the actual floor plan.
#
# Both modes share the same cell/path/marker/legend rendering code.
# Cell pixel size is capped at MAX_CELL_PX so a short survey never produces
# a single giant coloured rectangle ("solid-box" artefact).
#
# JS globals consumed (injected by generate_html_report):
#   POINTS, TOWERS, SURVEY_MODE, FLOORPLAN_URL, FLOORPLAN_BOUNDS

_STATIC_MAP_JS = r"""
(function() {
  var canvas = document.getElementById('static-map');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var W = canvas.width, H = canvas.height;

  var geo = POINTS.filter(function(p){ return p.latitude && p.longitude; });
  if (!geo.length) {
    ctx.fillStyle = '#e8ecf0';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#888';
    ctx.font = '16px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No location data', W/2, H/2);
    return;
  }

  /* ── Survey mode flags ── */
  var isIndoor   = (typeof SURVEY_MODE !== 'undefined' && SURVEY_MODE !== 'MAP_SURVEY');
  var fpUrl      = (typeof FLOORPLAN_URL    !== 'undefined') ? FLOORPLAN_URL    : null;
  var fpBounds   = (typeof FLOORPLAN_BOUNDS !== 'undefined') ? FLOORPLAN_BOUNDS : null;
  var tileB64    = (typeof MAP_TILE_B64    !== 'undefined') ? MAP_TILE_B64    : null;
  var tileBounds = (typeof MAP_TILE_BOUNDS !== 'undefined') ? MAP_TILE_BOUNDS : null;

  /* ── Viewport ── */
  var minLatP, maxLatP, minLonP, maxLonP;

  if (isIndoor && fpBounds) {
    /* Use the stored floor-plan bounds so heatmap cells align with the image */
    minLatP = fpBounds[0][0]; minLonP = fpBounds[0][1];
    maxLatP = fpBounds[1][0]; maxLonP = fpBounds[1][1];
  } else if (!isIndoor && tileBounds) {
    /* Use the stitched OSM tile extents so the overlay aligns with the tile image */
    minLatP = tileBounds.min_lat; maxLatP = tileBounds.max_lat;
    minLonP = tileBounds.min_lon; maxLonP = tileBounds.max_lon;
  } else {
    var lats = geo.map(function(p){ return p.latitude; });
    var lons = geo.map(function(p){ return p.longitude; });
    var minLat = Math.min.apply(null, lats), maxLat = Math.max.apply(null, lats);
    var minLon = Math.min.apply(null, lons), maxLon = Math.max.apply(null, lons);
    var padFrac  = 0.15;
    var latRange = (maxLat - minLat) || 0.001;
    var lonRange = (maxLon - minLon) || 0.001;
    minLatP = minLat - latRange * padFrac;
    maxLatP = maxLat + latRange * padFrac;
    minLonP = minLon - lonRange * padFrac;
    maxLonP = maxLon + lonRange * padFrac;
  }

  function toXY(lat, lon) {
    return [
      (lon - minLonP) / (maxLonP - minLonP) * W,
      H - (lat - minLatP) / (maxLatP - minLatP) * H,
    ];
  }

  /* ── Core rendering (called once background is ready) ── */
  function renderBody(bgImage) {
    if (bgImage) {
      ctx.drawImage(bgImage, 0, 0, W, H);
    } else {
      /* Outdoor: topo-style background + grid */
      ctx.fillStyle = '#dde4ea';
      ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = 'rgba(255,255,255,0.45)';
      ctx.lineWidth = 0.5;
      for (var gi = 1; gi < 8; gi++) {
        ctx.beginPath(); ctx.moveTo(W * gi / 8, 0);  ctx.lineTo(W * gi / 8, H); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, H * gi / 8);  ctx.lineTo(W, H * gi / 8); ctx.stroke();
      }
    }

    /* ── Grid-aggregate RSRP cells ── */
    // Indoor: divide the floor plan into ~15 zones so each tap fills a whole room.
    // Outdoor: use the fixed 5-m GRID_DEG.
    var GRID_DEG = 0.000045;
    var gridDeg;
    if (isIndoor && fpBounds) {
      var latSpan = fpBounds[1][0] - fpBounds[0][0];
      var lonSpan = fpBounds[1][1] - fpBounds[0][1];
      gridDeg = Math.max(latSpan, lonSpan) / 15;
    } else {
      gridDeg = GRID_DEG;
    }
    var cells = {};
    geo.forEach(function(p) {
      var gi = Math.floor(p.latitude  / gridDeg);
      var gj = Math.floor(p.longitude / gridDeg);
      var k  = gi + ',' + gj;
      if (!cells[k]) cells[k] = {
        lat: gi * gridDeg + gridDeg / 2,
        lon: gj * gridDeg + gridDeg / 2,
        rsrp_sum: 0, count: 0,
      };
      cells[k].rsrp_sum += (p.rsrp_dbm || 0);
      cells[k].count++;
    });

    var degPerPxX = (maxLonP - minLonP) / W;
    // Indoor: fill the full zone; outdoor: cap at 36 px to avoid solid-box artefact
    var cellPx = isIndoor
      ? Math.max(4, gridDeg / degPerPxX)
      : Math.min(36, Math.max(4, gridDeg / degPerPxX));

    Object.values(cells).forEach(function(c) {
      var avg = c.rsrp_sum / c.count;
      ctx.fillStyle   = rsrpColor(avg);
      ctx.globalAlpha = bgImage ? 0.75 : 0.85;
      var xy = toXY(c.lat, c.lon);
      ctx.fillRect(xy[0] - cellPx / 2, xy[1] - cellPx / 2, cellPx, cellPx);
    });

    /* ── Survey path ── */
    ctx.globalAlpha = 1;
    ctx.strokeStyle = '#f05a28';
    ctx.lineWidth   = 2.5;
    ctx.lineJoin    = 'round';
    ctx.beginPath();
    geo.forEach(function(p, i) {
      var xy = toXY(p.latitude, p.longitude);
      if (i === 0) ctx.moveTo(xy[0], xy[1]);
      else         ctx.lineTo(xy[0], xy[1]);
    });
    ctx.stroke();

    /* ── Start / end markers ── */
    function dot(lat, lon, color) {
      var xy = toXY(lat, lon);
      ctx.beginPath();
      ctx.arc(xy[0], xy[1], 5, 0, 2 * Math.PI);
      ctx.fillStyle   = color;
      ctx.globalAlpha = 1;
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth   = 1.5;
      ctx.stroke();
    }
    dot(geo[0].latitude, geo[0].longitude, '#007bff');
    if (geo.length > 1)
      dot(geo[geo.length - 1].latitude, geo[geo.length - 1].longitude, '#6c757d');

    /* ── Tower markers ── */
    TOWERS.forEach(function(t) {
      if (!t.latitude || !t.longitude) return;
      var xy = toXY(t.latitude, t.longitude);
      ctx.font      = '18px sans-serif';
      ctx.textAlign = 'center';
      ctx.globalAlpha = 1;
      ctx.fillText('\uD83D\uDCF6', xy[0], xy[1]);
    });

    /* ── RSRP legend ── */
    var legend = [
      ['#dc3545', 'Poor  (< \u2212100 dBm)'],
      ['#ffc107', 'Fair  (\u2212100\u2013\u221290 dBm)'],
      ['#28a745', 'Good  (\u221290\u2013\u221280 dBm)'],
      ['#007bff', 'Excellent (\u2265\u221280 dBm)'],
    ];
    ctx.font        = '11px "Segoe UI", sans-serif';
    ctx.textAlign   = 'left';
    ctx.globalAlpha = 0.93;
    var lx = 10, ly = H - legend.length * 20 - 12;
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    ctx.fillRect(lx - 4, ly - 4, 196, legend.length * 20 + 8);
    legend.forEach(function(entry, i) {
      ctx.fillStyle = entry[0];
      ctx.fillRect(lx, ly + i * 20, 14, 14);
      ctx.fillStyle = '#333';
      ctx.fillText(entry[1], lx + 20, ly + i * 20 + 11);
    });

    /* ── Distance scale bar (outdoor only) ── */
    if (!isIndoor) {
      var latMid   = (minLatP + maxLatP) / 2;
      var mPerDeg  = 111000 * Math.cos(latMid * Math.PI / 180);
      var lonSpanM = (maxLonP - minLonP) * mPerDeg;
      var niceM    = Math.pow(10, Math.floor(Math.log10(lonSpanM / 4)));
      var scalePx  = niceM / lonSpanM * W;
      var sx = W - scalePx - 20, sy = H - 16;
      ctx.globalAlpha = 0.90;
      ctx.fillStyle   = 'rgba(255,255,255,0.85)';
      ctx.fillRect(sx - 4, sy - 14, scalePx + 8, 18);
      ctx.strokeStyle = '#444';
      ctx.lineWidth   = 2;
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(sx + scalePx, sy); ctx.stroke();
      ctx.fillStyle   = '#333';
      ctx.font        = '10px "Segoe UI", sans-serif';
      ctx.textAlign   = 'center';
      ctx.globalAlpha = 1;
      ctx.fillText(niceM >= 1000 ? (niceM / 1000) + ' km' : niceM + ' m',
                   sx + scalePx / 2, sy - 3);
    }
    ctx.globalAlpha = 1;
  }

  /* ── Kick off: load background image then render ── */
  // Indoor: prefer embedded base64 floorplan; fall back to server URL.
  // Outdoor: use server-stitched OSM tile image when available.
  var fpSrc = (typeof FLOORPLAN_B64 !== 'undefined' && FLOORPLAN_B64) ? FLOORPLAN_B64 : fpUrl;
  if (isIndoor && fpSrc) {
    var img = new Image();
    img.onload  = function() { renderBody(img); };
    img.onerror = function() { renderBody(null); };  /* graceful fallback */
    if (!fpSrc.startsWith('data:')) img.crossOrigin = 'anonymous';
    img.src = fpSrc;
  } else if (!isIndoor && tileB64) {
    var tileImg = new Image();
    tileImg.onload  = function() { renderBody(tileImg); };
    tileImg.onerror = function() { renderBody(null); };
    tileImg.src = tileB64;
  } else {
    renderBody(null);
  }
})();
"""


# ── HTML report ────────────────────────────────────────────────────────────────

def generate_html_report(
    session: Dict[str, Any],
    points:  List[Dict[str, Any]],
    towers:  List[Dict[str, Any]],
    map_image_base64: Optional[str] = None,
    floorplan_b64: Optional[str] = None,
    tile_map_b64: Optional[str] = None,
    tile_map_bounds: Optional[Dict[str, float]] = None,
) -> str:
    stats    = compute_stats(points)
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pts_json = json.dumps(points)
    twr_json = json.dumps(towers)

    # ── Session metadata ──────────────────────────────────────────────────────
    session_name = session.get("name") or f"Session {session.get('id', 'N/A')}"
    raw_start    = session.get("started_at") or ""
    session_date = raw_start[:10] if raw_start else "N/A"
    session_time = raw_start[:19].replace("T", " ") if raw_start else "N/A"

    config_dict: Dict[str, Any] = {}
    try:
        config_dict = json.loads(session.get("config") or "{}")
    except Exception:
        pass

    band    = config_dict.get("band",  "—")
    pci_raw = config_dict.get("pci")
    pci     = str(pci_raw) if pci_raw is not None else "Any"
    mode    = config_dict.get("mode", "—")

    # ── Survey mode (state-machine) ───────────────────────────────────────────
    survey_mode      = config_dict.get("survey_mode", "MAP_SURVEY")
    floorplan_url    = config_dict.get("floorplan_url")
    floorplan_bounds = config_dict.get("floorplan_bounds")

    survey_mode_js      = json.dumps(survey_mode)
    floorplan_url_js    = json.dumps(floorplan_url)
    floorplan_bounds_js = json.dumps(floorplan_bounds)
    floorplan_b64_js    = json.dumps(floorplan_b64)   # data URL or null
    tile_map_b64_js     = json.dumps(tile_map_b64)
    tile_map_bounds_js  = json.dumps(tile_map_bounds)

    # ── Map section: embedded live snapshot beats canvas fallback ─────────────
    if map_image_base64:
        # Snapshot captured by leaflet-image on the client — exact visual match
        map_html_elem = (
            f'<img id="static-map" src="{map_image_base64}" '
            f'alt="Survey Map" style="width:100%;height:auto;border-radius:6px;display:block">'
        )
        map_js_block = ""
    else:
        # Canvas fallback: renders from stored point data (outdoor or indoor)
        map_html_elem = '<canvas id="static-map" width="960" height="520"></canvas>'
        map_js_block  = _STATIC_MAP_JS

    # ── Survey distance ───────────────────────────────────────────────────────
    dist_miles = _survey_distance_miles(points)

    # ── RSRP time-series data for chart ───────────────────────────────────────
    chart_labels = json.dumps([
        (p.get("timestamp") or "")[:19].replace("T", " ")
        for p in points if p.get("rsrp_dbm") is not None
    ])
    chart_data = json.dumps([
        p.get("rsrp_dbm") for p in points if p.get("rsrp_dbm") is not None
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCellSurvey Report — {session_name}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{ --accent:#f05a28; --sidebar:#2f5668; }}
  body  {{ background:#f3f4f6; font-family:'Segoe UI',sans-serif; padding:24px; max-width:1100px; margin:auto; }}
  h1    {{ color:var(--sidebar); }}
  h2    {{ color:var(--sidebar); border-bottom:2px solid var(--accent); padding-bottom:6px; margin-top:30px; }}
  .meta-bar   {{ background:#fff; border-radius:8px; padding:16px 20px; box-shadow:0 1px 4px rgba(0,0,0,.1);
                  margin-bottom:20px; display:flex; flex-wrap:wrap; gap:24px; align-items:center; }}
  .meta-item  {{ display:flex; flex-direction:column; }}
  .meta-label {{ font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:#888; }}
  .meta-val   {{ font-family:monospace; font-weight:700; font-size:1.05rem; color:var(--sidebar); }}
  .stat-card  {{ background:#fff; border-radius:8px; padding:18px; box-shadow:0 1px 4px rgba(0,0,0,.1); }}
  .stat-label {{ color:#888; font-size:.85em; }}
  .stat-val   {{ font-family:monospace; font-weight:600; font-size:1.1em; }}
  .map-wrap   {{ background:#fff; border-radius:8px; padding:12px; box-shadow:0 1px 4px rgba(0,0,0,.2); }}
  #static-map {{ border-radius:6px; display:block; max-width:100%; width:100%; }}
  #rsrp-chart-wrap {{ background:#fff; border-radius:8px; padding:16px; box-shadow:0 1px 4px rgba(0,0,0,.1); height:220px; position:relative; }}
  table       {{ font-size:.85em; }}
  .badge-excellent {{ background:#007bff; color:#fff;    padding:2px 8px; border-radius:10px; }}
  .badge-good      {{ background:#d4edda; color:#155724; padding:2px 8px; border-radius:10px; }}
  .badge-fair      {{ background:#fff3cd; color:#856404; padding:2px 8px; border-radius:10px; }}
  .badge-poor      {{ background:#f8d7da; color:#721c24; padding:2px 8px; border-radius:10px; }}
  .footer     {{ text-align:center; color:#aaa; font-size:.8em; margin-top:30px; }}
</style>
</head>
<body>
<h1>&#x1F4F6; OpenCellSurvey Report</h1>

<!-- ── Survey Metadata ─────────────────────────────────────────────────────── -->
<div class="meta-bar">
  <div class="meta-item">
    <span class="meta-label">Survey Name</span>
    <span class="meta-val">{session_name}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Date</span>
    <span class="meta-val">{session_date}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Started</span>
    <span class="meta-val">{session_time}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Band</span>
    <span class="meta-val">{band}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">PCI</span>
    <span class="meta-val">{pci}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Mode</span>
    <span class="meta-val">{mode}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Points</span>
    <span class="meta-val">{stats['total']}</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Distance</span>
    <span class="meta-val">{dist_miles} mi</span>
  </div>
  <div class="meta-item">
    <span class="meta-label">Generated</span>
    <span class="meta-val" style="font-size:.85rem;font-weight:500">{now}</span>
  </div>
</div>

<!-- ── Signal Statistics ───────────────────────────────────────────────────── -->
<h2>Signal Statistics</h2>
<div class="row g-3 mb-4">
  <div class="col-md-4">
    <div class="stat-card">
      <div class="text-uppercase text-muted mb-2" style="font-size:.75em;letter-spacing:.08em">RSRP (dBm)</div>
      <div class="d-flex justify-content-between"><span class="stat-label">Min (Worst)</span><span class="stat-val">{stats['rsrp']['min']}</span></div>
      <div class="d-flex justify-content-between"><span class="stat-label">Max (Best)</span> <span class="stat-val">{stats['rsrp']['max']}</span></div>
      <div class="d-flex justify-content-between"><span class="stat-label">Avg</span>         <span class="stat-val">{stats['rsrp']['avg']}</span></div>
    </div>
  </div>
  <div class="col-md-4">
    <div class="stat-card">
      <div class="text-uppercase text-muted mb-2" style="font-size:.75em;letter-spacing:.08em">RSRQ (dB)</div>
      <div class="d-flex justify-content-between"><span class="stat-label">Min</span><span class="stat-val">{stats['rsrq']['min']}</span></div>
      <div class="d-flex justify-content-between"><span class="stat-label">Max</span><span class="stat-val">{stats['rsrq']['max']}</span></div>
      <div class="d-flex justify-content-between"><span class="stat-label">Avg</span><span class="stat-val">{stats['rsrq']['avg']}</span></div>
    </div>
  </div>
  <div class="col-md-4">
    <div class="stat-card">
      <div class="text-uppercase text-muted mb-2" style="font-size:.75em;letter-spacing:.08em">SNR (dB)</div>
      <div class="d-flex justify-content-between"><span class="stat-label">Min</span><span class="stat-val">{stats['snr']['min']}</span></div>
      <div class="d-flex justify-content-between"><span class="stat-label">Max</span><span class="stat-val">{stats['snr']['max']}</span></div>
      <div class="d-flex justify-content-between"><span class="stat-label">Avg</span><span class="stat-val">{stats['snr']['avg']}</span></div>
    </div>
  </div>
</div>

<!-- ── Static Survey Map ───────────────────────────────────────────────────── -->
<h2>Survey Map</h2>
<div class="map-wrap mb-4">
  {map_html_elem}
</div>

<!-- ── RSRP Over Time ──────────────────────────────────────────────────────── -->
<h2>RSRP Over Time</h2>
<div id="rsrp-chart-wrap" class="mb-4">
  <canvas id="rsrp-export-chart"></canvas>
</div>

<!-- ── Full Data Table ─────────────────────────────────────────────────────── -->
<h2>Survey Data</h2>
<div class="table-responsive">
<table class="table table-bordered table-hover bg-white rounded">
<thead class="table-dark">
<tr>
  <th>Timestamp</th><th>Band</th><th>EARFCN</th><th>Freq&nbsp;MHz</th>
  <th>PCI</th><th>RSRP&nbsp;dBm</th><th>RSRQ&nbsp;dB</th><th>SNR&nbsp;dB</th>
  <th>Lat</th><th>Lon</th>
</tr>
</thead>
<tbody id="tbl"></tbody>
</table>
</div>

<div class="footer">Generated by OpenCellSurvey &mdash; {now}</div>

<script>
const POINTS          = {pts_json};
const TOWERS          = {twr_json};
const CHART_LABELS    = {chart_labels};
const CHART_DATA      = {chart_data};
const SURVEY_MODE      = {survey_mode_js};
const FLOORPLAN_URL    = {floorplan_url_js};
const FLOORPLAN_BOUNDS = {floorplan_bounds_js};
const FLOORPLAN_B64    = {floorplan_b64_js};
const MAP_TILE_B64    = {tile_map_b64_js};
const MAP_TILE_BOUNDS = {tile_map_bounds_js};

function rsrpColor(v) {{
  if (v >= -80)  return '#007bff';
  if (v >= -90)  return '#28a745';
  if (v >= -100) return '#ffc107';
  return '#dc3545';
}}
function rsrpBadge(v) {{
  var cls = v >= -80 ? 'badge-excellent' : v >= -90 ? 'badge-good' : v >= -100 ? 'badge-fair' : 'badge-poor';
  return '<span class="' + cls + '">' + v + '</span>';
}}

/* Survey map — embedded snapshot or canvas fallback */
{map_js_block}

/* RSRP time-series chart */
(function () {{
  var canvas = document.getElementById('rsrp-export-chart');
  if (!canvas || !CHART_DATA.length) return;
  new Chart(canvas, {{
    type: 'line',
    data: {{
      labels  : CHART_LABELS,
      datasets: [{{
        label          : 'RSRP (dBm)',
        data           : CHART_DATA,
        borderColor    : '#f05a28',
        backgroundColor: 'rgba(240,90,40,0.1)',
        borderWidth    : 1.5,
        pointRadius    : 0,
        tension        : 0.3,
        fill           : true,
      }}],
    }},
    options: {{
      responsive         : true,
      maintainAspectRatio: false,
      animation          : false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ ticks: {{ maxTicksLimit: 8, maxRotation: 0, font: {{ size: 9 }} }}, grid: {{ color: '#f0f0f0' }} }},
        y: {{
          min: -130, max: -40,
          ticks: {{ font: {{ size: 9 }}, callback: function(v) {{ return v + ' dB'; }}, stepSize: 20 }},
          grid: {{ color: '#f0f0f0' }},
        }},
      }},
    }},
  }});
}})();

/* Data table */
var tbody = document.getElementById('tbl');
POINTS.forEach(function(p) {{
  tbody.insertAdjacentHTML('beforeend', '<tr>' +
    '<td style="font-family:monospace;font-size:.8em">' + (p.timestamp||'').slice(0,19).replace('T',' ') + '</td>' +
    '<td>' + p.band + '</td><td>' + p.earfcn + '</td><td>' + p.freq_mhz + '</td>' +
    '<td>' + p.pci + '</td><td>' + rsrpBadge(p.rsrp_dbm) + '</td>' +
    '<td>' + p.rsrq_db + '</td><td>' + p.snr_db + '</td>' +
    '<td>' + (p.latitude  != null ? p.latitude.toFixed(6)  : '—') + '</td>' +
    '<td>' + (p.longitude != null ? p.longitude.toFixed(6) : '—') + '</td>' +
  '</tr>');
}});
</script>
</body>
</html>"""


def save_report(session_id: str, html: str) -> Path:
    path = cfg.EXPORTS_DIR / f"report_{session_id}.html"
    path.write_text(html, encoding="utf-8")
    return path
