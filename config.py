import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# DATA_DIR can be overridden by the systemd Environment directive.
# Production: /var/lib/opencellsurvey
# Dev fallback: <project_root>/data
_env_data = os.environ.get("DATA_DIR")
DATA_DIR = Path(_env_data) if _env_data else BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "surveys.db"

# Tiles live under DATA_DIR in production; keep them local in dev.
TILES_DIR = DATA_DIR / "tiles"
TILES_DIR.mkdir(parents=True, exist_ok=True)

EXPORTS_DIR = DATA_DIR / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

FLOORPLANS_DIR = DATA_DIR / "floorplans"
FLOORPLANS_DIR.mkdir(parents=True, exist_ok=True)

# TLS cert paths (used by run.sh / systemd service)
ETC_DIR  = Path(os.environ.get("ETC_DIR", "/etc/opencellsurvey"))
SSL_CERT = ETC_DIR / "cert.pem"
SSL_KEY  = ETC_DIR / "key.pem"

BINARY_SEARCH_PATHS = [
    "/usr/local/bin/cell_monitor",
    "/usr/bin/cell_monitor",
    "./cell_monitor",
    os.path.expanduser("~/cell_monitor"),
    # srsRAN_4G common build locations
    os.path.expanduser("~/srsRAN_4G/build/lib/examples/cell_monitor"),
    os.path.expanduser("~/srsran/build/lib/examples/cell_monitor"),
    "/opt/srsran/bin/cell_monitor",
    "/usr/local/lib/srsran/examples/cell_monitor",
]

LTE_BANDS = {
    2:  {"name": "Band 2",       "ul_low": 1850, "ul_high": 1910, "dl_low": 1930, "dl_high": 1990, "earfcn_dl_low": 600,   "earfcn_dl_high": 799},
    4:  {"name": "Band 4",       "ul_low": 1710, "ul_high": 1755, "dl_low": 2110, "dl_high": 2155, "earfcn_dl_low": 1200,  "earfcn_dl_high": 1949},
    5:  {"name": "Band 5",       "ul_low": 824,  "ul_high": 849,  "dl_low": 869,  "dl_high": 894,  "earfcn_dl_low": 2400,  "earfcn_dl_high": 2649},
    12: {"name": "Band 12",      "ul_low": 699,  "ul_high": 716,  "dl_low": 729,  "dl_high": 746,  "earfcn_dl_low": 5010,  "earfcn_dl_high": 5179},
    13: {"name": "Band 13",      "ul_low": 777,  "ul_high": 787,  "dl_low": 746,  "dl_high": 756,  "earfcn_dl_low": 5180,  "earfcn_dl_high": 5279},
    14: {"name": "Band 14",      "ul_low": 788,  "ul_high": 798,  "dl_low": 758,  "dl_high": 768,  "earfcn_dl_low": 5280,  "earfcn_dl_high": 5379},
    25: {"name": "Band 25",      "ul_low": 1850, "ul_high": 1915, "dl_low": 1930, "dl_high": 1995, "earfcn_dl_low": 8040,  "earfcn_dl_high": 8689},
    41: {"name": "Band 41",      "ul_low": 2496, "ul_high": 2690, "dl_low": 2496, "dl_high": 2690, "earfcn_dl_low": 39650, "earfcn_dl_high": 41589},
    48: {"name": "Band 48 (CBRS)","ul_low": 3550, "ul_high": 3700,"dl_low": 3550, "dl_high": 3700, "earfcn_dl_low": 55240, "earfcn_dl_high": 56739},
    66: {"name": "Band 66",      "ul_low": 1710, "ul_high": 1780, "dl_low": 2110, "dl_high": 2200, "earfcn_dl_low": 66436, "earfcn_dl_high": 67335},
    71: {"name": "Band 71",      "ul_low": 663,  "ul_high": 698,  "dl_low": 617,  "dl_high": 652,  "earfcn_dl_low": 68586, "earfcn_dl_high": 68935},
}

TDD_BANDWIDTH_PRB = {
    5:  25,
    10: 50,
    15: 75,
    20: 100,
}
