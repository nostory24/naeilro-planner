from __future__ import annotations

import heapq
import json
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests
from dotenv import load_dotenv
from tqdm import tqdm


BASE_URL = "https://apis.data.go.kr/B551457/run/v2"
ENDPOINT = "travelerTrainRunInfo2"

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
CACHE = ROOT / ".cache"
DB_PATH = OUT / "reachable.db"


@dataclass(frozen=True)
class Settings:
    service_key: str
    run_ymd: str
    num_of_rows: int
    sleep_seconds: float
    transfer_wait_minutes: int
    exclude_keywords: List[str]


def today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key or key == "YOUR_SERVICE_KEY_HERE":
        raise RuntimeError("builder/.env에 DATA_GO_KR_SERVICE_KEY를 입력하세요.")
    exclude = [x.strip() for x in os.getenv("EXCLUDE_KEYWORDS", "").split(",") if x.strip()]
    return Settings(
        service_key=unquote(key),
        run_ymd=os.getenv("RUN_YMD", "").strip() or today_yyyymmdd(),
        num_of_rows=int(os.getenv("NUM_OF_ROWS", "1000")),
        sleep_seconds=float(os.getenv("SLEEP_SECONDS", "0.1")),
        transfer_wait_minutes=int(os.getenv("TRANSFER_WAIT_MINUTES", "15")),
        exclude_keywords=exclude,
    )


class Client:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        CACHE.mkdir(parents=True, exist_ok=True)

    def page(self, p: int) -> Dict[str, Any]:
        cache = CACHE / f"runinfo_{self.s.run_ymd}_p{p}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        time.sleep(self.s.sleep_seconds)
        params = {
            "serviceKey": self.s.service_key,
            "pageNo": p,
            "numOfRows": self.s.num_of_rows,
            "returnType": "JSON",
            "cond[run_ymd::EQ]": self.s.run_ymd,
        }
        r = self.session.get(f"{BASE_URL}/{ENDPOINT}", params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:1000]}")
        data = r.json()
        header = data.get("response", {}).get("header", {})
        code = str(header.get("resultCode", "0"))
        if code not in {"0", "00"}:
            raise RuntimeError(f"API 오류 {code}: {header.get('resultMsg', '')}")
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data


def items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    it = data.get("response", {}).get("body", {}).get("items")
    if not it:
        return []
    item = it.get("item") if isinstance(it, dict) else it
    if item is None:
        return []
    return item if isinstance(item, list) else [item]


def total(data: Dict[str, Any]) -> int:
    return int(data.get("response", {}).get("body", {}).get("totalCount", 0) or 0)


def fetch_all(c: Client) -> List[Dict[str, Any]]:
    first = c.page(1)
    tot = total(first)
    rows = items(first)
    if tot == 0:
        print("WARN: totalCount=0. RUN_YMD에 데이터가 있는지 확인하세요.")
        return rows
    pages = (tot + c.s.num_of_rows - 1) // c.s.num_of_rows
    print(f"총 운행정보 row: {tot:,}개 / 페이지: {pages:,}개")
    for p in tqdm(range(2, pages + 1), desc="운행정보 수집"):
        rows.extend(items(c.page(p)))
    return rows


def connect() -> sqlite3.Connection:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    DROP TABLE IF EXISTS raw_train_stops;
    DROP TABLE IF EXISTS stations;
    DROP TABLE IF EXISTS direct_routes;
    DROP TABLE IF EXISTS reachable_routes;

    CREATE TABLE raw_train_stops (
        run_ymd TEXT, trn_no TEXT, trn_run_sn INTEGER,
        stn_cd TEXT, stn_nm TEXT, mrnt_cd TEXT, mrnt_nm TEXT,
        stop_se_cd TEXT, stop_se_nm TEXT, uppln_dn_se_cd TEXT,
        trn_arvl_dt TEXT, trn_dptre_dt TEXT,
        PRIMARY KEY(run_ymd, trn_no, trn_run_sn, stn_cd)
    );

    CREATE TABLE stations (
        station_id TEXT PRIMARY KEY,
        station_name TEXT NOT NULL,
        line_code TEXT,
        line_name TEXT
    );

    CREATE TABLE direct_routes (
        from_station_id TEXT NOT NULL,
        from_station TEXT NOT NULL,
        to_station_id TEXT NOT NULL,
        to_station TEXT NOT NULL,
        min_duration_minutes INTEGER NOT NULL,
        best_train_no TEXT,
        best_line_name TEXT,
        sample_count INTEGER,
        PRIMARY KEY(from_station_id, to_station_id)
    );

    CREATE TABLE reachable_routes (
        from_station_id TEXT NOT NULL,
        from_station TEXT NOT NULL,
        to_station_id TEXT NOT NULL,
        to_station TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        transfer_count INTEGER NOT NULL,
        ride_count INTEGER NOT NULL,
        path TEXT,
        legs TEXT,
        PRIMARY KEY(from_station_id, to_station_id)
    );

    CREATE INDEX idx_raw_train ON raw_train_stops(run_ymd, trn_no, uppln_dn_se_cd, trn_run_sn);
    CREATE INDEX idx_direct_from ON direct_routes(from_station_id);
    CREATE INDEX idx_reachable_search ON reachable_routes(from_station, duration_minutes, transfer_count);
    """)
    conn.commit()


def excluded(row: Dict[str, Any], keys: List[str]) -> bool:
    s = " ".join(str(row.get(k) or "") for k in ["stn_nm", "mrnt_nm", "trn_no", "stop_se_nm"])
    return any(k in s for k in keys)


def norm(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_ymd": str(row.get("run_ymd") or "").strip(),
        "trn_no": str(row.get("trn_no") or "").strip(),
        "trn_run_sn": int(str(row.get("trn_run_sn") or "0").strip() or 0),
        "stn_cd": str(row.get("stn_cd") or "").strip(),
        "stn_nm": str(row.get("stn_nm") or "").strip(),
        "mrnt_cd": str(row.get("mrnt_cd") or "").strip(),
        "mrnt_nm": str(row.get("mrnt_nm") or "").strip(),
        "stop_se_cd": str(row.get("stop_se_cd") or "").strip(),
        "stop_se_nm": str(row.get("stop_se_nm") or "").strip(),
        "uppln_dn_se_cd": str(row.get("uppln_dn_se_cd") or "").strip(),
        "trn_arvl_dt": None if row.get("trn_arvl_dt") is None else str(row.get("trn_arvl_dt")),
        "trn_dptre_dt": None if row.get("trn_dptre_dt") is None else str(row.get("trn_dptre_dt")),
    }


def store_raw(conn: sqlite3.Connection, rows: List[Dict[str, Any]], s: Settings) -> List[Dict[str, Any]]:
    clean = []
    skipped = 0
    for r in rows:
        if excluded(r, s.exclude_keywords):
            skipped += 1
            continue
        n = norm(r)
        if not n["run_ymd"] or not n["trn_no"] or not n["stn_cd"] or not n["stn_nm"]:
            skipped += 1
            continue
        clean.append(n)
    conn.executemany("""
    INSERT OR REPLACE INTO raw_train_stops
    (run_ymd,trn_no,trn_run_sn,stn_cd,stn_nm,mrnt_cd,mrnt_nm,stop_se_cd,stop_se_nm,uppln_dn_se_cd,trn_arvl_dt,trn_dptre_dt)
    VALUES
    (:run_ymd,:trn_no,:trn_run_sn,:stn_cd,:stn_nm,:mrnt_cd,:mrnt_nm,:stop_se_cd,:stop_se_nm,:uppln_dn_se_cd,:trn_arvl_dt,:trn_dptre_dt)
    """, clean)
    conn.commit()
    print(f"필터 후 정차 row: {len(clean):,}개 / 제외 row: {skipped:,}개")
    return clean


def parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "null":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def dep_time(r): return parse_dt(r.get("trn_dptre_dt")) or parse_dt(r.get("trn_arvl_dt"))
def arr_time(r): return parse_dt(r.get("trn_arvl_dt")) or parse_dt(r.get("trn_dptre_dt"))


def mins_between(a: datetime, b: datetime) -> Optional[int]:
    m = int((b - a).total_seconds() // 60)
    if m < 0:
        m += 1440
    if m <= 0 or m > 1440:
        return None
    return m


def build_stations(conn: sqlite3.Connection) -> Dict[str, str]:
    conn.execute("""
    INSERT OR REPLACE INTO stations(station_id,station_name,line_code,line_name)
    SELECT stn_cd, stn_nm, MIN(mrnt_cd), MIN(mrnt_nm)
    FROM raw_train_stops GROUP BY stn_cd, stn_nm
    """)
    conn.commit()
    names = {sid: name for sid, name in conn.execute("SELECT station_id, station_name FROM stations")}
    print(f"역 수: {len(names):,}개")
    return names


def build_direct(conn: sqlite3.Connection) -> None:
    cols = [c[1] for c in conn.execute("PRAGMA table_info(raw_train_stops)")]
    raw = [dict(zip(cols, r)) for r in conn.execute("SELECT * FROM raw_train_stops ORDER BY run_ymd,trn_no,uppln_dn_se_cd,trn_run_sn")]
    groups = defaultdict(list)
    for r in raw:
        groups[(r["run_ymd"], r["trn_no"], r["uppln_dn_se_cd"] or "")].append(r)

    print(f"열차 운행 그룹: {len(groups):,}개")
    best = {}
    sample = defaultdict(int)

    for (run_ymd, trn_no, direction), stops in tqdm(groups.items(), desc="직통 구간 생성"):
        stops.sort(key=lambda r: int(r.get("trn_run_sn") or 0))
        valid = [r for r in stops if dep_time(r) or arr_time(r)]
        for i, a in enumerate(valid):
            dt = dep_time(a)
            if not dt:
                continue
            for b in valid[i+1:]:
                at = arr_time(b)
                if not at:
                    continue
                m = mins_between(dt, at)
                if m is None:
                    continue
                key = (a["stn_cd"], b["stn_cd"])
                sample[key] += 1
                if key not in best or m < best[key]["minutes"]:
                    best[key] = {
                        "from_id": a["stn_cd"], "from_nm": a["stn_nm"],
                        "to_id": b["stn_cd"], "to_nm": b["stn_nm"],
                        "minutes": m, "trn_no": trn_no,
                        "line": a.get("mrnt_nm") or b.get("mrnt_nm") or "",
                    }

    rows = [(v["from_id"],v["from_nm"],v["to_id"],v["to_nm"],v["minutes"],v["trn_no"],v["line"],sample[k]) for k,v in best.items()]
    conn.executemany("""
    INSERT OR REPLACE INTO direct_routes
    (from_station_id,from_station,to_station_id,to_station,min_duration_minutes,best_train_no,best_line_name,sample_count)
    VALUES (?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    print(f"직통 구간 수: {len(rows):,}개")


def load_graph(conn):
    names = {sid: name for sid, name in conn.execute("SELECT station_id, station_name FROM stations")}
    edges = defaultdict(list)
    for a,b,m,trn,line in conn.execute("SELECT from_station_id,to_station_id,min_duration_minutes,best_train_no,best_line_name FROM direct_routes"):
        edges[a].append({"to": b, "minutes": int(m), "train_no": trn or "", "line": line or ""})
    return names, edges


def dijkstra(src, edges, wait):
    dist = {src: 0}
    hops = {src: 0}
    prev = {}
    pq = [(0, 0, src)]
    while pq:
        cost, hop, node = heapq.heappop(pq)
        if cost != dist.get(node): continue
        for e in edges.get(node, []):
            nxt = e["to"]
            w = 0 if hop == 0 else wait
            nc = cost + w + e["minutes"]
            nh = hop + 1
            if nc < dist.get(nxt, 10**12) or (nc == dist.get(nxt, 10**12) and nh < hops.get(nxt, 10**9)):
                dist[nxt] = nc
                hops[nxt] = nh
                prev[nxt] = (node, e, w)
                heapq.heappush(pq, (nc, nh, nxt))
    return dist, hops, prev


def reconstruct(src, dst, prev, names):
    nodes, legs = [], []
    cur = dst
    while cur != src:
        if cur not in prev: return "", ""
        before, e, wait = prev[cur]
        nodes.append(cur)
        legs.append((before, cur, e, wait))
        cur = before
    nodes.append(src)
    nodes.reverse()
    legs.reverse()
    path = " -> ".join(names.get(n,n) for n in nodes)
    texts = []
    for before, cur, e, wait in legs:
        t = f'{names.get(before,before)}→{names.get(cur,cur)}({e["minutes"]}분,열차번호 {e["train_no"]}, {e["line"]})'
        if wait: t = f"환승대기{wait}분 + " + t
        texts.append(t)
    return path, " | ".join(texts)


def build_reachable(conn, transfer_wait):
    names, edges = load_graph(conn)
    batch = []
    for src in tqdm(names.keys(), desc="환승 포함 최단경로"):
        dist, hops, prev = dijkstra(src, edges, transfer_wait)
        for dst, m in dist.items():
            if src == dst: continue
            path, legs = reconstruct(src, dst, prev, names)
            ride = hops.get(dst, 0)
            batch.append((src,names.get(src,src),dst,names.get(dst,dst),int(m),max(0,ride-1),ride,path,legs))
        if len(batch) >= 5000:
            conn.executemany("""
            INSERT OR REPLACE INTO reachable_routes
            (from_station_id,from_station,to_station_id,to_station,duration_minutes,transfer_count,ride_count,path,legs)
            VALUES (?,?,?,?,?,?,?,?,?)
            """, batch)
            conn.commit()
            batch.clear()
    if batch:
        conn.executemany("""
        INSERT OR REPLACE INTO reachable_routes
        (from_station_id,from_station,to_station_id,to_station,duration_minutes,transfer_count,ride_count,path,legs)
        VALUES (?,?,?,?,?,?,?,?,?)
        """, batch)
        conn.commit()
    conn.execute("VACUUM")
    conn.commit()
    print(f"reachable_routes: {conn.execute('SELECT COUNT(*) FROM reachable_routes').fetchone()[0]:,}개")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    s = load_settings()
    print(f"RUN_YMD={s.run_ymd}")
    print(f"TRANSFER_WAIT_MINUTES={s.transfer_wait_minutes}")
    print(f"EXCLUDE_KEYWORDS={', '.join(s.exclude_keywords)}")

    conn = connect()
    init_db(conn)

    print("[1/5] 운행정보 수집")
    raw = fetch_all(Client(s))
    print("[2/5] raw 정차정보 저장 및 필터링")
    store_raw(conn, raw, s)
    print("[3/5] 역 테이블 생성")
    build_stations(conn)
    print("[4/5] 열차별 정차역 기반 직통 구간 생성")
    build_direct(conn)
    print("[5/5] 환승 포함 최단경로 생성")
    build_reachable(conn, s.transfer_wait_minutes)
    print(f"완료: {DB_PATH}")
    print("이 파일을 web/output/reachable.db 로 복사하세요.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
