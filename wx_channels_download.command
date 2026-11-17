#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

SCRIPT="$DIR/wx_channels_download.py"
if [[ ! -f "$SCRIPT" ]]; then
  echo "找不到 wx_channels_download.py，请把它和本文件放在同一目录。"
  echo
  read -r -p "按回车键退出..."
  exit 1
fi

check_py() {
  local bin="$1"
  if [[ "$bin" != /* ]]; then
    command -v "$bin" >/dev/null 2>&1 || return 1
  else
    [[ -x "$bin" ]] || return 1
  fi
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

PY=""
for cand in python3 python /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if check_py "$cand"; then
    PY="$cand"
    break
  fi
done

if [[ -z "$PY" ]]; then
  echo "未找到 Python 3.8 或更高版本。"
  echo "请先安装 Python：https://www.python.org/downloads/"
  echo "在 Mac 上也可以用：brew install python"
  echo "本工具不会自动安装任何软件包。"
  echo
  read -r -p "按回车键退出..."
  exit 1
fi

"$PY" "$SCRIPT" menu
ERR=$?
if [[ "$ERR" -ne 0 ]]; then
  echo
  echo "程序退出代码：$ERR"
fi
echo
read -r -p "按回车键退出..."
exit "$ERR"
