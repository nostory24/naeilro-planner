from __future__ import annotations

import heapq
import json
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests
from dotenv import load_dotenv
from tqdm import tqdm


KORAIL_BASE_URL = "https://apis.data.go.kr/B551457/run/v2"
KORAIL_RUNINFO_ENDPOINT = "travelerTrainRunInfo2"

TAGO_BASE_URL = "https://apis.data.go.kr/1613000/TrainInfo"

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
CACHE = ROOT / ".cache"
DB_PATH = OUT / "reachable.db"

ALLOWED_TYPES = ["KTX", "ITX-새마을", "ITX 새마을", "ITX-청춘", "ITX 청춘", "새마을", "무궁화", "누리로"]
EXCLUDED_TYPES = ["SRT", "AREX", "공항철도", "지하철", "전철", "도시철도", "통근"]


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
        sleep_seconds=float(os.getenv("SLEEP_SECONDS", "0.12")),
        transfer_wait_minutes=int(os.getenv("TRANSFER_WAIT_MINUTES", "15")),
        exclude_keywords=exclude,
    )


def extract_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = data.get("response", {}).get("body", {})
    items = body.get("items")
    if not items:
        return []
    item = items.get("item") if isinstance(items, dict) else items
    if item is None:
        return []
    return item if isinstance(item, list) else [item]


def total_count(data: Dict[str, Any]) -> int:
    try:
        return int(data.get("response", {}).get("body", {}).get("totalCount", 0) or 0)
    except Exception:
        return 0


class CachedClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        CACHE.mkdir(parents=True, exist_ok=True)

    def get_json(
        self,
        url: str,
        params: Dict[str, Any],
        cache_key: str,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        path = CACHE / f"{cache_key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

        time.sleep(self.s.sleep_seconds)
        response = self.session.get(url, params=params, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1000]}")

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"JSON 파싱 실패: {response.text[:1000]}") from exc

        header = data.get("response", {}).get("header", {})
        code = str(header.get("resultCode", "0"))
        if code not in {"0", "00"}:
            raise RuntimeError(f"API 오류 {code}: {header.get('resultMsg', '')}")

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data


def normalize_station_name(name: str) -> str:
    value = (name or "").strip()
    if value.endswith("역"):
        value = value[:-1]
    aliases = {
        "신경주": "경주",
        "김천(구미)": "김천구미",
        "김천구미": "김천구미",
        "광주송정": "광주송정",
    }
    return aliases.get(value, value)


def normalize_train_no(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return str(int(text))
    return text.upper().replace(" ", "")


def train_type_allowed(name: str) -> bool:
    value = (name or "").strip()
    if not value:
        return False
    low = value.lower()
    if any(x.lower() in low for x in EXCLUDED_TYPES):
        return False
    return any(x.lower() in low for x in ALLOWED_TYPES)


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d%H%M%S",
        "%Y%m%d%H%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def departure_time(row: Dict[str, Any]) -> Optional[datetime]:
    return parse_dt(row.get("trn_dptre_dt")) or parse_dt(row.get("trn_arvl_dt"))


def arrival_time(row: Dict[str, Any]) -> Optional[datetime]:
    return parse_dt(row.get("trn_arvl_dt")) or parse_dt(row.get("trn_dptre_dt"))


def minutes_between(dep: datetime, arr: datetime) -> Optional[int]:
    minutes = int((arr - dep).total_seconds() // 60)
    if minutes < 0:
        minutes += 24 * 60
    if minutes <= 0 or minutes > 24 * 60:
        return None
    return minutes


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
    DROP TABLE IF EXISTS tago_stations;
    DROP TABLE IF EXISTS train_types;
    DROP TABLE IF EXISTS direct_routes;
    DROP TABLE IF EXISTS reachable_routes;

    CREATE TABLE raw_train_stops (
        run_ymd TEXT,
        trn_no TEXT,
        trn_run_sn INTEGER,
        stn_cd TEXT,
        stn_nm TEXT,
        mrnt_cd TEXT,
        mrnt_nm TEXT,
        stop_se_cd TEXT,
        stop_se_nm TEXT,
        uppln_dn_se_cd TEXT,
        trn_arvl_dt TEXT,
        trn_dptre_dt TEXT,
        PRIMARY KEY(run_ymd, trn_no, trn_run_sn, stn_cd)
    );

    CREATE TABLE stations (
        station_id TEXT PRIMARY KEY,
        station_name TEXT NOT NULL,
        line_code TEXT,
        line_name TEXT
    );

    CREATE TABLE tago_stations (
        station_id TEXT PRIMARY KEY,
        station_name TEXT NOT NULL,
        city_code TEXT,
        city_name TEXT
    );

    CREATE TABLE train_types (
        run_ymd TEXT NOT NULL,
        train_no TEXT NOT NULL,
        normalized_train_no TEXT NOT NULL,
        train_type TEXT NOT NULL,
        source_dep_station TEXT,
        source_arr_station TEXT,
        PRIMARY KEY(run_ymd, normalized_train_no)
    );

    CREATE TABLE direct_routes (
        from_station_id TEXT NOT NULL,
        from_station TEXT NOT NULL,
        to_station_id TEXT NOT NULL,
        to_station TEXT NOT NULL,
        min_duration_minutes INTEGER NOT NULL,
        best_train_no TEXT,
        best_train_type TEXT NOT NULL,
        best_line_name TEXT,
        sample_count INTEGER,
        PRIMARY KEY(from_station_id, to_station_id, best_train_type)
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
        used_train_types TEXT,
        uses_ktx INTEGER NOT NULL DEFAULT 0,
        has_unknown_train_type INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(from_station_id, to_station_id)
    );

    CREATE TABLE reachable_routes_no_ktx AS SELECT * FROM reachable_routes WHERE 0;

    CREATE INDEX idx_raw_train ON raw_train_stops(run_ymd, trn_no, uppln_dn_se_cd, trn_run_sn);
    CREATE INDEX idx_train_types_no ON train_types(normalized_train_no);
    CREATE INDEX idx_direct_from ON direct_routes(from_station_id);
    CREATE INDEX idx_reachable_search ON reachable_routes(from_station, duration_minutes, transfer_count);
    CREATE INDEX idx_reachable_no_ktx_search ON reachable_routes_no_ktx(from_station, duration_minutes, transfer_count);
    """)
    conn.commit()


def fetch_run_info_for_date(
    client: CachedClient,
    settings: Settings,
    run_ymd: str,
) -> Tuple[List[Dict[str, Any]], int]:
    url = f"{KORAIL_BASE_URL}/{KORAIL_RUNINFO_ENDPOINT}"
    common = {
        "serviceKey": settings.service_key,
        "numOfRows": settings.num_of_rows,
        "returnType": "JSON",
        "cond[run_ymd::EQ]": run_ymd,
    }

    first = client.get_json(
        url,
        {**common, "pageNo": 1},
        f"runinfo_{run_ymd}_p1",
    )
    rows = extract_items(first)
    total = total_count(first)

    if total == 0:
        return rows, 0

    pages = (total + settings.num_of_rows - 1) // settings.num_of_rows
    print(f"운행정보 기준일 {run_ymd}: {total:,}행 / {pages:,}페이지")

    for page in tqdm(range(2, pages + 1), desc="운행정보 수집"):
        data = client.get_json(
            url,
            {**common, "pageNo": page},
            f"runinfo_{run_ymd}_p{page}",
        )
        rows.extend(extract_items(data))

    return rows, total


def fetch_run_info(
    client: CachedClient,
    settings: Settings,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Requested date first. If no rows exist, search backward up to 14 days.
    This avoids creating an empty DB when today's data has not been published yet.
    """
    requested = datetime.strptime(settings.run_ymd, "%Y%m%d")

    for days_back in range(0, 15):
        candidate = (requested - timedelta(days=days_back)).strftime("%Y%m%d")
        rows, total = fetch_run_info_for_date(client, settings, candidate)

        if total > 0:
            if candidate != settings.run_ymd:
                print(
                    f"WARN: 요청일 {settings.run_ymd} 데이터가 없어 "
                    f"가장 가까운 데이터 날짜 {candidate}를 사용합니다."
                )
            return rows, candidate

        print(f"운행정보 없음: {candidate}")

    raise RuntimeError(
        f"{settings.run_ymd}부터 과거 14일 동안 운행정보를 찾지 못했습니다. "
        "builder/.env의 RUN_YMD를 실제 데이터가 있는 날짜로 지정하세요."
    )


def normalize_run_row(row: Dict[str, Any]) -> Dict[str, Any]:
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


def store_run_info(
    conn: sqlite3.Connection,
    rows: List[Dict[str, Any]],
    settings: Settings,
) -> List[Dict[str, Any]]:
    clean: List[Dict[str, Any]] = []
    skipped = 0

    for raw in rows:
        joined = " ".join(str(raw.get(k) or "") for k in ("stn_nm", "mrnt_nm", "stop_se_nm"))
        if any(keyword in joined for keyword in settings.exclude_keywords):
            skipped += 1
            continue

        row = normalize_run_row(raw)
        if not row["run_ymd"] or not row["trn_no"] or not row["stn_cd"] or not row["stn_nm"]:
            skipped += 1
            continue
        clean.append(row)

    conn.executemany("""
        INSERT OR REPLACE INTO raw_train_stops
        (run_ymd,trn_no,trn_run_sn,stn_cd,stn_nm,mrnt_cd,mrnt_nm,
         stop_se_cd,stop_se_nm,uppln_dn_se_cd,trn_arvl_dt,trn_dptre_dt)
        VALUES
        (:run_ymd,:trn_no,:trn_run_sn,:stn_cd,:stn_nm,:mrnt_cd,:mrnt_nm,
         :stop_se_cd,:stop_se_nm,:uppln_dn_se_cd,:trn_arvl_dt,:trn_dptre_dt)
    """, clean)
    conn.commit()
    print(f"정차정보 저장: {len(clean):,}행 / 제외 {skipped:,}행")
    return clean


def build_stations(conn: sqlite3.Connection) -> Dict[str, str]:
    conn.execute("""
        INSERT OR REPLACE INTO stations(station_id,station_name,line_code,line_name)
        SELECT stn_cd, stn_nm, MIN(mrnt_cd), MIN(mrnt_nm)
        FROM raw_train_stops
        GROUP BY stn_cd, stn_nm
    """)
    conn.commit()
    names = {sid: name for sid, name in conn.execute("SELECT station_id, station_name FROM stations")}
    print(f"운행정보 역 수: {len(names):,}개")
    return names


def fetch_tago_station_map(
    client: CachedClient,
    settings: Settings,
    conn: sqlite3.Connection,
) -> Dict[str, Dict[str, str]]:
    city_url = f"{TAGO_BASE_URL}/GetCtyCodeList"
    city_data = client.get_json(
        city_url,
        {"serviceKey": settings.service_key, "_type": "json", "pageNo": 1, "numOfRows": 100},
        "tago_city_codes",
    )
    cities = extract_items(city_data)

    station_map: Dict[str, Dict[str, str]] = {}
    station_rows = []

    for city in tqdm(cities, desc="TAGO 역 목록"):
        city_code = str(city.get("citycode") or city.get("cityCode") or "").strip()
        city_name = str(city.get("cityname") or city.get("cityName") or "").strip()
        if not city_code:
            continue

        url = f"{TAGO_BASE_URL}/GetCtyAcctoTrainSttnList"
        data = client.get_json(
            url,
            {
                "serviceKey": settings.service_key,
                "_type": "json",
                "cityCode": city_code,
                "pageNo": 1,
                "numOfRows": 1000,
            },
            f"tago_stations_{city_code}",
        )

        for row in extract_items(data):
            station_id = str(
                row.get("nodeid")
                or row.get("nodeId")
                or row.get("trainstationid")
                or row.get("trainStationId")
                or ""
            ).strip()
            station_name = str(
                row.get("nodename")
                or row.get("nodeName")
                or row.get("trainstationname")
                or row.get("trainStationName")
                or ""
            ).strip()
            if not station_id or not station_name:
                continue

            key = normalize_station_name(station_name)
            station_map[key] = {
                "station_id": station_id,
                "station_name": station_name,
                "city_code": city_code,
                "city_name": city_name,
            }
            station_rows.append((station_id, station_name, city_code, city_name))

    conn.executemany("""
        INSERT OR REPLACE INTO tago_stations(station_id,station_name,city_code,city_name)
        VALUES (?,?,?,?)
    """, station_rows)
    conn.commit()
    print(f"TAGO 역 매핑: {len(station_map):,}개")
    return station_map


def group_train_runs(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["run_ymd"], row["trn_no"], row.get("uppln_dn_se_cd") or "")
        groups[key].append(row)
    for stops in groups.values():
        stops.sort(key=lambda x: int(x.get("trn_run_sn") or 0))
    return groups


def terminal_pairs(
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]]
) -> Dict[Tuple[str, str], List[str]]:
    pair_to_train_nos: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for (_, train_no, _), stops in groups.items():
        valid = [s for s in stops if s.get("stn_nm")]
        if len(valid) < 2:
            continue
        dep_name = normalize_station_name(valid[0]["stn_nm"])
        arr_name = normalize_station_name(valid[-1]["stn_nm"])
        if dep_name and arr_name and dep_name != arr_name:
            pair_to_train_nos[(dep_name, arr_name)].append(train_no)

    return pair_to_train_nos


def fetch_train_type_mapping(
    client: CachedClient,
    settings: Settings,
    conn: sqlite3.Connection,
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]],
    tago_station_map: Dict[str, Dict[str, str]],
) -> Dict[str, str]:
    pairs = terminal_pairs(groups)
    print(f"고유 시발-종착 조합: {len(pairs):,}개")

    mapping: Dict[str, str] = {}
    source_info: Dict[str, Tuple[str, str, str]] = {}
    unresolved_pairs = 0
    failed_pairs = 0

    for (dep_name, arr_name), expected_train_nos in tqdm(pairs.items(), desc="열차종류 매핑"):
        dep = tago_station_map.get(dep_name)
        arr = tago_station_map.get(arr_name)
        if not dep or not arr:
            unresolved_pairs += 1
            continue

        url = f"{TAGO_BASE_URL}/GetStrtpntAlocFndTrainInfo"
        cache_key = f"tago_route_{settings.run_ymd}_{dep['station_id']}_{arr['station_id']}"

        try:
            data = client.get_json(
                url,
                {
                    "serviceKey": settings.service_key,
                    "_type": "json",
                    "depPlaceId": dep["station_id"],
                    "arrPlaceId": arr["station_id"],
                    "depPlandTime": settings.run_ymd,
                    "pageNo": 1,
                    "numOfRows": 1000,
                },
                cache_key,
            )
        except Exception as exc:
            failed_pairs += 1
            if failed_pairs <= 20:
                print(f"WARN: {dep_name}→{arr_name} TAGO 조회 실패: {exc}")
            continue

        for row in extract_items(data):
            train_no_raw = str(row.get("trainno") or row.get("trainNo") or "").strip()
            train_type = str(
                row.get("traingradename")
                or row.get("trainGradeName")
                or row.get("vehicletype")
                or row.get("vehicleType")
                or ""
            ).strip()

            normalized_no = normalize_train_no(train_no_raw)
            if not normalized_no or not train_type:
                continue
            if not train_type_allowed(train_type):
                continue

            mapping[normalized_no] = train_type
            source_info[normalized_no] = (train_no_raw, dep_name, arr_name)

    rows_to_insert = []
    for normalized_no, train_type in mapping.items():
        train_no_raw, dep_name, arr_name = source_info[normalized_no]
        rows_to_insert.append(
            (
                settings.run_ymd,
                train_no_raw,
                normalized_no,
                train_type,
                dep_name,
                arr_name,
            )
        )

    conn.executemany("""
        INSERT OR REPLACE INTO train_types
        (run_ymd,train_no,normalized_train_no,train_type,source_dep_station,source_arr_station)
        VALUES (?,?,?,?,?,?)
    """, rows_to_insert)
    conn.commit()

    runinfo_numbers = {normalize_train_no(key[1]) for key in groups}
    matched = sum(1 for no in runinfo_numbers if no in mapping)

    print(f"열차종류 매핑: {matched:,}/{len(runinfo_numbers):,}개 열차번호")
    print(f"TAGO 역명 매핑 실패 시발-종착 조합: {unresolved_pairs:,}개")
    print(f"TAGO 조회 실패 조합: {failed_pairs:,}개")

    return mapping


def build_direct_routes(
    conn: sqlite3.Connection,
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]],
    train_type_map: Dict[str, str],
) -> None:
    # 역쌍별 하나만 남기지 않고, 역쌍 + 열차종류별 최단 운행을 보존합니다.
    # 따라서 서울→부산에 KTX/ITX/무궁화가 모두 있으면 세 종류가 모두 남습니다.
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    sample_count: Dict[Tuple[str, str, str], int] = defaultdict(int)

    for (_, train_no, _), stops in tqdm(groups.items(), desc="직통 구간 생성"):
        valid = [s for s in stops if departure_time(s) or arrival_time(s)]
        if len(valid) < 2:
            continue

        train_type = train_type_map.get(normalize_train_no(train_no), "UNKNOWN")

        for i, dep_stop in enumerate(valid):
            dep_dt = departure_time(dep_stop)
            if dep_dt is None:
                continue

            for arr_stop in valid[i + 1:]:
                arr_dt = arrival_time(arr_stop)
                if arr_dt is None:
                    continue

                duration = minutes_between(dep_dt, arr_dt)
                if duration is None:
                    continue

                key = (dep_stop["stn_cd"], arr_stop["stn_cd"], train_type)
                sample_count[key] += 1
                current = best.get(key)

                if current is None or duration < current["minutes"]:
                    best[key] = {
                        "from_id": dep_stop["stn_cd"],
                        "from_name": dep_stop["stn_nm"],
                        "to_id": arr_stop["stn_cd"],
                        "to_name": arr_stop["stn_nm"],
                        "minutes": duration,
                        "train_no": train_no,
                        "train_type": train_type,
                        "line_name": dep_stop.get("mrnt_nm") or arr_stop.get("mrnt_nm") or "",
                    }

    rows = [
        (
            item["from_id"], item["from_name"],
            item["to_id"], item["to_name"],
            item["minutes"], item["train_no"],
            item["train_type"], item["line_name"],
            sample_count[key],
        )
        for key, item in best.items()
    ]

    conn.executemany("""
        INSERT OR REPLACE INTO direct_routes
        (from_station_id,from_station,to_station_id,to_station,min_duration_minutes,
         best_train_no,best_train_type,best_line_name,sample_count)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()
    print(f"직통 구간(열차종류별 보존): {len(rows):,}개")



def load_graph(conn: sqlite3.Connection, exclude_ktx: bool = False):
    names = {
        station_id: station_name
        for station_id, station_name in conn.execute("SELECT station_id,station_name FROM stations")
    }
    edges: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    query = """
        SELECT from_station_id,to_station_id,min_duration_minutes,
               best_train_no,best_train_type,best_line_name
        FROM direct_routes
    """
    params = ()
    if exclude_ktx:
        query += " WHERE UPPER(best_train_type) NOT LIKE '%KTX%'"
    for row in conn.execute(query, params):
        dep, arr, minutes, train_no, train_type, line_name = row
        edges[dep].append(
            {
                "to": arr,
                "minutes": int(minutes),
                "train_no": train_no or "",
                "train_type": train_type or "UNKNOWN",
                "line_name": line_name or "",
            }
        )

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
            if new_cost < old_cost or (new_cost == old_cost and new_hops < old_hops):
                distance[nxt] = new_cost
                hops[nxt] = new_hops
                previous[nxt] = (node, edge, waiting)
                heapq.heappush(queue, (new_cost, new_hops, nxt))

    return distance, hops, previous


def reconstruct(
    source: str,
    target: str,
    previous,
    names: Dict[str, str],
):
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
    used_types: List[str] = []
    has_unknown = False

    for before, current, edge, waiting in legs:
        train_type = edge.get("train_type") or "UNKNOWN"
        if train_type == "UNKNOWN":
            has_unknown = True
        elif train_type not in used_types:
            used_types.append(train_type)

        text = (
            f'{names.get(before,before)}→{names.get(current,current)}'
            f'({edge["minutes"]}분, {train_type}, 열차번호 {edge["train_no"]}, {edge["line_name"]})'
        )
        if waiting:
            text = f"환승대기{waiting}분 + " + text
        leg_texts.append(text)

    return path, " | ".join(leg_texts), used_types, has_unknown


def build_reachable_routes(
    conn: sqlite3.Connection,
    transfer_wait: int,
    table_name: str = "reachable_routes",
    exclude_ktx: bool = False,
) -> None:
    if table_name not in {"reachable_routes", "reachable_routes_no_ktx"}:
        raise ValueError("허용되지 않은 테이블명입니다.")

    conn.execute(f"DELETE FROM {table_name}")
    conn.commit()

    names, edges = load_graph(conn, exclude_ktx=exclude_ktx)
    batch = []
    desc = "KTX 제외 최단경로" if exclude_ktx else "전체 열차 최단경로"

    for source in tqdm(names, desc=desc):
        distance, hops, previous = dijkstra(source, edges, transfer_wait)

        for target, duration in distance.items():
            if source == target:
                continue

            path, legs, used_types, has_unknown = reconstruct(source, target, previous, names)
            ride_count = hops.get(target, 0)
            uses_ktx = int(any("KTX" in t.upper() for t in used_types))

            batch.append((
                source, names.get(source, source),
                target, names.get(target, target),
                int(duration), max(0, ride_count - 1), ride_count,
                path, legs, ",".join(used_types),
                uses_ktx, int(has_unknown),
            ))

        if len(batch) >= 5000:
            conn.executemany(f"""
                INSERT OR REPLACE INTO {table_name}
                (from_station_id,from_station,to_station_id,to_station,duration_minutes,
                 transfer_count,ride_count,path,legs,used_train_types,uses_ktx,has_unknown_train_type)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, batch)
            conn.commit()
            batch.clear()

    if batch:
        conn.executemany(f"""
            INSERT OR REPLACE INTO {table_name}
            (from_station_id,from_station,to_station_id,to_station,duration_minutes,
             transfer_count,ride_count,path,legs,used_train_types,uses_ktx,has_unknown_train_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, batch)
        conn.commit()

    total = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"{table_name}: {total:,}개")



def main() -> int:
    settings = load_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    print(f"RUN_YMD={settings.run_ymd}")
    print(f"TRANSFER_WAIT_MINUTES={settings.transfer_wait_minutes}")

    conn = connect()
    init_db(conn)
    client = CachedClient(settings)

    print("[1/7] 한국철도공사 운행정보 수집")
    raw_rows, resolved_run_ymd = fetch_run_info(client, settings)

    if resolved_run_ymd != settings.run_ymd:
        settings = Settings(
            service_key=settings.service_key,
            run_ymd=resolved_run_ymd,
            num_of_rows=settings.num_of_rows,
            sleep_seconds=settings.sleep_seconds,
            transfer_wait_minutes=settings.transfer_wait_minutes,
            exclude_keywords=settings.exclude_keywords,
        )

    print("[2/7] 정차정보 저장")
    clean_rows = store_run_info(conn, raw_rows, settings)

    print("[3/7] 역 테이블 생성")
    build_stations(conn)

    print("[4/7] TAGO 도시코드 및 역코드 수집")
    tago_station_map = fetch_tago_station_map(client, settings, conn)

    print("[5/7] 시발-종착 조합으로 열차종류 수집")
    groups = group_train_runs(clean_rows)
    train_type_map = fetch_train_type_mapping(
        client,
        settings,
        conn,
        groups,
        tago_station_map,
    )

    print("[6/7] 열차별 정차역 기반 직통 구간 생성")
    build_direct_routes(conn, groups, train_type_map)

    print("[7/8] 전체 열차 기준 최단경로 생성")
    build_reachable_routes(
        conn,
        settings.transfer_wait_minutes,
        table_name="reachable_routes",
        exclude_ktx=False,
    )

    print("[8/8] KTX 운행편 제외 후 대체경로 생성")
    build_reachable_routes(
        conn,
        settings.transfer_wait_minutes,
        table_name="reachable_routes_no_ktx",
        exclude_ktx=True,
    )

    conn.execute("VACUUM")
    conn.commit()

    print(f"완료: {DB_PATH}")
    print("이 파일을 web/output/reachable.db 로 복사하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
