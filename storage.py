import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from models import SurveyPoint, Tower
import config as cfg

logger = logging.getLogger(__name__)


# ── connection ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(cfg.DB_PATH))
    c.row_factory = sqlite3.Row
    return c


# ── schema ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS survey_sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT,
            notes       TEXT,
            started_at  TEXT NOT NULL,
            ended_at    TEXT,
            config      TEXT,
            point_count INTEGER DEFAULT 0
        );""")

        # Migrate existing databases that predate the name/notes columns
        existing_cols = {row[1] for row in db.execute("PRAGMA table_info(survey_sessions)")}
        if "name" not in existing_cols:
            db.execute("ALTER TABLE survey_sessions ADD COLUMN name TEXT")
        if "notes" not in existing_cols:
            db.execute("ALTER TABLE survey_sessions ADD COLUMN notes TEXT")

        db.executescript("""

        CREATE TABLE IF NOT EXISTS survey_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT    NOT NULL REFERENCES survey_sessions(id) ON DELETE CASCADE,
            timestamp   TEXT,
            t_ms        INTEGER,
            band        INTEGER,
            earfcn      INTEGER,
            freq_mhz    REAL,
            pci         INTEGER,
            n_prb       INTEGER,
            ports       INTEGER,
            rsrp_dbm    REAL,
            rsrq_db     REAL,
            snr_db      REAL,
            cfo_hz      REAL,
            frame_type  TEXT,
            latitude    REAL,
            longitude   REAL
        );

        CREATE TABLE IF NOT EXISTS towers (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            pci       INTEGER,
            band      INTEGER,
            azimuth   REAL,
            height_m  REAL,
            eirp_w    REAL,
            notes     TEXT,
            latitude  REAL NOT NULL,
            longitude REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        # Migrate towers table: add eirp_w if missing
        tower_cols = {row[1] for row in db.execute("PRAGMA table_info(towers)")}
        if "eirp_w" not in tower_cols:
            db.execute("ALTER TABLE towers ADD COLUMN eirp_w REAL")


# ── sessions ───────────────────────────────────────────────────────────────────

def create_session(session_id: str, config_dict: dict) -> None:
    with _conn() as db:
        db.execute(
            "INSERT INTO survey_sessions (id, started_at, config, point_count) VALUES (?,?,?,0)",
            (session_id, datetime.now(timezone.utc).isoformat(), json.dumps(config_dict)),
        )


def end_session(session_id: str) -> None:
    with _conn() as db:
        db.execute(
            "UPDATE survey_sessions SET ended_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), session_id),
        )


def get_sessions() -> List[Dict[str, Any]]:
    with _conn() as db:
        rows = db.execute("SELECT * FROM survey_sessions ORDER BY started_at DESC").fetchall()
    return [dict(r) for r in rows]


def rename_session(session_id: str, name: str, notes: str = "") -> None:
    with _conn() as db:
        db.execute(
            "UPDATE survey_sessions SET name=?, notes=? WHERE id=?",
            (name, notes if notes else None, session_id),
        )


def delete_session(session_id: str) -> None:
    with _conn() as db:
        db.execute("DELETE FROM survey_sessions WHERE id=?", (session_id,))


# ── points ─────────────────────────────────────────────────────────────────────

def save_point(session_id: str, point: SurveyPoint) -> None:
    try:
        with _conn() as db:
            db.execute(
                """INSERT INTO survey_points
                   (session_id,timestamp,t_ms,band,earfcn,freq_mhz,pci,n_prb,ports,
                    rsrp_dbm,rsrq_db,snr_db,cfo_hz,frame_type,latitude,longitude)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, point.timestamp, point.t_ms, point.band, point.earfcn,
                    point.freq_mhz, point.pci, point.n_prb, point.ports,
                    point.rsrp_dbm, point.rsrq_db, point.snr_db, point.cfo_hz,
                    point.frame_type, point.latitude, point.longitude,
                ),
            )
            db.execute(
                "UPDATE survey_sessions SET point_count = point_count + 1 WHERE id=?",
                (session_id,),
            )
    except Exception as exc:
        logger.error("save_point: %s", exc)


def get_session_points(session_id: str) -> List[Dict[str, Any]]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM survey_points WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── towers ─────────────────────────────────────────────────────────────────────

def get_all_towers() -> List[Dict[str, Any]]:
    with _conn() as db:
        rows = db.execute("SELECT * FROM towers").fetchall()
    return [dict(r) for r in rows]


def save_tower(tower: Tower) -> int:
    with _conn() as db:
        cur = db.execute(
            """INSERT INTO towers (name,pci,band,azimuth,height_m,eirp_w,notes,latitude,longitude)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tower.name, tower.pci, tower.band, tower.azimuth,
             tower.height_m, tower.eirp_w, tower.notes, tower.latitude, tower.longitude),
        )
        return cur.lastrowid


def update_tower(tower_id: int, tower: Tower) -> None:
    with _conn() as db:
        db.execute(
            """UPDATE towers SET name=?,pci=?,band=?,azimuth=?,height_m=?,eirp_w=?,notes=?,
               latitude=?,longitude=? WHERE id=?""",
            (tower.name, tower.pci, tower.band, tower.azimuth,
             tower.height_m, tower.eirp_w, tower.notes, tower.latitude, tower.longitude, tower_id),
        )


def delete_tower(tower_id: int) -> None:
    with _conn() as db:
        db.execute("DELETE FROM towers WHERE id=?", (tower_id,))


# ── settings ───────────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    with _conn() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as db:
        db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
