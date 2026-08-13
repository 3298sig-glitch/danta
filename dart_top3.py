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

import ast
import getpass
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Dict, Any, Optional

import requests

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_TSSTK_AQ_URL = "https://opendart.fss.or.kr/api/tsstkAqDecsn.json"       # 자기주식취득결정
DART_PIIC_URL = "https://opendart.fss.or.kr/api/piicDecsn.json"              # 유상증자결정
DART_FRIC_URL = "https://opendart.fss.or.kr/api/fricDecsn.json"              # 무상증자결정
DART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"            # 공시서류원본파일
NAVER_CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
NAVER_SISEJSON_URL = "https://api.finance.naver.com/siseJson.naver"
NAVER_INTEGRATION_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
NAVER_THEME_LIST_URL = "https://finance.naver.com/sise/theme.naver"
NAVER_THEME_DETAIL_URL = "https://finance.naver.com/sise/sise_group_detail.naver"
TOP_N = 5
HOT_THEME_COUNT = 15
DOCS_DIR = "docs"
DATA_DIR = os.path.join(DOCS_DIR, "data")

# 종합 스코어링 가중치 (공시 있는 종목만 후보로 삼고, 4개 신호를 0~100 스케일로 합산)
DISCLOSURE_WEIGHT = 0.35
MOMENTUM_WEIGHT = 0.30
SUPPLY_WEIGHT = 0.25
THEME_WEIGHT = 0.10

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
}


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
# 아래 4개는 금액/비율 기반 정규화 로직(compute_disclosure_score)에서 DART 구조화 API로
# 재조회하는 대상이라, 문자열을 여기 상수로 고정해 두고 재사용한다 (오타로 인한
# 매칭 불일치를 막기 위함).
KW_TREASURY_STOCK_ACQUISITION = "자기주식취득"
KW_SUPPLY_CONTRACT = "단일판매ㆍ공급계약체결"
KW_BONUS_ISSUE = "무상증자결정"
KW_RIGHTS_OFFERING = "유상증자결정"

POSITIVE_KEYWORDS: Dict[str, int] = {
    KW_TREASURY_STOCK_ACQUISITION: 3,   # 자사주 매입 (통상 호재)
    KW_SUPPLY_CONTRACT: 2,               # 대규모 수주 계약
    KW_BONUS_ISSUE: 2,
    "특허권": 1,
    "유형자산 양수": 1,
    "자산재평가": 1,
}

NEGATIVE_KEYWORDS: Dict[str, int] = {
    KW_RIGHTS_OFFERING: -3,
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


def _fetch_ohlc_via_fchart(stock_code: str, count: int = 60) -> List[Dict[str, Any]]:
    """네이버 금융의 구버전 차트 API(fchart/sise.nhn, XML 응답)로 일봉을 가져온다."""

    if not stock_code:
        print(
            "  (참고) 종목코드가 없어 일봉 차트를 건너뜁니다.",
            file=sys.stderr,
        )
        return []

    # params= 방식 대신 ASCII 문자열 URL을 직접 구성한다.
    # GitHub Actions 환경에서 multi-byte encoding 오류를 피하기 위한 방식
    url = (
        f"{NAVER_CHART_URL}"
        f"?symbol={stock_code}"
        f"&timeframe=day"
        f"&count={int(count)}"
        f"&requestType=0"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Referer": "https://finance.naver.com/",
        "Accept": "application/xml,text/xml,text/plain,*/*",
    }

    try:
        print(f"  네이버 차트 요청: {url}")

        resp = requests.get(
            url,
            headers=headers,
            timeout=15,
        )

        print(
            f"  네이버 차트 응답: {stock_code} "
            f"HTTP {resp.status_code}, {len(resp.content):,} bytes"
        )

        resp.raise_for_status()

        if not resp.content:
            print(
                f"  (참고) {stock_code} 네이버 응답이 비어 있습니다.",
                file=sys.stderr,
            )
            return []

        # 네이버의 이 XML 응답은 <?xml ... encoding="EUC-KR"?> 로 선언되어 오는 경우가 많은데,
        # 파이썬 내장 XML 파서(expat)는 EUC-KR 같은 멀티바이트 인코딩을 직접 지원하지 않아
        # ET.fromstring(bytes)를 바로 호출하면 "multi-byte encodings are not supported" 에러가 난다.
        # -> 우리가 먼저 직접 디코딩한 뒤, XML 선언부(인코딩 표기)를 제거하고 순수 텍스트로 파싱한다.
        try:
            text = resp.content.decode("euc-kr", errors="replace")
        except LookupError:
            text = resp.content.decode("utf-8", errors="replace")
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)

        try:
            root = ET.fromstring(text)

        except ET.ParseError as e:
            # 오류 확인을 위해 응답 앞부분만 ASCII-safe 방식으로 출력
            preview = text[:300]

            print(
                f"  (참고) {stock_code} XML 파싱 실패: {e}",
                file=sys.stderr,
            )
            print(
                f"  응답 앞부분: {preview}",
                file=sys.stderr,
            )

            return []

        items = list(root.iter("item"))

        if not items:
            preview = resp.content[:300].decode(
                "utf-8",
                errors="replace",
            )

            print(
                f"  (참고) {stock_code} <item> 데이터가 없습니다.",
                file=sys.stderr,
            )
            print(
                f"  응답 앞부분: {preview}",
                file=sys.stderr,
            )

            return []

        candles: List[Dict[str, Any]] = []

        for item in items:

            raw = item.get("data", "")

            parts = raw.split("|")

            if len(parts) < 6:
                continue

            d, o, h, l, c, v = parts[:6]

            if not all([d, o, h, l, c]):
                continue

            try:
                candles.append(
                    {
                        "date": d,
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": int(float(v)) if v else 0,
                    }
                )

            except (ValueError, TypeError):
                print(
                    f"  (참고) {stock_code} 잘못된 OHLC 데이터: {raw}",
                    file=sys.stderr,
                )
                continue

        if not candles:
            print(
                f"  (참고) {stock_code} 유효한 일봉 데이터가 없습니다.",
                file=sys.stderr,
            )
            return []

        print(
            f"  ✓ {stock_code} 일봉 {len(candles)}건 수집 "
            f"({candles[0]['date']} ~ {candles[-1]['date']})"
        )

        return candles

    except requests.RequestException as e:
        print(
            f"  (참고) {stock_code} 네이버 차트 HTTP 요청 실패: {e}",
            file=sys.stderr,
        )
        return []

    except Exception as e:
        print(
            f"  (참고) {stock_code} 일봉 차트 처리 실패: "
            f"{type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return []


def _fetch_ohlc_via_sisejson(stock_code: str, count: int = 60) -> List[Dict[str, Any]]:
    """네이버 금융의 또 다른 공개 차트 API(siseJson, 준-JSON 응답)로 일봉을 가져온다.

    _fetch_ohlc_via_fchart가 막히거나 응답 형식이 바뀌었을 때를 대비한 예비 경로다.
    응답이 진짜 JSON은 아니고(작은따옴표 사용) 파이썬 리스트 리터럴과 문법이 같아서
    ast.literal_eval로 안전하게 파싱한다.
    """
    try:
        end = date.today()
        start = end - timedelta(days=int(count * 1.6) + 10)  # 주말/휴장일 감안해 여유있게 조회
        params = {
            "symbol": stock_code,
            "requestType": 1,
            "startTime": start.strftime("%Y%m%d"),
            "endTime": end.strftime("%Y%m%d"),
            "timeframe": "day",
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Referer": f"https://finance.naver.com/item/fchart.naver?code={stock_code}",
        }
        resp = requests.get(NAVER_SISEJSON_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()

        print(
            f"  네이버 siseJson 응답: {stock_code} HTTP {resp.status_code}, "
            f"{len(resp.content):,} bytes"
        )

        rows = ast.literal_eval(resp.text.strip())
        if not rows or len(rows) < 2:
            print(f"  (참고) {stock_code} siseJson에 데이터 행이 없습니다.", file=sys.stderr)
            return []

        candles: List[Dict[str, Any]] = []
        for row in rows[1:]:  # 첫 행은 '날짜,시가,고가,저가,종가,거래량' 같은 헤더
            if len(row) < 6:
                continue
            d, o, h, l, c, v = row[:6]
            try:
                candles.append({
                    "date": str(d),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": int(float(v)) if v else 0,
                })
            except (ValueError, TypeError):
                continue

        if not candles:
            print(f"  (참고) {stock_code} siseJson 유효한 일봉 데이터가 없습니다.", file=sys.stderr)
            return []

        candles = candles[-count:]
        print(
            f"  ✓ {stock_code} 일봉 {len(candles)}건 수집 (siseJson 대체 경로) "
            f"({candles[0]['date']} ~ {candles[-1]['date']})"
        )
        return candles

    except Exception as e:  # noqa: BLE001 - 예비 경로이므로 여기서도 절대 예외를 밖으로 던지지 않는다
        print(
            f"  (참고) {stock_code} siseJson 대체 조회도 실패: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return []


def fetch_daily_ohlc(stock_code: str, count: int = 60) -> List[Dict[str, Any]]:
    """일봉(OHLC)을 가져온다. 1차로 fchart 방식을 시도하고, 실패하면 siseJson 방식으로 재시도한다."""
    candles = _fetch_ohlc_via_fchart(stock_code, count)
    if candles:
        return candles

    if stock_code:
        print(f"  ↳ {stock_code} 1차 조회 실패, 대체 경로(siseJson)로 재시도합니다.")
        candles = _fetch_ohlc_via_sisejson(stock_code, count)
    return candles


# ---------------------------------------------------------------------------
# 3. 종합 스코어링 (공시 금액 정규화 + 모멘텀 + 수급 + 테마)
# ---------------------------------------------------------------------------
# 후보군은 그대로 "전일 공시가 있었던 종목"만 대상으로 한다 (코스피 전체를 스캔하지
# 않음 - 비공식 API 호출량/차단 위험 때문에 범위를 의도적으로 좁게 유지).
# 키워드 합산 점수가 양수(net 호재)인 종목만 4개 신호를 0~100 스케일로 합산한
# 종합점수로 재랭킹하고, 음수/0인 종목은 원래 키워드 점수를 그대로 써서
# 자연스럽게 하위로 밀려나게 둔다 (모멘텀·수급이 좋아도 공시 자체가 악재면
# 급등 후보로 끌어올리지 않기 위함).


def _parse_krw_amount(text: str) -> Optional[int]:
    """네이버가 표시하는 '1,574조 1,105억' 같은 형식을 원 단위 정수로 변환한다."""
    if not text:
        return None
    text = text.strip()
    if text in ("-", "N/A", ""):
        return None

    total = 0
    matched_any = False

    m = re.search(r"([\d,]+)\s*조", text)
    if m:
        total += int(m.group(1).replace(",", "")) * 1_0000_0000_0000
        matched_any = True

    m = re.search(r"([\d,]+)\s*억", text)
    if m:
        total += int(m.group(1).replace(",", "")) * 1_0000_0000
        matched_any = True

    if matched_any:
        return total

    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def fetch_naver_integration(stock_code: str) -> Dict[str, Any]:
    """네이버 모바일 API로 시가총액/시가/최근 5거래일 수급(외국인·기관 순매수)을 가져온다.

    비공식 API라 실패해도 예외를 밖으로 던지지 않고 빈 결과를 돌려준다
    (호출부는 데이터가 없을 때를 항상 대비해야 한다).
    """
    result: Dict[str, Any] = {
        "market_cap": None,
        "open_price": None,
        "close_price": None,
        "deal_trend": [],  # 최신순: [{"date": "YYYYMMDD", "foreign_net": int, "organ_net": int}, ...]
    }
    if not stock_code:
        return result

    try:
        resp = requests.get(
            NAVER_INTEGRATION_URL.format(code=stock_code),
            headers=BROWSER_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for info in data.get("totalInfos", []) or []:
            code = info.get("code")
            value = info.get("value", "")
            if code == "marketValue":
                result["market_cap"] = _parse_krw_amount(value)
            elif code == "openPrice":
                digits = re.sub(r"[^\d]", "", value or "")
                result["open_price"] = int(digits) if digits else None
            elif code == "lastClosePrice":
                digits = re.sub(r"[^\d]", "", value or "")
                result["close_price"] = int(digits) if digits else None

        def _to_int(s: Optional[str]) -> int:
            s = (s or "").replace(",", "").replace("+", "").strip()
            try:
                return int(s)
            except ValueError:
                return 0

        for item in data.get("dealTrendInfos", []) or []:
            result["deal_trend"].append({
                "date": item.get("bizdate", ""),
                "foreign_net": _to_int(item.get("foreignerPureBuyQuant")),
                "organ_net": _to_int(item.get("organPureBuyQuant")),
            })

    except Exception as e:
        print(
            f"  (참고) {stock_code} 네이버 시총/수급 조회 실패: {type(e).__name__}: {e}",
            file=sys.stderr,
        )

    return result


def compute_momentum_score(ohlc: List[Dict[str, Any]]) -> Dict[str, Any]:
    """전일 거래량 급증/종가 위치/이동평균선 돌파로 가격·거래량 모멘텀을 점수화한다."""
    result: Dict[str, Any] = {
        "score": 50.0,
        "volume_ratio": None,
        "close_position": None,
        "ma5_break": None,
        "ma20_break": None,
        "summary": "일봉 데이터 부족",
    }
    if len(ohlc) < 6:
        return result

    last = ohlc[-1]
    closes = [c["close"] for c in ohlc]
    volumes = [c["volume"] for c in ohlc]

    prev_volumes = volumes[:-1]
    window = prev_volumes[-20:]
    avg_volume = sum(window) / len(window) if window else 0
    volume_ratio = (last["volume"] / avg_volume) if avg_volume > 0 else None

    day_range = last["high"] - last["low"]
    close_position = ((last["close"] - last["low"]) / day_range) if day_range > 0 else 0.5

    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / len(closes[-20:])
    ma5_break = last["close"] > ma5
    ma20_break = last["close"] > ma20

    score = 40.0
    if volume_ratio is not None:
        score += min(30.0, max(-10.0, (volume_ratio - 1) * 15))
    score += (close_position - 0.5) * 30
    if ma5_break:
        score += 10
    if ma20_break:
        score += 10
    score = min(100.0, max(0.0, score))

    parts = []
    if volume_ratio is not None:
        parts.append(f"거래량 {volume_ratio:.1f}배")
    parts.append(f"종가위치 {close_position * 100:.0f}%")
    if ma5_break:
        parts.append("5일선 위")
    if ma20_break:
        parts.append("20일선 위")

    result.update({
        "score": round(score, 1),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "close_position": round(close_position, 2),
        "ma5_break": ma5_break,
        "ma20_break": ma20_break,
        "summary": " · ".join(parts),
    })
    return result


def compute_supply_score(deal_trend: List[Dict[str, Any]]) -> Dict[str, Any]:
    """전일까지의 외국인·기관 연속 순매수 추세를 점수화한다 (당일 실시간 수급은 개장
    전에 존재 자체가 불가능해서 다루지 않는다 - 어디까지나 선행지표로만 사용)."""
    result: Dict[str, Any] = {
        "score": 50.0,
        "foreign_streak": 0,
        "organ_streak": 0,
        "summary": "수급 데이터 없음",
    }
    if not deal_trend:
        return result

    def _streak(key: str) -> int:
        count = 0
        for item in deal_trend:  # deal_trend는 최신순(bizdate desc)
            if item[key] > 0:
                count += 1
            else:
                break
        return count

    foreign_streak = _streak("foreign_net")
    organ_streak = _streak("organ_net")

    score = 50.0
    score += min(30.0, foreign_streak * 10)
    score += min(20.0, organ_streak * 8)
    if deal_trend[0]["foreign_net"] > 0 and deal_trend[0]["organ_net"] > 0:
        score += 10
    score = min(100.0, max(0.0, score))

    parts = []
    if foreign_streak > 0:
        parts.append(f"외국인 {foreign_streak}일 연속 순매수")
    if organ_streak > 0:
        parts.append(f"기관 {organ_streak}일 연속 순매수")
    if not parts:
        parts.append("뚜렷한 순매수 흐름 없음")

    result.update({
        "score": round(score, 1),
        "foreign_streak": foreign_streak,
        "organ_streak": organ_streak,
        "summary": " · ".join(parts),
    })
    return result


def fetch_hot_theme_members(top_n: int = HOT_THEME_COUNT) -> Dict[str, List[str]]:
    """네이버 테마 등락률 상위 페이지에서 상위 top_n개 테마만 추려, 그 테마들의
    소속 종목코드 -> 테마명 리스트 맵을 만든다. 전체 테마(300개 이상)를 다 크롤링하지
    않고 그날 뜬 상위 테마의 상세페이지만 추가로 조회하는 방식이라 호출량이 적다."""
    membership: Dict[str, List[str]] = defaultdict(list)

    try:
        resp = requests.get(
            NAVER_THEME_LIST_URL,
            headers={**BROWSER_HEADERS, "Referer": "https://finance.naver.com/"},
            timeout=15,
        )
        resp.raise_for_status()
        # 이 페이지는 <meta charset="utf-8">이라고 표기돼 있지만 실제 바이트는 EUC-KR이다
        # (직접 확인함). errors="replace"로 조용히 깨진 문자를 만들지 않도록 euc-kr로 디코딩.
        html = resp.content.decode("euc-kr", errors="replace")

        rows = re.findall(
            r'sise_group_detail\.naver\?type=theme&no=(\d+)">([^<]+)</a>',
            html,
        )

        for theme_no, theme_name in rows[:top_n]:
            theme_name = theme_name.strip()
            try:
                d = requests.get(
                    NAVER_THEME_DETAIL_URL,
                    params={"type": "theme", "no": theme_no},
                    headers={**BROWSER_HEADERS, "Referer": NAVER_THEME_LIST_URL},
                    timeout=15,
                )
                d.raise_for_status()
                detail_html = d.content.decode("euc-kr", errors="replace")
                codes = set(re.findall(r"/item/main\.naver\?code=(\w+)", detail_html))
                for code in codes:
                    membership[code].append(theme_name)
            except Exception as e:
                print(
                    f"  (참고) 테마 상세 조회 실패 ({theme_name}): {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                continue

    except Exception as e:
        print(f"  (참고) 테마 등락률 목록 조회 실패: {type(e).__name__}: {e}", file=sys.stderr)

    return dict(membership)


def compute_theme_score(stock_code: str, hot_theme_map: Dict[str, List[str]]) -> Dict[str, Any]:
    """오늘 뜬 상위 테마에 속하는지로 점수화한다."""
    themes = hot_theme_map.get(stock_code, [])
    if themes:
        score = min(100.0, 60.0 + len(themes) * 15)
        summary = "핫테마: " + ", ".join(themes)
    else:
        score = 30.0
        summary = "핫테마 소속 없음"
    return {"score": round(score, 1), "themes": themes, "summary": summary}


def _dart_event_lookup(api_key: str, url: str, corp_code: str, target_date: date) -> List[Dict[str, Any]]:
    """DART 주요사항보고서 구조화 API(자사주취득/유상증자/무상증자 등) 공통 조회."""
    if not corp_code:
        return []
    de = target_date.strftime("%Y%m%d")
    try:
        resp = requests.get(
            url,
            params={"crtfc_key": api_key, "corp_code": corp_code, "bgn_de": de, "end_de": de},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":
            return []
        return data.get("list", []) or []
    except Exception:
        return []


def fetch_treasury_stock_acquisition_ratio(
    api_key: str, corp_code: str, target_date: date, market_cap: Optional[int]
) -> Optional[float]:
    """자기주식취득결정의 실제 취득예정금액 / 시가총액(%) 비율."""
    if not market_cap:
        return None
    total = 0
    found = False
    for it in _dart_event_lookup(api_key, DART_TSSTK_AQ_URL, corp_code, target_date):
        for key in ("aqpln_prc_ostk", "aqpln_prc_estk"):
            raw = (it.get(key) or "").replace(",", "").strip()
            if raw.isdigit():
                total += int(raw)
                found = True
    if not found or total <= 0:
        return None
    return total / market_cap * 100


def fetch_rights_offering_ratio(
    api_key: str, corp_code: str, target_date: date, market_cap: Optional[int]
) -> Optional[float]:
    """유상증자결정의 자금조달 목적 금액 합계 / 시가총액(%) 비율."""
    if not market_cap:
        return None
    total = 0
    found = False
    for it in _dart_event_lookup(api_key, DART_PIIC_URL, corp_code, target_date):
        for key in ("fdpp_fclt", "fdpp_bsninh", "fdpp_op", "fdpp_dtrp", "fdpp_ocsa", "fdpp_etc"):
            raw = (it.get(key) or "").replace(",", "").strip()
            if raw.lstrip("-").isdigit():
                total += int(raw)
                found = True
    if not found or total <= 0:
        return None
    return total / market_cap * 100


def fetch_bonus_issue_ratio(api_key: str, corp_code: str, target_date: date) -> Optional[float]:
    """무상증자결정의 1주당 신주배정 비율(%). 이미 정규화된 지표라 시가총액이 필요 없다."""
    ratios = []
    for it in _dart_event_lookup(api_key, DART_FRIC_URL, corp_code, target_date):
        raw = (it.get("nstk_ascnt_ps_ostk") or "").strip()
        try:
            ratios.append(float(raw))
        except ValueError:
            continue
    if not ratios:
        return None
    return max(ratios) * 100


def fetch_supply_contract_ratio(api_key: str, rcept_no: str) -> Optional[float]:
    """단일판매·공급계약체결 공시 원문에서 거래소 서식상 필수 기재 항목인
    '최근 매출액 대비(%)' 수치를 파싱한다. DART가 이 항목은 구조화 API를 제공하지
    않아 원본파일(zip)을 받아 텍스트에서 정규식으로 찾는다. 회사마다 서식이
    조금씩 달라 실패할 수 있는데, 그 경우 None을 돌려줘서 호출부가 기존 키워드
    고정점수로 폴백하게 한다."""
    if not rcept_no:
        return None

    try:
        resp = requests.get(
            DART_DOCUMENT_URL,
            params={"crtfc_key": api_key, "rcept_no": rcept_no},
            timeout=15,
        )
        resp.raise_for_status()

        text_parts = []
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                raw = zf.read(name)
                for enc in ("utf-8", "euc-kr", "cp949"):
                    try:
                        text_parts.append(raw.decode(enc))
                        break
                    except UnicodeDecodeError:
                        continue
        full_text = "\n".join(text_parts)
    except Exception as e:
        print(
            f"  (참고) rcept_no={rcept_no} 공시원문 조회 실패: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return None

    plain = re.sub(r"<[^>]+>", " ", full_text)
    plain = re.sub(r"\s+", " ", plain)

    m = re.search(r"매출액\s*대비[^0-9%]{0,20}([\d]+(?:\.\d+)?)\s*%", plain)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def compute_disclosure_score(
    api_key: str,
    corp_code: str,
    target_date: date,
    market_cap: Optional[int],
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """종목의 공시 항목들을 가능하면 금액/비율 기준으로, 안 되면 기존 키워드
    고정점수를 0~100 스케일로 환산해 점수화한다."""
    item_scores = []
    basis_notes = []

    for it in items:
        matched = get_matched_keywords(it["report_nm"])
        keyword_score = it["score"]
        ratio: Optional[float] = None
        basis = "키워드 고정점수(폴백)"

        if KW_TREASURY_STOCK_ACQUISITION in matched:
            ratio = fetch_treasury_stock_acquisition_ratio(api_key, corp_code, target_date, market_cap)
            basis = "취득금액/시총"
        elif KW_RIGHTS_OFFERING in matched:
            ratio = fetch_rights_offering_ratio(api_key, corp_code, target_date, market_cap)
            basis = "조달금액/시총"
        elif KW_BONUS_ISSUE in matched:
            ratio = fetch_bonus_issue_ratio(api_key, corp_code, target_date)
            basis = "신주배정비율"
        elif KW_SUPPLY_CONTRACT in matched:
            ratio = fetch_supply_contract_ratio(api_key, it.get("rcept_no", ""))
            basis = "계약금액/매출액"

        if ratio is not None:
            item_scores.append(min(100.0, max(0.0, 40 + ratio * 6)))
            basis_notes.append(f"{basis} {ratio:.1f}%")
        else:
            item_scores.append(min(100.0, max(0.0, 50 + keyword_score * 10)))
            if basis != "키워드 고정점수(폴백)":
                basis_notes.append(f"{basis} 파싱 실패 → 폴백")

    score = sum(item_scores) / len(item_scores) if item_scores else 50.0
    return {
        "score": round(score, 1),
        "basis": basis_notes if basis_notes else ["키워드 고정점수(폴백)"],
    }


def evaluate_candidate(
    api_key: str,
    target_date: date,
    items: List[Dict[str, Any]],
    hot_theme_map: Dict[str, List[str]],
) -> Dict[str, Any]:
    """한 종목의 4개 신호(공시/모멘텀/수급/테마)를 계산하고, 랭킹에 쓸 점수를 정한다."""
    corp_code = items[0].get("corp_code", "") if items else ""
    stock_code = items[0].get("stock_code", "").strip() if items else ""
    keyword_score = sum(it["score"] for it in items)

    ohlc = fetch_daily_ohlc(stock_code)
    naver = fetch_naver_integration(stock_code)

    momentum = compute_momentum_score(ohlc)
    supply = compute_supply_score(naver["deal_trend"])
    theme = compute_theme_score(stock_code, hot_theme_map)

    if keyword_score > 0:
        disclosure = compute_disclosure_score(api_key, corp_code, target_date, naver["market_cap"], items)
        composite = (
            disclosure["score"] * DISCLOSURE_WEIGHT
            + momentum["score"] * MOMENTUM_WEIGHT
            + supply["score"] * SUPPLY_WEIGHT
            + theme["score"] * THEME_WEIGHT
        )
        rank_score: float = composite
    else:
        # 공시 자체가 순악재/중립이면 모멘텀·수급이 좋아도 급등 후보로 끌어올리지
        # 않는다 - 원래 키워드 점수(음수/0)를 그대로 써서 자연스럽게 하위로 정렬.
        disclosure = {"score": None, "basis": ["공시가 호재로 분류되지 않아 종합점수 미적용"]}
        composite = None
        rank_score = float(keyword_score)

    return {
        "keyword_score": keyword_score,
        "composite_score": round(composite, 1) if composite is not None else None,
        "rank_score": rank_score,
        "stock_code": stock_code,
        "market_cap": naver["market_cap"],
        "ohlc": ohlc,
        "signals": {
            "disclosure": disclosure,
            "momentum": momentum,
            "supply": supply,
            "theme": theme,
        },
    }


def build_date_json(
    target_date: date,
    ranked: List[tuple],
    stock_items: Dict[str, List[Dict[str, Any]]],
    profiles: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """웹페이지가 읽을 하루치 데이터 구조를 만든다.

    일봉 차트/모멘텀/수급/테마는 evaluate_candidate()에서 랭킹 계산 시 이미
    한 번 조회해둔 profiles를 그대로 재사용한다 (여기서 다시 조회하지 않음 -
    같은 종목을 두 번 조회하면 API 호출량이 불필요하게 늘어난다).
    """
    ranked_payload = []
    for corp_name, keyword_score in ranked:
        items = stock_items[corp_name]
        profile = profiles[corp_name]

        ranked_payload.append({
            "corp_name": corp_name,
            "stock_code": profile["stock_code"],
            "score": keyword_score,
            "composite_score": profile["composite_score"],
            "signals": profile["signals"],
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
            "ohlc": profile["ohlc"],
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
  .main-tabs {
    display: flex;
    align-items: center;
    gap: 4px;
    border-bottom: 1px solid var(--line);
    margin-bottom: 20px;
  }
  .main-tab {
    background: none;
    border: none;
    color: var(--text-muted);
    font-family: var(--sans);
    font-size: 13px;
    padding: 10px 4px;
    margin-right: 18px;
    border-bottom: 2px solid transparent;
    cursor: pointer;
  }
  .main-tab.active {
    color: var(--text);
    font-weight: 600;
    border-bottom-color: var(--rise);
  }
  .main-tab-link {
    margin-left: auto;
    margin-right: 0;
    color: var(--fall);
  }
  .main-tab-link:hover { color: var(--text); }
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
  .corp { flex: 1; }
  .corp-name {
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

  /* 추천 기준 패널 (탭으로 전환) */
  #panel-criteria {
    font-size: 13px;
    line-height: 1.7;
    color: var(--text-muted);
  }
  #panel-criteria strong { color: var(--text); }
  .criteria-list {
    margin: 10px 0;
    padding-left: 18px;
  }
  .criteria-list li { margin-bottom: 8px; }
  .criteria-note {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--line);
  }

  /* 신호 미터(공시/모멘텀/수급/테마) */
  .signals-block {
    margin: 0 0 18px;
    padding: 14px;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  .signals-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 14px;
    font-size: 12px;
    color: var(--text-muted);
    font-family: var(--mono);
  }
  .signals-head .composite-value {
    font-size: 20px;
    font-weight: 700;
    color: var(--text);
  }
  .signal-row { margin-bottom: 12px; }
  .signal-row:last-child { margin-bottom: 0; }
  .signal-top {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-bottom: 4px;
  }
  .signal-label { color: var(--text-muted); }
  .signal-value { font-family: var(--mono); color: var(--text); }
  .meter-track {
    height: 6px;
    background: var(--line);
    border-radius: 3px;
    overflow: hidden;
  }
  .meter-fill {
    height: 100%;
    background: var(--rise);
    border-radius: 3px;
  }
  .signal-summary {
    margin-top: 4px;
    font-size: 12px;
    color: var(--text-muted);
    font-family: var(--mono);
  }
  .composite-sub {
    margin-top: 2px;
    font-size: 11px;
    color: var(--text-muted);
    font-family: var(--mono);
  }

  /* 투자 전략 */
  .trade-plan {
    margin: 0 0 18px;
    padding: 14px;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  .trade-plan-head {
    font-size: 12px;
    color: var(--text-muted);
    font-family: var(--mono);
    margin-bottom: 10px;
  }
  .trade-plan-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    font-size: 13px;
  }
  .trade-plan-row .mono { font-family: var(--mono); }
  .trade-plan-select-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
  }
  .trade-plan-select-row select {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 5px 8px;
    font-family: var(--mono);
    font-size: 13px;
  }
  .trade-plan-pending {
    font-size: 12px;
    color: var(--text-muted);
    padding: 4px 0;
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
    .corp-name { font-size: 15px; }
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

    <nav class="main-tabs">
      <button class="main-tab active" id="tab-stocks" data-tab="stocks">추천 종목 상세</button>
      <button class="main-tab" id="tab-criteria" data-tab="criteria">추천 기준</button>
      <a class="main-tab main-tab-link" href="profit.html">내 수익 관리 &rarr;</a>
    </nav>

    <div id="panel-stocks">
      <div id="cards"><div class="loading">불러오는 중...</div></div>
    </div>

    <div id="panel-criteria" hidden>
      <p>이 사이트는 <strong>전일 코스피 공시가 있었던 종목만</strong> 후보로 삼아, 아래 4가지 신호를
      종합점수(0~100)로 합산해 상위 5종목을 뽑습니다.</p>
      <ul class="criteria-list">
        <li><strong>공시 (35%)</strong> — 키워드 점수를 기본으로 하되, 가능하면 계약금액/시가총액
        비율이나 매출액 대비 비율로 정규화해 규모를 반영합니다.</li>
        <li><strong>모멘텀 (30%)</strong> — 전일 거래량이 평소보다 급증했는지, 종가가 고가권에서
        마감했는지, 이동평균선을 돌파했는지를 봅니다.</li>
        <li><strong>수급 (25%)</strong> — 최근 며칠간 외국인·기관이 연속으로 순매수해온 흐름입니다.
        장 시작 전이라 '오늘 실시간 수급'은 볼 수 없어서, 어디까지나 선행지표로만 씁니다.</li>
        <li><strong>테마 (10%)</strong> — 그날 등락률 상위 테마에 속하는 종목인지 확인합니다.</li>
      </ul>
      <p class="criteria-note">공시 자체가 호재로 분류되지 않은 종목(악재·중립)은 모멘텀·수급이 좋아도
      종합점수를 적용하지 않고 원래 점수 그대로 하위로 둡니다.</p>
      <p class="criteria-note">개인 참고용 도구입니다. 투자 판단의 근거로 그대로 사용하지 마세요.</p>
    </div>

    <div class="disclaimer">
      전일 공시가 있었던 종목만 대상으로, 공시·모멘텀·수급·테마 4가지 신호를 종합해 순위를 매기는
      개인 참고용 도구입니다 (자세한 기준은 위 "추천 기준" 탭 참고). 투자 판단의 근거로 그대로 쓰지 마세요.
      종목을 클릭하면 일봉 차트와 상세 신호를 볼 수 있습니다.
    </div>
  </main>

  <div class="modal-backdrop" id="modal-backdrop">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-head">
        <h2 id="modal-title"></h2>
        <button class="modal-close" id="modal-close" aria-label="닫기">&times;</button>
      </div>
      <div class="chart-container" id="chart-container"></div>
      <div id="signals-container"></div>
      <div id="trade-plan-container"></div>
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

function switchMainTab(tab) {
  document.getElementById('tab-stocks').classList.toggle('active', tab === 'stocks');
  document.getElementById('tab-criteria').classList.toggle('active', tab === 'criteria');
  document.getElementById('panel-stocks').hidden = tab !== 'stocks';
  document.getElementById('panel-criteria').hidden = tab !== 'criteria';
}

const SIGNAL_LABELS = { disclosure: '공시', momentum: '모멘텀', supply: '수급', theme: '테마' };

function renderSignalRow(key, signal) {
  const label = SIGNAL_LABELS[key];
  if (!signal || signal.score === null || signal.score === undefined) {
    return `
      <div class="signal-row">
        <div class="signal-top"><span class="signal-label">${label}</span></div>
        <div class="signal-summary">${(signal && signal.basis && signal.basis.join(' · ')) || (signal && signal.summary) || '데이터 없음'}</div>
      </div>`;
  }
  const pct = Math.max(0, Math.min(100, signal.score));
  const summary = signal.summary || (signal.basis ? signal.basis.join(' · ') : '');
  return `
    <div class="signal-row">
      <div class="signal-top">
        <span class="signal-label">${label}</span>
        <span class="signal-value">${pct.toFixed(0)}</span>
      </div>
      <div class="meter-track"><div class="meter-fill" style="width:${pct}%"></div></div>
      <div class="signal-summary">${summary}</div>
    </div>`;
}

function renderSignalsBlock(company) {
  const signals = company.signals;
  if (!signals) return '';
  const compositeText = company.composite_score != null
    ? `종합점수 <span class="composite-value">${company.composite_score}</span>점`
    : '종합점수 미적용 (공시가 호재로 분류되지 않음)';
  return `
    <div class="signals-block">
      <div class="signals-head">${compositeText}</div>
      ${renderSignalRow('disclosure', signals.disclosure)}
      ${renderSignalRow('momentum', signals.momentum)}
      ${renderSignalRow('supply', signals.supply)}
      ${renderSignalRow('theme', signals.theme)}
    </div>`;
}

function renderTradePlan(company) {
  const tp = company.trade_plan;
  if (tp === undefined) return '';  // 스키마 자체에 없는 옛 데이터(구버전 JSON) 호환
  if (!tp) {
    return `
      <div class="trade-plan">
        <div class="trade-plan-head">투자 전략</div>
        <div class="trade-plan-pending">장 시작(09:05 이후) 시가가 반영되면 매수가/매도가/손절가가 표시됩니다.</div>
      </div>`;
  }
  const optionsHtml = Object.keys(tp.target_sell_prices)
    .sort((a, b) => Number(a) - Number(b))
    .map(pct => `<option value="${pct}">${pct}%</option>`)
    .join('');
  return `
    <div class="trade-plan">
      <div class="trade-plan-head">투자 전략</div>
      <div class="trade-plan-row">
        <span>매수가(시가)</span><span class="mono">${tp.buy_price.toLocaleString()}원</span>
      </div>
      <div class="trade-plan-select-row">
        <label for="target-profit-select">목표수익률</label>
        <select id="target-profit-select">${optionsHtml}</select>
      </div>
      <div class="trade-plan-row">
        <span>목표 매도가</span>
        <span class="mono delta-up" id="target-sell-price">${tp.target_sell_prices['1'].toLocaleString()}원</span>
      </div>
      <div class="trade-plan-row">
        <span>손절가(${tp.stop_loss_pct}%)</span>
        <span class="mono delta-down">${tp.stop_loss_price.toLocaleString()}원</span>
      </div>
    </div>`;
}

function wireTradePlanSelect(company) {
  const select = document.getElementById('target-profit-select');
  if (!select || !company.trade_plan) return;
  select.addEventListener('change', () => {
    const price = company.trade_plan.target_sell_prices[select.value];
    document.getElementById('target-sell-price').textContent = price.toLocaleString() + '원';
  });
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

  document.getElementById('signals-container').innerHTML = renderSignalsBlock(company);
  document.getElementById('trade-plan-container').innerHTML = renderTradePlan(company);
  wireTradePlanSelect(company);

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
    const compositeSub = company.composite_score != null
      ? `<div class="composite-sub">종합점수 ${company.composite_score}</div>`
      : '';
    const btn = document.createElement('button');
    btn.className = 'card';
    btn.innerHTML = `
      <div class="card-head">
        <span class="rank">${String(i + 1).padStart(2, '0')}</span>
        <div class="corp">
          <h2 class="corp-name">${company.corp_name}</h2>
          ${compositeSub}
        </div>
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
  document.getElementById('tab-stocks').addEventListener('click', () => switchMainTab('stocks'));
  document.getElementById('tab-criteria').addEventListener('click', () => switchMainTab('criteria'));
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
            "corp_code": item.get("corp_code", ""),
            "flr_nm": item.get("flr_nm", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "rm": item.get("rm", ""),
        })

    if not stock_scores:
        print(f"{target_date.isoformat()}(전일) 호재/악재 키워드에 매칭되는 공시가 없습니다.")
        return

    # 후보군(공시 있었던 종목 전체)에 대해 4개 신호(공시금액/모멘텀/수급/테마)를 계산한다.
    # 테마는 종목마다 다시 조회할 필요 없이 오늘의 핫테마 소속 맵을 한 번만 만들어 재사용.
    print("핫테마 목록 조회 중...")
    hot_theme_map = fetch_hot_theme_members()

    profiles: Dict[str, Dict[str, Any]] = {}
    for corp_name, items in stock_items.items():
        print(f"  {corp_name}({items[0].get('stock_code', '')}) 신호 계산 중...")
        profiles[corp_name] = evaluate_candidate(api_key, target_date, items, hot_theme_map)

    # 종합 랭킹 점수(rank_score) 높은 순으로 정렬 후 상위 N개만
    ranked = sorted(
        ((corp_name, profiles[corp_name]["keyword_score"]) for corp_name in stock_scores),
        key=lambda x: profiles[x[0]]["rank_score"],
        reverse=True,
    )[:TOP_N]

    print(f"=== {target_date.isoformat()}(전일) 코스피 급등 후보 TOP {TOP_N} ===\n")
    for rank, (corp_name, keyword_score) in enumerate(ranked, start=1):
        profile = profiles[corp_name]
        print(
            f"{rank}. {corp_name}  "
            f"(키워드점수: {keyword_score}, 종합점수: {profile['composite_score']})"
        )
        for it in stock_items[corp_name]:
            sign = "+" if it["score"] > 0 else ""
            print(f"   - {it['report_nm']} ({sign}{it['score']}) [rcept_no={it['rcept_no']}]")
        signals = profile["signals"]
        print(
            f"   모멘텀: {signals['momentum']['summary']} | "
            f"수급: {signals['supply']['summary']} | "
            f"테마: {signals['theme']['summary']}"
        )
        print()

    payload = build_date_json(target_date, ranked, stock_items, profiles)
    save_date_json(target_date, payload)
    print(f"docs/data/{target_date.isoformat()}.json 파일에 결과를 저장했습니다.")


if __name__ == "__main__":
    main()
