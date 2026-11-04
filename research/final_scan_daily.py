#!/usr/bin/env python3
"""日线参数优化后的最终扫描。核心 4 组 + 扩展 2 组。"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from api.hengpan.anchored_box import extract_series, check_series
from api.hengpan.tolerant_box import check_tolerant_series
from api.hengpan.ma_flat import check_ma_flat_series

RULES = [
    dict(id="fix_6_20", tier="core", kind="fixed", label="定高 6% / 20根 / 越界2",
         box_height=0.06, doji_amplitude=0.01, lookback=20, max_breach=2),
    dict(id="fix_8_30", tier="core", kind="fixed", label="定高 8% / 30根 / 越界2",
         box_height=0.08, doji_amplitude=0.01, lookback=30, max_breach=2),
    dict(id="tol_5_30", tier="core", kind="tolerant", label="容刺 5% / 30根 / 4刺 / 连续1",
         box_height=0.05, lookback=30, max_breach=4, max_consecutive_breach=1),
    dict(id="ma_30_40_1", tier="core", kind="ma", label="均线 MA30 / 40根 / 1% / ER<=0.5",
         ma_period=30, min_flat_bars=40, ma_tolerance=0.01, max_efficiency_ratio=0.5),
    dict(id="tol_6_30", tier="extended", kind="tolerant", label="容刺 6% / 30根 / 4刺 / 连续1",
         box_height=0.06, lookback=30, max_breach=4, max_consecutive_breach=1),
    dict(id="amp_0.5_20", tier="extended", kind="amplitude", label="振幅 x0.5 / 20根 / 上限8% / 越界2",
         amp_multiple=0.5, max_amplitude=0.08, lookback=20, max_breach=2),
]


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--scan-date", default=None)
    args = p.parse_args()

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
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df.sort_values(["code", "date"]).reset_index(drop=True)

    scan_date = args.scan_date or str(df["date"].max())
    calendar = sorted(df["date"].unique())
    cal_pos = {d: i for i, d in enumerate(calendar)}
    last_meta = df[df["date"] == scan_date].drop_duplicates("code").set_index("code")
    tail = df.groupby("code").tail(60)
    avg_amt = tail.groupby("code")["amount"].mean()
    universe = last_meta[(last_meta["market"].isin(["SH", "SZ"])) &
                         (~last_meta["name"].str.contains("ST", case=False, na=False))]
    universe = universe[avg_amt.reindex(universe.index).fillna(0) >= 3e7]
    codes = set(universe.index)
    print(f"universe={len(codes)} scan_date={scan_date}", flush=True)

    stats = {c["id"]: dict(analyzed=0, full=0, body=0, gap=0, insuff=0) for c in RULES}
    rows = []
    total = len(codes)
    for i, (code, group) in enumerate(df[df["code"].isin(codes)].groupby("code", sort=True), 1):
        group = group.sort_values("date").reset_index(drop=True)
        dates = group["date"].tolist()
        if dates[-1] != scan_date:
            continue
        full_series = extract_series(group)
        cont_start = continuous_tail(dates, cal_pos)
        cont_series = extract_series(group.iloc[cont_start:].reset_index(drop=True)) \
            if cont_start else full_series
        close = float(group["close"].iloc[-1])
        name = group["name"].iloc[-1]
        market = group["market"].iloc[-1]
        for c in RULES:
            st = stats[c["id"]]
            req = required(c)
            if c["kind"] == "ma":
                if len(dates) - cont_start < req:
                    st["insuff" if cont_start == 0 else "gap"] += 1
                    continue
                series = cont_series
            else:
                if len(dates) < req:
                    st["insuff"] += 1
                    continue
                if cal_pos[dates[-1]] - cal_pos[dates[-req]] + 1 != req:
                    st["gap"] += 1
                    continue
                series = full_series
            res = evaluate(series, c)
            if res is None:
                st["insuff"] += 1
                continue
            st["analyzed"] += 1
            st["full"] += int(bool(res["passed_full"]))
            st["body"] += int(bool(res["passed_body"]))
            if not res["passed_full"]:
                continue
            upper, lower = float(res["upper"]), float(res["lower"])
            rows.append({
                "tier": c["tier"], "rule_id": c["id"], "kind": c["kind"],
                "label": c["label"], "code": code, "name": name, "market": market,
                "scan_date": scan_date, "close": close,
                "lower": lower, "upper": upper,
                "box_width_pct": (upper - lower) / lower * 100,
                "actual_range_pct": float(res["actual_range"]) * 100,
                "breach_body": res.get("breach_body"),
                "breach_full": res.get("breach_full"),
                "longest_consecutive_breach": res.get("longest_consecutive_breach"),
                "segment_shift_ratio": res.get("segment_shift_ratio"),
                "flat_bars": res.get("flat_bars"),
                "ma_range_pct": (float(res["ma_range"]) * 100
                                 if res.get("ma_range") is not None else None),
                "efficiency_ratio": res.get("efficiency_ratio"),
                "avg_amount": res.get("avg_amount"),
                "lookback_start": res.get("lookback_start"),
            })
        if i % 500 == 0 or i == total:
            print(f"  {i}/{total}", flush=True)

    matches = pd.DataFrame(rows)
    os.makedirs(args.out, exist_ok=True)
    matches.to_csv(os.path.join(args.out, "matches_optimized.csv"), index=False,
                   encoding="utf-8-sig")

    summary = []
    for c in RULES:
        item = {"rule_id": c["id"], "tier": c["tier"], "label": c["label"],
                "params": json.dumps({k: v for k, v in c.items()
                                      if k not in ("id", "kind", "label", "tier")},
                                     ensure_ascii=False)}
        item.update(stats[c["id"]])
        summary.append(item)
    sm = pd.DataFrame(summary)
    sm.to_csv(os.path.join(args.out, "summary_optimized.csv"), index=False,
              encoding="utf-8-sig")

    # 去重短名单：核心优先，其次实际振幅
    if len(matches):
        matches["core"] = matches["tier"].eq("core")
        agg = matches.groupby("code").agg(
            name=("name", "first"), market=("market", "first"),
            close=("close", "first"),
            core_rules=("rule_id", lambda x: (matches.loc[x.index, "tier"] == "core").sum()),
            all_rules=("rule_id", "nunique"),
            core_rule_ids=("rule_id", lambda x: "、".join(sorted(set(
                matches.loc[x.index][matches.loc[x.index, "tier"] == "core"]["rule_id"])))),
            all_rule_ids=("rule_id", lambda x: "、".join(sorted(set(x)))),
            min_range=("actual_range_pct", "min"),
        ).reset_index()
        best = matches.sort_values("actual_range_pct").drop_duplicates("code")
        best = best[["code", "name", "market", "close", "actual_range_pct",
                     "box_width_pct", "rule_id", "tier"]]
        short = best.merge(agg, on=["code", "name", "market", "close"], how="left")
        short = short.sort_values(["core_rules", "actual_range_pct"],
                                  ascending=[False, True])
        short.to_csv(os.path.join(args.out, "shortlist_optimized.csv"), index=False,
                     encoding="utf-8-sig")

    print("\n=== summary ===")
    print(sm[["rule_id", "tier", "analyzed", "full", "body", "gap", "insuff"]]
          .to_string(index=False))
    if len(matches):
        print(f"\n命中 {len(matches)} 行，去重 {matches['code'].nunique()} 只")
        print("\n=== 核心口径前 40（按实际振幅）===")
        core = matches[matches.tier == "core"].sort_values("actual_range_pct")
        seen = set()
        shown = 0
        for _, r in core.iterrows():
            if r.code in seen:
                continue
            seen.add(r.code)
            print(f"{r.code} {r['name']:<7} {r.market} close={r.close:>8.2f} "
                  f"range={r.actual_range_pct:5.2f}% boxw={r.box_width_pct:5.2f}% "
                  f"via={r.rule_id}")
            shown += 1
            if shown >= 40:
                break


if __name__ == "__main__":
    main()
