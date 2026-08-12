"""
dart_top3.py
------------
매일 실행 시, '전일(어제)' 코스피(KOSPI) 상장사 전체 공시를 스캔해서
'호재'로 분류될 만한 공시 점수가 가장 높은 상위 5종목을 골라
docs/data/{날짜}.json 으로 저장하는 스크립트.
(예: 오늘 아침에 실행하면 어제 하루 동안 나온 공시를 대상으로 집계)

docs/index.html 은 이 JSON들을 브라우저에서 자바스크립트로 불러와 화면에
그려주는 정적 대시보드다 (날짜 선택, 종목 클릭 시 상세 공시 + 최근 일봉 차트 표시).
매일 실행할 때마다 그날 날짜의 JSON 파일이 하나씩 추가되며, 자연스럽게
과거 기록도 함께 쌓인다.

저장소: https://github.com/3298sig-glitch/danta

사용법:
    1) https://opendart.fss.or.kr 에서 무료로 API 키 발급
    2) pip install requests
    3) python dart_top3.py 실행 -> 화면에 뜨는 안내에 따라 API 키 입력
       (입력한 키는 화면에 표시되지 않습니다. 비밀번호 입력과 동일한 방식입니다.)

주의:
    - 이 스크립트는 '공시 제목(report_nm)'에 특정 키워드가 포함되는지만으로 1차 점수를 매기는
      단순 규칙 기반 버전입니다. 실제 계약 금액, 시가총액 대비 비율 등 정교한 판단은
      공시 원문(rcept_no로 상세 문서 조회)을 파싱해야 하며, 여기서는 확장 포인트로 남겨둡니다.
    - 일봉 차트는 네이버 금융의 공개 차트 데이터를 사용한다 (비공식 엔드포인트,
      키가 필요 없지만 네이버 쪽 사정으로 언제든 바뀌거나 막힐 수 있음).
    - 개인 참고용 도구이며, 투자 판단의 근거로 그대로 사용하지 마세요.
"""

import getpass
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Dict, Any, Optional

import requests

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
NAVER_CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
TOP_N = 5
DOCS_DIR = "docs"
DATA_DIR = os.path.join(DOCS_DIR, "data")


def get_api_key() -> str:
    """API 키를 가져온다.

    - 환경변수 DART_API_KEY가 설정되어 있으면 그걸 그대로 사용한다
      (GitHub Actions 등 자동화 환경에서 쓰는 방식, 화면 입력 불필요).
    - 없으면 화면에서 비밀번호처럼 직접 입력받는다 (로컬 실행용).
    """
    env_key = os.environ.get("DART_API_KEY", "").strip()
    if env_key:
        return env_key

    key = getpass.getpass("DART API 키를 입력하세요 (화면엔 안 보입니다): ").strip()
    if not key:
        print("API 키를 입력하지 않았습니다.", file=sys.stderr)
        sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# 1. 공시 제목 키워드 -> 점수 매핑 (필요에 따라 자유롭게 추가/조정하세요)
# ---------------------------------------------------------------------------
POSITIVE_KEYWORDS: Dict[str, int] = {
    "자기주식취득": 3,          # 자사주 매입 (통상 호재)
    "단일판매ㆍ공급계약체결": 2,  # 대규모 수주 계약
    "무상증자결정": 2,
    "특허권": 1,
    "유형자산 양수": 1,
    "자산재평가": 1,
}

NEGATIVE_KEYWORDS: Dict[str, int] = {
    "유상증자결정": -3,
    "전환사채권발행결정": -2,   # CB 발행은 대체로 단기 악재로 해석되는 경우가 많음
    "감사의견거절": -5,
    "관리종목지정": -5,
    "상장폐지": -5,
    "횡령ㆍ배임": -5,
    "단일판매ㆍ공급계약해지": -2,
    "자기주식취득신탁계약해지": -1,   # 자사주 '매입 중단' 성격 -> 취득(+3)과 반대 의미
    "자기주식처분": -1,               # 보유 자사주를 파는 것 -> 취득과 반대
}

ALL_KEYWORDS: Dict[str, int] = {**POSITIVE_KEYWORDS, **NEGATIVE_KEYWORDS}


def get_target_date() -> date:
    """조회 대상 날짜(전일)를 반환한다.

    주의: 주말/공휴일 다음 영업일에 실행하면 '전일'이 휴장일이라
    공시가 없을 수 있다. 필요하면 최근 영업일을 찾도록 확장하자
    (예: 토/일이면 직전 금요일로 보정하는 로직 추가).
    """
    return date.today() - timedelta(days=1)


def fetch_kospi_disclosures(api_key: str, target_date: date) -> List[Dict[str, Any]]:
    """지정한 날짜(전일)의 코스피(corp_cls=Y) 전체 공시 목록을 페이지네이션하며 모두 가져온다."""
    target_de = target_date.strftime("%Y%m%d")
    all_items: List[Dict[str, Any]] = []
    page_no = 1
    page_count = 100  # DART API 최대 100

    while True:
        params = {
            "crtfc_key": api_key,
            "bgn_de": target_de,
            "end_de": target_de,
            "corp_cls": "Y",       # Y=유가증권시장(코스피), K=코스닥, N=코넥스
            "page_no": page_no,
            "page_count": page_count,
        }
        resp = requests.get(DART_LIST_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "000":
            # 013 = 해당 조건에 데이터 없음 (당일 공시가 아직 없거나 없는 경우)
            if data.get("status") == "013":
                break
            print(f"DART API 오류: {data.get('status')} {data.get('message')}", file=sys.stderr)
            break

        items = data.get("list", [])
        all_items.extend(items)

        total_page = data.get("total_page", 1)
        if page_no >= total_page:
            break
        page_no += 1

    return all_items


def get_matched_keywords(report_nm: str) -> List[str]:
    """공시 제목에 매칭되는 키워드 중, 더 짧고 포괄적인 키워드는 제외하고
    가장 구체적인(긴) 키워드만 남긴다.

    예: '자기주식취득신탁계약해지결정'은 '자기주식취득'(짧음, +3)과
    '자기주식취득신탁계약해지'(길고 구체적, -1) 둘 다에 매칭되는데,
    이 경우 더 구체적인 후자만 적용해야 '취득'과 '취득 해지'가
    똑같이 호재로 오분류되는 걸 막을 수 있다.
    """
    matched = [k for k in ALL_KEYWORDS if k in report_nm]
    specific = [
        k for k in matched
        if not any(k != other and k in other for other in matched)
    ]
    return specific


def score_disclosure(report_nm: str) -> int:
    """공시 제목에 포함된 키워드를 기준으로 점수를 계산한다."""
    return sum(ALL_KEYWORDS[k] for k in get_matched_keywords(report_nm))


def fetch_daily_ohlc(stock_code: str, count: int = 60) -> List[Dict[str, Any]]:
    """네이버 금융 공개 차트 데이터로 최근 N일 일봉(OHLC)을 가져온다.

    실패하거나 종목코드가 없으면 빈 리스트를 반환한다 (차트가 없어도
    나머지 기능은 정상 동작해야 하므로, 여기서 예외를 밖으로 던지지 않는다).
    """
    if not stock_code:
        return []
    try:
        params = {
            "symbol": stock_code,
            "timeframe": "day",
            "count": count,
            "requestType": 0,
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Referer": "https://finance.naver.com/",
        }
        resp = requests.get(NAVER_CHART_URL, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        candles = []
        for item in root.iter("item"):
            raw = item.get("data", "")
            parts = raw.split("|")
            if len(parts) < 6:
                continue
            d, o, h, l, c, v = parts[:6]
            if not (d and o and h and l and c):
                continue
            candles.append({
                "date": d,           # YYYYMMDD
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": int(v) if v else 0,
            })
        return candles
    except Exception as e:  # noqa: BLE001 - 차트는 부가 기능이라 실패해도 전체를 막지 않는다
        print(f"  (참고) {stock_code} 일봉 차트를 가져오지 못했습니다: {e}", file=sys.stderr)
        return []


def build_date_json(target_date: date, ranked: List[tuple], stock_items: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """웹페이지가 읽을 하루치 데이터 구조를 만든다. 이 과정에서 상위 종목의 일봉 차트도 함께 가져온다."""
    ranked_payload = []
    for corp_name, score in ranked:
        items = stock_items[corp_name]
        stock_code = items[0].get("stock_code", "").strip() if items else ""
        print(f"  {corp_name}({stock_code}) 일봉 차트 조회 중...")
        ohlc = fetch_daily_ohlc(stock_code)

        ranked_payload.append({
            "corp_name": corp_name,
            "stock_code": stock_code,
            "score": score,
            "items": [
                {
                    "report_nm": it["report_nm"],
                    "score": it["score"],
                    "rcept_no": it["rcept_no"],
                    "flr_nm": it.get("flr_nm", ""),
                    "rcept_dt": it.get("rcept_dt", ""),
                    "rm": it.get("rm", ""),
                }
                for it in items
            ],
            "ohlc": ohlc,
        })

    return {
        "date": target_date.isoformat(),
        "ranked": ranked_payload,
    }


def save_date_json(target_date: date, payload: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{target_date.isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 날짜 인덱스(dates.json) 갱신 -> 웹페이지의 날짜 선택 드롭다운이 이걸 읽는다
    dates_path = os.path.join(DATA_DIR, "dates.json")
    dates: List[str] = []
    if os.path.exists(dates_path):
        try:
            with open(dates_path, "r", encoding="utf-8") as f:
                dates = json.load(f)
        except Exception:
            dates = []
    if target_date.isoformat() not in dates:
        dates.append(target_date.isoformat())
    dates = sorted(set(dates), reverse=True)
    with open(dates_path, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=2)


def save_index_html() -> None:
    """docs/index.html은 데이터에 의존하지 않는 고정 템플릿이라 매번 덮어써도 안전하다."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_HTML_TEMPLATE)


INDEX_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>코스피 호재 스캐너</title>
<link rel="preconnect" href="https://cdn.jsdelivr.net">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/jetbrains-mono/2.304/web/woff2/JetBrainsMono.min.css">
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg: #0B0F14;
    --surface: #131A22;
    --surface-hover: #1A2229;
    --line: #232D38;
    --text: #E8ECF1;
    --text-muted: #7C8898;
    --rise: #F0454F;
    --fall: #2F8FFF;
    --mono: 'JetBrains Mono', ui-monospace, monospace;
    --sans: 'Pretendard Variable', 'Pretendard', -apple-system, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; text-decoration: none; }
  a:focus-visible, button:focus-visible, select:focus-visible {
    outline: 2px solid var(--fall);
    outline-offset: 2px;
  }
  button { font-family: inherit; cursor: pointer; }

  .marquee {
    border-bottom: 1px solid var(--line);
    background: var(--surface);
    overflow: hidden;
    white-space: nowrap;
    padding: 10px 0;
  }
  .marquee-track {
    display: inline-block;
    padding-left: 100%;
    font-family: var(--mono);
    font-size: 13px;
    color: var(--text-muted);
    letter-spacing: 0.02em;
    animation: scroll-left 28s linear infinite;
  }
  @keyframes scroll-left {
    from { transform: translateX(0); }
    to { transform: translateX(-100%); }
  }
  @media (prefers-reduced-motion: reduce) {
    .marquee-track { animation: none; padding-left: 24px; }
  }

  main {
    max-width: 640px;
    margin: 0 auto;
    padding: 32px 20px 48px;
  }
  .masthead {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 28px;
    flex-wrap: wrap;
  }
  .masthead .eyebrow {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--rise);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .masthead h1 {
    margin: 6px 0 4px;
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  .masthead .sub {
    font-size: 13px;
    color: var(--text-muted);
    font-family: var(--mono);
  }
  #date-select {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 8px 12px;
    font-family: var(--mono);
    font-size: 13px;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    margin-bottom: 14px;
    overflow: hidden;
    width: 100%;
    text-align: left;
    color: inherit;
    transition: border-color 0.12s ease;
  }
  .card:hover { border-color: #3A4656; }
  .card-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 16px 18px;
  }
  .rank {
    font-family: var(--mono);
    font-size: 13px;
    color: var(--text-muted);
    flex-shrink: 0;
  }
  .corp {
    flex: 1;
    margin: 0;
    font-size: 17px;
    font-weight: 600;
  }
  .delta {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
  }
  .delta-total { font-size: 15px; }
  .delta-up { color: var(--rise); }
  .delta-down { color: var(--fall); }

  .empty, .loading {
    color: var(--text-muted);
    font-size: 14px;
    padding: 24px 0;
  }

  .disclaimer {
    margin-top: 24px;
    font-size: 12px;
    color: var(--text-muted);
    line-height: 1.6;
  }

  /* 상세 모달 */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    display: none;
    align-items: flex-end;
    justify-content: center;
    z-index: 10;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 16px 16px 0 0;
    width: 100%;
    max-width: 640px;
    max-height: 88vh;
    overflow-y: auto;
    padding: 20px;
  }
  @media (min-width: 640px) {
    .modal-backdrop { align-items: center; }
    .modal { border-radius: 16px; margin: 20px; }
  }
  .modal-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .modal-head h2 { margin: 0; font-size: 18px; }
  .modal-close {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 22px;
    line-height: 1;
    padding: 4px 8px;
  }
  .modal-close:hover { color: var(--text); }
  .chart-container {
    margin: 12px 0 18px;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
  }
  .chart-empty {
    padding: 40px 0;
    text-align: center;
    color: var(--text-muted);
    font-size: 13px;
  }
  .detail-item {
    display: block;
    padding: 12px 0;
    border-top: 1px solid var(--line);
  }
  .detail-item:hover .item-title { color: var(--fall); }
  .detail-item:first-child { border-top: none; }
  .detail-item-top {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: baseline;
  }
  .item-title { font-size: 14px; font-weight: 500; }
  .detail-meta {
    margin-top: 4px;
    font-size: 12px;
    color: var(--text-muted);
    font-family: var(--mono);
  }

  @media (max-width: 420px) {
    .corp { font-size: 15px; }
    .masthead h1 { font-size: 22px; }
  }
</style>
</head>
<body>
  <div class="marquee"><div class="marquee-track" id="marquee-track">불러오는 중...</div></div>
  <main>
    <div class="masthead">
      <div>
        <div class="eyebrow">KOSPI DISCLOSURE SCAN</div>
        <h1>코스피 호재 스캐너</h1>
        <div class="sub" id="sub-label">불러오는 중...</div>
      </div>
      <select id="date-select" aria-label="조회 날짜 선택"></select>
    </div>

    <div id="cards"><div class="loading">불러오는 중...</div></div>

    <div class="disclaimer">
      공시 제목의 키워드만으로 점수를 매기는 단순 규칙 기반 도구입니다.
      개인 참고용으로만 사용하고, 투자 판단의 근거로 그대로 쓰지 마세요.
      일봉 차트는 최근 데이터 기준이며, 종목을 클릭하면 상세 공시와 함께 볼 수 있습니다.
    </div>
  </main>

  <div class="modal-backdrop" id="modal-backdrop">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-head">
        <h2 id="modal-title"></h2>
        <button class="modal-close" id="modal-close" aria-label="닫기">&times;</button>
      </div>
      <div class="chart-container" id="chart-container"></div>
      <div id="modal-items"></div>
    </div>
  </div>

<script>
const WEEKDAY_KO = ['일','월','화','수','목','금','토'];

function formatDateKo(isoDate) {
  const d = new Date(isoDate + 'T00:00:00');
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${WEEKDAY_KO[d.getDay()]})`;
}

function formatRceptDt(dt) {
  if (!dt || dt.length < 8) return dt || '';
  return `${dt.slice(0,4)}.${dt.slice(4,6)}.${dt.slice(6,8)}`;
}

let currentChart = null;

function closeModal() {
  document.getElementById('modal-backdrop').classList.remove('open');
  if (currentChart) {
    currentChart.remove();
    currentChart = null;
  }
}

function openModal(company) {
  document.getElementById('modal-title').textContent = company.corp_name;
  const container = document.getElementById('chart-container');
  container.innerHTML = '';

  if (company.ohlc && company.ohlc.length > 0 && window.LightweightCharts) {
    const chart = LightweightCharts.createChart(container, {
      width: container.clientWidth || 560,
      height: 260,
      layout: { background: { color: '#131A22' }, textColor: '#7C8898', fontFamily: 'JetBrains Mono, monospace' },
      grid: { vertLines: { color: '#1D2530' }, horzLines: { color: '#1D2530' } },
      timeScale: { borderColor: '#232D38' },
      rightPriceScale: { borderColor: '#232D38' },
    });
    const series = chart.addCandlestickSeries({
      upColor: '#F0454F', downColor: '#2F8FFF',
      borderUpColor: '#F0454F', borderDownColor: '#2F8FFF',
      wickUpColor: '#F0454F', wickDownColor: '#2F8FFF',
    });
    const candleData = company.ohlc.map(c => ({
      time: `${c.date.slice(0,4)}-${c.date.slice(4,6)}-${c.date.slice(6,8)}`,
      open: c.open, high: c.high, low: c.low, close: c.close,
    })).sort((a, b) => a.time.localeCompare(b.time));
    series.setData(candleData);
    chart.timeScale().fitContent();
    currentChart = chart;
  } else {
    container.innerHTML = '<div class="chart-empty">일봉 차트를 불러올 수 없습니다.</div>';
  }

  const itemsEl = document.getElementById('modal-items');
  itemsEl.innerHTML = company.items.map(it => {
    const dir = it.score >= 0 ? 'up' : 'down';
    const arrow = it.score >= 0 ? '▲' : '▼';
    const sign = it.score > 0 ? '+' : '';
    const dartUrl = `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${it.rcept_no}`;
    return `
      <a class="detail-item" href="${dartUrl}" target="_blank" rel="noopener noreferrer">
        <div class="detail-item-top">
          <span class="item-title">${it.report_nm}</span>
          <span class="delta delta-${dir}">${arrow} ${sign}${it.score}</span>
        </div>
        <div class="detail-meta">${it.flr_nm || ''} · ${formatRceptDt(it.rcept_dt)}${it.rm ? ' · ' + it.rm : ''}</div>
      </a>`;
  }).join('');

  document.getElementById('modal-backdrop').classList.add('open');
}

function renderData(data) {
  const ranked = data.ranked || [];
  document.getElementById('sub-label').textContent =
    `${formatDateKo(data.date)} 공시 마감 기준 · TOP ${ranked.length}`;

  const marqueeText = ranked.length
    ? ranked.map(c => c.corp_name).join(' · ')
    : '오늘은 매칭된 종목이 없습니다';
  document.getElementById('marquee-track').textContent = `${marqueeText}  ·  ${marqueeText}`;

  const cardsEl = document.getElementById('cards');
  if (ranked.length === 0) {
    cardsEl.innerHTML = '<div class="empty">해당 날짜에는 매칭된 공시가 없습니다.</div>';
    return;
  }

  cardsEl.innerHTML = '';
  ranked.forEach((company, i) => {
    const dir = company.score >= 0 ? 'up' : 'down';
    const arrow = company.score >= 0 ? '▲' : '▼';
    const sign = company.score > 0 ? '+' : '';
    const btn = document.createElement('button');
    btn.className = 'card';
    btn.innerHTML = `
      <div class="card-head">
        <span class="rank">${String(i + 1).padStart(2, '0')}</span>
        <h2 class="corp">${company.corp_name}</h2>
        <span class="delta delta-${dir} delta-total">${arrow} ${sign}${company.score}</span>
      </div>`;
    btn.addEventListener('click', () => openModal(company));
    cardsEl.appendChild(btn);
  });
}

async function loadDate(isoDate) {
  document.getElementById('cards').innerHTML = '<div class="loading">불러오는 중...</div>';
  try {
    const res = await fetch(`data/${isoDate}.json`);
    const data = await res.json();
    renderData(data);
  } catch (e) {
    document.getElementById('cards').innerHTML = '<div class="empty">데이터를 불러오지 못했습니다.</div>';
  }
}

async function init() {
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal-backdrop').addEventListener('click', (e) => {
    if (e.target.id === 'modal-backdrop') closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
  });

  try {
    const res = await fetch('data/dates.json');
    const dates = await res.json();
    const select = document.getElementById('date-select');
    select.innerHTML = dates.map(d => `<option value="${d}">${d}</option>`).join('');
    select.addEventListener('change', (e) => loadDate(e.target.value));

    if (dates.length > 0) {
      loadDate(dates[0]);
    } else {
      document.getElementById('cards').innerHTML = '<div class="empty">아직 쌓인 데이터가 없습니다.</div>';
    }
  } catch (e) {
    document.getElementById('cards').innerHTML = '<div class="empty">날짜 목록을 불러오지 못했습니다.</div>';
  }
}

init();
</script>
</body>
</html>
"""


def main() -> None:
    api_key = get_api_key()
    target_date = get_target_date()
    disclosures = fetch_kospi_disclosures(api_key, target_date)

    save_index_html()  # index.html은 데이터와 무관한 고정 템플릿이라 항상 최신으로 유지

    if not disclosures:
        print(f"{target_date.isoformat()}(전일) 조회된 코스피 공시가 없습니다.")
        return

    # 종목(corp_name)별로 점수를 합산 (한 종목이 오늘 여러 건 공시했을 수도 있음)
    stock_scores: Dict[str, int] = defaultdict(int)
    stock_items: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in disclosures:
        corp_name = item.get("corp_name", "")
        report_nm = item.get("report_nm", "")

        s = score_disclosure(report_nm)
        if s == 0:
            continue  # 매칭되는 키워드가 없으면 스킵

        stock_scores[corp_name] += s
        stock_items[corp_name].append({
            "report_nm": report_nm,
            "score": s,
            "rcept_no": item.get("rcept_no", ""),
            "stock_code": item.get("stock_code", ""),
            "flr_nm": item.get("flr_nm", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "rm": item.get("rm", ""),
        })

    if not stock_scores:
        print(f"{target_date.isoformat()}(전일) 호재/악재 키워드에 매칭되는 공시가 없습니다.")
        return

    # 점수 높은 순으로 정렬 후 상위 N개만
    ranked = sorted(stock_scores.items(), key=lambda x: x[1], reverse=True)[:TOP_N]

    print(f"=== {target_date.isoformat()}(전일) 코스피 호재 점수 TOP {TOP_N} ===\n")
    for rank, (corp_name, score) in enumerate(ranked, start=1):
        print(f"{rank}. {corp_name}  (점수: {score})")
        for it in stock_items[corp_name]:
            sign = "+" if it["score"] > 0 else ""
            print(f"   - {it['report_nm']} ({sign}{it['score']}) [rcept_no={it['rcept_no']}]")
        print()

    payload = build_date_json(target_date, ranked, stock_items)
    save_date_json(target_date, payload)
    print(f"docs/data/{target_date.isoformat()}.json 파일에 결과를 저장했습니다.")


if __name__ == "__main__":
    main()
