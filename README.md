# 내일로 역 탐색기 v7.6

## 역 목록 로딩 수정

- SQLite를 Render에서 읽기 전용 `immutable` 모드로 열도록 변경
- 역 목록을 첫 조회 후 서버 메모리에 캐시
- 중복 역명 제거
- DB 잠금 대기시간을 3초로 제한
- 프런트엔드 역 목록 요청을 8초로 제한
- `/api/db-check` 진단 주소 추가
- 모바일 캐시 버전 `naeilro-v7-6`

## 배포 후 확인 주소

```text
https://naeilro-planner.onrender.com/api/health
https://naeilro-planner.onrender.com/api/db-check
https://naeilro-planner.onrender.com/api/stations
```

정상이라면 `/api/db-check`에 `station_count`와
`direct_route_count`가 숫자로 표시됩니다.

## 업로드

```bash
cp -r /c/Users/user/Desktop/naeilro_v7_6_station_loading_fix/* /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa/

cd /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa
git add .
git commit -m "Fix station loading v7.6"
git push
```

DB를 다시 만들 필요는 없습니다.
