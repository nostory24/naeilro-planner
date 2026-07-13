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
STATIC = ROOT / "static"

app = FastAPI(title="내일로 역 탐색기", version="7.4.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError("web/output/reachable.db 파일이 없습니다.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


def load_dynamic_graph(conn: sqlite3.Connection, excluded_categories: set[str]):
    names = {
        station_id: station_name
        for station_id, station_name in conn.execute(
            "SELECT station_id, station_name FROM stations"
        )
    }
    edges: dict[str, list[dict[str, Any]]] = defaultdict(list)

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

        edges[row["from_station_id"]].append({
            "to": row["to_station_id"],
            "minutes": int(row["min_duration_minutes"]),
            "train_no": row["best_train_no"] or "",
            "train_type": row["best_train_type"] or "UNKNOWN",
            "train_category": category,
            "line_name": row["best_line_name"] or "",
        })

    return names, edges


def dijkstra(source: str, edges, transfer_wait: int):
    distance = {source: 0}
    hops = {source: 0}
    previous = {}
    queue = [(0, 0, source)]

    while queue:
        cost, hop_count, node = heapq.heappop(queue)
        if cost != distance.get(node):
            continue

        for edge in edges.get(node, []):
            nxt = edge["to"]
            waiting = 0 if hop_count == 0 else transfer_wait
            new_cost = cost + waiting + edge["minutes"]
            new_hops = hop_count + 1

            old_cost = distance.get(nxt, 10**12)
            old_hops = hops.get(nxt, 10**9)

            if new_cost < old_cost or (
                new_cost == old_cost and new_hops < old_hops
            ):
                distance[nxt] = new_cost
                hops[nxt] = new_hops
                previous[nxt] = (node, edge, waiting)
                heapq.heappush(queue, (new_cost, new_hops, nxt))

    return distance, hops, previous


def reconstruct(source: str, target: str, previous, names: dict[str, str]):
    nodes = []
    legs = []
    current = target

    while current != source:
        if current not in previous:
            return "", "", [], False

        before, edge, waiting = previous[current]
        nodes.append(current)
        legs.append((before, current, edge, waiting))
        current = before

    nodes.append(source)
    nodes.reverse()
    legs.reverse()

    path = " -> ".join(names.get(node, node) for node in nodes)
    leg_texts = []
    used_types = []
    has_unknown = False

    for before, current, edge, waiting in legs:
        train_type = edge.get("train_type") or "UNKNOWN"
        category = edge.get("train_category") or normalize_train_category(train_type)

        if category == "미확인":
            has_unknown = True
        if train_type not in used_types:
            used_types.append(train_type)

        text = (
            f'{names.get(before, before)}→{names.get(current, current)}'
            f'({edge["minutes"]}분, {train_type}, '
            f'열차번호 {edge["train_no"]}, {edge["line_name"]})'
        )
        if waiting:
            text = f"환승대기{waiting}분 + " + text
        leg_texts.append(text)

    return path, " | ".join(leg_texts), used_types, has_unknown


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(
        STATIC / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


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
            SELECT DISTINCT station_name AS station
            FROM stations
            ORDER BY station_name
        """).fetchall()
        conn.close()
        return {"stations": [row["station"] for row in rows]}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/api/search")
def search(
    from_station: str = Query(...),
    minutes: int = Query(180, ge=1, le=3000),
    max_transfer: int = Query(5, ge=0, le=20),
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

        conn = db()
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
        names, edges = load_dynamic_graph(conn, excluded_categories)
        conn.close()

        distance, hops, previous = dijkstra(source_id, edges, transfer_wait)

        results = []
        for target_id, duration in distance.items():
            if target_id == source_id:
                continue

            ride_count = hops.get(target_id, 0)
            transfer_count = max(0, ride_count - 1)

            if duration > minutes or transfer_count > max_transfer:
                continue

            path, legs, used_types, has_unknown = reconstruct(
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
                "used_train_types": ",".join(used_types),
                "uses_ktx": int(any("KTX" in value.upper() for value in used_types)),
                "has_unknown_train_type": int(has_unknown),
            })

        results.sort(
            key=lambda row: (
                row["duration_minutes"],
                row["transfer_count"],
                row["to_station"],
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
        return JSONResponse(status_code=500, content={"error": str(exc)})
