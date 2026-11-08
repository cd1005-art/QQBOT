#!/bin/sh
set -e
CFG="${QQBOT_CONFIG:-/app/config/config.toml}"
if [ ! -f "$CFG" ]; then
  mkdir -p "$(dirname "$CFG")"
  cp /app/config.example.toml "$CFG"
  echo "[entrypoint] 未找到配置，已生成 $CFG（首次访问面板会引导填写）"
fi
echo "[entrypoint] 启动 qqbot，配置 $CFG"
exec python -m qqbot serve
