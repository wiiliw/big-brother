#!/usr/bin/env python3
"""Dage monitoring MVP data judgement.

Open-source data source:
  - AKShare: https://github.com/akfamily/akshare

Run without changing project dependencies:
  uv run --with akshare --with pandas --with numpy python dage_data_judgement.py

The file intentionally covers only the first two MVP blocks:
  1. Turnover temperature + leverage relative heat
  2. Broad-index relative volatility

It does not implement SOE market-value management or policy/window-guidance
signals yet.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

try:
    import akshare as ak
    import numpy as np
    import pandas as pd
except ImportError as exc:  # pragma: no cover - exercised by users without deps
    raise SystemExit(
        "Missing runtime dependencies. Run:\n"
        "  uv run --with akshare --with pandas --with numpy "
        "python dage_data_judgement.py"
    ) from exc


INDEX_POOL: dict[str, dict[str, str]] = {
    "000016": {"name": "上证50", "symbol": "sh000016", "circle": "core"},
    "000300": {"name": "沪深300", "symbol": "sh000300", "circle": "core"},
    "000510": {"name": "中证A500", "symbol": "sh000510", "circle": "middle"},
    "000905": {"name": "中证500", "symbol": "sh000905", "circle": "middle"},
    "000852": {"name": "中证1000", "symbol": "sh000852", "circle": "outer"},
}


def parse_yyyymmdd(value: str | None) -> date:
    if value:
        return datetime.strptime(value, "%Y%m%d").date()
    return date.today()


def to_yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def yuan_to_trillion(value: float) -> float:
    return value / 1_000_000_000_000


def robust_relative_metrics(current: float, baseline: pd.Series) -> dict[str, Any]:
    baseline = pd.to_numeric(baseline, errors="coerce").dropna()
    if not math.isfinite(current) or baseline.empty:
        return {"base": None, "mad": None, "multiple": None, "z": None, "percentile": None}

    base = float(baseline.median())
    mad = float((baseline - base).abs().median())
    scale = 1.4826 * mad
    multiple = current / base if base else None
    z_score = (current - base) / scale if scale else None
    percentile = float((baseline <= current).mean() * 100)

    return {
        "base": base,
        "mad": mad,
        "multiple": multiple,
        "z": z_score,
        "percentile": percentile,
    }


def status_from_bands(
    multiple: float | None,
    z_score: float | None,
    percentile: float | None,
    bands: list[tuple[str, float, float, float]],
) -> str:
    if multiple is None and z_score is None and percentile is None:
        return "insufficient_data"
    status = "normal"
    for label, multiple_cut, z_cut, percentile_cut in bands:
        if (
            (multiple is not None and multiple >= multiple_cut)
            or (z_score is not None and z_score >= z_cut)
            or (percentile is not None and percentile >= percentile_cut)
        ):
            status = label
    return status


def turnover_status(total_amount_yuan: float) -> str:
    trillion = yuan_to_trillion(total_amount_yuan)
    if trillion < 2:
        return "cold"
    if trillion < 2.5:
        return "active_normal"
    if trillion < 3:
        return "warm"
    if trillion < 3.5:
        return "overheat_watch"
    if trillion < 4:
        return "obvious_overheat"
    return "extreme_heat"


@dataclass(frozen=True)
class AkshareSource:
    sleep_seconds: float = 0.15

    def index_daily(self, symbol: str, end_date: date) -> pd.DataFrame:
        df = ak.stock_zh_index_daily(symbol=symbol)
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"].dt.date <= end_date]
        df = df.sort_values("date")
        return df

    def sse_turnover_yuan(self, query_date: date) -> float:
        df = ak.stock_sse_deal_daily(date=to_yyyymmdd(query_date))
        row = df[df["单日情况"] == "成交金额"]
        if row.empty:
            raise ValueError(f"SSE turnover row not found for {query_date}")
        # AKShare/SSE reports 成交金额 in 亿元.
        return float(row.iloc[0]["股票"]) * 100_000_000

    def szse_turnover_yuan(self, query_date: date) -> float:
        df = ak.stock_szse_summary(date=to_yyyymmdd(query_date))
        row = df[df["证券类别"] == "股票"]
        if row.empty:
            raise ValueError(f"SZSE stock row not found for {query_date}")
        # AKShare/SZSE reports 成交金额 in 元.
        return float(row.iloc[0]["成交金额"])

    def sse_margin_range(self, start_date: date, end_date: date) -> pd.DataFrame:
        df = ak.stock_margin_sse(start_date=to_yyyymmdd(start_date), end_date=to_yyyymmdd(end_date))
        df = df.copy()
        df["date"] = pd.to_datetime(df["信用交易日期"], format="%Y%m%d").dt.date
        df["sse_financing_buy_yuan"] = pd.to_numeric(df["融资买入额"], errors="coerce")
        return df[["date", "sse_financing_buy_yuan"]]

    def szse_margin_yuan(self, query_date: date) -> float:
        df = ak.stock_margin_szse(date=to_yyyymmdd(query_date))
        if df.empty:
            raise ValueError(f"SZSE margin row not found for {query_date}")
        # AKShare/SZSE reports 融资买入额 in 亿元.
        return float(df.iloc[0]["融资买入额"]) * 100_000_000


def turnover_for_date(source: AkshareSource, query_date: date) -> dict[str, Any]:
    sse = source.sse_turnover_yuan(query_date)
    szse = source.szse_turnover_yuan(query_date)
    total = sse + szse
    return {
        "date": query_date.isoformat(),
        "sse_turnover_yuan": sse,
        "szse_turnover_yuan": szse,
        "total_turnover_yuan": total,
        "total_turnover_trillion": yuan_to_trillion(total),
        "status": turnover_status(total),
    }


def financing_buy_for_date(source: AkshareSource, query_date: date) -> dict[str, Any]:
    sse_df = source.sse_margin_range(query_date, query_date)
    if sse_df.empty:
        raise ValueError(f"SSE margin data not found for {query_date}")
    sse = float(sse_df.iloc[-1]["sse_financing_buy_yuan"])
    szse = source.szse_margin_yuan(query_date)
    return {
        "date": query_date.isoformat(),
        "sse_financing_buy_yuan": sse,
        "szse_financing_buy_yuan": szse,
        "total_financing_buy_yuan": sse + szse,
    }


def latest_market_snapshot(source: AkshareSource, end_date: date, backtrack_days: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Find the latest date where turnover and financing-buy data both exist."""
    errors: list[str] = []
    for offset in range(backtrack_days + 1):
        query_date = end_date - timedelta(days=offset)
        try:
            turnover = turnover_for_date(source, query_date)
            financing = financing_buy_for_date(source, query_date)
            return turnover, financing
        except Exception as exc:  # data may not exist on holidays/non-trading days
            errors.append(f"{query_date}: {type(exc).__name__}: {exc}")
            time.sleep(source.sleep_seconds)
    raise RuntimeError("No complete market snapshot found. Recent errors: " + " | ".join(errors[-3:]))


def leverage_history(
    source: AkshareSource,
    end_date: date,
    trading_dates: list[date],
    max_points: int,
) -> pd.DataFrame:
    selected_dates = trading_dates[-max_points:]
    sse_range = source.sse_margin_range(selected_dates[0], selected_dates[-1])
    rows: list[dict[str, Any]] = []

    for query_date in selected_dates:
        try:
            sse_turnover = source.sse_turnover_yuan(query_date)
            szse_turnover = source.szse_turnover_yuan(query_date)
            sse_margin_row = sse_range[sse_range["date"] == query_date]
            if sse_margin_row.empty:
                raise ValueError("missing SSE margin")
            sse_buy = float(sse_margin_row.iloc[0]["sse_financing_buy_yuan"])
            szse_buy = source.szse_margin_yuan(query_date)
            total_turnover = sse_turnover + szse_turnover
            rows.append(
                {
                    "date": query_date,
                    "turnover_yuan": total_turnover,
                    "financing_buy_yuan": sse_buy + szse_buy,
                    "r": (sse_buy + szse_buy) / total_turnover if total_turnover else None,
                }
            )
        except Exception:
            # Keep the script resilient to per-day exchange API gaps.
            pass
        time.sleep(source.sleep_seconds)

    df = pd.DataFrame(rows).sort_values("date")
    return df


def leverage_judgement(
    latest: dict[str, Any],
    turnover: dict[str, Any],
    history: pd.DataFrame | None,
) -> dict[str, Any]:
    current_r = latest["total_financing_buy_yuan"] / turnover["total_turnover_yuan"]
    result: dict[str, Any] = {
        "date": latest["date"],
        "financing_buy_yuan": latest["total_financing_buy_yuan"],
        "financing_buy_ratio": current_r,
        "status": "latest_ratio_only",
        "relative": None,
    }

    if history is None or len(history) < 80:
        result["note"] = "Need more history for LFM/LFZ/LFP; rerun with --with-leverage-history."
        return result

    history = history.copy()
    history["r5"] = history["r"].rolling(5).mean()
    latest_r5 = float(history.iloc[-1]["r5"])
    baseline = history["r5"].dropna().iloc[:-20].tail(252)
    metrics = robust_relative_metrics(latest_r5, baseline)
    result["relative"] = {
        "r5": latest_r5,
        "base252": metrics["base"],
        "mad252": metrics["mad"],
        "lfm": metrics["multiple"],
        "lfz": metrics["z"],
        "lfp": metrics["percentile"],
    }
    result["status"] = status_from_bands(
        metrics["multiple"],
        metrics["z"],
        metrics["percentile"],
        [
            ("warming", 1.10, 1.0, 80),
            ("warning", 1.25, 2.0, 90),
            ("obvious_overheat", 1.50, 3.0, 95),
            ("extreme_heat", 1.80, 4.0, 98),
        ],
    )
    return result


def volatility_for_index(source: AkshareSource, code: str, meta: dict[str, str], end_date: date) -> dict[str, Any]:
    df = source.index_daily(symbol=meta["symbol"], end_date=end_date)
    if len(df) < 300:
        raise ValueError(f"Not enough index history for {code}")

    df = df.copy()
    df["ret"] = pd.to_numeric(df["close"], errors="coerce").pct_change()
    df["rv10"] = df["ret"].rolling(10).std() * math.sqrt(252)
    df["rv20"] = df["ret"].rolling(20).std() * math.sqrt(252)

    latest = df.dropna(subset=["rv10"]).iloc[-1]
    latest_idx = int(latest.name)
    baseline = df.loc[:latest_idx, "rv10"].dropna().iloc[:-20].tail(252)
    metrics = robust_relative_metrics(float(latest["rv10"]), baseline)

    last_5_ret = float((1 + df["ret"].tail(5)).prod() - 1)
    rv20 = float(latest["rv20"]) if math.isfinite(float(latest["rv20"])) else None
    move_z5 = None
    if rv20 and rv20 > 0:
        move_z5 = last_5_ret / (rv20 * math.sqrt(5 / 252))

    status = status_from_bands(
        metrics["multiple"],
        metrics["z"],
        metrics["percentile"],
        [
            ("warming", 1.10, 1.0, 80),
            ("warning", 1.30, 2.0, 90),
            ("obvious_abnormal", 1.60, 3.0, 95),
            ("extreme_abnormal", 2.00, 4.0, 98),
        ],
    )

    direction = "shock"
    if move_z5 is not None and move_z5 > 1.5:
        direction = "sharp_rise"
    elif move_z5 is not None and move_z5 < -1.5:
        direction = "sharp_fall"

    return {
        "code": code,
        "name": meta["name"],
        "circle": meta["circle"],
        "date": latest["date"].date().isoformat(),
        "close": float(latest["close"]),
        "rv10": float(latest["rv10"]),
        "base252": metrics["base"],
        "mad252": metrics["mad"],
        "vfm": metrics["multiple"],
        "vfz": metrics["z"],
        "vfp": metrics["percentile"],
        "move_z5": move_z5,
        "direction": direction,
        "status": status,
    }


def volatility_judgement(source: AkshareSource, end_date: date) -> dict[str, Any]:
    results = [volatility_for_index(source, code, meta, end_date) for code, meta in INDEX_POOL.items()]
    by_code = {item["code"]: item for item in results}

    core_abnormal = all(
        by_code[code]["vfm"] is not None and by_code[code]["vfm"] >= 1.60
        for code in ("000016", "000300")
    )
    broad_warning_count = sum(
        1 for item in results if item["vfm"] is not None and item["vfm"] >= 1.30
    )
    core_extreme = any(
        item["circle"] == "core"
        and item["vfm"] is not None
        and item["vfm"] >= 2.00
        and item["move_z5"] is not None
        and abs(item["move_z5"]) > 1.5
        for item in results
    )

    if core_extreme:
        market_status = "extreme_red"
    elif core_abnormal or broad_warning_count >= 3:
        market_status = "red"
    elif any(item["circle"] == "core" and item["vfm"] is not None and item["vfm"] >= 1.60 for item in results) or broad_warning_count >= 2:
        market_status = "orange"
    elif broad_warning_count >= 1:
        market_status = "yellow"
    else:
        market_status = "normal"

    return {
        "market_status": market_status,
        "warning_count": broad_warning_count,
        "indices": results,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source = AkshareSource(sleep_seconds=args.sleep)
    end_date = parse_yyyymmdd(args.end_date)

    turnover, financing = latest_market_snapshot(source, end_date, args.backtrack_days)

    history_df = None
    if args.with_leverage_history:
        csi300 = source.index_daily(INDEX_POOL["000300"]["symbol"], end_date)
        trading_dates = list(pd.to_datetime(csi300["date"]).dt.date)
        history_df = leverage_history(source, end_date, trading_dates, args.leverage_points)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": "AKShare open-source Python financial data interface",
        "turnover": turnover,
        "leverage": leverage_judgement(financing, turnover, history_df),
        "volatility": volatility_judgement(source, end_date),
        "scope_note": "Only MVP blocks 1 and 2 are implemented. SOE market-value management and policy/window-guidance are not implemented here.",
    }
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dage monitoring MVP data judgement")
    parser.add_argument("--end-date", help="End date in YYYYMMDD. Defaults to today.")
    parser.add_argument("--backtrack-days", type=int, default=10, help="Backtrack days for latest exchange summary.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Sleep seconds between exchange API calls.")
    parser.add_argument(
        "--with-leverage-history",
        action="store_true",
        help="Fetch historical turnover/margin data to compute LFM/LFZ/LFP. This can be slow.",
    )
    parser.add_argument(
        "--leverage-points",
        type=int,
        default=300,
        help="Trading dates to fetch for leverage history when --with-leverage-history is set.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
