#!/bin/bash
set -euo pipefail
umask 077

TAG="v1.11.0"
NAME="pal-arena-darwin-arm64-ef8cf269019e"
BASE="https://github.com/Jay1023CN/agent-cooperation/releases/download/$TAG"
CACHE="$HOME/Library/Caches/AgentCooperation"
RELEASES="$HOME/Applications/AgentCooperation/releases"
DATA="$HOME/.pal-arena/server"
SERVICE="$HOME/.pal-arena/services-server-$TAG"
PLIST="$HOME/Library/LaunchAgents/local.pal-arena.server.plist"
LABEL="gui/$(id -u)/local.pal-arena.server"

finish() { printf '\n按 Enter 关闭窗口。'; read -r _ || true; }
trap 'code=$?; if [ "$code" -ne 0 ]; then printf "\n安装没有完成，已有数据已保留。请把上面的错误发给 Codex。\n"; fi; finish' EXIT

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  printf '这个安装程序用于 Apple Silicon Mac。\n'
  exit 1
fi
if launchctl print "$LABEL" >/dev/null 2>&1; then
  printf 'Agent Cooperation 中心已经在后台运行。\n'
  open 'http://127.0.0.1:8765'
  exit 0
fi
if [ -e "$PLIST" ]; then
  printf '发现已有启动配置：%s\n请先让 Codex 检查它，再安装，避免重复启动中心。\n' "$PLIST"
  exit 1
fi
if /usr/sbin/lsof -n -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  printf '8765 端口已有服务。请先确认现有中心的状态。\n'
  exit 1
fi

mkdir -p "$CACHE" "$RELEASES" "$HOME/Library/LaunchAgents"
cd "$CACHE"
printf '正在下载 Agent Cooperation %s…\n' "$TAG"
if [ ! -f "$NAME.tar.gz" ]; then
  curl --fail --location --retry 3 "$BASE/$NAME.tar.gz" -o "$NAME.tar.gz.part"
  mv "$NAME.tar.gz.part" "$NAME.tar.gz"
fi
curl --fail --location --retry 3 "$BASE/$NAME.tar.gz.sha256" -o "$NAME.tar.gz.sha256"
shasum -a 256 -c "$NAME.tar.gz.sha256"
if [ ! -d "$RELEASES/$NAME" ]; then
  tar -xzf "$NAME.tar.gz" -C "$RELEASES"
fi
cd "$RELEASES/$NAME"
shasum -a 256 -c SHA256SUMS >/dev/null
EXE="$RELEASES/$NAME/pal-arena/pal-arena"
if [ ! -f "$SERVICE/local.pal-arena.server.plist" ]; then
  "$EXE" maintenance service --platform macos --role server --data "$DATA" --output "$SERVICE"
fi
cp "$SERVICE/local.pal-arena.server.plist" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf '正在启动中心…\n'
READY=0
for _ in $(seq 1 30); do
  if curl --fail --silent --max-time 2 'http://127.0.0.1:8765/health' >/dev/null; then READY=1; break; fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  printf '中心尚未启动，请检查：%s/logs/server.log\n' "$SERVICE"
  exit 1
fi
printf '\n中心已启动，之后登录这台 Mac 时会自动启动。\n工作台：http://127.0.0.1:8765\n登录令牌文件：%s/owner-token.txt\n' "$DATA"
printf '其他电脑访问前，请在这台 Mac 安装并登录 Tailscale，再在工作台的电脑页面开启共享。\n'
open 'http://127.0.0.1:8765'
