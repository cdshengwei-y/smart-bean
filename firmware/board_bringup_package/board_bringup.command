#!/bin/zsh

set -u

PACKAGE_DIR="${0:A:h}"
PLATFORMIO_PYTHON="$HOME/.platformio/penv/bin/python"

if [[ ! -x "$PLATFORMIO_PYTHON" ]]; then
  echo "未找到PlatformIO Python：$PLATFORMIO_PYTHON"
  read "?按回车退出。"
  exit 1
fi

cd "$PACKAGE_DIR" || exit 1

echo ""
echo "新扩展板到板烧录包"
echo "1. 构建/更新烧录包"
echo "2. 只烧录自检固件（扩展板先不要插合）"
echo "3. 运行自检并生成验收日志（断电插合后）"
echo "4. 自检PASS后烧录正式固件"
echo "5. 采集正式固件启动日志"
echo ""
read "choice?请选择 [1-5]："

case "$choice" in
  1)
    "$PLATFORMIO_PYTHON" build_release.py
    ;;
  2)
    "$PLATFORMIO_PYTHON" flash_release.py self-test
    ;;
  3)
    "$PLATFORMIO_PYTHON" run_self_test.py
    ;;
  4)
    "$PLATFORMIO_PYTHON" flash_release.py production
    ;;
  5)
    "$PLATFORMIO_PYTHON" capture_production_boot.py
    ;;
  *)
    echo "无效选择。"
    false
    ;;
esac

exit_code=$?
echo ""
read "?操作结束，按回车关闭。"
exit "$exit_code"
