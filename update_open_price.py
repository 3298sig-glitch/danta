"""
update_open_price.py
---------------------
docs/data/{오늘 날짜}.json 에 dart_top3.py가 07:00에 저장해 둔 TOP5 종목의
09:00 장 시작 시가(始價)를 조회해서, 매수가/목표수익률별 매도가/손절가를
같은 파일에 추가한다.

dart_top3.py는 07:00(장 시작 전)에 실행되기 때문에 그 시점엔 '오늘 시가'가
아직 존재하지 않는다(장이 안 열렸으므로). 그래서 09:05경 이 스크립트를
한 번 더 실행해서, 실제로 열린 시가를 매수가 기준으로 삼아 투자 전략
가격(매도가/손절가)을 확정한다.

사용법:
    1) dart_top3.py가 그날 이미 실행되어 docs/data/{오늘}.json이 있어야 한다.
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
from datetime import date
from typing import Any, Dict

from dart_top3 import DATA_DIR, fetch_naver_integration

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


def get_today_json_path() -> str:
    return os.path.join(DATA_DIR, f"{date.today().isoformat()}.json")


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

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"{path} 에 매수가/매도가/손절가를 반영했습니다.")


if __name__ == "__main__":
    main()
