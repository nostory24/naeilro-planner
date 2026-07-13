# 내일로 역 탐색기 v7.1

코레일 운행정보와 TAGO 열차정보를 결합해 다음 정보를 생성합니다.

- 열차별 정차역과 소요시간
- 환승 포함 최단경로
- 사용 열차종류
- KTX 포함 여부
- 지역 및 KTX 랜덤 제외 옵션

## 수정사항

- TAGO 도시코드 엔드포인트를 `GetCtyCodeList`로 수정
- 오늘 데이터가 없으면 최대 14일 전까지 자동으로 데이터 날짜 탐색
- 빈 운행정보로 DB 생성을 계속하지 않고 명확한 오류 출력
- `.env`, 캐시, builder 출력 DB가 GitHub에 올라가지 않도록 `.gitignore` 추가

## 사용법

```bash
cd builder
cp .env.example .env
notepad .env
pip install -r requirements.txt
python build_db.py
```

`.env`:

```env
DATA_GO_KR_SERVICE_KEY=일반인증키
RUN_YMD=
NUM_OF_ROWS=1000
SLEEP_SECONDS=0.12
TRANSFER_WAIT_MINUTES=15
EXCLUDE_KEYWORDS=SRT,AREX,공항철도,지하철,전철,도시철도,통근
```

`RUN_YMD`를 비우면 오늘 날짜부터 과거 14일까지 자동 탐색합니다.  
특정 날짜를 고정하려면 예를 들어 `RUN_YMD=20260707`로 입력하세요.

DB 생성 후:

```bash
cp output/reachable.db ../web/output/reachable.db
```

로컬 실행:

```bash
cd ../web
pip install -r requirements.txt
uvicorn app:app --reload
```

GitHub 배포:

```bash
cd ..
git add .
git commit -m "Upgrade to v7.1"
git push
```

Render가 자동 재배포합니다.


## v7.2 UI 개선

- 지역 제외 옵션을 접기/펼치기 형태로 변경
- 열차 제외 옵션을 접기/펼치기 형태로 변경
- 룰렛 뽑기 시작 시 룰렛 영역으로 자동 스크롤
- 최종 결과가 나오면 결과 영역으로 자동 스크롤
- 모바일 캐시 버전 `naeilro-v7-2` 적용
