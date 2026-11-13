#!/usr/bin/env bash
# QQBOT 安装脚本
#
#   bash install.sh                        # 默认装到 /opt/qqbot，注册 systemd
#   bash install.sh --dir ~/qqbot          # 装到指定目录
#   bash install.sh --no-service           # 不装系统服务，只准备环境
#   bash install.sh --check                # 只检查环境，不改任何文件
#
# 升级时重跑即可：config.toml 与 data/ 不会被覆盖。

set -Eeuo pipefail

APP_DIR=${QQBOT_DIR:-/opt/qqbot}
SVC_USER=${QQBOT_USER:-qqbot}
PORT_WEBHOOK=${QQBOT_PORT_WEBHOOK:-18900}
PORT_PANEL=${QQBOT_PORT_PANEL:-18901}
NO_SERVICE=0
CHECK_ONLY=0
PORTS_EXPLICIT=0
PIP_INDEX=${QQBOT_PIP_INDEX:-}

c_info()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
c_ok()    { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
c_warn()  { printf '\033[33m  ! \033[0m%s\n' "$*"; }
c_err()   { printf '\033[31m  ✗ %s\033[0m\n' "$*" >&2; }
die()     { c_err "$*"; exit 1; }

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)          APP_DIR="$2"; shift 2 ;;
    --user)         SVC_USER="$2"; shift 2 ;;
    --port-webhook) PORT_WEBHOOK="$2"; PORTS_EXPLICIT=1; shift 2 ;;
    --port-panel)   PORT_PANEL="$2"; PORTS_EXPLICIT=1; shift 2 ;;
    --pip-index)    PIP_INDEX="$2"; shift 2 ;;
    --no-service)   NO_SERVICE=1; shift ;;
    --check)        CHECK_ONLY=1; shift ;;
    -h|--help)      usage ;;
    *)              die "未知参数: $1（用 --help 查看用法）" ;;
  esac
done

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# ---------------- 1. 环境检查 ----------------

c_info "检查环境"

PY=""
for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[[ -n "$PY" ]] || die "未找到 python3，请先安装 Python 3.9 或更高版本"

PY_VER=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
PY_OK=$("$PY" -c 'import sys;print(1 if sys.version_info>=(3,9) else 0)')
[[ "$PY_OK" == "1" ]] || die "Python 版本过低（当前 $PY_VER），需要 3.9+"
c_ok "Python $PY_VER ($PY)"

command -v tar >/dev/null 2>&1 || die "缺少 tar"
[[ -d "$SRC_DIR/qqbot" ]] || die "找不到 qqbot/ 目录，请在解压后的源码目录内运行"
c_ok "源码目录 $SRC_DIR"

HAS_SYSTEMD=0
if [[ $NO_SERVICE -eq 0 ]] && command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  HAS_SYSTEMD=1
fi
if [[ $NO_SERVICE -eq 0 && $HAS_SYSTEMD -eq 0 ]]; then
  c_warn "未检测到 systemd，将跳过服务注册（可用 --no-service 消除此提示）"
fi
c_ok "目标目录 $APP_DIR"

if [[ $CHECK_ONLY -eq 1 ]]; then
  c_info "仅检查模式，未做任何改动"
  exit 0
fi

# ---------------- 2. 拷贝文件 ----------------

c_info "安装文件到 $APP_DIR"
mkdir -p "$APP_DIR" "$APP_DIR/data"

copy_tree() {
  local src="$1" dst="$2"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$src" "$dst"
  else
    rm -rf "$dst"
    cp -a "$src" "$dst"
  fi
}

for d in qqbot web deploy docker; do
  [[ -d "$SRC_DIR/$d" ]] && copy_tree "$SRC_DIR/$d/" "$APP_DIR/$d/"
done
for f in requirements.txt config.example.toml README.md LICENSE Dockerfile docker-compose.yml; do
  [[ -f "$SRC_DIR/$f" ]] && cp -f "$SRC_DIR/$f" "$APP_DIR/$f"
done
c_ok "代码已同步（data/ 与 config.toml 保留）"

# ---------------- 3. 依赖 ----------------

c_info "准备 Python 虚拟环境"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  "$PY" -m venv "$APP_DIR/venv" || die "创建 venv 失败（Debian/Ubuntu 需先装 python3-venv）"
  c_ok "venv 已创建"
else
  c_ok "venv 已存在，复用"
fi

PIP_ARGS=(-q --upgrade pip setuptools wheel)
if [[ -n "$PIP_INDEX" ]]; then PIP_ARGS+=(-i "$PIP_INDEX"); fi
"$APP_DIR/venv/bin/pip" install "${PIP_ARGS[@]}" >/dev/null 2>&1 || c_warn "pip 升级失败，继续"

INSTALL_ARGS=(-q -r "$APP_DIR/requirements.txt")
if [[ -n "$PIP_INDEX" ]]; then INSTALL_ARGS+=(-i "$PIP_INDEX"); fi
c_info "安装依赖（首次约 1~2 分钟）"
"$APP_DIR/venv/bin/pip" install "${INSTALL_ARGS[@]}" || die "依赖安装失败，可加 --pip-index https://mirrors.aliyun.com/pypi/simple/"
c_ok "依赖就绪"

# ---------------- 4. 配置 ----------------

NEW_CFG=0
if [[ ! -f "$APP_DIR/config.toml" ]]; then
  cp "$APP_DIR/config.example.toml" "$APP_DIR/config.toml"
  chmod 600 "$APP_DIR/config.toml"
  NEW_CFG=1
  c_warn "已生成 config.toml，需填写 app_id / app_secret / panel_password"
else
  c_ok "config.toml 已存在，未覆盖"
fi

# 端口：新建配置时写入；老配置仅在显式传参时改
if [[ $NEW_CFG -eq 1 || $PORTS_EXPLICIT -eq 1 ]]; then
  sed -i "/^\[webhook\]/,/^\[/ s/^port = .*/port = $PORT_WEBHOOK/" "$APP_DIR/config.toml"
  sed -i "/^\[panel\]/,/^\[/ s/^port = .*/port = $PORT_PANEL/" "$APP_DIR/config.toml"
  c_ok "端口已写入：webhook $PORT_WEBHOOK / panel $PORT_PANEL"
fi

# ---------------- 5. systemd ----------------

if [[ $HAS_SYSTEMD -eq 1 ]]; then
  c_info "注册系统服务"
  if [[ $EUID -ne 0 ]]; then
    c_warn "非 root 运行，跳过服务注册；请用 sudo 重跑或手动配置"
  else
    if ! id -u "$SVC_USER" >/dev/null 2>&1; then
      useradd -r -s /sbin/nologin -d "$APP_DIR" "$SVC_USER" 2>/dev/null \
        || c_warn "创建用户 $SVC_USER 失败，将用 root 运行"
    fi
    chown -R "$SVC_USER:$SVC_USER" "$APP_DIR" 2>/dev/null || true
    chmod 600 "$APP_DIR/config.toml"

    cat > /etc/systemd/system/qqbot.service <<UNIT
[Unit]
Description=QQ Bot Webhook Receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python -m qqbot serve
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=QQBOT_CONFIG=$APP_DIR/config.toml
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable --now qqbot >/dev/null 2>&1 || c_warn "服务启动失败，查 journalctl -u qqbot"
    c_ok "已注册并启动 qqbot.service"
  fi
fi

# ---------------- 6. 收尾 ----------------

cat <<TIPS

$(c_info "下一步")

1) 填配置
   nano $APP_DIR/config.toml
   必填：app_id / app_secret / panel.password
   （也可以先启动，打开面板走初始化引导填写）

2) 启动（未注册服务时）
   cd $APP_DIR && ./venv/bin/python -m qqbot serve

3) nginx 反代（对外必须 HTTPS）
   /qqbot/webhook   -> 127.0.0.1:$PORT_WEBHOOK
   /panel/api/      -> 127.0.0.1:$PORT_PANEL
   /panel/          -> $APP_DIR/web/panel   （静态文件，直接由 nginx 读）

   参考 $APP_DIR/deploy/nginx-qqbot-webhook.conf

4) 平台侧
   回调地址 https://你的域名/qqbot/webhook
   接收事件选「全部接收」，群内把机器人设为管理员

面板地址：http://127.0.0.1:$PORT_PANEL/panel/
日志：journalctl -u qqbot -f

TIPS
