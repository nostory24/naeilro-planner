from __future__ import annotations

import heapq
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "output" / "reachable.db"
STATIC_DIR = ROOT / "static"

app = FastAPI(title="내일로 역 탐색기", version="8.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_station_cache: list[str] | None = None


def open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError("web/output/reachable.db 파일이 없습니다.")

    uri = f"file:{DB_PATH.as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def normalize_train_category(train_type: str) -> str:
    value = (train_type or "").strip().upper()

    if "KTX" in value:
        return "KTX"
    if "ITX-새마을" in value or "ITX 새마을" in value:
        return "ITX-새마을"
    if "ITX-마음" in value or "ITX 마음" in value:
        return "ITX-마음"
    if "ITX-청춘" in value or "ITX 청춘" in value:
        return "ITX-청춘"
    if "무궁화" in value:
        return "무궁화호"
    if "누리로" in value:
        return "누리로"
    if "새마을" in value:
        return "새마을호"
    if not value or value == "UNKNOWN":
        return "미확인"
    return train_type.strip()


def load_graph(
    conn: sqlite3.Connection,
    excluded_categories: set[str],
) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    names = {
        row["station_id"]: row["station_name"]
        for row in conn.execute(
            "SELECT station_id, station_name FROM stations"
        ).fetchall()
    }

    graph: dict[str, list[dict[str, Any]]] = defaultdict(list)

    rows = conn.execute("""
        SELECT
            from_station_id,
            to_station_id,
            min_duration_minutes,
            best_train_no,
            best_train_type,
            best_line_name
        FROM direct_routes
    """).fetchall()

    for row in rows:
        category = normalize_train_category(row["best_train_type"])
        if category in excluded_categories:
            continue

        graph[row["from_station_id"]].append({
            "to": row["to_station_id"],
            "minutes": int(row["min_duration_minutes"]),
            "train_no": row["best_train_no"] or "",
            "train_type": row["best_train_type"] or "UNKNOWN",
            "line_name": row["best_line_name"] or "",
        })

    return names, graph


def dijkstra(
    source: str,
    graph: dict[str, list[dict[str, Any]]],
    transfer_wait: int,
):
    distance = {source: 0}
    ride_count = {source: 0}
    previous: dict[str, tuple[str, dict[str, Any], int]] = {}
    queue = [(0, 0, source)]

    while queue:
        current_cost, current_rides, node = heapq.heappop(queue)

        if current_cost != distance.get(node):
            continue

        for edge in graph.get(node, []):
            target = edge["to"]
            waiting = 0 if current_rides == 0 else transfer_wait
            next_cost = current_cost + waiting + edge["minutes"]
            next_rides = current_rides + 1

            old_cost = distance.get(target, 10**12)
            old_rides = ride_count.get(target, 10**9)

            if next_cost < old_cost or (
                next_cost == old_cost and next_rides < old_rides
            ):
                distance[target] = next_cost
                ride_count[target] = next_rides
                previous[target] = (node, edge, waiting)
                heapq.heappush(queue, (next_cost, next_rides, target))

    return distance, ride_count, previous


def reconstruct_route(
    source: str,
    target: str,
    previous: dict[str, tuple[str, dict[str, Any], int]],
    names: dict[str, str],
):
    nodes: list[str] = []
    legs: list[tuple[str, str, dict[str, Any], int]] = []
    current = target

    while current != source:
        if current not in previous:
            return "", "", []

        before, edge, waiting = previous[current]
        nodes.append(current)
        legs.append((before, current, edge, waiting))
        current = before

    nodes.append(source)
    nodes.reverse()
    legs.reverse()

    path = " → ".join(names.get(node, node) for node in nodes)
    leg_texts: list[str] = []
    train_types: list[str] = []

    for before, current, edge, waiting in legs:
        train_type = edge["train_type"] or "UNKNOWN"
        if train_type not in train_types:
            train_types.append(train_type)

        leg_text = (
            f'{names.get(before, before)}→{names.get(current, current)} '
            f'({edge["minutes"]}분, {train_type}, '
            f'열차번호 {edge["train_no"]}, {edge["line_name"]})'
        )
        if waiting:
            leg_text = f"환승대기 {waiting}분 + " + leg_text
        leg_texts.append(leg_text)

    return path, " | ".join(leg_texts), train_types


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
    )


@app.get("/api/health")
def health():
    return {
        "ok": DB_PATH.exists(),
        "db": str(DB_PATH),
        "db_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }


@app.get("/api/db-check")
def db_check():
    try:
        conn = open_db()
        station_count = conn.execute(
            "SELECT COUNT(*) FROM stations"
        ).fetchone()[0]
        direct_route_count = conn.execute(
            "SELECT COUNT(*) FROM direct_routes"
        ).fetchone()[0]
        conn.close()

        return {
            "ok": True,
            "station_count": station_count,
            "direct_route_count": direct_route_count,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(exc)},
        )


@app.get("/api/stations")
def stations():
    global _station_cache

    try:
        if _station_cache is None:
            conn = open_db()
            rows = conn.execute("""
                SELECT station_name
                FROM stations
                WHERE station_name IS NOT NULL
                  AND TRIM(station_name) <> ''
                ORDER BY station_name
            """).fetchall()
            conn.close()

            _station_cache = sorted({
                str(row["station_name"]).strip()
                for row in rows
                if str(row["station_name"]).strip()
            })

        return {
            "stations": _station_cache,
            "count": len(_station_cache),
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"역 목록 조회 실패: {exc}"},
        )


@app.get("/api/search")
def search(
    from_station: str = Query(...),
    minutes: int = Query(180, ge=1, le=3000),
    max_transfer: int = Query(2, ge=0, le=20),
    excluded_train_types: str = Query(""),
    transfer_wait: int = Query(15, ge=0, le=180),
    limit: int = Query(1000, ge=1, le=5000),
):
    try:
        excluded_categories = {
            value.strip()
            for value in excluded_train_types.split(",")
            if value.strip()
        }

        conn = open_db()

        source_row = conn.execute(
            "SELECT station_id FROM stations WHERE station_name = ? LIMIT 1",
            (from_station,),
        ).fetchone()

        if source_row is None:
            conn.close()
            return JSONResponse(
                status_code=404,
                content={"error": f"역을 찾을 수 없습니다: {from_station}"},
            )

        source_id = source_row["station_id"]
        names, graph = load_graph(conn, excluded_categories)
        conn.close()

        distance, rides, previous = dijkstra(
            source_id,
            graph,
            transfer_wait,
        )

        results: list[dict[str, Any]] = []

        for target_id, duration in distance.items():
            if target_id == source_id:
                continue

            ride_count = rides.get(target_id, 0)
            transfer_count = max(0, ride_count - 1)

            if duration > minutes or transfer_count > max_transfer:
                continue

            path, legs, train_types = reconstruct_route(
                source_id,
                target_id,
                previous,
                names,
            )

            results.append({
                "to_station": names.get(target_id, target_id),
                "duration_minutes": int(duration),
                "transfer_count": transfer_count,
                "ride_count": ride_count,
                "path": path,
                "legs": legs,
                "used_train_types": ",".join(train_types),
            })

        results.sort(
            key=lambda item: (
                item["duration_minutes"],
                item["transfer_count"],
                item["to_station"],
            )
        )

        results = results[:limit]

        return {
            "from_station": from_station,
            "minutes": minutes,
            "max_transfer": max_transfer,
            "excluded_train_types": sorted(excluded_categories),
            "count": len(results),
            "results": results,
        }

    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )
