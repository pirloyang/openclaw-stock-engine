#!/bin/bash
# ============================================================
# rule_pe_valuation.sh - PE/TTM 估值预警规则
# 数据源: engine.sh 已预取的全局变量 $RAW（qt.gtimg.cn 批量行情）
# gtimg 字段布局: 字段40=PE_TTM, 字段45=PE_动态
# ============================================================

rule_pe_valuation() {
  local code="$1" market="$2" price="$3"
  
  # 从 $RAW 全局变量中提取 PE 数据（避免重复 curl）
  if [ -z "$RAW" ]; then
    return 0
  fi
  
  local pfx
  case "$market" in
    sh) pfx="sh" ;;
    sz) pfx="sz" ;;
    *) return 0 ;;
  esac
  
  # 从 RAW 中 grep 该标的的行情行
  # 格式: v_sz300964="51~本川智能~300964~115.25~..."
  local line
  line=$(echo "$RAW" | grep -m1 "v_${pfx}${code}=")
  
  if [ -z "$line" ]; then
    return 0
  fi
  
  # 提取引号内数据，取字段40=PE_TTM
  local data
  data=$(echo "$line" | sed 's/^[^"]*"//' | sed 's/"$//')
  
  local pe_ttm
  pe_ttm=$(echo "$data" | cut -d'~' -f40 2>/dev/null)
  
  # 清理数字
  pe_ttm=$(echo "$pe_ttm" | sed 's/[^0-9.]//g')
  
  if [ -z "$pe_ttm" ] || [ "$pe_ttm" = "0" ] || [ "$pe_ttm" = "0.00" ]; then
    return 0
  fi
  
  # 判定
  if [ "$(echo "$pe_ttm > 200" | bc 2>/dev/null)" = "1" ]; then
    echo "pe_extreme|sell|PE_TTM=${pe_ttm}极高估值,风险极大"
  elif [ "$(echo "$pe_ttm > 100" | bc 2>/dev/null)" = "1" ]; then
    echo "pe_overvalued|sell|PE_TTM=${pe_ttm}偏高估值"
  elif [ "$(echo "$pe_ttm > 80" | bc 2>/dev/null)" = "1" ]; then
    echo "pe_overvalued|sell|PE_TTM=${pe_ttm}估值偏高"
  fi
}
