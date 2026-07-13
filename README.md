# 내일로 역 탐색기 v8

기존 v7 계열의 누적 패치를 제거하고 웹 서버와 화면 코드를 새로 정리한 버전입니다.

## 유지된 기능

- 출발역 자동완성
- 최대 시간 및 환승 횟수
- 지역별 랜덤 제외
- 열차 종류별 제외
- 체크박스로 랜덤 후보 선택
- 전체 선택 및 전체 해제
- 룰렛 애니메이션
- 룰렛 자동 스크롤
- 다시 뽑기
- iPhone PWA
- Render 배포

## 폴더 구조

```text
builder/
  build_db.py
  requirements.txt
  .env.example

web/
  app.py
  requirements.txt
  output/reachable.db
  static/index.html
```

## 설치 및 배포 순서

### 1. 압축 풀기

```text
C:\Users\user\Desktop\naeilro_v8_clean_rebuild
```

### 2. 기존 DB 복사

이미 만든 v7.4 DB를 사용합니다.

```bash
cp /c/Users/user/Desktop/naeilro_v7_4_multi_train_type_filters/web/output/reachable.db /c/Users/user/Desktop/naeilro_v8_clean_rebuild/web/output/reachable.db
```

### 3. 로컬 확인

```bash
cd /c/Users/user/Desktop/naeilro_v8_clean_rebuild/web
pip install -r requirements.txt
uvicorn app:app --reload
```

브라우저:

```text
http://127.0.0.1:8000
```

### 4. GitHub 연결 폴더로 복사

```bash
cp -r /c/Users/user/Desktop/naeilro_v8_clean_rebuild/* /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa/

cp /c/Users/user/Desktop/naeilro_v8_clean_rebuild/.gitignore /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa/.gitignore
```

### 5. GitHub 업로드

```bash
cd /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa
git add .
git commit -m "Rebuild app as v8"
git push
```

### 6. Render 설정

```text
Root Directory: web
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

자동 배포가 시작되지 않으면:

```text
Manual Deploy → Deploy latest commit
```

### 7. 확인 주소

```text
https://naeilro-planner.onrender.com/api/health
https://naeilro-planner.onrender.com/api/db-check
https://naeilro-planner.onrender.com/api/stations
```

## DB를 다시 만들 때

```bash
cd /c/Users/user/Desktop/naeilro_v8_clean_rebuild/builder
cp .env.example .env
notepad .env
pip install -r requirements.txt
python build_db.py
cp output/reachable.db ../web/output/reachable.db
```
