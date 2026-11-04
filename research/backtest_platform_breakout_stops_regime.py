#!/usr/bin/env python3
"""平台突破策略回测 v2：每日检测，-5% 止损 / +10% 止盈 / 最长 5 日。

平台定义：最近 W 根（20/30/60）价格三条件 + 箱体质量 >=0.8。
突破定义：
  A: close > box_top
  B: close > box_top 且 当日量 / 前20日均量 >= 1.5
  C: close > box_top*1.02 且 放量 >=1.5 且 当日涨幅 >=2%

交易：突破日收盘买入；次日起每日检查止损/止盈；第 5 个交易日收盘强制平仓。
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
DETECT_STRIDE = 1
MONITOR_DAYS = 5
STOP_LOSS = 0.05
TAKE_PROFIT = 0.10
MAX_HOLD = 5
COST = 0.0015
PRE_FILTER = None  # 不预筛，严格每日全量检测

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
    flags, max_high = {}, {}
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
        max_high[w] = hi.to_numpy()
    return flags, max_high


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def simulate(j, entry, open_, high, low, close, n):
    """从 j 日收盘买入，返回 (exit_index, exit_price, reason, mfe, mae)。"""
    stop = entry * (1 - STOP_LOSS)
    target = entry * (1 + TAKE_PROFIT)
    max_high = entry
    min_low = entry
    for d in range(j + 1, min(j + MAX_HOLD, n - 1) + 1):
        o, h, l = open_[d], high[d], low[d]
        max_high = max(max_high, h)
        min_low = min(min_low, l)
        if o <= stop:
            return d, o, "stop_gap", max_high / entry - 1, min_low / entry - 1
        if o >= target:
            return d, o, "target_gap", max_high / entry - 1, min_low / entry - 1
        hit_stop, hit_target = l <= stop, h >= target
        if hit_stop and hit_target:
            return d, stop, "stop_both", max_high / entry - 1, min_low / entry - 1
        if hit_stop:
            return d, stop, "stop", max_high / entry - 1, min_low / entry - 1
        if hit_target:
            return d, target, "target", max_high / entry - 1, min_low / entry - 1
        if d == j + MAX_HOLD:
            return d, close[d], "time", max_high / entry - 1, min_low / entry - 1
    # 数据不足 5 根：不交易
    return None


def main():
    csv, out = sys.argv[1], sys.argv[2]
    index_csv = sys.argv[3] if len(sys.argv) > 3 else "/Users/allbule/Developer/PythonProject/workplaces01/api/data/index_sh000001_day.csv"
    os.makedirs(out, exist_ok=True)
    idx_df = pd.read_csv(index_csv, encoding="utf-8-sig")
    idx_df["date"] = idx_df["date"].astype(str).str[:10]
    idx_df["ma20"] = idx_df["close"].rolling(20).mean()
    idx_df["regime"] = idx_df["close"] > idx_df["ma20"]
    regime_map = dict(zip(idx_df["date"], idx_df["regime"]))
    idx_close_map = dict(zip(idx_df["date"], idx_df["close"]))
    idx_ma_map = dict(zip(idx_df["date"], idx_df["ma20"]))
    df = load(csv)
    groups = [(code, g.sort_values("date").reset_index(drop=True))
              for code, g in df.groupby("code", sort=True)]
    print(f"stocks={len(groups)}", file=sys.stderr, flush=True)

    trades, market_frames = [], []
    for idx, (code, g) in enumerate(groups, 1):
        n = len(g)
        if n < 90:
            continue
        date = g["date"].to_numpy()
        open_ = g["open"].to_numpy(dtype=float)
        close = g["close"].to_numpy(dtype=float)
        high = g["high"].to_numpy(dtype=float)
        low = g["low"].to_numpy(dtype=float)
        volume = g["volume"].to_numpy(dtype=float)
        amount = g["amount"].to_numpy(dtype=float)
        flags, max_high = price_flags(g)
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
                if PRE_FILTER is not None and close[i] < PRE_FILTER * max_high[w][i]:
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
                sim = simulate(j, close[j], open_, high, low, close, n)
                if sim is None:
                    break
                exit_idx, exit_price, reason, mfe, mae = sim
                gross = exit_price / close[j] - 1
                limit_like = day_ret >= (0.195 if code.startswith(("300", "301", "688", "689")) else 0.095)
                for variant in signals:
                    trades.append({
                        "code": code, "name": g["name"].iloc[0],
                        "market": g["market"].iloc[0], "variant": variant,
                        "detect_date": detect_date, "platform_start": platform_start,
                        "platform_window": w, "box_top": top, "box_bottom": bottom,
                        "box_quality": quality,
                        "signal_date": date[j], "exit_date": date[exit_idx],
                        "entry_price": close[j], "exit_price": exit_price,
                        "stop_price": close[j] * (1 - STOP_LOSS),
                        "target_price": close[j] * (1 + TAKE_PROFIT),
                        "day_return": day_ret, "vol_ratio": vr,
                        "holding_days": exit_idx - j, "exit_reason": reason,
                        "mfe": mfe, "mae": mae,
                        "trade_return": gross,
                        "net_return": gross - COST,
                        "limit_like": bool(limit_like),
                        "index_close": idx_close_map.get(date[j]),
                        "index_ma20": idx_ma_map.get(date[j]),
                        "regime_on": bool(regime_map.get(date[j], False)),
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
    mkt_index = (1 + mkt_daily).cumprod()
    mkt_daily.to_csv(os.path.join(out, "market_daily.csv"), encoding="utf-8-sig", header=True)

    if len(tr):
        tr["entry_mkt"] = tr["signal_date"].map(mkt_index)
        tr["exit_mkt"] = tr["exit_date"].map(mkt_index)
        tr["market_return"] = tr["exit_mkt"] / tr["entry_mkt"] - 1
        tr["excess"] = tr["trade_return"] - tr["market_return"]
        tr.to_csv(os.path.join(out, "backtest_trades.csv"), index=False,
                  encoding="utf-8-sig")

    summary, daily_rows = [], []
    for variant in VARIANTS:
        d = tr[tr["variant"] == variant] if len(tr) else tr
        d = d[~d["limit_like"]] if len(d) else d
        if not len(d):
            summary.append({"variant": variant, "trades": 0})
            continue
        ex = d["excess"].dropna()
        t_stat = ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex))) if len(ex) > 1 and ex.std(ddof=1) > 0 else np.nan
        reasons = d["exit_reason"].value_counts(normalize=True).to_dict()
        summary.append({
            "variant": variant, "trades": len(d),
            "mean_gross": d["trade_return"].mean(),
            "median_gross": d["trade_return"].median(),
            "win_rate_gross": (d["trade_return"] > 0).mean(),
            "mean_net": d["net_return"].mean(),
            "median_net": d["net_return"].median(),
            "win_rate_net": (d["net_return"] > 0).mean(),
            "mean_market": d["market_return"].mean(),
            "mean_excess": ex.mean(),
            "median_excess": ex.median(),
            "t_stat": t_stat,
            "avg_holding_days": d["holding_days"].mean(),
            "stop_rate": reasons.get("stop", 0) + reasons.get("stop_gap", 0) + reasons.get("stop_both", 0),
            "target_rate": reasons.get("target", 0) + reasons.get("target_gap", 0),
            "time_rate": reasons.get("time", 0),
            "avg_mfe": d["mfe"].mean(), "avg_mae": d["mae"].mean(),
            "avg_day_return": d["day_return"].mean(),
            "avg_vol_ratio": d["vol_ratio"].mean(),
            "avg_platform_window": d["platform_window"].mean(),
            "signal_days": d["signal_date"].nunique(),
            "avg_trades_per_day": len(d) / d["signal_date"].nunique(),
        })
        # 粗略 cohort 曲线：按 exit_date 等权平均，非资金约束组合
        cohort = d.groupby("exit_date")["net_return"].mean().sort_index()
        first, last = cohort.index.min(), cohort.index.max()
        mkt_period = mkt_daily.loc[first:last]
        eq_s = (1 + cohort).cumprod()
        eq_m = (1 + mkt_period).cumprod()
        dd = (eq_s / eq_s.cummax() - 1).min()
        summary[-1].update({
            "cohort_cum": eq_s.iloc[-1] - 1,
            "market_period_cum": eq_m.iloc[-1] - 1,
            "cohort_max_drawdown": dd,
        })
        for dt, row in pd.DataFrame({"cohort": cohort}).iterrows():
            daily_rows.append({"variant": variant, "exit_date": dt,
                               "cohort_return": row["cohort"]})
    sm = pd.DataFrame(summary)
    sm.to_csv(os.path.join(out, "backtest_summary.csv"), index=False,
              encoding="utf-8-sig")
    pd.DataFrame(daily_rows).to_csv(os.path.join(out, "backtest_cohort_daily.csv"),
                                    index=False, encoding="utf-8-sig")

    if len(tr):
        by_w, by_reason, monthly = [], [], []
        for variant in VARIANTS:
            d = tr[(tr["variant"] == variant) & (~tr["limit_like"])]
            for w, g in d.groupby("platform_window"):
                by_w.append({"variant": variant, "platform_window": w,
                             "trades": len(g),
                             "mean_gross": g["trade_return"].mean(),
                             "win_rate_gross": (g["trade_return"] > 0).mean(),
                             "mean_net": g["net_return"].mean(),
                             "win_rate_net": (g["net_return"] > 0).mean(),
                             "mean_excess": g["excess"].mean(),
                             "avg_holding_days": g["holding_days"].mean()})
            for reason, g in d.groupby("exit_reason"):
                by_reason.append({"variant": variant, "exit_reason": reason,
                                  "trades": len(g),
                                  "mean_gross": g["trade_return"].mean(),
                                  "win_rate_gross": (g["trade_return"] > 0).mean(),
                                  "mean_net": g["net_return"].mean()})
            d = d.copy(); d["month"] = d["signal_date"].str[:7]
            for month, g in d.groupby("month"):
                monthly.append({"variant": variant, "month": month,
                                "trades": len(g),
                                "mean_net": g["net_return"].mean(),
                                "win_rate_net": (g["net_return"] > 0).mean(),
                                "mean_excess": g["excess"].mean()})
        pd.DataFrame(by_w).to_csv(os.path.join(out, "backtest_by_window.csv"),
                                  index=False, encoding="utf-8-sig")
        pd.DataFrame(by_reason).to_csv(os.path.join(out, "backtest_exit_reasons.csv"),
                                       index=False, encoding="utf-8-sig")
        pd.DataFrame(monthly).to_csv(os.path.join(out, "backtest_monthly.csv"),
                                     index=False, encoding="utf-8-sig")

    print("\n=== summary ===")
    print(sm.to_string(index=False))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
