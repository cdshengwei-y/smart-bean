#!/bin/bash
# 冒烟测试运行器（参考 flutter-smoke-auto 的 scoped-run 理念）。
# 用法：
#   ./run_smoke.sh              # 全部旅程（需录音豆开机在旁）
#   ./run_smoke.sh cold         # 仅冷启动（只读，无需设备）
#   ./run_smoke.sh record       # 连接+录音（mutates-recordings）
#   ./run_smoke.sh files        # 文件页（依赖 record 已跑过）
# 依赖：python3 + Pillow（截屏像素判定 APP 是否已自动重连设备）。
set -u
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export PATH="$JAVA_HOME/bin:$HOME/.maestro/bin/bin:$HOME/Library/Android/sdk/platform-tools:$PATH"
cd "$(dirname "$0")"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
RECORD_DEVICE_URL="${RECORD_DEVICE_URL:-http://ai-recorder-bean-ye-2.local/files}"

MODE="${1:-all}"
REPORT_DIR="report"
mkdir -p "$REPORT_DIR"
TS=$(date +%Y%m%d_%H%M%S)
REPORT="$REPORT_DIR/smoke_$TS.txt"

# 判断 APP 控制页是否处于"已连接"态：
# 已连接态在屏幕 30%~40% 高度带有浅绿"开始录音"大按钮（实测 RGB≈152,212,191），
# 未连接态该区域是深色卡片背景（≈29,35,31）。扫描整带避开中央话筒图标。
detect_preconnected() {
  "$ADB" exec-out screencap -p > /tmp/smoke_screen.png 2>/dev/null || { echo "false"; return; }
  python3 - <<'PYEOF'
try:
    from PIL import Image
    im = Image.open("/tmp/smoke_screen.png").convert("RGB")
    w, h = im.size
    px = im.load()
    hits = 0
    for fy in range(30, 41, 2):
        for fx in range(20, 81, 5):
            r, g, b = px[int(w*fx/100), int(h*fy/100)]
            if g > 150 and (g - r) > 25 and b > 120:
                hits += 1
    print("true" if hits >= 5 else "false")
except Exception:
    print("false")
PYEOF
}

run_flow() {
  local file="$1"
  local name="$2"
  echo "===== ${name} =====" | tee -a "${REPORT}"
  if maestro test --no-reinstall-driver "${file}" >>"${REPORT}" 2>&1; then
    echo "PASS  ${name}" | tee -a "${REPORT}"
    return 0
  else
    echo "FAIL  ${name}（详见 ${REPORT}）" | tee -a "${REPORT}"
    return 1
  fi
}

run_record() {
  local state
  state=$(detect_preconnected)
  echo "APP 启动前连接状态: PRECONNECTED=${state}" | tee -a "${REPORT}"
  if maestro test --no-reinstall-driver -e "PRECONNECTED=${state}" \
      flows/02_connect_and_record.yaml >>"${REPORT}" 2>&1; then
    echo "PASS  连接并录音" | tee -a "${REPORT}"
    return 0
  else
    echo "FAIL  连接并录音（详见 ${REPORT}）" | tee -a "${REPORT}"
    return 1
  fi
}

# 旅程2整链路（点按→落卡校验→自动重试）。
# BLE 链路存在约 1 分钟一次的偶发闪断（自动重连 <1s 内完成），
# 点按可能恰好落在断连窗口 → 单次 attempt 可能"点了但没录上"，
# 所以按"流程+落卡校验"为整体最多重试 3 次，落卡才算真 PASS。
run_record_verified() {
  local attempt verify_out verify_rc
  for attempt in 1 2 3; do
    echo "===== 连接并录音（第 ${attempt}/3 次尝试）=====" | tee -a "${REPORT}"
    run_record || true
    echo "----- 设备侧校验 -----" | tee -a "${REPORT}"
    verify_out=$(verify_recording_landed 2>&1)
    verify_rc=$?
    echo "${verify_out}" | tee -a "${REPORT}"
    if [ "${verify_rc}" -eq 0 ]; then
      echo "PASS  录音落卡校验" | tee -a "${REPORT}"
      return 0
    fi
    echo "第 ${attempt} 次尝试未落卡，准备重试..." | tee -a "${REPORT}"
    sleep 3
  done
  echo "FAIL  录音落卡校验（3 次尝试均未在设备 TF 卡发现新录音）" | tee -a "${REPORT}"
  return 1
}

# 旅程2的端到端校验：Maestro 坐标点按无法断言（settle 卡死坑），
# 假 PASS 曾经漏掉"点了但没录上"。改为查设备 TF 卡，
# 确认 3 分钟内确有新录音文件落卡才算真 PASS。
verify_recording_landed() {
  python3 - "$RECORD_DEVICE_URL" <<'PYEOF'
import json, sys, urllib.request, datetime
url = sys.argv[1]
try:
    files = json.load(urllib.request.urlopen(url, timeout=8))["files"]
    now = datetime.datetime.now(datetime.UTC)
    fresh = []
    for f in files:
        try:
            t = datetime.datetime.strptime(f["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC)
            if (now - t).total_seconds() < 180:
                fresh.append(f["name"])
        except ValueError:
            pass
    if fresh:
        print("PASS-VERIFY 新录音落卡: " + ", ".join(fresh))
        sys.exit(0)
    print("FAIL-VERIFY 设备 TF 卡 3 分钟内没有新录音（流程可能点了但没录上）")
    sys.exit(1)
except Exception as e:
    print("FAIL-VERIFY 无法读取设备文件列表: %s" % e)
    sys.exit(1)
PYEOF
}

FAILURES=0
case "$MODE" in
  cold)   run_flow flows/01_cold_start.yaml "冷启动" || FAILURES=$((FAILURES+1)) ;;
  record) run_record_verified || FAILURES=$((FAILURES+1)) ;;
  files)  run_flow flows/03_files_page.yaml "文件页" || FAILURES=$((FAILURES+1)) ;;
  all)
    run_flow flows/01_cold_start.yaml "冷启动" || FAILURES=$((FAILURES+1))
    run_record_verified || FAILURES=$((FAILURES+1))
    run_flow flows/03_files_page.yaml "文件页" || FAILURES=$((FAILURES+1))
    ;;
  *) echo "用法: ./run_smoke.sh [cold|record|files|all]"; exit 2 ;;
esac

echo "" | tee -a "${REPORT}"
echo "结果: ${FAILURES} 个失败（报告: ${REPORT}）" | tee -a "${REPORT}"
exit "${FAILURES}"
