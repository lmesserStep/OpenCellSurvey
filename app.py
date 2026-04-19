import asyncio
import json
import logging
import math
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import config as cfg
import gps
import storage
from export import generate_html_report, save_report, _fetch_osm_tile_map
from models import ScanConfig, Tower
from survey_runner import SurveyRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

storage.init_db()

app = FastAPI(title="OpenCellSurvey", version="1.0.0")

BASE_DIR = Path(__file__).parent
app.mount("/static",     StaticFiles(directory=str(BASE_DIR / "static")),         name="static")
app.mount("/tiles",      StaticFiles(directory=str(cfg.TILES_DIR)),               name="tiles")
app.mount("/floorplans", StaticFiles(directory=str(cfg.FLOORPLANS_DIR)),          name="floorplans")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

runner:           SurveyRunner      = SurveyRunner()
active_session_id: Optional[str]   = None
ws_clients:       Set[WebSocket]   = set()


# ── WebSocket broadcast ────────────────────────────────────────────────────────

async def broadcast(event_type: str, data: dict) -> None:
    msg  = json.dumps({"type": event_type, "data": data})
    dead: Set[WebSocket] = set()
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ── Survey callbacks ───────────────────────────────────────────────────────────

async def _on_survey_data(point) -> None:
    if active_session_id:
        lat, lon = gps.get_position()
        point.latitude  = lat
        point.longitude = lon
        storage.save_point(active_session_id, point)
    await broadcast("survey_data", point.model_dump())


async def _on_log(line: str) -> None:
    await broadcast("log", {"message": line})


async def _on_runner_stop() -> None:
    """Called when the binary process exits for any reason."""
    global active_session_id
    if active_session_id:
        storage.end_session(active_session_id)
    await broadcast("status", {"running": False, "session_id": active_session_id})


runner.register_data_callback(_on_survey_data)
runner.register_log_callback(_on_log)
runner.register_stop_callback(_on_runner_stop)


# ══════════════════════════════════════════════════════════════════════════════
# Page routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"page": "dashboard"})


@app.get("/survey", response_class=HTMLResponse)
async def page_survey(request: Request):
    return templates.TemplateResponse(request, "survey.html", {
        "page": "survey",
        "bands": cfg.LTE_BANDS,
    })


@app.get("/map", response_class=HTMLResponse)
async def page_map(request: Request):
    return templates.TemplateResponse(request, "map.html", {
        "page": "map",
        "sessions": storage.get_sessions(),
    })


@app.get("/towers", response_class=HTMLResponse)
async def page_towers(request: Request):
    return templates.TemplateResponse(request, "towers.html", {
        "page": "towers",
        "towers": storage.get_all_towers(),
    })


@app.get("/exports", response_class=HTMLResponse)
async def page_exports(request: Request):
    return templates.TemplateResponse(request, "exports.html", {
        "page": "exports",
        "sessions": storage.get_sessions(),
    })


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return templates.TemplateResponse(request, "settings.html", {
        "page": "settings",
        "binary_path":  storage.get_setting("binary_path", cfg.BINARY_SEARCH_PATHS[0]),
        "units":        storage.get_setting("units", "meters"),
        "search_paths": cfg.BINARY_SEARCH_PATHS,
    })


@app.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request):
    return templates.TemplateResponse(request, "logs.html", {"page": "logs"})


# ══════════════════════════════════════════════════════════════════════════════
# API — survey control
# ══════════════════════════════════════════════════════════════════════════════

def _check_binary(path: str) -> str:
    """Return an error string if the binary is unusable, else empty string."""
    import stat as _stat
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return f"File not found: {expanded}"
    if not os.path.isfile(expanded):
        return f"Not a regular file: {expanded}"
    try:
        mode = os.stat(expanded).st_mode
    except OSError as e:
        return f"Cannot stat file: {e}"
    if not (mode & (_stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)):
        return f"File has no execute bit set: {expanded}"
    return ""


@app.post("/api/survey/start")
async def api_start_survey(scan_cfg: ScanConfig):
    global active_session_id
    if runner.is_running:
        raise HTTPException(400, "Survey already running")

    binary = os.path.expanduser(scan_cfg.binary_path)
    scan_cfg.binary_path = binary   # store the expanded path
    err = _check_binary(binary)
    if err:
        raise HTTPException(400, err)

    if scan_cfg.frame_type == "TDD" and not scan_cfg.bandwidth_mhz:
        raise HTTPException(400, "bandwidth_mhz is required when frame_type is TDD")

    active_session_id = str(uuid.uuid4())[:8]
    storage.create_session(active_session_id, scan_cfg.model_dump())
    await runner.start(scan_cfg)
    await broadcast("status", {"running": True, "session_id": active_session_id})
    return {"session_id": active_session_id, "status": "started"}


@app.post("/api/survey/stop")
async def api_stop_survey():
    global active_session_id
    if not runner.is_running:
        raise HTTPException(400, "No survey running")
    await runner.stop()
    if active_session_id:
        storage.end_session(active_session_id)
    await broadcast("status", {"running": False, "session_id": active_session_id})
    return {"status": "stopped"}


@app.get("/api/survey/status")
async def api_survey_status():
    return {"running": runner.is_running, "session_id": active_session_id}


# ── Manual point injection (indoor surveys) ───────────────────────────────────

@app.post("/api/survey/point")
async def api_inject_point(body: dict):
    """Record one survey point manually — used by the indoor REC button.

    The client sends the last-received RF measurement merged with the current
    indoor position.  The point is saved to the active session and broadcast
    as a survey_data event so the live heatmap updates immediately.
    """
    from models import SurveyPoint as _SP

    if not active_session_id:
        return {"ok": False, "error": "No active survey session"}

    now_ts = datetime.now(timezone.utc).isoformat()
    try:
        point = _SP(
            t_ms       = int(datetime.now().timestamp() * 1000),
            band       = int(body.get("band")       or 0),
            earfcn     = int(body.get("earfcn")     or 0),
            freq_mhz   = float(body.get("freq_mhz") or 0.0),
            pci        = int(body.get("pci")        or 0),
            n_prb      = int(body.get("n_prb")      or 0),
            ports      = int(body.get("ports")      or 0),
            rsrp_dbm   = float(body.get("rsrp_dbm") or -120.0),
            rsrq_db    = float(body.get("rsrq_db")  or -20.0),
            snr_db     = float(body.get("snr_db")   or 0.0),
            cfo_hz     = float(body.get("cfo_hz")   or 0.0),
            frame_type = str(body.get("frame_type") or "FDD"),
            latitude   = body.get("latitude"),
            longitude  = body.get("longitude"),
            timestamp  = now_ts,
        )
    except Exception as exc:
        raise HTTPException(400, f"Invalid point data: {exc}")

    storage.save_point(active_session_id, point)
    data = point.model_dump()
    data["session_id"] = active_session_id
    await broadcast("survey_data", data)
    return {"ok": True, "session_id": active_session_id}


# ── GPS ────────────────────────────────────────────────────────────────────────

@app.post("/api/gps/update")
async def api_gps_update(body: dict):
    lat = body.get("latitude")
    lon = body.get("longitude")
    if lat is not None and lon is not None:
        gps.update_position(float(lat), float(lon))
        await broadcast("gps", {"latitude": lat, "longitude": lon})
    return {"ok": True}


@app.get("/api/gps/position")
async def api_gps_position():
    lat, lon = gps.get_position()
    return {"latitude": lat, "longitude": lon}


# ── Sessions & points ──────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def api_sessions():
    return storage.get_sessions()


@app.patch("/api/sessions/{session_id}")
async def api_rename_session(session_id: str, body: dict):
    name = body.get("name", "").strip()
    notes = body.get("notes", "").strip()
    if not name:
        raise HTTPException(400, "Session name cannot be empty")
    storage.rename_session(session_id, name, notes)
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    storage.delete_session(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/points")
async def api_session_points(session_id: str):
    return storage.get_session_points(session_id)


# ── Towers ─────────────────────────────────────────────────────────────────────

@app.get("/api/towers")
async def api_get_towers():
    return storage.get_all_towers()


@app.post("/api/towers")
async def api_add_tower(tower: Tower):
    tower_id = storage.save_tower(tower)
    return {"id": tower_id, **tower.model_dump()}


@app.put("/api/towers/{tower_id}")
async def api_update_tower(tower_id: int, tower: Tower):
    storage.update_tower(tower_id, tower)
    return {"ok": True}


@app.delete("/api/towers/{tower_id}")
async def api_delete_tower(tower_id: int):
    storage.delete_tower(tower_id)
    return {"ok": True}


# ── Propagation helpers ────────────────────────────────────────────────────────

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing in degrees (0 = North) from point 1 → point 2."""
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    angle = math.degrees(math.atan2(dlon * math.cos(math.radians(lat1)), dlat))
    return (angle + 360) % 360


def _azimuth_attenuation(angle_offset: float) -> float:
    """
    Sector antenna gain roll-off based on degrees off boresight.
    Breakpoints (linear interpolation): 0°→0 dB, 30°→3, 60°→10, 90°→20, 180°→30.
    """
    breakpoints = [(0.0, 0.0), (30.0, 3.0), (60.0, 10.0), (90.0, 20.0), (180.0, 30.0)]
    for i in range(len(breakpoints) - 1):
        a0, db0 = breakpoints[i]
        a1, db1 = breakpoints[i + 1]
        if angle_offset <= a1:
            t = (angle_offset - a0) / (a1 - a0)
            return db0 + t * (db1 - db0)
    return 30.0


# ── Propagation Simulation ──────────────────────────────────────────────────────

@app.post("/api/propagation/simulate")
async def api_propagation_simulate(body: dict):
    """
    Simulate RF propagation using log-distance path loss model.

      FSPL(dB)  = 32.44 + 20·log10(d_km) + 20·log10(f_MHz)
      PL_total  = FSPL + 10·n·log10(d_km) − height_gain + az_atten + fading
      RSRP(dBm) = EIRP_dBm − PL_total

    n=3.0 (suburban), height_gain=10·log10(h_m), fading=±3 dB random.
    """
    tower_id = body.get("tower_id")
    grid_size_m = body.get("grid_size_meters", 20)
    max_distance_km = body.get("max_distance_km", 5)

    if not tower_id:
        raise HTTPException(400, "tower_id is required")

    towers = storage.get_all_towers()
    tower = next((t for t in towers if t["id"] == tower_id), None)
    if not tower:
        raise HTTPException(404, "Tower not found")

    band = tower.get("band")
    if not band or band not in cfg.LTE_BANDS:
        raise HTTPException(400, f"Invalid or missing band: {band}")

    band_info = cfg.LTE_BANDS[band]
    freq_mhz = (band_info["dl_low"] + band_info["dl_high"]) / 2

    # EIRP: watts → dBm  (1 W = 30 dBm)
    eirp_w = tower.get("eirp_w")
    if eirp_w and eirp_w > 0:
        tx_power_dbm = 10 * math.log10(eirp_w) + 30
    else:
        tx_power_dbm = 46  # typical base station default

    # Tower geometry
    azimuth  = float(tower.get("azimuth") or 0)          # degrees, 0 = North
    height_m = max(1.0, float(tower.get("height_m") or 30.0))
    height_gain = 10 * math.log10(height_m)               # dB

    # Log-distance path loss exponent (suburban)
    n = 3.0

    lat_center = tower["latitude"]
    lon_center = tower["longitude"]
    lat_delta  = max_distance_km / 111.0
    lon_delta  = max_distance_km / (111.0 * math.cos(math.radians(lat_center)))
    grid_delta = grid_size_m / 111000.0

    points = []
    lat = lat_center - lat_delta
    while lat <= lat_center + lat_delta:
        lon = lon_center - lon_delta
        while lon <= lon_center + lon_delta:
            dlat = lat - lat_center
            dlon = lon - lon_center
            distance_km = math.sqrt(
                (dlat * 111) ** 2 +
                (dlon * 111 * math.cos(math.radians(lat_center))) ** 2
            )

            if 0.01 <= distance_km <= max_distance_km:
                # Free-space path loss
                fspl_db = 32.44 + 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz)
                # Log-distance additional loss
                log_dist_loss = 10 * n * math.log10(distance_km)
                # Antenna azimuth attenuation
                brng   = _bearing(lat_center, lon_center, lat, lon)
                offset = abs(brng - azimuth)
                if offset > 180:
                    offset = 360 - offset
                az_atten = _azimuth_attenuation(offset)
                # Small-scale fading ±3 dB
                fading = random.uniform(-3.0, 3.0)

                rsrp_dbm = tx_power_dbm - fspl_db - log_dist_loss + height_gain - az_atten + fading

                if rsrp_dbm >= -115:
                    points.append({
                        "lat": lat,
                        "lon": lon,
                        "rsrp_dbm": round(rsrp_dbm, 1),
                        "distance_km": round(distance_km, 2),
                    })

            lon += grid_delta
        lat += grid_delta

    return {"points": points, "band": band, "freq_mhz": freq_mhz, "tower_id": tower_id}


# ── Bands ──────────────────────────────────────────────────────────────────────

@app.get("/api/bands")
async def api_bands():
    return cfg.LTE_BANDS


# ── Settings ───────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_get_settings():
    return {
        "binary_path": storage.get_setting("binary_path", cfg.BINARY_SEARCH_PATHS[0]),
        "units":       storage.get_setting("units", "meters"),
    }


@app.post("/api/settings")
async def api_save_settings(body: dict):
    for key, value in body.items():
        storage.set_setting(key, str(value))
    return {"ok": True}


# ── Binary detection ───────────────────────────────────────────────────────────

@app.post("/api/binary/detect")
async def api_detect_binary():
    for path in cfg.BINARY_SEARCH_PATHS:
        expanded = os.path.expanduser(path)
        if not _check_binary(expanded):
            return {"found": True, "path": expanded}
    return {"found": False, "path": None}


@app.post("/api/binary/validate")
async def api_validate_binary(body: dict):
    """Check an arbitrary path and return a human-readable result."""
    path = body.get("path", "").strip()
    if not path:
        return {"ok": False, "error": "No path provided"}
    err = _check_binary(path)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "path": os.path.expanduser(path)}


# ── Export ─────────────────────────────────────────────────────────────────────

def _embed_floorplan(session: dict) -> Optional[str]:
    """Return a base64 data URL for the session's floor plan, or None."""
    import base64, mimetypes
    try:
        config_dict = json.loads(session.get("config") or "{}")
        fp_url = config_dict.get("floorplan_url")  # e.g. "/floorplans/plan.svg"
        if fp_url:
            # fp_url is a server path like "/floorplans/filename.svg".
            # Resolve just the filename against cfg.FLOORPLANS_DIR (the actual
            # storage location) rather than BASE_DIR which doesn't have the files.
            filename = Path(fp_url).name
            fp_path  = cfg.FLOORPLANS_DIR / filename
            if fp_path.exists():
                mime = mimetypes.guess_type(str(fp_path))[0] or "image/png"
                raw  = fp_path.read_bytes()
                return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    except Exception:
        pass
    return None


@app.post("/api/export/{session_id}")
async def api_export(session_id: str):
    sessions = storage.get_sessions()
    session  = next((s for s in sessions if s["id"] == session_id), None)
    if not session:
        raise HTTPException(404, "Session not found")
    points = storage.get_session_points(session_id)
    towers = storage.get_all_towers()

    # For outdoor surveys fetch OSM tiles server-side so the report has a real map.
    tile_b64, tile_bounds = None, None
    try:
        config_dict = json.loads(session.get("config") or "{}")
    except Exception:
        config_dict = {}
    if config_dict.get("survey_mode", "MAP_SURVEY") == "MAP_SURVEY":
        tile_b64, tile_bounds = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_osm_tile_map, points
        )

    html = generate_html_report(
        session, points, towers,
        floorplan_b64=_embed_floorplan(session),
        tile_map_b64=tile_b64,
        tile_map_bounds=tile_bounds,
    )
    path = save_report(session_id, html)
    return FileResponse(
        str(path),
        media_type="text/html",
        filename=f"report_{session_id}.html",
    )


@app.post("/api/export/{session_id}/with-map")
async def api_export_with_map(session_id: str, body: dict):
    sessions = storage.get_sessions()
    session  = next((s for s in sessions if s["id"] == session_id), None)
    if not session:
        raise HTTPException(404, "Session not found")
    points = storage.get_session_points(session_id)
    towers = storage.get_all_towers()
    map_image_base64 = body.get("map_image_base64")

    html = generate_html_report(
        session, points, towers,
        map_image_base64=map_image_base64,
        floorplan_b64=_embed_floorplan(session),
    )
    path = save_report(session_id, html)
    return FileResponse(
        str(path),
        media_type="text/html",
        filename=f"report_{session_id}.html",
    )


# ── Floor plan upload ──────────────────────────────────────────────────────────

@app.post("/api/floorplan/upload")
async def api_upload_floorplan(file: UploadFile = File(...)):
    allowed = {".png", ".jpg", ".jpeg", ".pdf", ".svg"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    dest = cfg.FLOORPLANS_DIR / file.filename
    dest.write_bytes(await file.read())
    return {"filename": file.filename, "url": f"/floorplans/{file.filename}"}


@app.get("/api/floorplans")
async def api_list_floorplans():
    items = [
        {"filename": p.name, "url": f"/floorplans/{p.name}"}
        for p in cfg.FLOORPLANS_DIR.iterdir()
        if p.is_file()
    ]
    return items


# ── Tile proxy (offline caching) ───────────────────────────────────────────────

@app.get("/api/tiles/{z}/{x}/{y}.png")
async def proxy_tile(z: int, x: int, y: int):
    tile_path = cfg.TILES_DIR / str(z) / str(x) / f"{y}.png"
    _CORS = {"Access-Control-Allow-Origin": "*"}
    if tile_path.exists():
        return FileResponse(str(tile_path), media_type="image/png", headers=_CORS)
    try:
        url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "OpenCellSurvey/1.0"})
        if resp.status_code == 200:
            tile_path.parent.mkdir(parents=True, exist_ok=True)
            tile_path.write_bytes(resp.content)
            return Response(content=resp.content, media_type="image/png", headers=_CORS)
    except Exception as exc:
        logger.warning("Tile fetch failed z=%d x=%d y=%d: %s", z, x, y, exc)
    raise HTTPException(404, "Tile not available offline")


# ── Tile bulk download ─────────────────────────────────────────────────────────

@app.post("/api/tiles/download")
async def api_download_tiles(body: dict):
    """Pre-download and cache map tiles for offline use."""
    import math as _math

    lat      = float(body.get("lat",      39.5))
    lon      = float(body.get("lon",     -98.35))
    radius   = float(body.get("radius_km", 10))
    z_min    = int(body.get("zoom_min", 10))
    z_max    = int(body.get("zoom_max", 16))
    z_max    = min(z_max, 17)   # cap to avoid absurd counts

    def _tile_xy(lat_d, lon_d, z):
        n = 2 ** z
        x = int((lon_d + 180) / 360 * n)
        lr = _math.radians(lat_d)
        y = int((1 - _math.log(_math.tan(lr) + 1 / _math.cos(lr)) / _math.pi) / 2 * n)
        return x, y

    lat_d = radius / 111.0
    lon_d = radius / (111.0 * _math.cos(_math.radians(lat)))

    # Build full tile list across zoom levels
    tiles = []
    for z in range(z_min, z_max + 1):
        x0, y1 = _tile_xy(lat - lat_d, lon - lon_d, z)
        x1, y0 = _tile_xy(lat + lat_d, lon + lon_d, z)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                tiles.append((z, tx, ty))

    MAX_TILES = 3000
    if len(tiles) > MAX_TILES:
        raise HTTPException(400,
            f"Too many tiles ({len(tiles)}). Reduce radius or max zoom (max {MAX_TILES}).")

    async def _stream():
        downloaded = cached = failed = 0
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "OpenCellSurvey/1.0"}) as client:
            for z, tx, ty in tiles:
                tile_path = cfg.TILES_DIR / str(z) / str(tx) / f"{ty}.png"
                if tile_path.exists():
                    cached += 1
                else:
                    try:
                        url = f"https://tile.openstreetmap.org/{z}/{tx}/{ty}.png"
                        r = await client.get(url)
                        if r.status_code == 200:
                            tile_path.parent.mkdir(parents=True, exist_ok=True)
                            tile_path.write_bytes(r.content)
                            downloaded += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                    await asyncio.sleep(0.05)   # ~20 req/s — polite to OSM

                yield json.dumps({"downloaded": downloaded, "cached": cached,
                                  "failed": failed, "total": len(tiles)}) + "\n"

        yield json.dumps({"done": True, "downloaded": downloaded,
                          "cached": cached, "failed": failed, "total": len(tiles)}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket endpoint
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    # Greet with current state
    await websocket.send_text(json.dumps({
        "type": "status",
        "data": {"running": runner.is_running, "session_id": active_session_id},
    }))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "gps":
                    d = msg.get("data", {})
                    gps.update_position(d["latitude"], d["longitude"])
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except Exception as exc:
        logger.error("WS error: %s", exc)
        ws_clients.discard(websocket)
