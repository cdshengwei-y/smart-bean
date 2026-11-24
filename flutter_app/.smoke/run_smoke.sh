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
# 已连接态有全宽浅绿"开始录音"大按钮（RGB≈152,212,191），
# v0.5.52 更新横幅会把按钮从 ~36% 压到 ~44% 高度，扫描带取 25%~55%。
# 未连接态该带只有右侧"查找"小按钮（同为浅绿）→ 用"整行跨度>60%屏宽
# 且连续组高≥10 个采样行"区分大按钮与小按钮。
detect_preconnected() {
  "$ADB" exec-out screencap -p > /tmp/smoke_screen.png 2>/dev/null || { echo "false"; return; }
  python3 - <<'PYEOF'
try:
    from PIL import Image
    im = Image.open("/tmp/smoke_screen.png").convert("RGB")
    w, h = im.size
    px = im.load()
    rows = []
    for y in range(int(h*0.25), int(h*0.55), 4):
        xs = [x for x in range(0, w, 6)
              if (lambda r,g,b: g > 150 and (g-r) > 25 and b > 120)(*px[x, y])]
        if xs and (max(xs) - min(xs)) > w * 0.6:
            rows.append(y)
    ok = False
    if rows:
        start = prev = rows[0]
        for y in rows[1:] + [10**9]:
            if y - prev > 8:
                if prev - start >= 40:  # 按钮高约 130px ≈ 33 行
                    ok = True
                start = y
            prev = y
    print("true" if ok else "false")
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

# 读取设备录音状态（true/false，统一小写），失败返回 unknown。
device_recording_state() {
  curl -s --max-time 6 "${RECORD_DEVICE_URL%/files}/status" 2>/dev/null \
    | python3 -c "import json,sys; print(str(json.load(sys.stdin).get('recording')).lower())" 2>/dev/null || echo "unknown"
}

# 动态定位全宽色块按钮中心（布局会被更新横幅等元素推移，固定坐标不可靠）。
# 参数：light=浅绿开始录音按钮(152,212,191)；dark=深绿设备行(32,80,66)。
# 输出绝对坐标 "x y"，找不到输出空串。
locate_wide_button() {
  "$ADB" exec-out screencap -p > /tmp/smoke_btn.png 2>/dev/null || return
  python3 - "$1" <<'PYEOF'
import sys
mode = sys.argv[1]
try:
    from PIL import Image
    im = Image.open("/tmp/smoke_btn.png").convert("RGB")
    w, h = im.size
    px = im.load()
    if mode == "light":
        match = lambda r,g,b: g > 150 and (g - r) > 25 and b > 120
    else:  # dark green device row
        match = lambda r,g,b: 60 < g < 110 and r < 60 and 50 < b < 90
    # 逐行统计：只认单行跨度 >60% 屏宽的行（按钮行），取第一组连续按钮行
    # （组高≥40 采样行）的中心——开始录音按钮约 130px 高。
    rows = []
    for y in range(int(h*0.25), int(h*0.60), 4):
        xs = [x for x in range(0, w, 6) if match(*px[x, y])]
        if xs and (max(xs) - min(xs)) > w * 0.6:
            rows.append(y)
    if mode == "light" and rows:
        start = prev = rows[0]
        result = None
        for y in rows[1:] + [10**9]:
            if y - prev > 8:
                if prev - start >= 40:
                    result = (start + prev) // 2
                    break
                start = y
            prev = y
        if result:
            print(w // 2, result)
    elif rows:
        print(w // 2, (rows[0] + rows[-1]) // 2)
except Exception:
    pass
PYEOF
}

# 旅程2（纯 adb 实现）。Maestro 在这台 MIUI 上点按要走无障碍树快照，
# 该快照会在工作流卡片动画期间无限挂起（waitForIdleTimeout 也压不住），
# 所以点按/等待全部用 adb input + sleep，判活用像素与设备 /status。
run_record_adb() {
  local shot_dir="report/shots_${TS}"
  mkdir -p "${shot_dir}"

  "$ADB" shell am start -n com.aibean.controller/com.aibean.ai_recording_bean.MainActivity >/dev/null 2>&1
  sleep 4
  local state
  state=$(detect_preconnected)
  echo "启动后连接状态: PRECONNECTED=${state}" | tee -a "${REPORT}"

  if [ "${state}" != "true" ]; then
    echo "步骤1: 点查找→等扫描→动态定位设备行并点击（内层重试）" | tee -a "${REPORT}"
    # BLE 空闲闪断会打断连接建立且 APP 自动重连 3 次即放弃，
    # 所以连接本身也要内层重试：每轮 点查找→点设备→轮询 15 秒。
    local round
    for round in 1 2 3; do
      "$ADB" shell input tap $((1280*81/100)) $((2772*38/100))
      sleep 8
      "$ADB" exec-out screencap -p > "${shot_dir}/scan_list_r${round}.png" 2>/dev/null
      local pos
      pos=$(locate_wide_button dark)
      if [ -z "${pos}" ]; then
        echo "第 ${round} 轮：扫描结果里没有设备行" | tee -a "${REPORT}"
        continue
      fi
      echo "第 ${round} 轮：设备行位置 ${pos}，点击连接" | tee -a "${REPORT}"
      "$ADB" shell input tap ${pos}
      local wait=0 connected=false
      while [ ${wait} -lt 15 ]; do
        sleep 1
        wait=$((wait+1))
        if [ "$(detect_preconnected)" = "true" ]; then connected=true; break; fi
      done
      if [ "${connected}" = "true" ]; then break; fi
      echo "第 ${round} 轮：15 秒内未连上（可能撞上 BLE 闪断窗口）" | tee -a "${REPORT}"
    done
    state=$(detect_preconnected)
    echo "连接后状态: PRECONNECTED=${state}" | tee -a "${REPORT}"
    if [ "${state}" != "true" ]; then
      "$ADB" exec-out screencap -p > "${shot_dir}/connect_failed.png" 2>/dev/null
      echo "FAIL  连接设备失败（3 轮重试后开始录音按钮仍未出现）" | tee -a "${REPORT}"
      return 1
    fi
  fi

  echo "步骤2: 动态定位开始录音按钮并点击，轮询设备 /status 确认真开录" | tee -a "${REPORT}"
  local pos2
  pos2=$(locate_wide_button light)
  if [ -z "${pos2}" ]; then
    echo "FAIL  找不到开始录音按钮（浅绿全宽按钮）" | tee -a "${REPORT}"
    return 1
  fi
  "$ADB" exec-out screencap -p > "${shot_dir}/before_record.png" 2>/dev/null
  "$ADB" shell input tap ${pos2}
  local rec="false"
  for i in 1 2 3 4 5; do
    sleep 2
    rec=$(device_recording_state)
    [ "${rec}" = "true" ] && break
  done
  if [ "${rec}" = "true" ]; then
    echo "PASS  录音已开始（设备 recording=true）" | tee -a "${REPORT}"
  else
    "$ADB" exec-out screencap -p > "${shot_dir}/record_not_started.png" 2>/dev/null
    echo "FAIL  点开始录音后设备未进入录音态（recording=${rec}）" | tee -a "${REPORT}"
    # 若其实已在录音则补一次停止，避免留下失控录音
    if [ "${rec}" != "false" ]; then
      "$ADB" shell input tap ${pos2} 2>/dev/null
    fi
    return 1
  fi

  echo "步骤3: 录 10 秒" | tee -a "${REPORT}"
  sleep 10
  echo "步骤4: 点停止录音" | tee -a "${REPORT}"
  "$ADB" shell input tap ${pos2}
  sleep 3
  local stopped
  stopped=$(device_recording_state)
  "$ADB" exec-out screencap -p > "${shot_dir}/after_stop.png" 2>/dev/null
  if [ "${stopped}" = "true" ]; then
    echo "WARN  仍在录音，再点一次停止" | tee -a "${REPORT}"
    "$ADB" shell input tap ${pos2}
    sleep 3
    stopped=$(device_recording_state)
  fi
  if [ "${stopped}" = "false" ]; then
    echo "PASS  录音已停止（设备 recording=false）" | tee -a "${REPORT}"
  else
    echo "WARN  停止后 recording=${stopped}（不影响落卡校验）" | tee -a "${REPORT}"
  fi
  return 0
}

# 旅程2整链路（点按→落卡校验→自动重试）。
# BLE 链路存在约 1 分钟一次的偶发闪断（自动重连 <1s 内完成），
# 点按可能恰好落在断连窗口 → 单次 attempt 可能"点了但没录上"，
# 所以按"流程+落卡校验"为整体最多重试 3 次，落卡才算真 PASS。
run_record_verified() {
  local attempt verify_out verify_rc
  for attempt in 1 2 3; do
    echo "===== 连接并录音（第 ${attempt}/3 次尝试）=====" | tee -a "${REPORT}"
    run_record_adb || true
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
