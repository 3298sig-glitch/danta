"""
update_open_price.py
---------------------
dart_top3.py가 07:00에 저장해 둔 오늘자 TOP5 종목의 09:00 장 시작 시가(始價)를
조회해서, 매수가/목표수익률별 매도가/손절가를 같은 JSON 파일에 추가한다.

파일명 기준 주의: dart_top3.py는 공시 자체는 전일(어제) 것을 스캔하지만, 파일명과
화면에 표시되는 날짜는 '이 추천이 적용되는 당일'(오늘, kst_today())을 쓴다
(전일 날짜로 표시되면 사용자가 헷갈리기 때문). 이 스크립트도 반드시 같은
kst_today() 기준으로 파일을 찾아야 07:00이 방금 만든 파일을 찾을 수 있다
(GitHub Actions 러너는 UTC로 돌기 때문에 date.today()를 쓰면 하루씩 밀린다).

dart_top3.py는 07:00(장 시작 전)에 실행되기 때문에 그 시점엔 '오늘 시가'가
아직 존재하지 않는다(장이 안 열렸으므로). 그래서 09:05경 이 스크립트를
한 번 더 실행해서, 실제로 열린 시가를 매수가 기준으로 삼아 투자 전략
가격(매도가/손절가)을 확정한다.

같은 이유로 07:00 시점의 일봉(ohlc)에도 당일 캔들이 없다 - fetch_daily_ohlc()를
장중(09:00 이후)에 다시 호출하면 네이버 차트 API가 당일 캔들을 실시간(그 시점까지의
시가/고가/저가/현재가/거래량)으로 포함해서 주므로, 이 스크립트가 그것도 같이
갱신해서 상세 모달의 일봉차트에 당일 캔들이 보이게 한다.

사용법:
    1) dart_top3.py가 그날 이미 실행되어 해당 날짜의 JSON이 있어야 한다.
    2) python update_open_price.py

주의:
    - 그날 매칭되는 공시가 없었다면 dart_top3.py가 JSON을 아예 만들지 않는데,
      이 경우 이 스크립트는 에러가 아니라 정상적인 '오늘은 업데이트할 게 없음'으로
      처리하고 조용히 종료한다.
    - 개인 참고용 도구이며, 투자 판단의 근거로 그대로 사용하지 마세요.
"""

import json
import os
import sys
from typing import Any, Dict

from dart_top3 import (
    DATA_DIR,
    fetch_daily_ohlc,
    fetch_kospi_market_summary,
    fetch_market_headlines,
    fetch_naver_integration,
    fetch_sector_movers,
    kst_today,
)

STOP_LOSS_PCT = 3.0  # 고정 손절폭(%) - 목표수익률과 무관하게 항상 매수가의 -3%
TARGET_PROFIT_OPTIONS_PCT = list(range(1, 11))  # 목표수익률 선택지: 1%~10%
# 10% 초과 값은 매수가 × (1 + r/100) 공식이 단순해서 화면(자바스크립트)에서
# 직접 계산해도 충분하므로 여기서 더 큰 값까지 미리 만들어두지 않는다.


def build_trade_plan(buy_price: int) -> Dict[str, Any]:
    """매수가를 기준으로 손절가와 목표수익률(1~10%)별 매도가를 계산한다."""
    stop_loss_price = round(buy_price * (1 - STOP_LOSS_PCT / 100))
    target_sell_prices = {
        str(pct): round(buy_price * (1 + pct / 100))
        for pct in TARGET_PROFIT_OPTIONS_PCT
    }
    return {
        "buy_price": buy_price,
        "stop_loss_price": stop_loss_price,
        "stop_loss_pct": -STOP_LOSS_PCT,
        "target_sell_prices": target_sell_prices,
    }


def warn(message: str) -> None:
    """일반 stderr 로그와 함께, GitHub Actions가 실행 요약 화면에 바로 띄워주는
    ::warning:: 어노테이션도 찍는다 - 스텝 로그를 안 열어봐도 실패를 바로 알 수 있게."""
    print(f"::warning::{message}")
    print(f"  (참고) {message}", file=sys.stderr)


def get_today_json_path() -> str:
    # dart_top3.py가 오늘(kst_today()) 날짜로 저장한 파일과 반드시 같은 기준을 써야
    # 07:00이 방금 만든 파일을 09:05가 제대로 찾을 수 있다.
    return os.path.join(DATA_DIR, f"{kst_today().isoformat()}.json")


def main() -> None:
    path = get_today_json_path()
    if not os.path.exists(path):
        print(f"{path} 파일이 없습니다 (오늘은 매칭된 공시가 없었을 수 있습니다). 종료합니다.")
        return

    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    ranked = payload.get("ranked", [])
    if not ranked:
        print("오늘 후보 종목이 없어 시가 반영을 건너뜁니다.")
        return

    for entry in ranked:
        corp_name = entry.get("corp_name", "")
        stock_code = entry.get("stock_code", "")
        print(f"{corp_name}({stock_code}) 09:00 시가 조회 중...")

        naver = fetch_naver_integration(stock_code)
        open_price = naver.get("open_price")

        if not open_price:
            print(f"  (참고) {corp_name} 시가를 가져오지 못했습니다. trade_plan을 비워둡니다.", file=sys.stderr)
            entry["trade_plan"] = None
            continue

        entry["trade_plan"] = build_trade_plan(open_price)
        print(f"  매수가(시가): {open_price:,}원 / 손절가: {entry['trade_plan']['stop_loss_price']:,}원")

        # 일봉 차트에 당일 캔들이 보이도록 장중 기준으로 다시 조회해서 교체한다.
        # 조회 실패로 빈 배열이 오면 기존(어제까지의) 데이터를 그대로 둔다 -
        # trade_plan과 마찬가지로 실패했다고 있던 데이터를 지우면 안 된다.
        fresh_ohlc = fetch_daily_ohlc(stock_code)
        if fresh_ohlc:
            entry["ohlc"] = fresh_ohlc
        else:
            print(f"  (참고) {corp_name} 당일 일봉 재조회 실패, 기존 일봉을 유지합니다.", file=sys.stderr)

    print("오늘의 시황(코스피 지수/상승·하락 종목수) 조회 중...")
    market_summary = fetch_kospi_market_summary()
    if market_summary:
        print(
            f"  코스피 {market_summary['index_price']:,}"
            f" ({market_summary['index_change']:+,}, {market_summary['index_change_pct']:+.2f}%)"
            f" - {market_summary['sentiment']}"
            f" (상승 {market_summary['rise_count']}·보합 {market_summary['flat_count']}"
            f"·하락 {market_summary['fall_count']})"
        )

        # 업종 등락/뉴스 헤드라인은 지수 조회와 완전히 독립적으로 실패 처리한다 -
        # 하나가 깨져도 나머지(지수, 또는 업종/헤드라인 각각)는 그대로 살아남는다.
        sector_movers = fetch_sector_movers()
        if sector_movers:
            market_summary.update(sector_movers)
            print(
                f"  업종: 상승 {sector_movers['top_gainer_sector']['name']}"
                f" {sector_movers['top_gainer_sector']['change_pct']:+.2f}%"
                f" · 하락 {sector_movers['top_loser_sector']['name']}"
                f" {sector_movers['top_loser_sector']['change_pct']:+.2f}%"
            )
        else:
            warn("업종별 등락 조회 실패, 오늘의 시황에서 업종 정보를 건너뜁니다.")

        headlines = fetch_market_headlines()
        if headlines:
            market_summary["headlines"] = headlines
            print(f"  헤드라인 {len(headlines)}건 수집")
        else:
            warn("헤드라인 조회 실패, 오늘의 시황에서 헤드라인을 건너뜁니다.")

        payload["market_summary"] = market_summary
    else:
        warn("오늘의 시황(코스피 지수) 조회 실패, 오늘의 시황 표시를 건너뜁니다.")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"{path} 에 매수가/매도가/손절가를 반영했습니다.")


if __name__ == "__main__":
    main()
