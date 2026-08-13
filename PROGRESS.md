# danta 진행사항 (2026-08-13 기준)

danta는 코스피 호재 공시 스캐너로, 매일 아침 공시 기반 TOP5 추천 종목을 산출해 GitHub Pages(`sigg3298.dothome.co.kr`)로 배포한다.

## 상태: v2 재설계 — 구현·배포·검증 완료, 운영 중

2026-08-13에 v2 재설계(스코어링 고도화 + 09:05 2차 배치 + UI 확장 + 투자전략/수익관리)를 설계부터 배포까지 전부 마쳤다. 현재는 "설계 중"이 아니라 "운영 중" 상태이며, 남은 작업은 실측 데이터를 보며 점수 기준점을 튜닝하는 것뿐이다.

## v2 확정 스펙

1. **스코어링**: 후보군은 기존과 동일하게 "전일 코스피 공시가 있었던 종목"만 대상 (코스피 전체 스캔은 API 호출량/차단 위험으로 기각).
   - 공시 35% + 모멘텀 30%(거래량배율·종가위치·이평선) + 수급 25%(전일까지 외국인/기관 연속 순매수 추세) + 테마 10%(그날 상위 테마 소속 여부) 가중합
   - 공시 점수는 DART 구조화 API(자사주취득 등) 또는 공시원문의 "최근 매출액 대비 %" 파싱으로 정규화, 실패 시 키워드 고정점수로 폴백
2. **실행 구조**: 07:00 KST(공시스캔+후보선정+스코어링) + 09:05 KST 2차 배치(신규, 09:00 시가 조회해서 매수가 반영·매도가/손절가 재계산 후 재배포)
3. **UI**: 상단 고정 탭(추천 종목/추천 기준/수익 관리)으로 구성 — 최초엔 좌측 햄버거 메뉴였으나 이후 상단 탭으로 변경(커밋 `3b56281`). 종목 상세 모달에 일봉차트(MA5/MA20 포함)+공시+모멘텀+수급+테마 신호등 표시.
4. **투자 전략**: 종목별 목표수익률 선택(1~10%+) → 매수가=09:00 시가, 매도가=매수가×(1+목표수익률), 손절가=매수가×(1-3%, 고정폭)
5. **수익 관리**: 사용자가 매수가/매도가/수량 입력 → 수익률·수익금·보유기간 계산. 닷홈 PHP+MySQL에 저장, PIN 비밀번호로 접근 보호(공개 URL이라 최소 방어 수준으로 사용자와 합의).

## 구현 완료 항목

| 단계 | 내용 | 관련 파일 |
|---|---|---|
| ① 스코어링 엔진 | 4개 신호 함수(모멘텀/수급/테마/공시) + `composite_score`/`signals` JSON 필드 | `dart_top3.py` |
| ② 09:05 2차 배치 | 09:00 시가를 매수가로 반영, 매도가/손절가(trade_plan) 계산 | `update_open_price.py`, `.github/workflows/daily_open_price.yml` |
| ③ UI 확장 | 상단 탭(추천 종목/추천 기준/수익 관리), 종목 상세 모달(신호 미터+투자전략+MA선+신호등) | `docs/index.html` (템플릿은 `dart_top3.py`의 `INDEX_HTML_TEMPLATE`) |
| ④ 수익 관리 | PIN 세션 인증 + MySQL CRUD, 수동 배포 워크플로우 | `docs/profit.html`, `docs/api/*.php`, `.github/workflows/deploy_profit_backend.yml` |

**최초 커밋**: `2dbbdfe` (①~④ 전부 완료 후 한 번에 push, [[feedback-commit-at-end]] 방침에 따름)

## 배포 검증

- `deploy_profit_backend.yml` 수동 실행 성공, 사용자가 `sigg3298.dothome.co.kr/profit.html`에서 PIN 로그인까지 직접 확인
- curl로 `profit.html` 200, 미인증 `api/trades.php` 401, `api/session.php` 정상 응답 재확인
- GitHub Actions 워크플로우 4개(Daily DART Top3, Daily Open Price Update, Deploy Profit Backend, pages-build-deployment) 정상 등록·실행 확인

## 런칭 후 튜닝 이력

- **2026-08-13 (커밋 `04c1957`)**: 영진약품 실사례에서 "수급 없음인데 50점, 핫테마 없음인데 30점" 문제 발견 → `SUPPLY_BASE_SCORE=10.0`, `THEME_BASE_SCORE=5.0`으로 하향 조정(`dart_top3.py`). 화면 표시 날짜를 공시 대상일(전일)에서 오늘(`display_date`)로 분리. 종목 상세 모달 압축(차트 150px, 신호 2열 그리드, 공시원문 기본 접기), 디자인 리프레시, 일봉차트에 MA5/MA20, 메인 카드에 4신호 신호등(50점 기준, 빨강=신호있음) 추가.
- **2026-08-13 (커밋 `8060209`)**: 신호등 색상을 빨강→초록으로 변경, 종목 상세 모달 크게 확대.

## 메인 화면 배경 리디자인 (2026-08-13, 이 문서 최초 작성 이후)

배경이 검정 단색이라 밋밋하다는 피드백으로 여러 차례 반복:

1. **1차 — 웨이브 라인**: 마스트헤드 뒤에 SVG로 상승 라인 하나만 옅게(`.bg-wave`, `position: absolute` + `mask-image` 페이드). "밀도가 낮다"는 피드백.
2. **2차 — 격자+캔들 텍스처**: 격자선 + 캔들스틱 클러스터 3개 추가. 그래도 "맘에 안 든다"는 피드백.
3. **3차 — "단타의신" 풀스크린 히어로**: `position: fixed; z-index: -1`로 화면 전체를 덮는 대형 캔들+골드/그린 라인+흩뿌린 수익률 텍스트+파티클로 확장. **실제로는 전혀 안 보이는 버그가 있었음** — `body`가 자기 stacking context를 안 만든 상태에서 자식에 음수 `z-index`를 주면, body 자신의 배경이 오히려 그 위에 그려져서 완전히 가려짐(CSS stacking context 규칙). `body { isolation: isolate; }`로 해결.
4. **4차 — 실사 사진으로 최종 교체**: 그려서 만든 배경 대신 Pexels에서 실제 트레이딩 데스크 사진(`docs/images/stock-background.jpg`, 어두운 방에 캔들차트 모니터 여러 대, 상업적 사용 무료·출처 표기 불필요)을 `background-image: cover`로 깔고, 골드/레드 톤 radial-gradient + 균일 다크 오버레이를 얹는 방식으로 최종 정착. 이전 SVG 히어로 레이어(캔들/라인/파티클/텍스트)는 전부 제거 — 사진과 중복되면 과함. `background-attachment: fixed`를 써서 `isolation: isolate` 트릭 자체가 필요 없어짐(별도 포지셔닝 레이어가 없으므로).
   - 마스트헤드/추천기준 탭에는 `.content-panel`(반투명 다크 + `backdrop-filter: blur`)을 씌워 사진 위에서도 텍스트 가독성 확보.
   - 이후 "더 밝게"라는 피드백으로 오버레이 어둡기를 0.55→0.4로, 글로우를 0.22→0.28로 조정.

## 테마 신호 클릭 가능 링크 (2026-08-13)

추천 종목 중 테마 신호가 있는 종목(예: 두산퓨얼셀, LS)을 모달에서 봐도 테마 관련 내용을 볼 방법이 없다는 피드백 → 공시 원문 링크와 같은 패턴으로 해결:
- `fetch_hot_theme_members()`가 테마명 → 네이버 테마 상세페이지 URL 맵도 함께 반환하도록 확장 (테마 번호는 목록 페이지 크롤링 시점에 이미 알고 있어서 추가 호출 없이 가능).
- `compute_theme_score()`/`evaluate_candidate()`에 `theme_urls`를 threading, `signals.theme.theme_links`에 `{name, url}` 배열로 저장.
- 프론트(`renderSignalRow`)에서 테마 요약을 클릭 가능한 `<a target="_blank">`로 렌더링 (URL 없는 옛 데이터는 텍스트로 자연 폴백).
- 앞으로 매일 07:00 자동 스캔부터 계속 자동으로 반영됨. 오늘자(2026-08-13) 이미 나온 데이터에도 라이브로 재조회해서 즉시 패치함.

## "추천종목검증" 탭 추가 (2026-08-13)

과거에 추천했던 종목이 실제로 얼마나 정확했는지 확인할 방법이 없다는 요청으로, "추천 기준" 옆에 "추천종목검증" 탭을 신설:
- **신규 스크립트 `build_verification_data.py`**: `docs/data/{날짜}.json`에 쌓인 과거 추천 이력(오늘 제외)을 모아 종목코드별로 `dart_top3.py`의 `fetch_daily_ohlc()`를 다시 라이브로 호출해서 전일종가/당일고가/현재가를 계산 → `docs/data/verification.json`으로 저장. 저장된 `ohlc`를 그대로 못 쓴 이유: `fetch_daily_ohlc()`는 "호출 시점 기준 최근 60일"을 가져오는 방식이라 07:00(장 시작 전) 스냅샷엔 그날 자신의 고가가 아직 없고, 이후 그 파일이 다시 갱신되지도 않음.
- `.github/workflows/daily.yml`에 `python dart_top3.py` 다음 스텝으로 추가 — 새 워크플로우 파일 없이 매일 자동 갱신됨.
- **색상 규칙(사용자 확정)**: 이 탭만 예외적으로 글로벌 관례(상승=초록/하락=빨강)를 씀 — 메인 카드 등락률(국내 관례, 상승=빨강)과 의도적으로 다름. `.verify-up`/`.verify-down`/`.verify-flat` 클래스로 분리.
- **주의할 점**: 색상/화살표는 "전일종가 대비 당일고가"(추천일에 실제로 팝업했는지) 기준이고, 옆에 표시되는 등락률/등락폭 숫자는 "전일종가 대비 현재가"(지금 들고 있었다면 손익) 기준 — 둘이 다른 지표라서 마이너스 등락률인데 초록 화살표인 경우가 생길 수 있음(사용자 스펙 그대로).

## 다음에 열어볼 것 (튜닝 대상)

- `dart_top3.py`의 `compute_supply_score`/`compute_theme_score` 근처에 있는 `SUPPLY_BASE_SCORE`, `THEME_BASE_SCORE`, 신호등 임계값(50점)은 하루치 실측만으로 정한 값 — 며칠 더 지켜본 뒤 재조정 가능성 높음.

## 겪었던 함정 / 재사용 가능한 교훈

- **워크플로우는 push 전엔 존재하지 않음**: 새 `.yml` 워크플로우 파일을 로컬에만 두고 "커밋은 나중에" 미루면, GitHub Actions 탭에 아예 나타나지 않아 수동 실행이 불가능하다. 실행이 필요한 시점이 오면 먼저 push부터 해야 한다.
- **네이버 시세 페이지 인코딩 함정**: `finance.naver.com` 계열 페이지는 `<meta charset="utf-8">`로 표기돼도 실제 바이트는 EUC-KR인 경우가 있었다(테마 목록 페이지). `errors="replace"`로 디코딩하면 예외 없이 조용히 깨져서, 실제 파일로 저장 후 Read 도구로 한글 확인하는 습관이 필요. 터미널(git bash) 직접 출력은 콘솔 코드페이지 때문에 멀쩡한 UTF-8도 깨져 보일 수 있어 오탐 주의(여러 번 발생: 테마 페이지, `python -m json.tool` 파이프, 병합 후 확인 시 등 — 반드시 파일로 저장해서 Read 도구로 확인할 것).
- **CSS stacking context 함정**: `position: fixed` 자식에 음수 `z-index`를 줘서 콘텐츠 뒤로 보내려 했는데, 부모(`body`)가 자기 stacking context를 안 만들면 부모 자신의 배경이 오히려 그 자식 위에 그려져서 완전히 가려질 수 있다. `isolation: isolate`(또는 `z-index`+`position`)로 부모에 stacking context를 만들어주면 해결.
- **자동 배치와의 병합 패턴**: `daily.yml`/`daily_open_price.yml`이 수시로(예약 또는 수동 실행으로) `docs/data/*.json`+`docs/index.html`+`docs/.ftp-deploy-sync-state.json`을 커밋·푸시하기 때문에, 로컬에서 배경/기능 작업을 커밋하고 푸시하려 하면 거의 매번 원격에 새 커밋이 먼저 올라와 있어 병합이 필요하다. 패턴: `docs/index.html`은 항상 `dart_top3.py`의 `INDEX_HTML_TEMPLATE`에서 재생성해서 해결(소스 오브 트루스가 dart_top3.py이므로 안전), `docs/data/*.json`은 원격(최신 실데이터)을 채택(`git checkout --theirs`)한 뒤 그 위에 내 쪽 데이터 패치(예: 테마 링크)를 다시 적용.
- **테스트 방법론** (브라우저 없는 환경에서 검증): (1) HTML 태그 균형 체크, (2) Node `--check`로 JS 문법 검사, (3) jsdom + Node 24 전역 fetch로 실제 DOM 클릭/입력 이벤트 실행, (4) PHP는 winget으로 로컬 설치(닷홈과 동일 8.4) 후 `php -S` 내장 서버 + SQLite(운영은 MySQL)로 실제 CRUD 끝까지 실행. **추가로 이번엔 Playwright+Chromium을 `npm install --no-save playwright` + `npx playwright install chromium`으로 로컬 설치해서 실제 스크린샷까지 찍어 눈으로 확인하는 방법도 씀** — `docs/` 폴더를 `python -m http.server`로 띄운 로컬 서버를 대상으로 함. 확인 후엔 `_screenshot_tmp.mjs`와 `node_modules`를 반드시 정리(git status에 안 걸리도록).
