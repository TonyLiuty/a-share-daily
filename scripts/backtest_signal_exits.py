"""Backtest explicit entry and exit rules on saved signals and Tencent daily bars.

This is a research-only tool. It fetches adjusted daily bars on demand, enters at
the next trading day's open, and never changes the production strategy.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Iterable

import requests


@dataclass(frozen=True)
class Bar:
    day: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Trade:
    signal_date: str
    code: str
    name: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    exit_reason: str
    holding_days: int
    gross_return: float


def market_prefix(code: str) -> str:
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def fetch_bars(code: str, cache_dir: Path, throttle: float = 0.25) -> list[Bar]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{code}.json"
    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
    else:
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": f"{market_prefix(code)}{code},day,,,600,qfq"}
        response = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        time.sleep(throttle)
    rows = payload.get("data", {}).get(f"{market_prefix(code)}{code}", {}).get("qfqday", [])
    bars: list[Bar] = []
    for row in rows:
        if len(row) < 5:
            continue
        try:
            bars.append(Bar(str(row[0]), float(row[1]), float(row[3]), float(row[4]), float(row[2])))
        except (TypeError, ValueError):
            continue
    return sorted(bars, key=lambda item: item.day)


def moving_average(bars: list[Bar], index: int, window: int = 20) -> float | None:
    if index + 1 < window:
        return None
    return mean(bar.close for bar in bars[index - window + 1 : index + 1])


def simulate(row: dict[str, str], bars: list[Bar], stop_loss: float, take_profit: float, max_hold: int, trend_days: int) -> Trade | None:
    entry_day = row.get("entry_date", "")
    start = next((i for i, bar in enumerate(bars) if bar.day == entry_day), None)
    if start is None or start >= len(bars):
        return None
    entry = bars[start].open
    if entry <= 0:
        return None
    stop_price, take_price = entry * (1 - stop_loss), entry * (1 + take_profit)
    last = min(start + max_hold - 1, len(bars) - 1)
    for index in range(start, last + 1):
        bar = bars[index]
        holding = index - start + 1
        hit_stop = bar.low <= stop_price
        hit_take = bar.high >= take_price
        if hit_stop:  # conservative when both barriers are crossed intraday
            exit_price, reason = stop_price, "stop_loss"
        elif hit_take:
            exit_price, reason = take_price, "take_profit"
        elif holding >= trend_days:
            ma20 = moving_average(bars, index)
            if ma20 is not None and bar.close < ma20:
                exit_price, reason = bar.close, "trend_break"
            else:
                continue
        elif holding == max_hold:
            exit_price, reason = bar.close, "max_hold"
        else:
            continue
        return Trade(row["signal_date"], row["code"], row.get("name", ""), bar.day, entry, bar.day, exit_price, reason, holding, exit_price / entry - 1)
    bar = bars[last]
    return Trade(row["signal_date"], row["code"], row.get("name", ""), bars[start].day, entry, bar.day, bar.close, "max_hold", last - start + 1, bar.close / entry - 1)


def read_signals(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        code, day = row.get("code", ""), row.get("signal_date", "")
        if len(code) == 6 and day:
            unique[(day, code)] = row
    return list(unique.values())


def summarize(trades: list[Trade], costs_bp: Iterable[int]) -> dict:
    gross = [trade.gross_return for trade in trades]
    by_reason: dict[str, int] = {}
    for trade in trades:
        by_reason[trade.exit_reason] = by_reason.get(trade.exit_reason, 0) + 1
    return {
        "trades_n": len(trades),
        "signal_dates_n": len({trade.signal_date for trade in trades}),
        "gross_mean_return": mean(gross) if gross else None,
        "win_rate": sum(value > 0 for value in gross) / len(gross) if gross else None,
        "exit_reasons": by_reason,
        "cost_sensitivity": {f"{bp}bp": (mean(gross) - bp / 10000) if gross else None for bp in costs_bp},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only saved-signal exit backtest")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-loss", type=float, default=0.08)
    parser.add_argument("--take-profit", type=float, default=0.12)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--trend-days", type=int, default=5)
    args = parser.parse_args()
    rows = read_signals(args.signals)
    codes = sorted({row["code"] for row in rows})
    bars = {code: fetch_bars(code, args.cache) for code in codes}
    trades = [trade for row in rows if (trade := simulate(row, bars.get(row["code"], []), args.stop_loss, args.take_profit, args.max_hold, args.trend_days))]
    result = {
        "schema_version": 1,
        "strategy_version": "1.0.0",
        "research_only": True,
        "data_source": "Tencent fqkline qfq daily bars",
        "rules": {
            "entry": "signal close -> next trading day open",
            "exit_priority": ["stop_loss", "take_profit", "trend_break", "max_hold"],
            "stop_loss": args.stop_loss,
            "take_profit": args.take_profit,
            "trend_break": f"close below MA20 after {args.trend_days} trading days",
            "max_hold_days": args.max_hold,
            "same_bar_policy": "stop loss wins when both barriers are crossed",
        },
        "input": {"signals": str(args.signals), "signals_n": len(rows), "codes_n": len(codes)},
        "summary": summarize(trades, (5, 10, 20)),
        "trades": [asdict(trade) for trade in trades],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", "trades_n": len(trades), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
