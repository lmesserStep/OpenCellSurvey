# OpenCellSurvey (OCS)

A self-hosted LTE RF survey tool that drives **srsRAN's `cell_monitor`** binary and displays live signal measurements on an interactive map. Run walking surveys, visualize RSRP/RSRQ/SNR on a color-coded grid, and export PDF-ready HTML reports — all served over HTTPS from a Raspberry Pi or Linux server.

---

## Features

- **Live map** — 5 m grid cells color-coded by RSRP (Excellent / Good / Fair / Poor)
- **Survey path** — orange polyline drawn in real time via GPS
- **Multi-band LTE** — Bands 2, 4, 5, 12, 13, 14, 25, 41, 48 (CBRS), 66, 71
- **PRB selection** — 6 / 15 / 25 / 50 / 75 / 100 resource blocks; required for Band 48
- **Live RSRP chart** — rolling 100-sample Chart.js graph
- **Survey statistics** — sample count, distance (miles), avg / best / worst RSRP
- **Tower propagation** — FSPL-based RF coverage simulation overlay
- **HTML export** — report with embedded map screenshot, RSRP chart, and measurement table
- **Session management** — rename, delete, and revisit past surveys
- **Runs as a systemd service** — auto-starts on boot, HTTPS on port 8443

---

## Requirements

| Requirement | Notes |
|---|---|
| Debian / Ubuntu Linux | Installer uses `apt-get` |
| Python 3.10+ | Installed automatically if missing |
| `srsRAN_4G` `cell_monitor` | Built separately; see [srsRAN docs](https://docs.srsran.com/) |
| GPS daemon (`gpsd`) | Optional but recommended for path tracking |
| SDR hardware | e.g. USRP B200, LimeSDR, RTL-SDR (band-dependent) |

---

## Quick Install

Clone the repo and run the installer as root:

```bash
git clone https://github.com/lmesserStep/OCS.git
cd OCS
sudo ./install.sh
```

The installer is **idempotent** — safe to re-run for upgrades:

```bash
sudo ./install.sh upgrade
```

### What the installer does

| Step | Action |
|---|---|
| 1 | Stops the service if upgrading |
| 2 | Installs system packages (`python3`, `python3-venv`, `sqlite3`, `openssl`, `rsync`, …) |
| 3 | Creates a dedicated `opencellsurvey` system user |
| 4 | Creates `/opt/opencellsurvey`, `/var/lib/opencellsurvey`, `/etc/opencellsurvey` |
| 5 | Generates a self-signed TLS certificate (10-year validity) |
| 6 | Creates a Python virtualenv and installs dependencies from `requirements.txt` |
| 7 | Deploys application files to `/opt/opencellsurvey/app` via `rsync` |
| 8 | Writes the systemd service unit to `/etc/systemd/system/opencellsurvey.service` |
| 9 | Enables and starts the service |

Persistent data in `/var/lib/opencellsurvey` is **never deleted** during upgrades.

---

## Accessing the UI

After installation, open a browser on the same network:

```
https://<device-ip>:8443
```

Accept the self-signed certificate warning. To avoid it, replace the generated cert with one signed by a trusted CA.

---

## Service Management

```bash
sudo systemctl status  opencellsurvey
sudo systemctl stop    opencellsurvey
sudo systemctl restart opencellsurvey
sudo journalctl -u     opencellsurvey -f
```

---

## Uninstall

```bash
sudo ./uninstall.sh
```

Survey data in `/var/lib/opencellsurvey` is preserved unless you remove it manually.

---

## Directory Layout

```
/opt/opencellsurvey/
├── app/          # Application code (rsync'd from repo)
└── venv/         # Python virtualenv

/var/lib/opencellsurvey/
├── data/
│   ├── surveys.db      # SQLite database
│   ├── exports/        # Generated HTML reports
│   └── floorplans/     # Uploaded floor-plan images
└── tiles/              # Cached map tiles

/etc/opencellsurvey/
├── cert.pem
└── key.pem
```

---

## Python Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | Web framework + WebSocket server |
| `uvicorn[standard]` | ASGI server (HTTPS) |
| `jinja2` | HTML templates |
| `pydantic` | Request/response validation |
| `httpx` | Async HTTP (tile fetching) |
| `Pillow` | Image processing for map export |
| `python-multipart` | File upload support |

---

## License

MIT
