#!/usr/bin/env python3
"""用 HengPan_XG 的横盘算法扫描本地日线 K 线 CSV。

数据约定：列名 日期/市场/代码/名称/开盘/最高/最低/收盘/成交量(手)/成交额(元)，
前复权日线。只调用纯算法函数，不依赖 Baostock、FastAPI 或 SQLite。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

from api.hengpan.anchored_box import extract_series, check_series
from api.hengpan.tolerant_box import check_tolerant_series
from api.hengpan.boll_box import check_boll_box_series
from api.hengpan.ma_flat import check_ma_flat_series

# ---------------------------------------------------------------- 规则定义
# kind: anchored / tolerant / boll / ma
RULES = [
    # main/develop 默认口径
    dict(id="tol_4_80", kind="tolerant", label="容刺 4% / 80根 / 4刺 / 连续1",
         box_height=0.04, lookback=80, max_breach=4, max_consecutive_breach=1),
    dict(id="fixed_4_80", kind="fixed", label="定高 4% / 80根 / 越界2",
         box_height=0.04, doji_amplitude=0.005, lookback=80, max_breach=2),
    dict(id="amp_1.0_80", kind="amplitude", label="振幅 x1.0 / 80根 / 越界2",
         amp_multiple=1.0, max_amplitude=None, lookback=80, max_breach=2),
    dict(id="boll_20", kind="boll", label="布林矩形 BOLL30x2 / 最少80根 / 容差20%",
         boll_period=30, boll_multiplier=2.0, min_box_bars=80, rectangle_tolerance=0.20),
    dict(id="ma_1_30", kind="ma", label="均线走平 MA30 / 最少30根 / 1% / ER<=0.5",
         ma_period=30, min_flat_bars=30, ma_tolerance=0.01, max_efficiency_ratio=0.5),
    # 日线适配的放宽预设
    dict(id="tol_3_80", kind="tolerant", label="容刺 3% / 80根 / 3刺 / 连续1",
         box_height=0.03, lookback=80, max_breach=3, max_consecutive_breach=1),
    dict(id="tol_5_80", kind="tolerant", label="容刺 5% / 80根 / 5刺 / 连续1",
         box_height=0.05, lookback=80, max_breach=5, max_consecutive_breach=1),
    dict(id="tol_4_40", kind="tolerant", label="容刺 4% / 40根 / 4刺 / 连续1",
         box_height=0.04, lookback=40, max_breach=4, max_consecutive_breach=1),
    dict(id="fixed_6_40", kind="fixed", label="定高 6% / 40根 / 越界2",
         box_height=0.06, doji_amplitude=0.005, lookback=40, max_breach=2),
    dict(id="fixed_8_40", kind="fixed", label="定高 8% / 40根 / 越界2",
         box_height=0.08, doji_amplitude=0.005, lookback=40, max_breach=2),
    dict(id="fixed_10_40", kind="fixed", label="定高 10% / 40根 / 越界2",
         box_height=0.10, doji_amplitude=0.005, lookback=40, max_breach=2),
    dict(id="amp_0.8_80", kind="amplitude", label="振幅 x0.8 / 80根 / 越界2",
         amp_multiple=0.8, max_amplitude=None, lookback=80, max_breach=2),
    dict(id="amp_1.2_80", kind="amplitude", label="振幅 x1.2 / 80根 / 越界2",
         amp_multiple=1.2, max_amplitude=None, lookback=80, max_breach=2),
    dict(id="boll_10", kind="boll", label="布林矩形 BOLL30x2 / 最少80根 / 容差10%",
         boll_period=30, boll_multiplier=2.0, min_box_bars=80, rectangle_tolerance=0.10),
    dict(id="boll_30", kind="boll", label="布林矩形 BOLL30x2 / 最少80根 / 容差30%",
         boll_period=30, boll_multiplier=2.0, min_box_bars=80, rectangle_tolerance=0.30),
    dict(id="ma_1.5_30", kind="ma", label="均线走平 MA30 / 最少30根 / 1.5% / ER<=0.5",
         ma_period=30, min_flat_bars=30, ma_tolerance=0.015, max_efficiency_ratio=0.5),
    dict(id="ma_1_50_er0.3", kind="ma", label="均线走平 MA30 / 最少50根 / 1% / ER<=0.3",
         ma_period=30, min_flat_bars=50, ma_tolerance=0.01, max_efficiency_ratio=0.3),
]


def required_bars(rule):
    kind = rule["kind"]
    if kind == "tolerant":
        return rule["lookback"]
    if kind in ("fixed", "amplitude"):
        return rule["lookback"] + 1
    if kind == "boll":
        return rule["boll_period"] + rule["min_box_bars"]
    if kind == "ma":
        return rule["ma_period"] + rule["min_flat_bars"] - 1
    raise ValueError(kind)


def evaluate(series, rule):
    kind = rule["kind"]
    if kind == "tolerant":
        return check_tolerant_series(
            series, box_height=rule["box_height"], lookback=rule["lookback"],
            max_breach=rule["max_breach"],
            max_consecutive_breach=rule["max_consecutive_breach"])
    if kind in ("fixed", "amplitude"):
        return check_series(
            series, doji_amplitude=rule.get("doji_amplitude", 0.005),
            box_height=rule.get("box_height", 0.04), lookback=rule["lookback"],
            max_breach=rule["max_breach"], box_type=kind,
            amp_multiple=rule.get("amp_multiple", 1.0),
            max_amplitude=rule.get("max_amplitude"))
    if kind == "boll":
        return check_boll_box_series(
            series, boll_period=rule["boll_period"],
            boll_multiplier=rule["boll_multiplier"],
            min_box_bars=rule["min_box_bars"],
            rectangle_tolerance=rule["rectangle_tolerance"])
    if kind == "ma":
        return check_ma_flat_series(
            series, ma_period=rule["ma_period"],
            min_flat_bars=rule["min_flat_bars"],
            ma_tolerance=rule["ma_tolerance"],
            max_efficiency_ratio=rule["max_efficiency_ratio"])
    raise ValueError(kind)


def continuous_tail(dates, cal_pos):
    """返回最后一段连续交易日的起始下标；没有缺口时为 0。"""
    start = 0
    for i in range(1, len(dates)):
        if cal_pos[dates[i]] - cal_pos[dates[i - 1]] != 1:
            start = i
    return start


def to_float(value):
    return None if value is None else (float(value) if np.isfinite(value) else None)


def match_row(rule, stock, result):
    upper, lower = float(result["upper"]), float(result["lower"])
    width = (upper - lower) / lower * 100 if lower > 0 else None
    row = {
        "rule_id": rule["id"], "rule_label": rule["label"], "mode": result.get("mode"),
        "code": stock["code"], "name": stock["name"], "market": stock["market"],
        "scan_date": stock["scan_date"], "close": stock["close"],
        "box_lower": lower, "box_upper": upper, "box_width_pct": width,
        "actual_range_pct": (result["actual_range"] * 100
                             if result.get("actual_range") is not None else None),
        "amplitude_pct": result["amplitude"] * 100,
        "breach_full": result.get("breach_full"),
        "breach_body": result.get("breach_body"),
        "longest_consecutive_breach": result.get("longest_consecutive_breach"),
        "segment_shift_ratio": result.get("segment_shift_ratio"),
        "rectangle_error": result.get("rectangle_error"),
        "box_bars": result.get("box_bars"),
        "bandwidth_pct": (result["bandwidth"] * 100
                          if result.get("bandwidth") is not None else None),
        "flat_bars": result.get("flat_bars"),
        "ma_range_pct": (result["ma_range"] * 100
                         if result.get("ma_range") is not None else None),
        "efficiency_ratio": result.get("efficiency_ratio"),
        "price_ma_distance_pct": (result["price_ma_distance"] * 100
                                  if result.get("price_ma_distance") is not None else None),
        "tail_state": result.get("tail_state"),
        "tail_bars": result.get("tail_bars"),
        "history_limited": result.get("history_limited"),
        "avg_amount": result.get("avg_amount"),
        "lookback_start": result.get("lookback_start"),
        "passed_full": result["passed_full"],
        "passed_body": result["passed_body"],
    }
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--scan-date", default=None)
    parser.add_argument("--limit-per-rule", type=int, default=0,
                        help="0 表示保留全部命中")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    usecols = ["日期", "市场", "代码", "名称", "开盘", "最高", "最低", "收盘",
               "成交量(手)", "成交额(元)"]
    print(f"读取 {args.csv} ...", flush=True)
    df = pd.read_csv(args.csv, encoding="utf-8-sig", usecols=usecols,
                     dtype={"代码": str})
    df = df.rename(columns={
        "日期": "date", "市场": "market", "代码": "code", "名称": "name",
        "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
        "成交量(手)": "volume", "成交额(元)": "amount",
    })
    df["code"] = df["code"].str.zfill(6)
    df["date"] = df["date"].astype(str).str[:10]
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["name"] = df["name"].astype(str).str.strip()
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    print(f"载入 {len(df):,} 行，{df['code'].nunique():,} 只，"
          f"{df['date'].min()} ~ {df['date'].max()}", flush=True)

    scan_date = args.scan_date or str(df["date"].max())
    calendar = sorted(df["date"].unique())
    cal_pos = {day: i for i, day in enumerate(calendar)}
    print(f"扫描日 {scan_date}，交易日历 {len(calendar)} 天", flush=True)

    stats = {rule["id"]: {"analyzed": 0, "passed_body": 0, "passed_full": 0,
                          "gap": 0, "insufficient": 0, "stale": 0}
             for rule in RULES}
    rows = []
    total = df["code"].nunique()
    for index, (code, group) in enumerate(df.groupby("code", sort=True), start=1):
        group = group.sort_values("date").reset_index(drop=True)
        dates = group["date"].tolist()
        if dates[-1] != scan_date:
            for rule in RULES:
                stats[rule["id"]]["stale"] += 1
            continue
        stock = {
            "code": code,
            "name": group["name"].iloc[-1],
            "market": group["market"].iloc[-1],
            "scan_date": scan_date,
            "close": float(group["close"].iloc[-1]),
        }
        full_series = None
        cont_series = None
        cont_start = None
        for rule in RULES:
            required = required_bars(rule)
            if rule["kind"] in ("boll", "ma"):
                if cont_start is None:
                    cont_start = continuous_tail(dates, cal_pos)
                if len(dates) - cont_start < required:
                    stats[rule["id"]]["insufficient" if len(dates) - cont_start < required
                                      and cont_start == 0 else "gap"] += 1
                    continue
                if cont_series is None:
                    cont_series = extract_series(group.iloc[cont_start:].reset_index(drop=True))
                series = cont_series
            else:
                if len(dates) < required:
                    stats[rule["id"]]["insufficient"] += 1
                    continue
                first = dates[-required]
                if cal_pos[dates[-1]] - cal_pos[first] + 1 != required:
                    stats[rule["id"]]["gap"] += 1
                    continue
                if full_series is None:
                    full_series = extract_series(group)
                series = full_series
            result = evaluate(series, rule)
            if result is None:
                stats[rule["id"]]["insufficient"] += 1
                continue
            stats[rule["id"]]["analyzed"] += 1
            stats[rule["id"]]["passed_body"] += int(bool(result["passed_body"]))
            stats[rule["id"]]["passed_full"] += int(bool(result["passed_full"]))
            if result["passed_body"]:
                if args.limit_per_rule and \
                        stats[rule["id"]]["passed_body"] > args.limit_per_rule:
                    continue
                rows.append(match_row(rule, stock, result))
        if index % 500 == 0 or index == total:
            print(f"  进度 {index}/{total}", flush=True)

    summary = []
    for rule in RULES:
        item = {"rule_id": rule["id"], "label": rule["label"], "kind": rule["kind"],
                "params": json.dumps({k: v for k, v in rule.items()
                                      if k not in ("id", "kind", "label")},
                                     ensure_ascii=False)}
        item.update(stats[rule["id"]])
        summary.append(item)
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(os.path.join(args.out, "summary.csv"), index=False,
                      encoding="utf-8-sig")

    matches = pd.DataFrame(rows)
    matches.to_csv(os.path.join(args.out, "matches_all.csv"), index=False,
                   encoding="utf-8-sig")

    print("\n=== 汇总 ===")
    print(summary_df[["rule_id", "analyzed", "passed_body", "passed_full",
                      "gap", "insufficient"]].to_string(index=False))
    print(f"\n命中明细 {len(matches)} 行 -> {os.path.join(args.out, 'matches_all.csv')}")
    if len(matches):
        top = matches.sort_values(
            ["rule_id", "breach_body", "breach_full", "box_width_pct"],
            na_position="last").groupby("rule_id").head(10)
        print("\n=== 每组前 10（按实体刺、整体刺、箱宽升序）===")
        cols = [c for c in ("rule_id", "code", "name", "close", "box_width_pct",
                            "breach_body", "breach_full", "actual_range_pct",
                            "box_bars", "flat_bars", "efficiency_ratio") if c in top]
        print(top[cols].to_string(index=False))


if __name__ == "__main__":
    main()
