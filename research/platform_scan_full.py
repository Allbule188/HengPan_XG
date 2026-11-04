#!/usr/bin/env python3
"""全市场平台扫描：成交量分析 + 低位 + 快速下跌。日线 K 线 CSV。

说明：快速下跌在现有代码里依赖低位判断，必须同时开启 use_low_position。
"""
import contextlib
import json
import os
import sys

import numpy as np
import pandas as pd

from api.analyzers.combined_analyzer import analyze_stock

CONFIGS = [
    dict(id="strict_page_volume", windows=[20, 30, 60],
         box_threshold=0.10, ma_diff_threshold=0.02, volatility_threshold=0.02,
         use_volume_analysis=True, volume_change_threshold=0.5,
         volume_stability_threshold=0.5, volume_increase_threshold=1.5,
         use_low_position=True, high_point_lookback_days=300,
         decline_period_days=180, decline_threshold=0.30,
         use_rapid_decline_detection=True, rapid_decline_days=30,
         rapid_decline_threshold=0.15,
         use_box_detection=True, box_quality_threshold=0.8,
         use_breakthrough_prediction=False, use_breakthrough_confirmation=False,
         use_window_weights=False, window_weights={}),
    dict(id="mid_volume_08", windows=[20, 30, 60],
         box_threshold=0.10, ma_diff_threshold=0.02, volatility_threshold=0.02,
         use_volume_analysis=True, volume_change_threshold=0.8,
         volume_stability_threshold=0.7, volume_increase_threshold=1.5,
         use_low_position=True, high_point_lookback_days=300,
         decline_period_days=180, decline_threshold=0.30,
         use_rapid_decline_detection=True, rapid_decline_days=30,
         rapid_decline_threshold=0.15,
         use_box_detection=True, box_quality_threshold=0.8,
         use_breakthrough_prediction=False, use_breakthrough_confirmation=False,
         use_window_weights=False, window_weights={}),
    dict(id="relaxed_volume_09", windows=[20, 30, 60],
         box_threshold=0.10, ma_diff_threshold=0.02, volatility_threshold=0.02,
         use_volume_analysis=True, volume_change_threshold=0.9,
         volume_stability_threshold=0.75, volume_increase_threshold=1.5,
         use_low_position=True, high_point_lookback_days=300,
         decline_period_days=180, decline_threshold=0.30,
         use_rapid_decline_detection=True, rapid_decline_days=30,
         rapid_decline_threshold=0.15,
         use_box_detection=True, box_quality_threshold=0.8,
         use_breakthrough_prediction=False, use_breakthrough_confirmation=False,
         use_window_weights=False, window_weights={}),
]


def load(path, scan_date):
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
        if group["date"].iloc[-1] != scan_date:
            continue
        groups.append((code, group))
    return groups


def evaluate(group, cfg):
    return analyze_stock(
        group, cfg["windows"],
        box_threshold=cfg["box_threshold"],
        ma_diff_threshold=cfg["ma_diff_threshold"],
        volatility_threshold=cfg["volatility_threshold"],
        volume_change_threshold=cfg["volume_change_threshold"],
        volume_stability_threshold=cfg["volume_stability_threshold"],
        volume_increase_threshold=cfg["volume_increase_threshold"],
        use_volume_analysis=cfg["use_volume_analysis"],
        use_breakthrough_prediction=cfg["use_breakthrough_prediction"],
        use_window_weights=cfg["use_window_weights"],
        window_weights=cfg["window_weights"],
        use_low_position=cfg["use_low_position"],
        high_point_lookback_days=cfg["high_point_lookback_days"],
        decline_period_days=cfg["decline_period_days"],
        decline_threshold=cfg["decline_threshold"],
        use_rapid_decline_detection=cfg["use_rapid_decline_detection"],
        rapid_decline_days=cfg["rapid_decline_days"],
        rapid_decline_threshold=cfg["rapid_decline_threshold"],
        use_breakthrough_confirmation=cfg["use_breakthrough_confirmation"],
        breakthrough_confirmation_days=cfg.get("breakthrough_confirmation_days", 1),
        use_box_detection=cfg["use_box_detection"],
        box_quality_threshold=cfg["box_quality_threshold"],
    )


def funnel_flags(result, cfg):
    d = result.get("details", {})
    price_any = box_any = volume_any = False
    for w in cfg["windows"]:
        wd = d.get(w, {})
        price_ok = (wd.get("box_range", np.inf) <= cfg["box_threshold"] and
                    wd.get("ma_diff", np.inf) <= cfg["ma_diff_threshold"] and
                    wd.get("volatility", np.inf) <= cfg["volatility_threshold"])
        box = wd.get("box_analysis", {}) or {}
        box_ok = bool(box.get("is_box_pattern", False) and
                      box.get("box_quality", 0) >= cfg["box_quality_threshold"] and
                      wd.get("volatility", np.inf) <= cfg["volatility_threshold"])
        vol = wd.get("volume_analysis", {}) or {}
        volume_ok = vol.get("status") == "符合条件"
        if price_ok:
            price_any = True
        if price_ok and box_ok:
            box_any = True
        if price_ok and box_ok and volume_ok:
            volume_any = True
    max_box = bool((result.get("box_analysis", {}) or {}).get("is_box_pattern", False))
    low = bool(result.get("is_low_position"))
    rapid = bool(result.get("is_rapid_decline"))
    final = bool(result.get("is_platform"))
    explained = volume_any and low and rapid
    return dict(price_any=price_any, box_any=box_any, volume_any=volume_any,
                max_box=max_box, low=low, rapid=rapid, final=final,
                explained=explained)


def best_window_details(result, cfg):
    d = result.get("details", {})
    passing = result.get("platform_windows") or []
    w = passing[0] if passing else (cfg["windows"][-1] if cfg["windows"] else None)
    wd = d.get(w, {}) if w is not None else {}
    box = wd.get("box_analysis", {}) or {}
    vol = wd.get("volume_analysis", {}) or {}
    return {
        "best_window": w,
        "box_range": wd.get("box_range"),
        "ma_diff": wd.get("ma_diff"),
        "volatility": wd.get("volatility"),
        "box_quality": box.get("box_quality"),
        "support_levels": box.get("support_levels"),
        "resistance_levels": box.get("resistance_levels"),
        "volume_change_ratio": vol.get("volume_change_ratio"),
        "volume_stability": vol.get("volume_stability"),
    }


def main():
    csv = sys.argv[1]
    out = sys.argv[2]
    scan_date = sys.argv[3] if len(sys.argv) > 3 else "2026-11-03"
    groups = load(csv, scan_date)
    os.makedirs(out, exist_ok=True)
    print(f"groups={len(groups)} scan_date={scan_date}", file=sys.stderr, flush=True)

    all_summary = []
    all_funnel = []
    matches_all = []
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
        for cfg in CONFIGS:
            min_rows = max(max(cfg["windows"]) + 10, cfg.get("high_point_lookback_days", 0))
            counts = dict(universe=0, insufficient=0, price_any=0, box_any=0,
                          volume_any=0, max_box=0, low=0, rapid=0, final=0,
                          explained=0, errors=0)
            rows = []
            for idx, (code, group) in enumerate(groups, 1):
                if len(group) < min_rows:
                    counts["insufficient"] += 1
                    continue
                counts["universe"] += 1
                try:
                    result = evaluate(group, cfg)
                except Exception as exc:
                    counts["errors"] += 1
                    continue
                f = funnel_flags(result, cfg)
                for key in ("price_any", "box_any", "volume_any", "max_box",
                            "low", "rapid", "final", "explained"):
                    counts[key] += int(f[key])
                if result.get("is_platform"):
                    wd = best_window_details(result, cfg)
                    decline = result.get("decline_details", {}) or {}
                    rows.append({
                        "code": code, "name": group["name"].iloc[-1],
                        "market": group["market"].iloc[-1],
                        "close": float(group["close"].iloc[-1]),
                        "st": "ST" in group["name"].iloc[-1].upper(),
                        "platform_windows": ",".join(str(w) for w in result.get("platform_windows", [])),
                        "selection_reasons": " | ".join(str(v) for v in result.get("selection_reasons", {}).values()),
                        **wd,
                        "decline_pct": decline.get("decline_percentage"),
                        "high_date": decline.get("high_date"),
                        "high_price": decline.get("high_price"),
                        "low_date": decline.get("low_date"),
                        "max_rapid_decline": decline.get("max_rapid_decline"),
                        "rapid_start": decline.get("rapid_decline_start_date"),
                        "rapid_end": decline.get("rapid_decline_end_date"),
                        "is_low_position": result.get("is_low_position"),
                        "is_rapid_decline": result.get("is_rapid_decline"),
                    })
                if idx % 500 == 0 or idx == len(groups):
                    print(f"  {cfg['id']} {idx}/{len(groups)} final={counts['final']}",
                          file=sys.stderr, flush=True)
            cfg_summary = dict(config=cfg, **counts)
            all_summary.append(cfg_summary)
            all_funnel.append({"config": cfg["id"], **counts})
            if rows:
                matches_all.extend([{**r, "config": cfg["id"]} for r in rows])
                pd.DataFrame(rows).to_csv(
                    os.path.join(out, f"matches_{cfg['id']}.csv"),
                    index=False, encoding="utf-8-sig")
            print(f"{cfg['id']}: {counts}", file=sys.stderr, flush=True)

    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(all_summary, fh, ensure_ascii=False, indent=2, default=str)
    pd.DataFrame(all_funnel).to_csv(os.path.join(out, "funnel.csv"),
                                    index=False, encoding="utf-8-sig")
    if matches_all:
        pd.DataFrame(matches_all).to_csv(os.path.join(out, "matches_all.csv"),
                                         index=False, encoding="utf-8-sig")
    print(f"-> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
