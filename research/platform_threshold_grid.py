#!/usr/bin/env python3
"""平台扫描阈值敏感性：量能、低位回撤、快速下跌窗口/幅度。"""
import contextlib
import os
import sys

import numpy as np
import pandas as pd

from api.analyzers.price_analyzer import calculate_price_features
from api.analyzers.box_detector import analyze_box_pattern
from api.analyzers.volume_analyzer import calculate_volume_features
from api.analyzers.decline_analyzer import analyze_decline_speed

WINDOWS = [20, 30, 60]
BOX_THRESHOLD = 0.10
MA_DIFF = 0.02
VOLATILITY = 0.02
BOX_QUALITY = 0.8
SCAN_DATE = "2026-11-03"


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
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    groups = []
    for code, group in df.groupby("code", sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        if group["date"].iloc[-1] == SCAN_DATE and len(group) >= 300:
            groups.append((code, group))
    return groups


def main():
    csv, out = sys.argv[1], sys.argv[2]
    groups = load(csv)
    print(f"groups={len(groups)}", file=sys.stderr, flush=True)
    rows = []
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
        for i, (code, g) in enumerate(groups, 1):
            price = {w: calculate_price_features(g, w) for w in WINDOWS}
            box = {w: analyze_box_pattern(g, w) for w in WINDOWS}
            vol = {w: calculate_volume_features(g, w) for w in WINDOWS}
            decline = analyze_decline_speed(g, 300, 180, 0.0, 30, 0.0)
            dd = decline.get("details", {}) if decline.get("status") == "analyzed" else {}
            rapid = {}
            high_date = dd.get("high_date")
            if high_date is not None:
                hit = g.index[g["date"] == str(high_date)[:10]]
                after = g.iloc[hit[0]:].reset_index(drop=True) if len(hit) else g.iloc[0:0]
            else:
                after = g.iloc[0:0]
            for rd in (10, 15, 20, 30):
                value = 0.0
                if len(after) >= rd:
                    for j in range(len(after) - rd + 1):
                        win = after.iloc[j:j + rd]
                        hi = float(win["high"].max())
                        lo = float(win["low"].min())
                        value = max(value, (hi - lo) / hi if hi > 0 else 0.0)
                rapid[rd] = value
            price_ok = {w: (price[w]["box_range"] <= BOX_THRESHOLD and
                            price[w]["ma_diff"] <= MA_DIFF and
                            price[w]["volatility"] <= VOLATILITY) for w in WINDOWS}
            box_ok = {w: (price_ok[w] and
                          (box[w].get("box_quality", 0) or 0) >= BOX_QUALITY and
                          (box[w].get("volatility") or np.inf) <= VOLATILITY)
                      for w in WINDOWS}
            rows.append({
                "code": code, "name": g["name"].iloc[-1], "market": g["market"].iloc[-1],
                "close": float(g["close"].iloc[-1]),
                "box_any": any(box_ok.values()),
                "max_box_ok": bool(box[max(WINDOWS)].get("is_box_pattern", False)),
                **{f"price_ok_{w}": price_ok[w] for w in WINDOWS},
                **{f"box_ok_{w}": box_ok[w] for w in WINDOWS},
                **{f"vc_{w}": vol[w].get("volume_change_ratio") for w in WINDOWS},
                **{f"vs_{w}": vol[w].get("volume_stability") for w in WINDOWS},
                "decline_pct": dd.get("decline_percentage"),
                **{f"max_rapid_{rd}": rapid[rd] for rd in (10, 15, 20, 30)},
                "high_date": dd.get("high_date"),
                "low_date": dd.get("low_date"),
                "decline_status": decline.get("status"),
            })
            if i % 500 == 0 or i == len(groups):
                print(f"  {i}/{len(groups)}", file=sys.stderr, flush=True)

    df = pd.DataFrame(rows)
    os.makedirs(out, exist_ok=True)
    df.to_csv(os.path.join(out, "per_stock_metrics.csv"), index=False, encoding="utf-8-sig")

    # 时间差（日历天）用于低位周期
    hi = pd.to_datetime(df["high_date"], errors="coerce")
    lo = pd.to_datetime(df["low_date"], errors="coerce")
    df["decline_calendar_days"] = (lo - hi).dt.days

    grid = []
    for vc, vs in [(0.5, 0.5), (0.8, 0.7), (0.9, 0.75), (1.0, 0.8), (1.2, 0.9), (1.5, 1.0)]:
        vol_any = pd.Series(False, index=df.index)
        for w in WINDOWS:
            vol_any |= (df[f"box_ok_{w}"].fillna(False) &
                        (df[f"vc_{w}"] <= vc) & (df[f"vs_{w}"] <= vs))
        for decline_th in (0.10, 0.15, 0.20, 0.25, 0.30):
            for period in (180, 365):
                low_ok = (df["decline_pct"] >= decline_th) & (df["decline_calendar_days"] <= period)
                for rapid_days, rapid_th in [(10, 0.10), (10, 0.15),
                                             (15, 0.10), (15, 0.15),
                                             (20, 0.10), (20, 0.15),
                                             (30, 0.10), (30, 0.15), (30, 0.20)]:
                    rapid_ok = df[f"max_rapid_{rapid_days}"] >= rapid_th
                    final = vol_any & df["max_box_ok"].fillna(False) & low_ok & rapid_ok
                    grid.append({
                        "volume_change": vc, "volume_stability": vs,
                        "decline_threshold": decline_th, "decline_period_days": period,
                        "rapid_days": rapid_days, "rapid_threshold": rapid_th,
                        "vol_any": int(vol_any.sum()),
                        "low_ok": int(low_ok.sum()),
                        "rapid_ok": int(rapid_ok.sum()),
                        "final": int(final.sum()),
                    })
    grid_df = pd.DataFrame(grid)
    grid_df = grid_df.drop_duplicates(
        subset=["volume_change", "volume_stability", "decline_threshold",
                "decline_period_days", "rapid_days", "rapid_threshold"])
    grid_df.to_csv(os.path.join(out, "threshold_grid.csv"), index=False, encoding="utf-8-sig")
    print(f"\nmetrics -> {out}/per_stock_metrics.csv", file=sys.stderr)
    print(grid_df[(grid_df["final"] > 0) & (grid_df["final"] <= 300)]
          .sort_values("final", ascending=False).head(60).to_string(index=False))


if __name__ == "__main__":
    main()
