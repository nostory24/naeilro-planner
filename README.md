# 내일로 역 탐색기 v7.4

## 열차 종류별 제외 옵션

아래 열차를 각각 선택해서 제외할 수 있습니다.

- KTX 및 KTX-산천
- ITX-새마을
- ITX-마음
- ITX-청춘
- 새마을호
- 무궁화호
- 누리로
- 차종 미확인

예를 들어 `KTX`만 체크하면 KTX 운행편만 제거하고,
ITX·무궁화·누리로 등 남은 운행편으로 최단경로를 다시 계산합니다.

여러 열차 종류를 동시에 제외할 수도 있습니다.

## 중요

v7.3의 DB는 역쌍별 열차 종류를 보존하므로 그대로 사용할 수 있습니다.
아직 v7.3 DB를 만들지 않았다면 다음 명령으로 새로 생성해야 합니다.

```bash
cd builder
python build_db.py
cp output/reachable.db ../web/output/reachable.db
```

GitHub 업로드:

```bash
cd ..
git add .
git commit -m "Upgrade to v7.4 train filters"
git push
```

Render 설정:

```text
Root Directory: web
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```
