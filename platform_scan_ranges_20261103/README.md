# 平台定义扫描：平台起点 / 终点（2026-11-03）

## 口径

当前平台算法是**末端窗口法**：对每个窗口 W，只检查最近 W 根 K 线。因此：

- **平台终点**：固定为扫描日最后一根 K 线，2026-11-03。
- **平台起点**：最长“通过平台条件”的窗口的第一根 K 线。
- 起点是算法窗口的边界，不是被检测出来的“横盘真正开始日”。如果横盘在窗口之前就开始了，算法不会知道。
- 算法不回溯寻找已经结束的历史平台段；它只回答“截至扫描日，最近 W 根是否构成平台”。

## 配置

| 参数 | 值 |
|---|---|
| 数据 | `his_Kline_data_qfq/all_stocks_360_day.csv`，日线前复权 |
| 范围 | 全市场（沪/深/北交所），扫描日有 K 线且 ≥300 根，共 5,389 只 |
| 窗口 | 20 / 30 / 60 / 90 / 120 根 |
| 振幅阈值 | 10% |
| 均线粘合度 | 2% |
| 波动率阈值 | 2%/根 |
| 箱体检测 | 开启，箱体质量 ≥0.8 |
| 成交量 / 低位 / 快速下跌 | 核心口径不启用；扩展口径另算 |
| 突破 | 关闭（只标注） |

## 结果

核心平台定义命中 **213 只**：

| 平台长度 | 起点 | 数量 |
|---:|---|---:|
| 90 根 | 2026-06-23 | 1 |
| 60 根 | 2026-08-04 | 25 |
| 30 根 | 2026-09-15 | 125 |
| 20 根 | 2026-09-30 | 62 |

扩展口径（量能 0.9/0.75 + 低位 300 根/30%/180 天 + 快速下跌 30 根/15%）命中 **25 只**；页面原量能 0.5/0.5 命中 **2 只**。

## 文件

- `platform_matches_all.csv`：213 只核心平台，含 `platform_start` / `platform_end` / `platform_bars` / `window_starts` / 箱体质量 / 量能 / 回撤
- `platform_matches_extended_09.csv`：扩展口径 25 只
- `summary.json`：配置与计数

## 复现

```bash
PYTHONPATH=/tmp/HengPan_XG_develop \
  /Users/allbule/Developer/PythonProject/workplaces01/.venv/bin/python \
  research/platform_range_scan.py \
  /Users/allbule/Developer/PythonProject/workplaces01/api/data/his_Kline_data_qfq/all_stocks_360_day.csv \
  platform_scan_ranges_20261103
```

## 平台突破指标 PBI

对每只平台股计算 0–100 的 Platform Breakout Indicator：

```text
price_score   = clip((close / box_top - 0.95) / 0.10, 0, 1)
volume_score  = clip((vol_ratio_5_20 - 1.0) / 1.5, 0, 1)
signal_score  = signal_count / 4          # MACD / RSI / KDJ / 布林带
confirm_score = 1 if has_confirmation else 0

PBI = 40 * price_score + 30 * volume_score + 20 * signal_score + 10 * confirm_score
```

- `box_top`：平台最强阻力位（箱体上沿）。
- `vol_ratio_5_20`：最近 5 根平均成交量 / 前 20 根平均成交量。
- `signal_count`：四个技术指标中有几个给出突破信号。
- `has_confirmation`：突破日放量 1.5 倍、涨幅 >2%，且随后 1 根收盘不低于突破日收盘的 98%。

突破状态：

| 状态 | 条件 |
|---|---|
| 已确认突破 | `close > box_top*1.02` 且 `vol_ratio >= 1.5` 且 `has_confirmation` |
| 突破中 | `close > box_top` 且 `vol_ratio >= 1.2` |
| 突破未放量 | `close > box_top` 且 `vol_ratio < 1.2` |
| 临近突破 | `box_top*0.97 <= close <= box_top` |
| 箱体内 | `box_bottom <= close < box_top*0.97` |
| 跌破下沿 | `close < box_bottom*0.98` |

结果文件：

- `platform_breakout_indicator.csv`：213 只平台股的 PBI、状态、量比、技术信号、箱体上下沿
- `platform_breakout_top30.csv`：PBI 前 30

注意：PBI 是形态强度排序，不是突破成功概率，也没有做未来收益验证。

## 注意

- 平台终点是扫描日，不是“平台结束日”。如果平台在扫描日之前已经结束，当前算法不会给出它。
- 扩展口径的量能按“最长平台窗口”计算；同一只票在短窗口可能通过、在长窗口可能不通过。
- 结果只是形态候选，不含未来收益检验。
