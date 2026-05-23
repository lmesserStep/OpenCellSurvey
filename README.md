# OpenCellSurvey (OCS)

A self-hosted LTE RF survey tool that uses a custom measurement application built with **srsRAN helper functions** to decode LTE MAC/PHY frames and stream live RF measurements into an interactive web UI. Run walking surveys, visualize RSRP/RSRQ/SNR on a color-coded grid, and export PDF-ready HTML reports — all served over HTTPS from a Raspberry Pi or Linux server.

---

## Features

- **Live map** — 5 m grid cells color-coded by RSRP (Excellent / Good / Fair / Poor)
- **Survey path** — orange polyline drawn in real time via GPS
- **Multi-band LTE** — Bands 2, 4, 5, 12, 13, 14, 25, 41, 48 (CBRS), 66, 71
- **PRB selection** — 6 / 15 / 25 / 50 / 75 / 100 resource blocks; required for Band 48
- **Live RSRP chart** — rolling 100-sample Chart.js graph
- **Survey statistics** — sample count, distance in miles, average / best / worst RSRP
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
| Custom LTE measurement application | Built separately using srsRAN helper functions to decode LTE MAC/PHY frames and produce survey measurements |
| GPS daemon (`gpsd`) | Optional but recommended for path tracking |
| SDR hardware | e.g. USRP B200, LimeSDR, RTL-SDR, depending on band and RF requirements |

---

## Quick Install

Clone the repo and run the installer as root:

```bash
git clone https://github.com/lmesserStep/OpenCellSurvey.git
cd OCS
sudo ./install.sh
