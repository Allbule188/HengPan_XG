#!/usr/bin/env python3
"""平台突破策略回测：突破日收盘买入，次日收盘卖出。

平台定义：最近 W 根（20/30/60）价格三条件 + 箱体质量 >=0.8。
突破定义：
  A: close > box_top
  B: close > box_top 且 当日量 / 前20日均量 >= 1.5
  C: close > box_top*1.02 且 放量 >=1.5 且 当日涨幅 >=2%

检测频率：每 2 个交易日检测一次平台；检测后 5 个交易日内寻找突破。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

from api.analyzers.box_detector import analyze_box_pattern

WINDOWS = (20, 30, 60)
BOX_THRESHOLD = 0.10
MA_DIFF = 0.02
VOLATILITY = 0.02
BOX_QUALITY = 0.8
AMT_MIN = 3e7
DETECT_STRIDE = 2
MONITOR_DAYS = 5
COST = 0.0015  # 单边约 0.075%，买卖合计 0.15%
SCAN_DATE = "2026-11-03"

VARIANTS = ("A", "B", "C")


def load(path):
    usecols = ["日期", "市场", "代码", "名称", "开盘", "最高", "最低", "收盘",
               "成交量(手)", "成交额(元)"]
    df = pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, dtype={"代码": str})
    df = df.rename(columns={"日期": "date", "市场": "market", "代码": "code",
                            "名称": "name", "开盘": "open", "最高": "high",
                            "最低": "low", "收盘": "close",
                            "成交量(手)": "volume", "成交额(元)": "amount"})
    df["code"] = df["code"].str.zfill(6)
    df["date"] = df["date"].astype(str).str[:10]
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["name"] = df["name"].astype(str).str.strip()
    df = df[df["market"].isin(["SH", "SZ"])]
    df = df[~df["name"].str.contains("ST", case=False, na=False)]
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def price_flags(g):
    close, high, low = g["close"], g["high"], g["low"]
    flags = {}
    for w in WINDOWS:
        hi = high.rolling(w).max()
        lo = low.rolling(w).min()
        box_range = (hi - lo) / lo
        ma_stack = np.vstack([close.rolling(p).mean().to_numpy()
                              for p in (5, 10, 20, 30)])
        ma_diff = np.nanstd(ma_stack, axis=0) / np.nanmean(ma_stack, axis=0)
        vol = close.pct_change().rolling(w).std()
        flags[w] = ((box_range <= BOX_THRESHOLD) &
                    (pd.Series(ma_diff, index=g.index) <= MA_DIFF) &
                    (vol <= VOLATILITY)).fillna(False)
    return flags


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def main():
    csv, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    df = load(csv)
    groups = [(code, g.sort_values("date").reset_index(drop=True))
              for code, g in df.groupby("code", sort=True)]
    print(f"stocks={len(groups)}", file=sys.stderr, flush=True)

    trades = []
    market_frames = []
    for idx, (code, g) in enumerate(groups, 1):
        n = len(g)
        if n < 90:
            continue
        date = g["date"].to_numpy()
        close = g["close"].to_numpy(dtype=float)
        high = g["high"].to_numpy(dtype=float)
        low = g["low"].to_numpy(dtype=float)
        volume = g["volume"].to_numpy(dtype=float)
        amount = g["amount"].to_numpy(dtype=float)
        flags = price_flags(g)
        amt20 = pd.Series(amount).rolling(20).mean().to_numpy()
        ret_next = np.full(n, np.nan)
        ret_next[:-1] = close[1:] / close[:-1] - 1
        market_frames.append(pd.DataFrame({
            "date": date, "ret_next": ret_next, "amt20": amt20}))

        any_flag = np.zeros(n, dtype=bool)
        for w in WINDOWS:
            any_flag |= flags[w].to_numpy()
        start = max(WINDOWS)
        for i in range(start, n - 2, DETECT_STRIDE):
            if not any_flag[i] or not np.isfinite(amt20[i]) or amt20[i] < AMT_MIN:
                continue
            chosen = None
            for w in sorted(WINDOWS, reverse=True):
                if not flags[w].iloc[i]:
                    continue
                window = g.iloc[i - w + 1:i + 1]
                box = analyze_box_pattern(window, w)
                q = finite(box.get("box_quality"), 0.0)
                supports = box.get("support_levels") or []
                resists = box.get("resistance_levels") or []
                if q >= BOX_QUALITY and len(supports) and len(resists):
                    chosen = (w, float(resists[0]), float(supports[0]), q)
                    break
            if chosen is None:
                continue
            w, top, bottom, quality = chosen
            platform_start = date[i - w + 1]
            detect_date = date[i]
            for j in range(i + 1, min(i + 1 + MONITOR_DAYS, n - 1)):
                if close[j] < bottom * 0.98:
                    break
                prior = volume[max(0, j - 20):j]
                if len(prior) < 5:
                    continue
                vr = volume[j] / np.mean(prior) if np.mean(prior) > 0 else np.nan
                day_ret = close[j] / close[j - 1] - 1
                signals = []
                if close[j] > top:
                    signals.append("A")
                if close[j] > top and vr >= 1.5:
                    signals.append("B")
                if close[j] > top * 1.02 and vr >= 1.5 and day_ret >= 0.02:
                    signals.append("C")
                if not signals:
                    continue
                next_ret = close[j + 1] / close[j] - 1
                limit_like = day_ret >= (0.195 if code.startswith(("300", "301", "688", "689")) else 0.095)
                for variant in signals:
                    trades.append({
                        "code": code, "name": g["name"].iloc[0],
                        "market": g["market"].iloc[0],
                        "variant": variant,
                        "detect_date": detect_date,
                        "platform_start": platform_start,
                        "platform_window": w,
                        "box_top": top, "box_bottom": bottom,
                        "box_quality": quality,
                        "signal_date": date[j], "sell_date": date[j + 1],
                        "close": close[j], "next_close": close[j + 1],
                        "day_return": day_ret, "vol_ratio": vr,
                        "trade_return": next_ret,
                        "net_return": next_ret - COST,
                        "limit_like": bool(limit_like),
                    })
                break
        if idx % 500 == 0 or idx == len(groups):
            print(f"  {idx}/{len(groups)} trades={len(trades)}", file=sys.stderr, flush=True)

    tr = pd.DataFrame(trades)
    if len(tr):
        tr = tr.sort_values(["variant", "box_quality", "platform_window"],
                            ascending=[True, False, False])
        tr = tr.drop_duplicates(["code", "signal_date", "variant"], keep="first")
    tr.to_csv(os.path.join(out, "backtest_trades.csv"), index=False,
              encoding="utf-8-sig")

    mkt = pd.concat(market_frames, ignore_index=True)
    mkt = mkt[(mkt["amt20"] >= AMT_MIN) & mkt["ret_next"].notna()]
    mkt_daily = mkt.groupby("date")["ret_next"].mean().sort_index()
    mkt_daily.to_csv(os.path.join(out, "market_daily.csv"),
                     encoding="utf-8-sig", header=True)

    # 给每笔交易补市场同期收益
    if len(tr):
        tr["market_return"] = tr["signal_date"].map(mkt_daily)
        tr["excess"] = tr["trade_return"] - tr["market_return"]
        tr.to_csv(os.path.join(out, "backtest_trades.csv"), index=False,
                  encoding="utf-8-sig")

    summary = []
    daily_rows = []
    for variant in VARIANTS:
        d = tr[tr["variant"] == variant] if len(tr) else tr
        d = d[~d["limit_like"]] if len(d) else d
        if not len(d):
            summary.append({"variant": variant, "trades": 0})
            continue
        ex = d["excess"].dropna()
        n = len(d)
        t_stat = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex))) if len(ex) > 1 and ex.std(ddof=1) > 0 else np.nan
        # 按信号日等权组合，现金日收益 0
        first, last = d["signal_date"].min(), d["signal_date"].max()
        dates = mkt_daily.loc[first:last].index
        strat = d.groupby("signal_date")["trade_return"].mean().reindex(dates).fillna(0.0)
        mkt_s = mkt_daily.reindex(dates).fillna(0.0)
        eq_s = (1 + strat).cumprod()
        eq_m = (1 + mkt_s).cumprod()
        dd = (eq_s / eq_s.cummax() - 1).min()
        sharpe = (np.sqrt(252) * strat.mean() / strat.std(ddof=1)
                  if strat.std(ddof=1) > 0 else np.nan)
        summary.append({
            "variant": variant, "trades": n,
            "mean_return": d["trade_return"].mean(),
            "median_return": d["trade_return"].median(),
            "win_rate": (d["trade_return"] > 0).mean(),
            "mean_net": d["net_return"].mean(),
            "win_rate_net": (d["net_return"] > 0).mean(),
            "mean_market": d["market_return"].mean(),
            "mean_excess": ex.mean(),
            "median_excess": ex.median(),
            "t_stat": t_stat,
            "avg_day_return": d["day_return"].mean(),
            "avg_vol_ratio": d["vol_ratio"].mean(),
            "avg_platform_window": d["platform_window"].mean(),
            "signal_days": d["signal_date"].nunique(),
            "avg_trades_per_day": n / d["signal_date"].nunique(),
            "cum_strategy": eq_s.iloc[-1] - 1,
            "cum_market": eq_m.iloc[-1] - 1,
            "max_drawdown": dd,
            "sharpe": sharpe,
            "first_signal": first, "last_signal": last,
        })
        for dt, row in pd.DataFrame({"strategy": strat, "market": mkt_s}).iterrows():
            daily_rows.append({"variant": variant, "date": dt,
                               "strategy_return": row["strategy"],
                               "market_return": row["market"]})
    sm = pd.DataFrame(summary)
    sm.to_csv(os.path.join(out, "backtest_summary.csv"), index=False,
              encoding="utf-8-sig")
    pd.DataFrame(daily_rows).to_csv(os.path.join(out, "backtest_daily.csv"),
                                    index=False, encoding="utf-8-sig")

    # 按平台窗口拆分
    if len(tr):
        by_w = []
        for variant in VARIANTS:
            d = tr[(tr["variant"] == variant) & (~tr["limit_like"])]
            for w, g in d.groupby("platform_window"):
                by_w.append({"variant": variant, "platform_window": w,
                             "trades": len(g),
                             "mean_return": g["trade_return"].mean(),
                             "win_rate": (g["trade_return"] > 0).mean(),
                             "mean_excess": g["excess"].mean()})
        pd.DataFrame(by_w).to_csv(os.path.join(out, "backtest_by_window.csv"),
                                  index=False, encoding="utf-8-sig")

    print("\n=== summary ===")
    print(sm.to_string(index=False))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
