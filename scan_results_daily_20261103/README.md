# 日线 K 线横盘扫描结果（2026-11-03）

## 数据

- 来源：`workplaces01/api/data/his_Kline_data_qfq/all_stocks_360_day.csv`
- A 股日线，前复权（本地 0x000f 重建），5,578 只，2025-05-13 ~ 2026-11-03
- 扫描日：2026-11-03；用全市场交易日历检查回验窗口，停牌缺口跳过

## 算法

使用 `origin/develop` 的五个纯算法模块：

- `fixed` / `amplitude`：`api/hengpan/anchored_box.py`
- `tolerant`：`api/hengpan/tolerant_box.py`
- `boll_box`：`api/hengpan/boll_box.py`
- `ma_flat`：`api/hengpan/ma_flat.py`

脚本：`research/scan_daily_kline.py`。运行时需要 develop 工作区在 `PYTHONPATH` 中，例如：

```bash
PYTHONPATH=/tmp/HengPan_XG_develop \
  /Users/allbule/Developer/PythonProject/workplaces01/.venv/bin/python \
  research/scan_daily_kline.py \
  --csv /Users/allbule/Developer/PythonProject/workplaces01/api/data/his_Kline_data_qfq/all_stocks_360_day.csv \
  --out scan_results_daily_20261103 --scan-date 2026-11-03
```

## 文件

| 文件 | 内容 |
| --- | --- |
| `summary.csv` | 17 组规则的 analyzed / passed_body / passed_full / gap / insufficient |
| `matches_all.csv` | 实体口径全部命中（760 行，13 组规则有命中） |
| `matches_full_only.csv` | 整体口径命中（613 行） |
| `shortlist_narrow.csv` | 实体口径下去重、实际振幅 ≤10% 的 73 只 |
| `shortlist_narrow_full.csv` | 整体口径下去重、实际振幅 ≤10% 的 65 只 |
| `cross_family.csv` | 命中 ≥3 个模式家族的 7 只 |

## 关键结论

- 仓库默认的容刺 4% / 80 根在日线上是 **0 只**；3%、5% 也是 0；定高 4% / 80 根同样 0。
- 原因是默认参数按 60 分钟 80 根（约 20 个交易日）设计，日线 80 根约 4 个月，4% 箱体过严。
- 日线上较有意义的口径是：定高 6%~10% / 40 根、均线走平 1%~1.5% / 30~50 根、容刺 4% / 40 根。
- 布林矩形和振幅模式不保证价格窄，布林 10% 的 3 只实际振幅仍有 23%~43%。

## 免责声明

结果只是形态候选，不含未来收益检验，不构成投资建议。
