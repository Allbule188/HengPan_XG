#!/usr/bin/env python3
"""日线横盘参数曲面扫描。用法：--family tf 或 abm。"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from api.hengpan.anchored_box import extract_series, check_series
from api.hengpan.tolerant_box import check_tolerant_series
from api.hengpan.boll_box import check_boll_box_series
from api.hengpan.ma_flat import check_ma_flat_series

WIDTHS = (0.03, 0.04, 0.05, 0.06)
LOOKBACKS = (20, 30, 40, 60)


def build_configs(family):
    configs = []
    if family == "tf":
        for w in WIDTHS:
            for lb in LOOKBACKS:
                configs.append(dict(id=f"tol_{w*100:g}_{lb}", kind="tolerant",
                                    box_height=w, lookback=lb, max_breach=4,
                                    max_consecutive_breach=1))
        for w in (0.04, 0.06, 0.08, 0.10):
            for lb in LOOKBACKS:
                configs.append(dict(id=f"fix_{w*100:g}_{lb}", kind="fixed",
                                    box_height=w, doji_amplitude=0.01,
                                    lookback=lb, max_breach=2))
    elif family == "abm":
        for k in (0.5, 0.8, 1.0):
            for lb in (20, 30, 40):
                configs.append(dict(id=f"amp_{k:g}_{lb}", kind="amplitude",
                                    amp_multiple=k, max_amplitude=0.08,
                                    lookback=lb, max_breach=2))
        for mb in (30, 40, 60):
            for tol in (0.10, 0.15, 0.20):
                configs.append(dict(id=f"boll_{mb}_{tol*100:g}", kind="boll",
                                    boll_period=30, boll_multiplier=2.0,
                                    min_box_bars=mb, rectangle_tolerance=tol))
        for mp in (20, 30):
            for mf in (20, 30, 40):
                for tol in (0.005, 0.01, 0.015):
                    configs.append(dict(id=f"ma_{mp}_{mf}_{tol*100:g}", kind="ma",
                                        ma_period=mp, min_flat_bars=mf,
                                        ma_tolerance=tol, max_efficiency_ratio=0.5))
    else:
        raise ValueError(family)
    return configs


def required_bars(c):
    if c["kind"] == "tolerant":
        return c["lookback"]
    if c["kind"] in ("fixed", "amplitude"):
        return c["lookback"] + 1
    if c["kind"] == "boll":
        return c["boll_period"] + c["min_box_bars"]
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
    if c["kind"] == "boll":
        return check_boll_box_series(series, boll_period=c["boll_period"],
                                     boll_multiplier=c["boll_multiplier"],
                                     min_box_bars=c["min_box_bars"],
                                     rectangle_tolerance=c["rectangle_tolerance"])
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
    p.add_argument("--family", required=True, choices=("tf", "abm"))
    p.add_argument("--scan-date", default=None)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    configs = build_configs(args.family)
    print(f"family={args.family} configs={len(configs)}", flush=True)

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
    cal_pos = {day: i for i, day in enumerate(calendar)}

    # 可交易池：沪深、非 ST、近 60 日均额 >= 3000 万，且扫描日有 K 线
    last_meta = df[df["date"] == scan_date].drop_duplicates("code").set_index("code")
    tail = df.groupby("code").tail(60)
    avg_amt = tail.groupby("code")["amount"].mean()
    universe = last_meta[(last_meta["market"].isin(["SH", "SZ"])) &
                         (~last_meta["name"].str.contains("ST", case=False, na=False))]
    universe = universe[avg_amt.reindex(universe.index).fillna(0) >= 3e7]
    codes = set(universe.index)
    print(f"universe={len(codes)} scan_date={scan_date}", flush=True)

    stats = {c["id"]: dict(analyzed=0, body=0, full=0, gap=0, insuff=0,
                           ranges=[], widths=[], amounts=[], narrow_box=0)
             for c in configs}
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
        for c in configs:
            st = stats[c["id"]]
            req = required_bars(c)
            if c["kind"] in ("boll", "ma"):
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
            st["body"] += int(bool(res["passed_body"]))
            st["full"] += int(bool(res["passed_full"]))
            if res["passed_full"]:
                st["ranges"].append(float(res["actual_range"]))
                w = (float(res["upper"]) - float(res["lower"])) / float(res["lower"])
                st["widths"].append(w)
                if res.get("avg_amount") is not None:
                    st["amounts"].append(float(res["avg_amount"]))
                if c["kind"] == "amplitude" and w <= 0.20:
                    st["narrow_box"] += 1
        if i % 500 == 0 or i == total:
            print(f"  {i}/{total}", flush=True)

    rows = []
    for c in configs:
        st = stats[c["id"]]
        r = np.array(st["ranges"], dtype=float)
        w = np.array(st["widths"], dtype=float)
        a = np.array(st["amounts"], dtype=float)
        rows.append({
            "id": c["id"], "kind": c["kind"],
            "params": json.dumps({k: v for k, v in c.items() if k not in ("id", "kind")},
                                 ensure_ascii=False),
            "analyzed": st["analyzed"], "body": st["body"], "full": st["full"],
            "gap": st["gap"], "insuff": st["insuff"],
            "range_median": float(np.median(r)) if len(r) else None,
            "range_p25": float(np.quantile(r, 0.25)) if len(r) else None,
            "range_p75": float(np.quantile(r, 0.75)) if len(r) else None,
            "range_le8": int((r <= 0.08).sum()), "range_le10": int((r <= 0.10).sum()),
            "boxw_median": float(np.median(w)) if len(w) else None,
            "narrow_box": st["narrow_box"],
            "amt_median": float(np.median(a)) if len(a) else None,
            "pass_rate": st["full"] / st["analyzed"] if st["analyzed"] else None,
        })
    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    cols = ["id", "full", "body", "range_median", "range_le8", "range_le10",
            "boxw_median", "narrow_box", "pass_rate"]
    print(out[cols].to_string(index=False))
    print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
