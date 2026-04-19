from pydantic import BaseModel
from typing import Optional


class SurveyPoint(BaseModel):
    t_ms: int
    band: int
    earfcn: int
    freq_mhz: float
    pci: int
    n_prb: int
    ports: int
    rsrp_dbm: float
    rsrq_db: float
    snr_db: float
    cfo_hz: float
    frame_type: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: Optional[str] = None


class ScanConfig(BaseModel):
    band: int
    pci: Optional[int] = None
    mode: str = "normal"          # "fast" | "normal"
    frame_type: str = "FDD"       # "FDD" | "TDD"
    bandwidth_mhz: Optional[int] = None   # Required when frame_type == "TDD"
    binary_path: str = "/usr/local/bin/cell_monitor"
    # Survey mode state machine — locked for the lifetime of a session
    survey_mode: str = "MAP_SURVEY"       # MAP_SURVEY | INDOOR_OVERLAY | INDOOR_ONLY
    floorplan_url: Optional[str] = None   # e.g. "/floorplans/building1.png"
    floorplan_bounds: Optional[list] = None  # [[south, west], [north, east]]


class Tower(BaseModel):
    id: Optional[int] = None
    name: str
    pci: Optional[int] = None
    band: Optional[int] = None
    azimuth: Optional[float] = None
    height_m: Optional[float] = None
    eirp_w: Optional[float] = None   # Effective Isotropic Radiated Power in Watts
    notes: Optional[str] = None
    latitude: float
    longitude: float


class AppSettings(BaseModel):
    binary_path: str = "/usr/local/bin/cell_monitor"
    units: str = "meters"   # "meters" | "feet"
    default_band: int = 13
