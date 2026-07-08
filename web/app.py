from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "output" / "reachable.db"
STATIC = ROOT / "static"

app = FastAPI(title="내일로 역 탐색기", version="5.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError("web/output/reachable.db 파일이 없습니다.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    return {"ok": DB_PATH.exists(), "db": str(DB_PATH)}


@app.get("/api/stations")
def stations():
    try:
        conn = db()
        rows = conn.execute("""
            SELECT DISTINCT from_station AS station
            FROM reachable_routes
            ORDER BY from_station
        """).fetchall()
        conn.close()
        return {"stations": [r["station"] for r in rows]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/search")
def search(
    from_station: str = Query(...),
    minutes: int = Query(180, ge=1, le=3000),
    max_transfer: int = Query(5, ge=0, le=20),
    limit: int = Query(1000, ge=1, le=5000),
):
    try:
        conn = db()
        rows = conn.execute(
            """
            SELECT
                to_station,
                duration_minutes,
                transfer_count,
                ride_count,
                path,
                legs
            FROM reachable_routes
            WHERE from_station = ?
              AND duration_minutes <= ?
              AND transfer_count <= ?
            ORDER BY duration_minutes ASC, transfer_count ASC, to_station ASC
            LIMIT ?
            """,
            (from_station, minutes, max_transfer, limit),
        ).fetchall()
        conn.close()
        return {
            "from_station": from_station,
            "minutes": minutes,
            "max_transfer": max_transfer,
            "count": len(rows),
            "results": rows_to_dicts(rows),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
