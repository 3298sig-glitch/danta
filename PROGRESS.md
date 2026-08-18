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
- **(수정, 같은 날) 기준 통일**: 처음엔 색상은 "전일종가 대비 당일고가", 등락률/등락폭 숫자는 "전일종가 대비 현재가" 기준으로 서로 다르게 만들었는데, 마이너스 등락률인데 초록 화살표로 보이는 경우가 있어 혼란스럽다는 피드백으로 **색상·등락률·등락폭 전부 "전일종가 대비 당일고가" 기준으로 통일**함. 등락 금액엔 "원" 단위도 추가.
- **겪었던 함정**: 외부(다른 AI 등)에서 작성해 붙여넣은 "버그 3개" 프롬프트를 받았는데, 실제로 로컬+라이브 데이터를 직접 대조해보니 코드 버그가 아니라 표본이 작아서(2일치 10종목) 우연히 전부 상승으로 나온 것이었고, "계산이 틀렸다"는 지적도 실은 원래 스펙(현재가 기준)대로 정확히 계산된 것이었음. **바로 "고쳐줘"를 따르지 않고 실제 코드·실제 배포된 라이브 데이터를 먼저 대조해서 진짜 버그인지 확인하는 습관이 유효했음** — 이번엔 실제로는 버그가 아니라 스펙을 바꾸고 싶다는 요청이었던 것으로 드러남.

## PIN 로그인 안 되던 문제 — 원인은 FTP 배포 구조 자체였음 (2026-08-13)

"내 수익 관리"에서 정확한 PIN(3298)을 넣어도 "PIN이 틀립니다"로 뜨는 문제. `login.php`의 비교 로직(`hash_equals`) 자체는 정상이었고, **진짜 원인은 `docs/api/config.php`가 라이브 서버에 아예 없었던 것**(curl로 직접 확인: `require_once` Fatal Error).
- `config.php`는 `.gitignore`돼 있고 `deploy_profit_backend.yml`이 배포 시점에만 GitHub Secrets로 생성함.
- 그런데 `daily.yml`/`daily_open_price.yml`도 매번 `FTP-Deploy-Action`으로 `docs/` 전체를 서버와 동기화하는데, 이 두 워크플로우 체크아웃엔 `config.php`가 없어서(git에 없으니까) **이 action의 기본 sync/mirror 동작(로컬에 없는 원격 파일 삭제)에 의해 자동 실행될 때마다 `config.php`가 삭제됨**. `deploy_profit_backend.yml`을 수동 실행해서 살려놔도 다음 07:00/09:05 자동 배치가 바로 다시 지워버리는 구조였음.
- **수정**: `daily.yml`/`daily_open_price.yml`의 `FTP-Deploy-Action` 스텝에 `exclude: api/config.php`(+ 기본 제외 3줄 재명시, `exclude`를 쓰면 기본값이 대체되는 방식이라) 추가. `deploy_profit_backend.yml`은 그대로 둠.
- **후속 1 (2026-08-13)**: 로그인 세션이 쿠키로 유지돼서 재접속 시 PIN 없이 바로 들어가지는 걸 "버그"로 오인함 — 실제론 정상적인 세션 유지 동작(쿠키 없는 상태에선 여전히 PIN 요구됨, curl로 검증). 그래도 매번 PIN을 요구하길 원해서 `profit.html`의 `init()`이 페이지 로드마다 `api/logout.php`를 호출해 세션을 강제 종료하도록 변경(`checkSession()` 제거).
- **후속 2, 진짜 원인 (2026-08-14)**: config.php는 살아났는데 "등록 실패"/"목록이 안 쌓임" 문제가 또 나옴 → `trades.php`가 `PDOException: Host 'xxx' is not allowed to connect`로 전부 실패하고 있었음. **원인은 `DB_HOST` 시크릿이 `localhost`가 아니었던 것** — 닷홈 무료호스팅은 원격 DB 접속 UI 자체가 없고 `localhost` 소켓 접속만 지원함([[reference_danta_dothome_hosting]] 참고). `DB_HOST`를 `localhost`로 재등록 + `deploy_profit_backend.yml` 재실행으로 완전히 해결 — curl로 로그인/조회/한글 종목 등록/목록 누적까지 전부 재검증 완료.

## "추천 기준" 탭 위치 이동 + 카드형 디자인 (2026-08-13)

"추천 기준" 탭을 맨 왼쪽으로 이동(기본 활성 탭은 "추천 종목 상세" 그대로 유지). 패널 내용을 `<ul><li>` 나열에서 카드 4개(공시/모멘텀/수급/테마) 그리드로 리디자인 — 카드마다 기준명+가중치%(색상 포인트: 빨강/골드/파랑/초록, 기존 팔레트 재사용)를 크게, 설명은 작게. 배경 사진 위에서도 읽히도록 각 카드에 `.content-panel`(반투명 다크+blur) 재사용. 전문용어(이동평균선/외국인·기관/핫테마)에 괄호 풀이 추가.

## 다음에 열어볼 것 (튜닝 대상)

- `dart_top3.py`의 `compute_supply_score`/`compute_theme_score` 근처에 있는 `SUPPLY_BASE_SCORE`, `THEME_BASE_SCORE`, 신호등 임계값(50점)은 하루치 실측만으로 정한 값 — 며칠 더 지켜본 뒤 재조정 가능성 높음.

## KST 타임존 버그로 3일간(8/16~8/18) 업데이트 안 되던 문제 (2026-08-18)

"오늘 아침 업데이트가 안 된다"는 신고를 5단계로 조사 — GitHub Actions는 매일 정상 실행(초록)됐고, `dates.json`도 2026-08-15에서 멈춰있었음. **원인은 `dart_top3.py`가 `date.today()`(naive, 서버 타임존 기준)를 쓰는데 GitHub Actions 러너는 UTC로 돌아서**, KST 07:00(=UTC 전날 22:00) 크론이 실행되는 시점의 `date.today()`가 실제 KST 날짜보다 항상 하루 이전을 가리켰던 것. 그 결과 "전일 공시 스캔 대상"도 하루 더 밀려서, 화요일(8/18) 실행분이 월요일(정상 거래일, 실제 공시 있었을 것)이 아니라 일요일(휴장, 공시 없음)을 스캔해 조용히 빈 결과로 끝남.
- **수정**: `dart_top3.py`에 `kst_today()` 헬퍼 신설(UTC+9 고정 오프셋 — 한국은 서머타임이 없어서 `zoneinfo` 대신 이 방식을 씀, Windows 로컬처럼 IANA tzdata가 없는 환경에서도 추가 설치 없이 동작). `get_target_date()`/`display_date`/OHLC 폴백 경로 전부 이걸로 교체. **주말 보정 로직도 추가**: 스캔 대상이 토/일이면 직전 금요일까지 당김(코드에 원래 있던 "필요하면 확장하자" 주석을 실제 구현). `update_open_price.py`/`build_verification_data.py`도 같은 기준으로 통일.
- **주의**: 공휴일(설날/추석 등 평일 휴장일)까지는 보정 안 함 — 그런 날은 여전히 공시 없이 조용히 스킵될 수 있음, 다음에 비슷한 신고 들어오면 먼저 그 날짜가 공휴일인지부터 확인할 것.
- **후속 (2026-08-18, 바로 같은 날)**: 실제로 타임존 버그 수정 직후 재실행했더니 8/17(월)이 광복절 대체공휴일이라 또 빔 — "공휴일까지는 보정 안 함"이라던 위 한계가 바로 실전에서 걸림. `get_target_date()`에 `holidays` 파이썬 패키지(`holidays.SouthKorea()`) 기반 공휴일 판정을 추가해서, 주말이거나 한국 공휴일(대체공휴일 포함)이면 영업일을 찾을 때까지 계속 거슬러 올라가도록 함(설/추석 연휴 등 연쇄 휴장일도 while 루프로 자동 처리). 공공데이터포털 API(네트워크 호출+API키 필요) 대신 오프라인 계산 라이브러리를 택한 이유는 이 프로젝트의 "외부 호출 최소화" 방침과 맞아서. `daily.yml`/`daily_open_price.yml`에 `pip install holidays` 추가.

## 종목 상세 3종 기능 추가 (2026-08-18)

- **매수가 직접 입력**: 투자전략 영역에 "실제 매수가 직접 입력" 체크박스+입력창 추가. 체크하면 손절가(-3%)/목표 매도가를 `update_open_price.py`의 `build_trade_plan()`과 같은 공식으로 **클라이언트에서 재계산**(서버 재호출 없음). "추천가"/"내 매수가" 태그로 기준을 구분해서 표시.
- **"내 수익 관리" 연동**: 모달에 "내 수익 관리에 등록 →" 버튼 추가 — 클릭 시 종목명/종목코드/(직접입력 있으면 그 값의) 매수가를 `localStorage`(`dantaPendingTrade`)에 저장하고 `profit.html`로 이동, 그쪽 `applyPendingTrade()`가 폼에 채워넣고 즉시 삭제. 두 페이지가 완전히 별도 정적 문서라 세션/쿼리스트링보다 로컬스토리지가 훨씬 간단해서 이 방식을 택함.
- **3신호 이상 골드 별**: 공시/수급/모멘텀/테마 중 3개 이상 초록불(신호 있음)인 종목은 카드 리스트와 상세 모달 타이틀 양쪽에 `★`(골드, 은은한 glow) 표시 — `countOnSignals()`/`starBadge()` 헬퍼로 재사용.
- **차트 Y축 금액 표시**: `LightweightCharts`의 `localization.priceFormatter`로 가격축 라벨을 "75,000원"(천단위 콤마) 형식으로 변경. 차트 높이는 150px가 아니라 이미 320px로 확대돼 있어서(이전 세션 작업) 눈금이 심하게 빽빽하진 않음 — 라이브러리 자체엔 "정확히 N개 눈금만" 강제하는 옵션은 없어서 폰트만 살짝(10px) 줄여 여유를 더 줌.
- Playwright로 세 기능 전부 스크린샷+수치 검증(재계산 값이 공식과 정확히 일치, localStorage 저장→profit.html 폼 채움까지 확인).

## "매수가 직접입력 추가하니 투자전략 영역이 사라졌다" — 실은 무관한 기존 버그 (2026-08-18)

바로 위 기능 추가 직후 "매수가/매도가/손절가 표시 영역 자체가 사라졌다"는 신고가 들어와서 커밋 diff부터 재확인했지만 구조적 문제 없었고, 로컬 재현으로도 정상 렌더링됨을 재확인함(내 UI 커밋은 원인이 아니었음). 라이브 데이터를 직접 까보니 오늘자 JSON에 `trade_plan` 키 자체가 없었음(`null`도 아니고 키 부재) — `renderTradePlan()`의 `if (tp === undefined) return ''`(구버전 스키마 호환 분기)에 걸려 아무 것도 안 보인 것.
- **진짜 원인**: `save_date_json()`이 항상 파일을 통째로 덮어씀. `dart_top3.py`는 애초에 `trade_plan`을 안 만들고, `update_open_price.py`(09:05 배치)가 나중에 별도로 추가하는 구조인데, 같은 날 `dart_top3.py`를 재실행하면(타임존/공휴일 로직 테스트하려고 우리가 여러 번 수동 재실행했음) 시가 반영으로 추가됐던 `trade_plan`이 매번 조용히 지워짐. 이번 기능 추가와 시점이 겹쳐서 원인처럼 보였을 뿐 실제로는 완전히 별개의, 훨씬 이전부터 있던 파이프라인 버그.
- **수정**: `save_date_json()`이 파일을 쓰기 전에 기존 파일이 있으면 읽어서, 종목코드가 일치하는 항목의 `trade_plan`을 새 payload에 이어붙이도록 함. 로컬에서 "기존 파일 있음 → 재실행" 시나리오를 단위 테스트로 재현해서 보존되는 것 확인.
- **교훈**: 최근에 내가 직접 UI를 고친 직후에 문제가 생기면 내 변경부터 의심하기 쉽지만, 실제로는 그 시점에 우리가 반복해서 수동 트리거했던 다른 워크플로우가 원인인 경우도 있음 — 라이브 데이터/커밋 이력을 먼저 대조하는 습관([[feedback_bugfix_workflow]])이 이번에도 유효했음.

## "추천 기준 > 공시" 카드에 호재 키워드 목록 표시 (2026-08-18)

`POSITIVE_KEYWORDS`(dart_top3.py) 6개(자기주식취득/단일판매·공급계약체결/무상증자결정/특허권/유형자산 양수/자산재평가)를 "추천 기준" 공시 카드에 쉬운 설명과 함께 나열. **하드코딩 이중관리를 피하려고 코드가 화면을 직접 만들게 함**:
- `DISCLOSURE_KEYWORD_GLOSSARY` 딕셔너리(키워드→쉬운 설명)를 `POSITIVE_KEYWORDS` 바로 옆에 추가, 설명 없는 키워드는 이름만 표시되는 폴백 처리.
- `render_disclosure_keywords_html()`이 `POSITIVE_KEYWORDS`를 순회해 `<ul class="kw-list">` HTML을 생성.
- 템플릿엔 `<!--DISCLOSURE_KEYWORDS-->` 플레이스홀더만 심어두고, `save_index_html()`이 `docs/index.html`을 쓰기 직전에 실제 목록으로 치환(단순 문자열 치환이라 템플릿 안의 다른 CSS/JS `{}` 문법과 충돌 없음 — `.format()`은 안 씀).
- **앞으로 `POSITIVE_KEYWORDS`에 키워드를 추가/삭제하면 `DISCLOSURE_KEYWORD_GLOSSARY`도 같이 업데이트할 것**(코드에도 주석으로 남겨둠).

## 배경 사진 위 회색 글자 명암비 개선 (2026-08-18)

"추천 기준 > 공시" 카드 설명이 배경 사진 위에서 잘 안 보인다는 피드백 → 실제 WCAG 명암비를 계산해서 확인함(python으로 relative luminance 직접 계산). `.content-panel`(불투명도 0.72) 위에 배경 사진의 밝은 글로우 영역이 겹치면 기존 `--text-muted`(#7C8898)의 실제 명암비가 **3.51:1**까지 떨어져 WCAG 최소 기준(4.5:1) 미달이었음 — 어두운 사진 영역에서도 4.98:1로 여유 없었음.
- **수정**: `--text-muted`를 `#7C8898` → `#B7C0CC`로, `.content-panel` 배경 불투명도를 `0.72` → `0.85`로 조정. 같은 최악 케이스(밝은 글로우 위)로 재계산한 결과 **8.66:1**로 안전한 수준 확보(WCAG AAA 7:1도 통과).
- `--text-muted`는 CSS 변수 하나라 카드 서브텍스트/신호 요약/디스클레이머/차트 범례 등 **사이트 전체 27곳**에 한 번에 적용됨 — 개별로 안 고쳐도 됨.
- `profit.html`은 별도 문서라 자기만의 `--text-muted`가 있고, 배경이 사진이 아니라 단색이라 이번 문제와 무관해서 이번엔 안 건드림(원하면 톤 통일 가능).

## 겪었던 함정 / 재사용 가능한 교훈

- **워크플로우는 push 전엔 존재하지 않음**: 새 `.yml` 워크플로우 파일을 로컬에만 두고 "커밋은 나중에" 미루면, GitHub Actions 탭에 아예 나타나지 않아 수동 실행이 불가능하다. 실행이 필요한 시점이 오면 먼저 push부터 해야 한다.
- **네이버 시세 페이지 인코딩 함정**: `finance.naver.com` 계열 페이지는 `<meta charset="utf-8">`로 표기돼도 실제 바이트는 EUC-KR인 경우가 있었다(테마 목록 페이지). `errors="replace"`로 디코딩하면 예외 없이 조용히 깨져서, 실제 파일로 저장 후 Read 도구로 한글 확인하는 습관이 필요. 터미널(git bash) 직접 출력은 콘솔 코드페이지 때문에 멀쩡한 UTF-8도 깨져 보일 수 있어 오탐 주의(여러 번 발생: 테마 페이지, `python -m json.tool` 파이프, 병합 후 확인 시 등 — 반드시 파일로 저장해서 Read 도구로 확인할 것).
- **CSS stacking context 함정**: `position: fixed` 자식에 음수 `z-index`를 줘서 콘텐츠 뒤로 보내려 했는데, 부모(`body`)가 자기 stacking context를 안 만들면 부모 자신의 배경이 오히려 그 자식 위에 그려져서 완전히 가려질 수 있다. `isolation: isolate`(또는 `z-index`+`position`)로 부모에 stacking context를 만들어주면 해결.
- **자동 배치와의 병합 패턴**: `daily.yml`/`daily_open_price.yml`이 수시로(예약 또는 수동 실행으로) `docs/data/*.json`+`docs/index.html`+`docs/.ftp-deploy-sync-state.json`을 커밋·푸시하기 때문에, 로컬에서 배경/기능 작업을 커밋하고 푸시하려 하면 거의 매번 원격에 새 커밋이 먼저 올라와 있어 병합이 필요하다. 패턴: `docs/index.html`은 항상 `dart_top3.py`의 `INDEX_HTML_TEMPLATE`에서 재생성해서 해결(소스 오브 트루스가 dart_top3.py이므로 안전), `docs/data/*.json`은 원격(최신 실데이터)을 채택(`git checkout --theirs`)한 뒤 그 위에 내 쪽 데이터 패치(예: 테마 링크)를 다시 적용.
- **테스트 방법론** (브라우저 없는 환경에서 검증): (1) HTML 태그 균형 체크, (2) Node `--check`로 JS 문법 검사, (3) jsdom + Node 24 전역 fetch로 실제 DOM 클릭/입력 이벤트 실행, (4) PHP는 winget으로 로컬 설치(닷홈과 동일 8.4) 후 `php -S` 내장 서버 + SQLite(운영은 MySQL)로 실제 CRUD 끝까지 실행. **추가로 이번엔 Playwright+Chromium을 `npm install --no-save playwright` + `npx playwright install chromium`으로 로컬 설치해서 실제 스크린샷까지 찍어 눈으로 확인하는 방법도 씀** — `docs/` 폴더를 `python -m http.server`로 띄운 로컬 서버를 대상으로 함. 확인 후엔 `_screenshot_tmp.mjs`와 `node_modules`를 반드시 정리(git status에 안 걸리도록).
- **GitHub Actions는 UTC, KST 새벽 배치는 항상 의심할 것**: `date.today()`/`datetime.now()`처럼 타임존 없는 날짜 계산은 로컬(한국) 개발자 눈엔 멀쩡해 보여도 UTC로 도는 CI에선 자정 근처 실행 시 하루 밀린다. KST 처리 시 `zoneinfo.ZoneInfo("Asia/Seoul")`은 Windows 로컬처럼 IANA tzdata가 없는 환경에서 `ZoneInfoNotFoundError`로 바로 깨짐 — 한국은 서머타임이 없으므로 `timezone(timedelta(hours=9))` 고정 오프셋을 쓰는 게 더 안전하고 이식성도 좋음.
