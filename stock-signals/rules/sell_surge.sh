#!/bin/bash
# sell_surge.sh — 连板后高位天量派发卖出信号
# =================================================
# 基于华天科技（002185）5/27→5/28经典顶部案例提炼
#
# 核心逻辑：
#   连板/大涨后 → 天量换手 + 触板回落 → 射击星确认 = 卖出
#
# 两条规则：
#   R1: surge_touch_plate_dump — 天量触板回落（第一道警报）
#   R2: surge_shooting_star_confirm — 射击星+天量确认卖出（执行信号）
#
# 依赖：
#   - 日K线缓存（10根）：load_kline_cache <code>
#   - 换手率：$STOCK_TURNOVER（全局变量，engine.sh 注入）
#   - 成交额：$AMOUNT（全局变量，engine.sh 注入）
#   - 20日高点：$high20（参数传入）
#   - 流通股本：$TOTAL_SHARES（全局变量，用于估算成交额阈值）
# =================================================

# ──────────────────────────────────────────────
# R1: 天量触板回落 — 第一道警报
# ──────────────────────────────────────────────
# 条件（硬量化）：
#   1. 今日涨幅 5%~9.5%（非涨停，涨停是封住了）
#   2. 最高价距涨停 < 0.5%（摸到涨停门口）
#   3. 收盘价距涨停 > 2.0%（没封住，被砸回）
#   4. 换手率 > 25%（1/4以上流通盘换手）
#   5. 成交额 > 150亿（或量比 > 2.5倍均量）
#   6. 前N天有连板/大涨背景（至少1个涨停或连续3日涨幅>15%）
#
# 输出：surge_touch_plate_dump — 天量派发嫌疑
# ──────────────────────────────────────────────
rule_surge_touch_plate_dump() {
  local code="$1" name="$2" price="$3" change="$4" open="$5" high="$6" low="$7"
  local yclose="$8" vol="$9" high20="${15}" ma20="${12}"
  local turnover="${STOCK_TURNOVER:-0}" amount="${AMOUNT:-0}"

  # ── 条件1：涨幅5%~9.5%（涨停已封的不算） ──
  local abs_change=$(echo "$change" | sed 's/^-//' | tr -d '%')
  [ "$(echo "$abs_change >= 5 && $abs_change < 9.5" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件2：最高距涨停 < 0.5% ──
  # 涨停价 = 昨收 * 1.10（非ST）
  local limit_up=$(echo "scale=2; $yclose * 1.10" | bc -l 2>/dev/null)
  [ -z "$limit_up" ] || [ "$(echo "$limit_up == 0" | bc -l 2>/dev/null)" = "1" ] && return
  local high_to_limit=$(echo "scale=4; ($limit_up - $high) / $limit_up * 100" | bc -l 2>/dev/null)
  [ "$(echo "$high_to_limit < 0.5" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件3：收盘距涨停 > 2.0%（没封住） ──
  local close_to_limit=$(echo "scale=4; ($limit_up - $price) / $limit_up * 100" | bc -l 2>/dev/null)
  [ "$(echo "$close_to_limit > 2.0" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件4：换手率 > 25% ──
  [ "$(echo "$turnover > 25" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件5：量能验证（成交额>150亿 或 量比>2.5） ──
  local vol_ok=0
  if [ -n "$amount" ] && [ "$(echo "$amount > 15000000000" | bc -l 2>/dev/null)" = "1" ]; then
    vol_ok=1
  fi
  # 量比检查（通过缓存最近10日均量）
  local avg10v="${14}"  # avg5v参数位置
  if [ -n "$avg10v" ] && [ "$(echo "$avg10v > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local vol_ratio=$(echo "scale=2; $vol / $avg10v" | bc -l 2>/dev/null)
    [ "$(echo "$vol_ratio > 2.5" | bc -l 2>/dev/null)" = "1" ] && vol_ok=1
  fi
  [ "$vol_ok" -eq 0 ] && return

  # ── 条件6：前N天有连板/大涨背景 ──
  # 用缓存检查前几日是否有涨停或连续大涨
  local cache="$SIGNAL_DIR/cache/${code}.day"
  local surge_bg=0
  if [ -f "$cache" ]; then
    local lines=$(wc -l < "$cache")
    if [ "$lines" -ge 4 ]; then
      # 检查前2天是否有涨停（涨幅>=9.5%）
      local d1_close=$(tail -2 "$cache" | head -1 | awk '{print $1}')
      local d2_close=$(tail -3 "$cache" | head -1 | awk '{print $1}')
      local d3_close=$(tail -4 "$cache" | head -1 | awk '{print $1}')
      if [ -n "$d1_close" ] && [ -n "$d2_close" ] && [ -n "$d3_close" ]; then
        local d1_chg=$(echo "scale=2; ($d1_close - $d2_close) / $d2_close * 100" | bc -l 2>/dev/null)
        local d2_chg=$(echo "scale=2; ($d2_close - $d3_close) / $d3_close * 100" | bc -l 2>/dev/null)
        # 前2天有涨停
        if [ "$(echo "$d1_chg >= 9.5" | bc -l 2>/dev/null)" = "1" ] || \
           [ "$(echo "$d2_chg >= 9.5" | bc -l 2>/dev/null)" = "1" ]; then
          surge_bg=1
        fi
        # 或连续3日累计涨幅>15%
        if [ "$surge_bg" -eq 0 ] && [ -n "$d3_close" ]; then
          local d3_chg=$(echo "scale=2; ($d3_close - $(tail -5 "$cache" | head -1 | awk '{print $1}')) / $(tail -5 "$cache" | head -1 | awk '{print $1}') * 100" | bc -l 2>/dev/null)
          [ "$(echo "$d3_chg >= 15" | bc -l 2>/dev/null)" = "1" ] && surge_bg=1
        fi
      fi
    fi
  fi
  [ "$surge_bg" -eq 0 ] && return

  # ── 全部命中！ ──
  local note="天量触板回落-派发嫌疑,换手${turnover}%,距涨停${close_to_limit}%,前有连板背景"
  echo "{\"rule\":\"surge_touch_plate_dump\",\"direction\":\"bearish_warn\",\"strength\":\"very_high\",\"note\":\"${note}\"}"
}

# ──────────────────────────────────────────────
# R2: 射击星+天量确认卖出 — 执行信号
# ──────────────────────────────────────────────
# 条件（华天5/28模板）：
#   1. 平开或低开（开≈昨收，或开<昨收）
#   2. 上影线/收盘 > 2.0%（上影显著）
#   3. 实体/开盘 < 2.0%（实体小）
#   4. 成交额 > 100亿（天量维持）
#   5. 收盘 < 最高（没封住涨停）
#   6. 昨日已触发 R1 或 昨日换手>25%
#
# 输出：surge_shooting_star_confirm — 射击星确认卖出
# ──────────────────────────────────────────────
rule_surge_shooting_star_confirm() {
  local code="$1" name="$2" price="$3" change="$4" open="$5" high="$6" low="$7"
  local yclose="$8" vol="$9" high20="${15}" ma20="${12}"
  local turnover="${STOCK_TURNOVER:-0}" amount="${AMOUNT:-0}"

  [ -z "$open" ] || [ "$open" = "0.000" ] && return

  # ── 条件1：平开或低开（开≈昨收，偏差<1.5%） ──
  local open_vs_yclose=$(echo "scale=4; ($open - $yclose) / $yclose * 100" | bc -l 2>/dev/null)
  [ "$(echo "$open_vs_yclose < 1.5" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件2：上影线/收盘 > 2.0% ──
  local upper_end=$(echo "if($price > $open) $price else $open" | bc -l 2>/dev/null)
  local upper_shadow=$(echo "scale=2; $high - $upper_end" | bc -l 2>/dev/null)
  [ "$(echo "$upper_shadow <= 0" | bc -l 2>/dev/null)" = "1" ] && return
  local upper_pct=$(echo "scale=4; $upper_shadow / $price * 100" | bc -l 2>/dev/null)
  [ "$(echo "$upper_pct > 2.0" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件3：实体/开盘 < 2.0%（小实体） ──
  local body=$(echo "scale=2; $price - $open" | bc -l 2>/dev/null)
  local body_abs=$(echo "$body" | sed 's/^-//')
  local body_pct=$(echo "scale=4; $body_abs / $open * 100" | bc -l 2>/dev/null)
  [ "$(echo "$body_pct < 2.0" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件4：成交额 > 100亿（天量维持） ──
  if [ -n "$amount" ] && [ "$(echo "$amount > 10000000000" | bc -l 2>/dev/null)" = "1" ]; then
    :  # 通过
  else
    # 备选：量比>2.0
    local avg10v="${14}"
    if [ -n "$avg10v" ] && [ "$(echo "$avg10v > 0" | bc -l 2>/dev/null)" = "1" ]; then
      local vol_ratio=$(echo "scale=2; $vol / $avg10v" | bc -l 2>/dev/null)
      [ "$(echo "$vol_ratio > 2.0" | bc -l 2>/dev/null)" != "1" ] && return
    else
      return
    fi
  fi

  # ── 条件5：收盘 < 最高（没封住涨停） ──
  [ "$(echo "$price < $high" | bc -l 2>/dev/null)" != "1" ] && return

  # ── 条件6：昨日已触发R1 或 昨日换手>25% ──
  local prev_surge=0
  local cache="$SIGNAL_DIR/cache/${code}.day"
  if [ -f "$cache" ]; then
    local lines=$(wc -l < "$cache")
    if [ "$lines" -ge 2 ]; then
      # 读取昨日成交量
      local prev_vol=$(tail -2 "$cache" | head -1 | awk '{print $2}')
      local prev_close=$(tail -2 "$cache" | head -1 | awk '{print $1}')
      if [ -n "$prev_vol" ] && [ -n "$avg10v" ] && [ "$(echo "$avg10v > 0" | bc -l 2>/dev/null)" = "1" ]; then
        local prev_vol_ratio=$(echo "scale=2; $prev_vol / $avg10v" | bc -l 2>/dev/null)
        # 昨日量比>2.5 且 涨幅>5% → 昨日是放量大涨日
        if [ "$(echo "$prev_vol_ratio > 2.5" | bc -l 2>/dev/null)" = "1" ]; then
          prev_surge=1
        fi
      fi
    fi
  fi
  [ "$prev_surge" -eq 0 ] && return

  # ── 全部命中！ ──
  local note="射击星确认卖出-平开冲高回落留长上影,上影${upper_pct}%,换手${turnover}%,天量维持"
  echo "{\"rule\":\"surge_shooting_star_confirm\",\"direction\":\"sell_signal\",\"strength\":\"very_high\",\"note\":\"${note}\"}"
}

# ──────────────────────────────────────────────
# R3: 两天累计极端博弈区判定（辅助确认）
# ──────────────────────────────────────────────
# 条件：
#   1. 昨日换手 > 25%
#   2. 今日换手 > 22%
#   3. 两天累计涨幅 > 12%
#   4. 有连板背景
#
# 输出：surge_extreme_gamble — 极端博弈区
# ──────────────────────────────────────────────
rule_surge_extreme_gamble() {
  local code="$1" name="$2" price="$3" change="$4"
  local turnover="${STOCK_TURNOVER:-0}"

  # 今日换手 > 22%
  [ "$(echo "$turnover > 22" | bc -l 2>/dev/null)" != "1" ] && return

  # 读取昨日数据
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] && return
  local lines=$(wc -l < "$cache")
  [ "$lines" -lt 3 ] && return

  local prev_close=$(tail -2 "$cache" | head -1 | awk '{print $1}')
  local prev_open=$(tail -2 "$cache" | head -1 | awk '{print $3}')
  local prev_high=$(tail -2 "$cache" | head -1 | awk '{print $4}')
  local prev_low=$(tail -2 "$cache" | head -1 | awk '{print $5}')
  local prev_vol=$(tail -2 "$cache" | head -1 | awk '{print $2}')
  local d3_close=$(tail -3 "$cache" | head -1 | awk '{print $1}')
  [ -z "$prev_close" ] || [ -z "$d3_close" ] && return

  # 昨日换手 > 25%
  [ "$(echo "$turnover > 25" | bc -l 2>/dev/null)" != "1" ] && [ "$(echo "$turnover > 22" | bc -l 2>/dev/null)" != "1" ] && return

  # 两天累计涨幅 > 12%（从前天收盘到今天收盘）
  local two_day_chg=$(echo "scale=2; ($price - $d3_close) / $d3_close * 100" | bc -l 2>/dev/null)
  [ "$(echo "$two_day_chg > 12" | bc -l 2>/dev/null)" != "1" ] && return

  # 有连板背景（前3天至少1个涨停）
  local surge_bg=0
  if [ "$lines" -ge 4 ]; then
    local d4_close=$(tail -4 "$cache" | head -1 | awk '{print $1}')
    if [ -n "$d4_close" ] && [ -n "$d3_close" ]; then
      local d3_chg=$(echo "scale=2; ($d3_close - $d4_close) / $d4_close * 100" | bc -l 2>/dev/null)
      [ "$(echo "$d3_chg >= 9.5" | bc -l 2>/dev/null)" = "1" ] && surge_bg=1
    fi
    local d2_chg=$(echo "scale=2; ($prev_close - $d3_close) / $d3_close * 100" | bc -l 2>/dev/null)
    [ "$(echo "$d2_chg >= 9.5" | bc -l 2>/dev/null)" = "1" ] && surge_bg=1
  fi
  [ "$surge_bg" -eq 0 ] && return

  local note="两天换手超阈值+涨幅${two_day_chg}%+连板背景=极端博弈区"
  echo "{\"rule\":\"surge_extreme_gamble\",\"direction\":\"bearish_warn\",\"strength\":\"high\",\"note\":\"${note}\"}"
}
