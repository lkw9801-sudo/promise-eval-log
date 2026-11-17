# 약속 탑승권 · 실시간 정확도 검증 키트 (6조)

파이썬 3.9 이상, **표준 라이브러리만** 씁니다(설치할 것 없음). 노트북에서 실행하세요.

## 바로 실행 (윈도우, 더블클릭)
- `1_api_check.bat` → `check_out/check_report.md` 를 Claude에게 보내기
- `2_start_logging.bat` → 2주 동안 창을 열어 두기 (노트북 절전 끄기)
- `3_evaluate.bat` → 그래프 리포트가 브라우저로 열림
- `4_live_prototype.bat` → 프로토타입 v0.6을 서울시 실시간 데이터로 실행 (http://localhost:8787, 콘솔 「데이터」에서 「실시간 · 서울시 API」 클릭)

`config.json`에 인증키가 이미 들어 있습니다. **팀 밖으로 공유하지 마세요.**

## 노트북 없이 기록하기 (GitHub Actions)
1. GitHub에 **공개(Public)** 저장소를 새로 만들고, 이 폴더 내용을 그대로 올린다 (`config.json`은 올리지 말 것 · .gitignore에 들어 있음)
2. 저장소 Settings → Secrets and variables → Actions → New repository secret 2개
   - `SEOUL_SUBWAY_KEY` = 서울시 실시간 지하철 키
   - `DATA_GO_KR_KEY` = 공공데이터포털 키
3. Actions 탭 → 「실시간 기록」 → Run workflow (5분 시험) → 끝나면 `logs/` 폴더에 오늘 날짜 파일이 생겼는지 확인
4. 그 뒤로는 정해진 시간대마다 자동 실행. 분석할 때 저장소의 `logs/`를 내려받아 `python evaluate.py`
- 공개 저장소는 Actions 무료·무제한. 비공개면 월 2,000분 한도라 부족함
- 시험 실행에서 연결 실패가 나면 서울시 서버가 해외 서버 접속을 막는 경우 → Claude에게 알려 주면 다른 방법으로 전환

## 프로토타입 실시간 실행
```
python server.py          # 서울시 실시간
python server.py --mock   # 인터넷 없이 연습
```

## 0. 준비 (이미 끝남 · 키를 바꿀 때만)
1. `config.example.json` → `config.json` 으로 복사
2. 인증키 2개 넣기
   - `seoul_subway`: 서울 열린데이터광장 **실시간 지하철** 인증키 (프로토타입 실행기 config.json에 있는 키)
   - `data_go_kr`: 공공데이터포털 일반 인증키(**Encoding** 값). 「버스도착정보조회」와 **「버스위치정보조회」** 둘 다 활용신청되어 있어야 함
3. 도보·환승 시간(`origin_walk_min`, `walk_after_min`, `final_walk_min`)을 프로토타입 데이터 시트 값과 똑같이 맞추기

## 1. API 실응답 확인
```
python api_check.py --repeat 6
```
- `check_out/check_report.md`의 ❌·⚠️를 모두 없앨 것
- 방면 표기가 config와 다르면 config의 `board_dir`/`alight_dir`를 실제 표기로 수정
- 버스 정류소 stId가 출력되면 config의 `board_stId`/`alight_stId`에 고정
- `check_out/`의 원본 JSON은 발표 자료(실데이터 증거)로 보관

## 2. 기록 수집 (2주, 계속 켜 두기)
```
python logger.py --estimate   # 하루 호출 수 → 각 사이트 '일일 트래픽'보다 작은지 확인
python logger.py --now 20     # 20분 시험 기록
python logger.py              # 본 기록 (config의 시간대에만 호출)
```
- 노트북 절전·덮개 닫힘 절전 끄기, 전원 연결. 매일 `logs/날짜.jsonl` 크기가 늘어나는지 확인
- 로그는 **원본 응답 그대로** 저장 → 나중에 계산 방식을 바꿔도 다시 평가 가능

## 3. 정확도 검증 + 그래프
```
python evaluate.py            # out/results.csv, out/summary.md
python report.py              # out/report.html (발표용 그래프, 캡처해서 슬라이드에)
```
1주 차에 한 번 미리 돌려서 '분석 제외'가 많은지 확인하세요(많으면 기록 간격·시간대 조정).

## 시험용 (인터넷 없이)
```
python mock_logs.py --days 3 --out mock_logs
python evaluate.py --logs mock_logs && python report.py --mock
```
모의 데이터 결과는 **발표에 쓰면 안 됩니다.**

## 평가 방법 요약 (발표 '평가 설계' 슬라이드용)
- 약속 시각을 10분 간격으로 가상 설정, 약속 60분 전부터 앱을 본다고 가정
- 앱은 **그 순간까지 기록된 실시간 응답만** 보고 권장 출발을 계산(미래 정보 사용 금지). 환승 이후 차량은 배차 간격으로 이어 붙이고, 구간 소요시간은 **다른 날** 기록의 중앙값
- 사용자는 앱이 '지금 출발'이라고 할 때 나감 → 이후 이동은 **같은 열차번호·차량번호가 실제로 역·정류소에 도착한 기록**으로 재생
- 비교 기준(기존 방식): 소요시간만 보고 거꾸로 계산 + 같은 여유 (설문 89.4%가 앱 밖에서 하는 방식)
- **정확도(주 지표): 출발 순간 앱이 보여 준 예상 도착 vs 실제 도착의 차이** (평균 절대 오차, ±1·2분 이내 비율, 예상보다 늦은 비율, 1시간 전 예측 오차)
- **성공 기준: 여유 지킴률(약속 여유만큼 일찍 도착)을 성공률로 사용, 목표 95%** (config의 success_target_pct)
- 지표: 정시율, 여유 지킴률, 평균 도착 여유, 지각 건·분, 너무 일찍(여유+10분 초과), 변경 알림 횟수(2분 이상 변동·2분 간격 제한), '늦어도' 시각 출발 시 정시율
- 한계: 기록 간격(지하철 30초·버스 60초)만큼 실제 도착 시각 오차, 도보·환승 시간은 가정값, 역 안 혼잡·개인 걸음 속도 미반영
