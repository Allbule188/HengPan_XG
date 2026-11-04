#!/usr/bin/env python3
"""按当前平台定义扫描，并输出平台起点/终点。

平台定义：最近 W 根 K 线内，box_range / ma_diff / volatility 同时达标，
且箱体质量 >= 0.8；任一窗口通过即可。平台终点固定为扫描日最后一根。
"""
import contextlib
import json
import os
import sys

import numpy as np
import pandas as pd

from api.analyzers.combined_analyzer import analyze_stock
from api.analyzers.volume_analyzer import calculate_volume_features
from api.analyzers.decline_analyzer import analyze_decline_speed

WINDOWS = [20, 30, 60, 90, 120]
BOX_THRESHOLD = 0.10
MA_DIFF = 0.02
VOLATILITY = 0.02
BOX_QUALITY = 0.8
SCAN_DATE = "2026-11-03"
MIN_BARS = 300


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
        if group["date"].iloc[-1] == SCAN_DATE and len(group) >= MIN_BARS:
            groups.append((code, group))
    return groups


def analyze(df):
    return analyze_stock(
        df, WINDOWS,
        box_threshold=BOX_THRESHOLD,
        ma_diff_threshold=MA_DIFF,
        volatility_threshold=VOLATILITY,
        volume_change_threshold=0.5,
        volume_stability_threshold=0.5,
        volume_increase_threshold=1.5,
        use_volume_analysis=False,
        use_breakthrough_prediction=False,
        use_window_weights=False,
        window_weights={},
        use_low_position=False,
        high_point_lookback_days=MIN_BARS,
        decline_period_days=180,
        decline_threshold=0.30,
        use_rapid_decline_detection=False,
        rapid_decline_days=30,
        rapid_decline_threshold=0.15,
        use_breakthrough_confirmation=False,
        breakthrough_confirmation_days=1,
        use_box_detection=True,
        box_quality_threshold=BOX_QUALITY,
    )


def main():
    csv, out = sys.argv[1], sys.argv[2]
    groups = load(csv)
    os.makedirs(out, exist_ok=True)
    print(f"groups={len(groups)} scan_date={SCAN_DATE}", file=sys.stderr, flush=True)

    rows = []
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
        for i, (code, g) in enumerate(groups, 1):
            res = analyze(g)
            if not res.get("is_platform"):
                if i % 1000 == 0:
                    print(f"  {i}/{len(groups)} matches={len(rows)}",
                          file=sys.stderr, flush=True)
                continue
            passing = sorted(res.get("platform_windows") or [])
            if not passing:
                continue
            best = max(passing)
            details = res.get("details", {})
            d = details.get(best, {})
            box = d.get("box_analysis", {}) or {}
            start = g["date"].iloc[-best]
            end = g["date"].iloc[-1]
            vol = calculate_volume_features(g, best)
            vc, vs = vol.get("volume_change_ratio"), vol.get("volume_stability")
            decline = analyze_decline_speed(g, MIN_BARS, 180, 0.30, 30, 0.15)
            dd = decline.get("details", {}) if decline.get("status") == "analyzed" else {}
            low_ok = bool(decline.get("is_low_position"))
            rapid_ok = bool(decline.get("is_rapid_decline"))
            vol05 = bool(vc is not None and vs is not None and vc <= 0.5 and vs <= 0.5)
            vol08 = bool(vc is not None and vs is not None and vc <= 0.8 and vs <= 0.7)
            vol09 = bool(vc is not None and vs is not None and vc <= 0.9 and vs <= 0.75)
            window_starts = ";".join(f"{w}:{g['date'].iloc[-w]}" for w in passing)
            rows.append({
                "code": code, "name": g["name"].iloc[-1], "market": g["market"].iloc[-1],
                "close": float(g["close"].iloc[-1]),
                "st": "ST" in g["name"].iloc[-1].upper(),
                "platform_start": start, "platform_end": end,
                "platform_bars": best,
                "platform_calendar_days": (pd.to_datetime(end) - pd.to_datetime(start)).days,
                "platform_windows": ",".join(str(w) for w in passing),
                "window_starts": window_starts,
                "box_range": d.get("box_range"),
                "ma_diff": d.get("ma_diff"),
                "volatility": d.get("volatility"),
                "box_quality": box.get("box_quality"),
                "support_levels": json.dumps(box.get("support_levels"), ensure_ascii=False),
                "resistance_levels": json.dumps(box.get("resistance_levels"), ensure_ascii=False),
                "volume_change_ratio": vc,
                "volume_stability": vs,
                "decline_pct": dd.get("decline_percentage"),
                "max_rapid_decline": dd.get("max_rapid_decline"),
                "high_date": dd.get("high_date"),
                "low_date": dd.get("low_date"),
                "is_low_position": low_ok,
                "is_rapid_decline": rapid_ok,
                "vol_ok_05": vol05, "vol_ok_08": vol08, "vol_ok_09": vol09,
                "extended_05": bool(vol05 and low_ok and rapid_ok),
                "extended_08": bool(vol08 and low_ok and rapid_ok),
                "extended_09": bool(vol09 and low_ok and rapid_ok),
            })
            if i % 500 == 0 or i == len(groups):
                print(f"  {i}/{len(groups)} matches={len(rows)}",
                      file=sys.stderr, flush=True)

    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values(["platform_bars", "box_quality", "box_range"],
                            ascending=[False, False, True])
    df.to_csv(os.path.join(out, "platform_matches_all.csv"), index=False,
              encoding="utf-8-sig")
    ext = df[df["extended_09"]] if len(df) else df
    ext.to_csv(os.path.join(out, "platform_matches_extended_09.csv"), index=False,
               encoding="utf-8-sig")
    summary = {
        "windows": WINDOWS,
        "box_threshold": BOX_THRESHOLD,
        "ma_diff_threshold": MA_DIFF,
        "volatility_threshold": VOLATILITY,
        "box_quality_threshold": BOX_QUALITY,
        "scan_date": SCAN_DATE,
        "universe": len(groups),
        "matches": len(df),
        "matches_extended_05": int(df["extended_05"].sum()) if len(df) else 0,
        "matches_extended_08": int(df["extended_08"].sum()) if len(df) else 0,
        "matches_extended_09": int(df["extended_09"].sum()) if len(df) else 0,
    }
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"\nsummary: {summary}", file=sys.stderr)
    if len(df):
        cols = ["code", "name", "market", "close", "platform_start", "platform_end",
                "platform_bars", "box_range", "box_quality", "extended_09"]
        print("\n=== 前 50（按平台长度、箱体质量排序）===")
        print(df[cols].head(50).to_string(index=False))


if __name__ == "__main__":
    main()
