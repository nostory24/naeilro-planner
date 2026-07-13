# 내일로 역 탐색기 v7.5

## 수정사항

- 검색 시 발생하던 `trainExcluded is not defined` 오류 수정
- 삭제된 `trainExcluded()` 호출을 전부 제거
- 열차 필터는 서버의 `excluded_train_types` 방식으로만 처리
- 지역 제외 필터와 열차 제외 필터가 서로 독립적으로 동작
- 모바일 브라우저 캐시 버전 `naeilro-v7-5` 적용
- Render용 `web/requirements.txt` 유지

## 열차 필터 동작

체크한 차종의 운행편만 제거한 뒤 남은 열차로 최단경로를 다시 계산합니다.

- KTX·KTX-산천
- ITX-새마을
- ITX-마음
- ITX-청춘
- 새마을호
- 무궁화호
- 누리로
- 차종 미확인

## 배포

v7.4용 새 DB를 이미 만들었다면 그대로 사용해도 됩니다.

```bash
cp -r /c/Users/user/Desktop/naeilro_v7_5_train_filter_bugfix/* /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa/

cd /c/Users/user/Desktop/naeilro_v6_korail_runinfo_sqlite_pwa
git add .
git commit -m "Fix v7.5 train filter"
git push
```

Render에서 자동 배포가 시작되지 않으면:

```text
Manual Deploy → Deploy latest commit
```
