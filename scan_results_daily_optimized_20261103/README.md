# 日线参数优化后的横盘扫描（2026-11-03）

## 优化思路

原仓库默认参数按 60 分钟 80 根设计（约 20 个交易日）。直接套到日线 80 根（约 4 个月）后，4% 箱体全市场 0 只。本次按日线重新标定窗口和箱宽，并用参数曲面 + 历史事件研究筛选。

### 数据与股票池

- 数据：`workplaces01/api/data/his_Kline_data_qfq/all_stocks_360_day.csv`
- A 股日线、前复权、5,578 只、2025-05-13 ~ 2026-11-03
- 股票池：沪深（不含北交所）、非 ST、近 60 日日均成交额 ≥ 3000 万，共 4,809 只
- 扫描日：2026-11-03；回验窗口用交易日历检查停牌缺口

### 参数曲面（最新交易日）

见 `param_sweep/sweep_tf.csv`、`param_sweep/sweep_abm.csv`。关键结论：

- 容刺 3% 全灭；4% 只在 20/30/40 根有 16/3/1 只；5% 在 20/30/40 根有 46/20/7 只；6% 在 20/30/40 根有 90/55/26 只。
- 定高 6% 在 20/30/40 根有 11/2/1 只；8% 有 83/46/24 只；10% 有 211/126/63 只。
- 振幅 x0.5/20 根（末端振幅上限 8%）有 21 只，箱宽中位 10.6%。
- 布林矩形在日线上普遍偏宽：10% 容差只有 16 只但实际振幅中位 19.7%，不适合窄箱体目标。
- 均线走平 0.5% 容差只有 2~17 只；1% 容差、MA20/30、最少 30~40 根有 10~65 只。

### 历史事件研究

见 `param_sweep/event_tf_summary.csv`、`param_sweep/event_am_summary.csv`。9 个月末扫描日（实际可用 8 个），统计 T+5/10/20：

- 多数规则跑不赢全市场等权；横盘选股在这个样本里主要体现为低波动、低 beta，而不是超额收益。
- 相对“低波动率前 N”基准，`fix_6_20` 在 5/10/20 日的平均超额为 +0.63%/+1.00%/+1.02%，7/7 个日期为正（10 日）；`fix_8_30` 为 +0.28%/+0.70%/+0.59%。
- 箱体保持率（T+N 收盘仍在箱内）：`fix_8_30` 5/10/20 日为 85%/74%/58%，`fix_8_20` 为 83%/69%/56%，`fix_6_20` 为 77%/60%/41%。
- 容刺 4%/20 的箱体太窄，20 日后仅约 39% 仍在箱内；5%/30~6%/30 约 48%~50%。
- 样本只有 8 个日期、存在重叠窗口、无交易成本、未做 point-in-time 复权和退市处理，不能当作 alpha 证明。

## 最终口径

### 核心 4 组

| 规则 | 参数 |
| --- | --- |
| 定高 6% / 20 根 | `box_height=0.06, doji_amplitude=0.01, lookback=20, max_breach=2` |
| 定高 8% / 30 根 | `box_height=0.08, doji_amplitude=0.01, lookback=30, max_breach=2` |
| 容刺 5% / 30 根 | `box_height=0.05, lookback=30, max_breach=4, max_consecutive_breach=1` |
| 均线走平 MA30 / 40 根 / 1% | `ma_period=30, min_flat_bars=40, ma_tolerance=0.01, max_efficiency_ratio=0.5` |

### 扩展 2 组（想放宽时用）

| 规则 | 参数 |
| --- | --- |
| 容刺 6% / 30 根 | `box_height=0.06, lookback=30, max_breach=4, max_consecutive_breach=1` |
| 振幅 x0.5 / 20 根 / 上限 8% | `amp_multiple=0.5, max_amplitude=0.08, lookback=20, max_breach=2` |

## 结果

- 核心 4 组：87 行命中，去重 60 只；其中 19 只命中 ≥2 组核心规则，6 只命中 ≥3 组，2 只命中 4 组（辽港股份、中储股份）。
- 扩展 2 组再加入 34 只，合计去重 94 只。
- 文件：
  - `summary_optimized.csv`：6 组规则统计
  - `matches_optimized.csv`：全部命中明细（163 行）
  - `shortlist_core.csv`：核心 60 只
  - `shortlist_all_optimized.csv`：全部 94 只

## 复现

```bash
PYTHONPATH=/tmp/HengPan_XG_develop \
  /Users/allbule/Developer/PythonProject/workplaces01/.venv/bin/python \
  research/final_scan_daily.py \
  --csv /Users/allbule/Developer/PythonProject/workplaces01/api/data/his_Kline_data_qfq/all_stocks_360_day.csv \
  --out scan_results_daily_optimized_20261103 --scan-date 2026-11-03
```

参数曲面和历史事件研究脚本：`research/sweep_daily_params.py`、`research/event_study_daily.py`。

## 免责声明

这是形态候选池，不是买入名单。历史事件研究没有证明稳定 alpha，实盘前需自行做收益、风控和交易成本验证。
