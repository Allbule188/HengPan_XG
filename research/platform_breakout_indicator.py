#!/usr/bin/env python3
"""平台突破指标 PBI：对平台股计算突破状态与 0-100 强度。

PBI = 40*价格位置 + 30*量能扩张 + 20*技术信号 + 10*突破确认
"""
import json
import os
import sys

import numpy as np
import pandas as pd

from api.analyzers.breakthrough_analyzer import (
    analyze_breakthrough, check_breakthrough_confirmation)
from api.analyzers.volume_analyzer import check_volume_breakthrough

SCAN_DATE = "2026-11-03"


def clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def load_kline(path):
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
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def parse_levels(value):
    if isinstance(value, str):
        try:
            arr = json.loads(value)
            return [float(x) for x in arr if x is not None and np.isfinite(float(x))]
        except Exception:
            return []
    return []


def state_of(close, top, bottom, vol_ratio, confirmed):
    if close < bottom * 0.98:
        return "跌破下沿"
    if close > top * 1.02 and vol_ratio >= 1.5 and confirmed:
        return "已确认突破"
    if close > top and vol_ratio >= 1.2:
        return "突破中"
    if close > top:
        return "突破未放量"
    if close >= top * 0.97:
        return "临近突破"
    return "箱体内"


def main():
    platform_csv, kline_csv, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    plat = pd.read_csv(platform_csv, encoding="utf-8-sig")
    plat["code"] = plat["code"].astype(str).str.zfill(6)
    kline = load_kline(kline_csv)
    by_code = {code: g.sort_values("date").reset_index(drop=True)
               for code, g in kline.groupby("code")}
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for _, p in plat.iterrows():
        code = p["code"]
        g = by_code.get(code)
        if g is None or g.empty or g["date"].iloc[-1] != SCAN_DATE:
            continue
        bars = int(p["platform_bars"])
        window = g.iloc[-bars:]
        top_levels = parse_levels(p.get("resistance_levels"))
        bottom_levels = parse_levels(p.get("support_levels"))
        top = top_levels[0] if top_levels else float(window["high"].max())
        bottom = bottom_levels[0] if bottom_levels else float(window["low"].min())
        close = float(g["close"].iloc[-1])
        high = float(g["high"].iloc[-1])
        vol = g["volume"].to_numpy(dtype=float)
        recent5 = float(np.mean(vol[-5:])) if len(vol) >= 5 else np.nan
        prior20 = float(np.mean(vol[-25:-5])) if len(vol) >= 25 else np.nan
        vol_ratio = recent5 / prior20 if prior20 and np.isfinite(prior20) else np.nan

        vol_breakout, vol_details = check_volume_breakthrough(g, 5, 1.5)
        bt = analyze_breakthrough(g)
        signal_count = int(bt.get("signal_count", 0))
        conf = check_breakthrough_confirmation(g, 1)
        confirmed = bool(conf.get("has_confirmation"))

        price_score = clip((close / top - 0.95) / 0.10)
        volume_score = clip((vol_ratio - 1.0) / 1.5) if np.isfinite(vol_ratio) else 0.0
        signal_score = signal_count / 4.0
        confirm_score = 1.0 if confirmed else 0.0
        pbi = 40 * price_score + 30 * volume_score + 20 * signal_score + 10 * confirm_score

        rows.append({
            "code": code, "name": p["name"], "market": p["market"],
            "close": close, "platform_start": p["platform_start"],
            "platform_end": p["platform_end"], "platform_bars": bars,
            "box_top": top, "box_bottom": bottom,
            "box_height_pct": (top - bottom) / bottom * 100 if bottom > 0 else np.nan,
            "close_vs_top_pct": (close - top) / top * 100,
            "high_vs_top_pct": (high - top) / top * 100,
            "distance_to_top_pct": (top - close) / top * 100,
            "vol_ratio_5_20": vol_ratio,
            "volume_breakout_5_15": bool(vol_breakout),
            "signal_count": signal_count,
            "macd": bool(bt.get("signals", {}).get("MACD")),
            "rsi": bool(bt.get("signals", {}).get("RSI")),
            "kdj": bool(bt.get("signals", {}).get("KDJ")),
            "boll": bool(bt.get("signals", {}).get("布林带")),
            "has_confirmation": confirmed,
            "breakout_state": state_of(close, top, bottom, vol_ratio, confirmed),
            "price_score": price_score * 40,
            "volume_score": volume_score * 30,
            "signal_score": signal_score * 20,
            "confirm_score": confirm_score * 10,
            "PBI": pbi,
            "st": "ST" in str(p["name"]).upper(),
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("PBI", ascending=False)
    out.to_csv(os.path.join(out_dir, "platform_breakout_indicator.csv"),
               index=False, encoding="utf-8-sig")
    top = out.head(30) if len(out) else out
    top.to_csv(os.path.join(out_dir, "platform_breakout_top30.csv"),
               index=False, encoding="utf-8-sig")
    print(f"rows={len(out)} -> {out_dir}", file=sys.stderr)
    print("state counts:", out["breakout_state"].value_counts().to_dict() if len(out) else {},
          file=sys.stderr)
    cols = ["code", "name", "market", "close", "box_top", "close_vs_top_pct",
            "vol_ratio_5_20", "signal_count", "has_confirmation",
            "breakout_state", "PBI"]
    print(out[cols].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
