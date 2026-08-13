"""
build_verification_data.py
---------------------------
과거 추천 종목들이 실제로 얼마나 정확했는지 검증하기 위한 데이터를 만든다.
docs/data/{날짜}.json 에 쌓인 과거 추천 이력(오늘 제외)을 모아, 종목별로
dart_top3.py의 fetch_daily_ohlc()를 다시 호출해 실제 캔들을 찾아
(전일종가/당일고가/현재가)을 계산하고 docs/data/verification.json으로 저장한다.

왜 저장된 ohlc를 그대로 못 쓰는가: dart_top3.py는 매일 07:00(장 시작 전)에
그 시점 기준 "최근 60일" 캔들을 가져와 저장하는데, 이는 추천일 자체의 캔들이
아직 존재하지 않는 시점의 스냅샷이라 당일 고가나 이후 현재가를 담고 있지
않다. 그래서 이 스크립트가 검증 시점에 다시 라이브로 조회한다.

색상 판정 기준(전일종가 대비 당일고가)과 등락률/등락폭 표시 기준(전일종가
대비 현재가)이 다르다 — 사용자 스펙에 따른 의도적인 구분이다.

사용법: python build_verification_data.py (매일 dart_top3.py 다음에 실행)
"""

import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from dart_top3 import DATA_DIR, fetch_daily_ohlc


def _find_candle(ohlc: List[Dict[str, Any]], yyyymmdd: str) -> Optional[Dict[str, Any]]:
    for c in ohlc:
        if c.get("date") == yyyymmdd:
            return c
    return None


def _prev_trading_candle(ohlc: List[Dict[str, Any]], yyyymmdd: str) -> Optional[Dict[str, Any]]:
    """yyyymmdd보다 이전인 캔들 중 가장 최근 것(직전 영업일 종가)을 찾는다."""
    prior = [c for c in ohlc if c.get("date", "") < yyyymmdd]
    if not prior:
        return None
    return max(prior, key=lambda c: c["date"])


def load_history_entries() -> List[Dict[str, str]]:
    """오늘을 제외한 모든 날짜 파일에서 (종목명, 종목코드, 추천일자) 목록을 모은다."""
    dates_path = os.path.join(DATA_DIR, "dates.json")
    if not os.path.exists(dates_path):
        return []
    with open(dates_path, "r", encoding="utf-8") as f:
        dates = json.load(f)

    today_str = date.today().isoformat()
    entries: List[Dict[str, str]] = []
    for d in dates:
        if d == today_str:
            continue  # 오늘은 아직 당일고가/현재가가 확정되지 않아 검증 대상에서 제외
        path = os.path.join(DATA_DIR, f"{d}.json")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        for entry in payload.get("ranked", []):
            stock_code = entry.get("stock_code", "")
            if not stock_code:
                continue
            entries.append({
                "corp_name": entry.get("corp_name", ""),
                "stock_code": stock_code,
                "rec_date": d,
            })
    return entries


def main() -> None:
    entries = load_history_entries()
    if not entries:
        print("검증할 과거 추천 이력이 없습니다 (오늘 데이터만 존재). 종료합니다.")
        return

    ohlc_cache: Dict[str, List[Dict[str, Any]]] = {}
    results: List[Dict[str, Any]] = []

    for e in entries:
        stock_code = e["stock_code"]
        rec_date_str = e["rec_date"]
        yyyymmdd = rec_date_str.replace("-", "")

        if stock_code not in ohlc_cache:
            print(f"{e['corp_name']}({stock_code}) 일봉 재조회 중...")
            ohlc_cache[stock_code] = fetch_daily_ohlc(stock_code)
        ohlc = ohlc_cache[stock_code]

        if not ohlc:
            print(f"  (참고) {e['corp_name']} 일봉 조회 실패, 건너뜁니다.")
            continue

        prev_candle = _prev_trading_candle(ohlc, yyyymmdd)
        day_candle = _find_candle(ohlc, yyyymmdd)
        latest_candle = max(ohlc, key=lambda c: c["date"])

        if not prev_candle or prev_candle.get("close") is None:
            print(f"  (참고) {e['corp_name']} 전일 종가를 찾지 못해 건너뜁니다.")
            continue

        prev_close = prev_candle["close"]
        day_high = day_candle["high"] if day_candle else None
        current_price = latest_candle["close"]

        # 등락률/등락폭/색상 판정 전부 "전일종가 대비 당일고가" 기준으로 통일한다
        # (2026-08-13, 사용자 확정 - 이전엔 %/금액만 현재가 기준이었는데 헷갈린다는
        # 피드백으로 색상 판정 기준과 일치시킴).
        if day_high is None:
            direction = "unknown"
            change_amount = None
            change_pct = None
        else:
            change_amount = round(day_high - prev_close, 2)
            change_pct = round((day_high - prev_close) / prev_close * 100, 2) if prev_close else None
            if day_high > prev_close:
                direction = "up"
            elif day_high < prev_close:
                direction = "down"
            else:
                direction = "flat"

        results.append({
            "corp_name": e["corp_name"],
            "stock_code": stock_code,
            "rec_date": rec_date_str,
            "prev_close": prev_close,
            "day_high": day_high,
            "current_price": current_price,
            "change_pct": change_pct,
            "change_amount": change_amount,
            "direction": direction,
        })

    results.sort(key=lambda r: r["rec_date"], reverse=True)

    judged = [r for r in results if r["direction"] in ("up", "down", "flat")]
    up_count = sum(1 for r in judged if r["direction"] == "up")
    hit_rate = round(up_count / len(judged) * 100, 1) if judged else None

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_count": len(results),
        "up_count": up_count,
        "hit_rate": hit_rate,
        "entries": results,
    }

    out_path = os.path.join(DATA_DIR, "verification.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"{out_path} 에 {len(results)}건 저장 (적중률 {hit_rate}%)")


if __name__ == "__main__":
    main()
