#!/bin/bash
# 截图文字提取脚本 - 针对股票截图优化
# 用法: ocr_image.sh <图片路径>
#
# PSM模式说明:
#   6 = 统一文本块 (默认,适合截图段落)
#   4 = 单列文本 (适合表格)
#   3 = 全自动 (默认)

IMAGE="$1"
LANG="${2:-chi_sim+eng}"

if [ -z "$IMAGE" ] || [ ! -f "$IMAGE" ]; then
  echo "用法: ocr_image.sh <图片路径>"
  echo "示例: ocr_image.sh screenshot.png"
  exit 1
fi

# 先试PSM6 (大块文本，适合截图整体)
echo "=== 识别结果 ==="
tesseract "$IMAGE" stdout -l "$LANG" --psm 6 \
  --oem 3 \
  -c tessedit_char_whitelist='0123456789.+-%:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ一股新春光蓝标金风科技智控华友钼业五洲交通通信达有色资源矿基金属电机器自动化车零配舟日月天地人上下左右中大小多少前后' 2>/dev/null

echo ""
echo "=== 数值提取 (精简) ==="
tesseract "$IMAGE" stdout -l "$LANG" --psm 6 --oem 3 \
  -c tessedit_char_whitelist='0123456789.+-%' 2>/dev/null | tr '\n' ' ' | tr -s ' '
echo ""
