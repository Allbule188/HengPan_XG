#!/usr/bin/env python3
"""日线横盘参数的历史事件研究：T+5/10/20 收益 + 箱体保持率。

用法：--part tf（容刺+定高）或 --part am（振幅+均线）。
"""
import argparse
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from api.hengpan.anchored_box import extract_series, check_series
from api.hengpan.tolerant_box import check_tolerant_series
from api.hengpan.ma_flat import check_ma_flat_series

RULES = {
    "tol_4_20": dict(kind="tolerant", box_height=0.04, lookback=20, max_breach=4,
                     max_consecutive_breach=1),
    "tol_5_20": dict(kind="tolerant", box_height=0.05, lookback=20, max_breach=4,
                     max_consecutive_breach=1),
    "tol_6_20": dict(kind="tolerant", box_height=0.06, lookback=20, max_breach=4,
                     max_consecutive_breach=1),
    "tol_5_30": dict(kind="tolerant", box_height=0.05, lookback=30, max_breach=4,
                     max_consecutive_breach=1),
    "tol_6_30": dict(kind="tolerant", box_height=0.06, lookback=30, max_breach=4,
                     max_consecutive_breach=1),
    "fix_6_20": dict(kind="fixed", box_height=0.06, doji_amplitude=0.01,
                     lookback=20, max_breach=2),
    "fix_8_20": dict(kind="fixed", box_height=0.08, doji_amplitude=0.01,
                     lookback=20, max_breach=2),
    "fix_8_30": dict(kind="fixed", box_height=0.08, doji_amplitude=0.01,
                     lookback=30, max_breach=2),
    "amp_0.5_20": dict(kind="amplitude", amp_multiple=0.5, max_amplitude=0.08,
                       lookback=20, max_breach=2),
    "ma_20_30_1": dict(kind="ma", ma_period=20, min_flat_bars=30,
                       ma_tolerance=0.01, max_efficiency_ratio=0.5),
    "ma_30_40_1": dict(kind="ma", ma_period=30, min_flat_bars=40,
                       ma_tolerance=0.01, max_efficiency_ratio=0.5),
}
PARTS = {
    "tf": ["tol_4_20", "tol_5_20", "tol_6_20", "tol_5_30", "tol_6_30",
           "fix_6_20", "fix_8_20", "fix_8_30"],
    "am": ["amp_0.5_20", "ma_20_30_1", "ma_30_40_1"],
}
HORIZONS = (5, 10, 20)


def required(c):
    if c["kind"] == "tolerant":
        return c["lookback"]
    if c["kind"] in ("fixed", "amplitude"):
        return c["lookback"] + 1
    if c["kind"] == "ma":
        return c["ma_period"] + c["min_flat_bars"] - 1
    raise ValueError(c["kind"])


def evaluate(series, c):
    if c["kind"] == "tolerant":
        return check_tolerant_series(series, box_height=c["box_height"],
                                     lookback=c["lookback"], max_breach=c["max_breach"],
                                     max_consecutive_breach=c["max_consecutive_breach"])
    if c["kind"] in ("fixed", "amplitude"):
        return check_series(series, doji_amplitude=c.get("doji_amplitude", 0.01),
                            box_height=c.get("box_height", 0.04),
                            lookback=c["lookback"], max_breach=c["max_breach"],
                            box_type=c["kind"], amp_multiple=c.get("amp_multiple", 1.0),
                            max_amplitude=c.get("max_amplitude"))
    if c["kind"] == "ma":
        return check_ma_flat_series(series, ma_period=c["ma_period"],
                                    min_flat_bars=c["min_flat_bars"],
                                    ma_tolerance=c["ma_tolerance"],
                                    max_efficiency_ratio=c["max_efficiency_ratio"])
    raise ValueError(c["kind"])


def continuous_tail(dates, cal_pos):
    start = 0
    for i in range(1, len(dates)):
        if cal_pos[dates[i]] - cal_pos[dates[i - 1]] != 1:
            start = i
    return start


def month_end_dates(calendar, months):
    out = []
    for ym in months:
        days = [d for d in calendar if d[:7] == ym]
        if days:
            out.append(days[-1])
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--part", required=True, choices=("tf", "am"))
    args = p.parse_args()

    rule_ids = PARTS[args.part]
    print(f"part={args.part} rules={rule_ids}", flush=True)

    usecols = ["日期", "市场", "代码", "名称", "开盘", "最高", "最低", "收盘",
               "成交量(手)", "成交额(元)"]
    df = pd.read_csv(args.csv, encoding="utf-8-sig", usecols=usecols,
                     dtype={"代码": str})
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
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    calendar = sorted(df["date"].unique())
    cal_pos = {d: i for i, d in enumerate(calendar)}
    months = ["2025-09", "2025-10", "2025-12", "2026-01", "2026-03",
              "2026-04", "2026-06", "2026-08", "2026-09"]
    scan_dates = month_end_dates(calendar, months)
    print("scan_dates:", scan_dates, flush=True)

    # 只保留有足够未来数据的扫描日
    scan_dates = [d for d in scan_dates
                  if cal_pos[d] + max(HORIZONS) < len(calendar)]
    print("usable:", scan_dates, flush=True)

    universe_rows = []
    match_rows = []
    total = df["code"].nunique()
    for i, (code, group) in enumerate(df.groupby("code", sort=True), 1):
        group = group.sort_values("date").reset_index(drop=True)
        dates = group["date"].tolist()
        pos_map = {d: j for j, d in enumerate(dates)}
        closes = group["close"].to_numpy(dtype=float)
        amounts = group["amount"].to_numpy(dtype=float)
        name = group["name"].iloc[-1]
        market = group["market"].iloc[-1]
        cont_start = continuous_tail(dates, cal_pos)
        for scan_date in scan_dates:
            if scan_date not in pos_map:
                continue
            j = pos_map[scan_date]
            hist_len = j + 1
            if hist_len < 21:
                continue
            amt60 = float(np.nanmean(amounts[max(0, hist_len - 60):hist_len]))
            if not np.isfinite(amt60) or amt60 < 3e7:
                continue
            rets = np.diff(np.log(closes[max(0, hist_len - 21):hist_len]))
            vol20 = float(np.std(rets, ddof=1)) if len(rets) >= 20 else np.nan
            close0 = closes[j]
            fwd = {}
            for h in HORIZONS:
                target = calendar[cal_pos[scan_date] + h]
                if target in pos_map:
                    fwd[h] = closes[pos_map[target]] / close0 - 1
                else:
                    fwd[h] = np.nan
            universe_rows.append({"date": scan_date, "code": code, "name": name,
                                  "market": market, "close": close0, "amt60": amt60,
                                  "vol20": vol20,
                                  **{f"ret{h}": fwd[h] for h in HORIZONS}})
            # 历史序列按规则需要裁剪
            hist = group.iloc[:hist_len]
            full_series = None
            cont_series = None
            for rid in rule_ids:
                c = RULES[rid]
                req = required(c)
                if c["kind"] in ("ma",):
                    start = max(cont_start, 0)
                    if hist_len - start < req:
                        continue
                    if cont_series is None:
                        cont_series = extract_series(
                            group.iloc[start:hist_len].reset_index(drop=True))
                    series = cont_series
                else:
                    if hist_len < req:
                        continue
                    if cal_pos[dates[j]] - cal_pos[dates[j - req + 1]] + 1 != req:
                        continue
                    if full_series is None:
                        full_series = extract_series(hist)
                    series = full_series
                res = evaluate(series, c)
                if res is None or not res["passed_full"]:
                    continue
                match_rows.append({
                    "date": scan_date, "rule": rid, "code": code, "name": name,
                    "market": market, "close": close0,
                    "lower": float(res["lower"]), "upper": float(res["upper"]),
                    "actual_range": float(res["actual_range"]),
                    "box_width": (float(res["upper"]) - float(res["lower"]))
                                 / float(res["lower"]),
                    **{f"ret{h}": fwd[h] for h in HORIZONS},
                })
        if i % 500 == 0 or i == total:
            print(f"  {i}/{total} universe={len(universe_rows)} matches={len(match_rows)}",
                  flush=True)

    uni = pd.DataFrame(universe_rows)
    mat = pd.DataFrame(match_rows)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    uni.to_csv(args.out.replace(".csv", "_universe.csv"), index=False,
               encoding="utf-8-sig")
    mat.to_csv(args.out.replace(".csv", "_matches.csv"), index=False,
               encoding="utf-8-sig")

    rows = []
    for scan_date in scan_dates:
        base = uni[uni["date"] == scan_date]
        if base.empty:
            continue
        for rid in rule_ids:
            m = mat[(mat["date"] == scan_date) & (mat["rule"] == rid)]
            if m.empty:
                continue
            for h in HORIZONS:
                b = base[base[f"ret{h}"].notna()]
                mm = m[m[f"ret{h}"].notna()]
                if b.empty or mm.empty:
                    continue
                n = len(mm)
                lv = b[b["vol20"].notna()].sort_values("vol20").head(n)
                inside = ((mm["close"] * (1 + mm[f"ret{h}"]) >= mm["lower"]) &
                          (mm["close"] * (1 + mm[f"ret{h}"]) <= mm["upper"])).mean()
                rows.append({
                    "date": scan_date, "rule": rid, "horizon": h,
                    "n": n,
                    "mean_ret": mm[f"ret{h}"].mean(),
                    "median_ret": mm[f"ret{h}"].median(),
                    "win_rate": (mm[f"ret{h}"] > 0).mean(),
                    "base_mean": b[f"ret{h}"].mean(),
                    "base_median": b[f"ret{h}"].median(),
                    "base_win": (b[f"ret{h}"] > 0).mean(),
                    "lowvol_mean": lv[f"ret{h}"].mean() if len(lv) else np.nan,
                    "excess_all": mm[f"ret{h}"].mean() - b[f"ret{h}"].mean(),
                    "excess_lowvol": (mm[f"ret{h}"].mean() - lv[f"ret{h}"].mean())
                                     if len(lv) else np.nan,
                    "inside_rate": inside,
                })
    ev = pd.DataFrame(rows)
    ev.to_csv(args.out, index=False, encoding="utf-8-sig")

    summary = []
    for rid in rule_ids:
        for h in HORIZONS:
            d = ev[(ev["rule"] == rid) & (ev["horizon"] == h)]
            if d.empty:
                continue
            summary.append({
                "rule": rid, "horizon": h, "dates": len(d),
                "avg_n": d["n"].mean(),
                "mean_excess_all": d["excess_all"].mean(),
                "median_excess_all": d["excess_all"].median(),
                "pos_dates_all": (d["excess_all"] > 0).sum(),
                "mean_excess_lowvol": d["excess_lowvol"].mean(),
                "median_excess_lowvol": d["excess_lowvol"].median(),
                "pos_dates_lowvol": (d["excess_lowvol"] > 0).sum(),
                "avg_inside": d["inside_rate"].mean(),
                "avg_win": d["win_rate"].mean(),
            })
    sm = pd.DataFrame(summary)
    sm.to_csv(args.out.replace(".csv", "_summary.csv"), index=False,
              encoding="utf-8-sig")
    print("\n=== summary ===")
    print(sm.to_string(index=False))
    print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
