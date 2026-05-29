# 股票监控指标体系 V3.0

## 架构

```
stock-signals/
├── engine.sh          ← 信号引擎（数据预取→规则消费，零网络调用）
├── rules/             ← 7个规则模块（纯计算，无curl）
│   ├── price.sh       ← 价格异动分级
│   ├── trend.sh       ← 均线排列/金叉死叉/MACD顶底背离(EMA)/MA偏离限制
│   ├── volume.sh      ← 量比/量价矩阵（缓存成交量为统一「股」单位）
│   ├── sector.sh      ← 该涨不涨/该跌不跌（大盘对照）
│   ├── support_resistance.sh ← 突破/2B法则/筹码密集区
│   ├── kline.sh       ← 锤子线/上吊线/十字星/红三兵/三只乌鸦
│   └── entry_exit.sh  ← 入场止损位(阳线实体50%)
├── cache/             ← 69只标的×60天日线（价格+成交量）
├── price_cache.sh     ← 收盘缓存更新脚本
└── README.md
```

## 数据流

```
tools.sh (holdings/history) → 获取代码列表
         ↓
engine.sh → 1. batch fetch gtimg(25只/批) → realtime prices
            2. read cache → MA5/10/20/60, MACD(EMA), 量比
            3. fetch market(上证/创业板) → sector rules
            4. for each stock → call all rules → calc resonance → JSON
```

## 信号分级

| 级别 | 触发条件 | 行动 |
|------|---------|------|
| 🔴 L3 紧急 | >±7% | 需综合判断 |
| ⚡⚡ L2 强势 | >±4% | 关注 |
| ⚡ L1 常规 | >±2% | 观察 |

## 共振规则（两真一并）

| 买入信号数 | 判决 |
|-----------|------|
| ≥3 | 三重共振-出手 |
| 2 | 双重确认-可参与 |
| 1 | 单一信号-观察 |
| 卖出≥2 | 卖出确认-减仓 |
| 卖出=1 | 卖出预警-关注 |

## 规则清单（7模块 × 14条规则）

1. price_action — 价格异动分级
2. bullish/bearish_arrangement — 均线多空排列
3. ma_golden/death_cross — 5日线金叉/死叉20日线
4. macd_bottom/top_div — MACD底背离/顶背离(EMA算法)
5. macd_zone — MACD零轴位置
6. ma5_gap(>5%禁止加仓) / ma20_gap(>30%禁止买入)
7. volume_ratio — 量比(今日量/5日均量)
8. volume_price — 量价矩阵(涨增/涨缩/跌增/跌缩)
9. should_rise_fail / should_fall_strong — 该涨不涨/该跌不跌
10. breakout — 20日高低点突破
11. 2b — 2B假突破猎杀
12. density_zone — 筹码密集区
13. hammer/hanging_man/doji — K线形态
14. entry_stop_loss — 入场止损位

## 定时任务

| 时间 | 任务 | 调用 |
|------|------|------|
| 09:35-14:55 每10分 | 盘中监控 | bash tools.sh monitor |
| 15:05 | 缓存更新 | bash price_cache.sh update |
| 08:30/09:25/12:00/15:30/21:00/22:00 | 6份报告 | 引擎+规则+ma缓存 |
