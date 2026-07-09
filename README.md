# 내일로 역 탐색기 v6 - 코레일 열차운행정보 기반

v6는 기존 TAGO 출발역→도착역 전수조회 방식 대신  
공공데이터포털 **한국철도공사_열차운행정보** API를 사용합니다.

사용 API:

```text
https://apis.data.go.kr/B551457/run/v2/travelerTrainRunInfo2
```

핵심:
- 열차별 정차역 row를 수집
- `trn_no + trn_run_sn` 기준으로 정차 순서 복원
- 같은 열차 안의 모든 역쌍을 직통 구간으로 생성
- 환승 포함 최단경로 계산
- `reachable.db` 생성
- iPhone PWA, 룰렛 랜덤, 다시 뽑기 포함

## 1. DB 생성

```bash
cd builder
pip install -r requirements.txt
cp .env.example .env
notepad .env
python build_db.py
```

`.env`:

```env
DATA_GO_KR_SERVICE_KEY=여기에_일반인증키
RUN_YMD=20260707
TRANSFER_WAIT_MINUTES=15
```

생성 결과:

```text
builder/output/reachable.db
```

## 2. 웹앱으로 DB 복사

```bash
cp output/reachable.db ../web/output/reachable.db
```

## 3. 로컬 테스트

```bash
cd ../web
pip install -r requirements.txt
uvicorn app:app --reload
```

접속:

```text
http://127.0.0.1:8000
```

## 4. Render 배포

GitHub에 전체 업로드 후 Render:

```text
Root Directory: web
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

iPhone Safari → Render 주소 접속 → 공유 → 홈 화면에 추가.


## v6.1 추가 기능

- 검색 결과 리스트에 체크박스 추가
- 체크 해제한 역은 랜덤 룰렛 후보에서 제외
- 전체 선택 / 전체 해제 버튼 추가
- 랜덤 방식은 균등 랜덤만 사용
- 다시 뽑기 버튼은 체크된 후보 목록 기준으로 다시 추첨


## v6.2 추가 기능

- 출발역 드롭다운을 검색 가능한 입력창으로 변경
- 역 이름 일부만 입력하면 브라우저 자동완성 후보 표시
- 예: `구` 입력 → 구미, 구포 등 후보 표시
- Enter 키로 바로 검색 가능


## v6.3 추가 기능

- 출발역 초기값 제거
- 출발역 자동완성 후보를 직접 만든 스크롤 목록으로 변경
- 도착 지역 필터 추가: 서울, 경기, 충남 등
- 지역 필터는 검색 결과와 랜덤 후보 모두에 적용
- 모바일 브라우저 캐시 갱신을 위해 Service Worker 캐시 버전 변경


## v6.4 추가 기능

- 랜덤 박스 안에 `랜덤 제외 지역` 필터 추가
- 서울/경기/충남 등을 체크하면 해당 지역 역은 랜덤 후보에서 제외
- 제외 지역 역은 리스트에는 보이지만 체크박스가 비활성화됨
- Service Worker 캐시 버전 `naeilro-v6-4`로 변경


## v6.5 수정

- 미분류를 줄이기 위해 지역 매핑표 보강
- 역명이 정확히 매핑표에 없어도 키워드 기반으로 지역 추정
- 랜덤 제외 지역을 체크 해제하면 해당 지역 역들이 랜덤 후보로 다시 복구됨
- Service Worker 캐시 버전 `naeilro-v6-5`로 변경
