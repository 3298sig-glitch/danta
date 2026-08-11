"""
dart_top3.py
------------
매일 실행 시, '전일(어제)' 코스피(KOSPI) 상장사 전체 공시를 스캔해서
'호재'로 분류될 만한 공시 점수가 가장 높은 상위 3종목을 출력하는 스크립트.
(예: 오늘 아침에 실행하면 어제 하루 동안 나온 공시를 대상으로 집계)

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
    - 개인 참고용 도구이며, 투자 판단의 근거로 그대로 사용하지 마세요.
"""

import getpass
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Dict, Any

import requests

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"


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


def generate_html_report(target_date: date, ranked: List[tuple], stock_reasons: Dict[str, List[str]]) -> str:
    """결과를 간단한 HTML 페이지로 만든다. GitHub Pages로 배포하면 웹에서 바로 볼 수 있다."""
    rows = []
    medals = ["🥇", "🥈", "🥉"]
    for i, (corp_name, score) in enumerate(ranked):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        reasons_html = "".join(f"<li>{r}</li>" for r in stock_reasons[corp_name])
        rows.append(f"""
        <div class="card">
          <h2>{medal} {corp_name} <span class="score">점수 {score:+d}</span></h2>
          <ul>{reasons_html}</ul>
        </div>""")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>코스피 호재 공시 TOP 3</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; background: #f7f7f8; color: #1a1a1a; }}
  h1 {{ font-size: 22px; }}
  .updated {{ color: #666; font-size: 14px; margin-bottom: 24px; }}
  .card {{ background: white; border-radius: 12px; padding: 16px 20px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .card h2 {{ font-size: 18px; margin: 0 0 8px; }}
  .score {{ font-weight: normal; color: #666; font-size: 14px; }}
  ul {{ margin: 8px 0 0; padding-left: 20px; font-size: 14px; color: #444; }}
  .disclaimer {{ margin-top: 32px; font-size: 12px; color: #999; }}
</style>
</head>
<body>
  <h1>코스피 호재 공시 TOP 3</h1>
  <div class="updated">기준일: {target_date.isoformat()} (전일 공시)</div>
  {"".join(rows)}
  <div class="disclaimer">개인 참고용 도구이며, 투자 판단의 근거로 그대로 사용하지 마세요.</div>
</body>
</html>"""
    return html


def save_html_report(html: str) -> None:
    os.makedirs("docs", exist_ok=True)
    with open(os.path.join("docs", "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main() -> None:
    api_key = get_api_key()
    target_date = get_target_date()
    disclosures = fetch_kospi_disclosures(api_key, target_date)
    if not disclosures:
        print(f"{target_date.isoformat()}(전일) 조회된 코스피 공시가 없습니다.")
        return

    # 종목(corp_name)별로 점수를 합산 (한 종목이 오늘 여러 건 공시했을 수도 있음)
    stock_scores: Dict[str, int] = defaultdict(int)
    stock_reasons: Dict[str, List[str]] = defaultdict(list)

    for item in disclosures:
        corp_name = item.get("corp_name", "")
        report_nm = item.get("report_nm", "")
        rcept_no = item.get("rcept_no", "")

        s = score_disclosure(report_nm)
        if s == 0:
            continue  # 매칭되는 키워드가 없으면 스킵

        stock_scores[corp_name] += s
        stock_reasons[corp_name].append(f"{report_nm} ({'+' if s > 0 else ''}{s}) [rcept_no={rcept_no}]")

    if not stock_scores:
        print(f"{target_date.isoformat()}(전일) 호재/악재 키워드에 매칭되는 공시가 없습니다.")
        return

    # 점수 높은 순으로 정렬 후 상위 3개만
    ranked = sorted(stock_scores.items(), key=lambda x: x[1], reverse=True)[:3]

    print(f"=== {target_date.isoformat()}(전일) 코스피 호재 점수 TOP 3 ===\n")
    for rank, (corp_name, score) in enumerate(ranked, start=1):
        print(f"{rank}. {corp_name}  (점수: {score})")
        for reason in stock_reasons[corp_name]:
            print(f"   - {reason}")
        print()

    html = generate_html_report(target_date, ranked, stock_reasons)
    save_html_report(html)
    print("docs/index.html 파일에 결과를 저장했습니다.")


if __name__ == "__main__":
    main()
