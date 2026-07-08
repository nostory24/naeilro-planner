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
