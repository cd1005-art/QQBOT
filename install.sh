#!/usr/bin/env bash
# QQBOT 安装脚本
#
#   bash install.sh                        # 默认装到 /opt/qqbot，注册 systemd
#   bash install.sh --dir ~/qqbot          # 装到指定目录
#   bash install.sh --no-service           # 不装系统服务，只准备环境
#   bash install.sh --check                # 只检查环境，不改任何文件
#   bash install.sh --upgrade              # 升级：自动备份，失败自动回滚
#   bash install.sh --restore <备份目录>    # 手动回滚到指定备份
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
UPGRADE=0
RESTORE_FROM=""
SVC_NAME=${QQBOT_SERVICE:-qqbot}
DIR_EXPLICIT=0
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
    --dir)          APP_DIR="$2"; DIR_EXPLICIT=1; shift 2 ;;
    --user)         SVC_USER="$2"; shift 2 ;;
    --port-webhook) PORT_WEBHOOK="$2"; PORTS_EXPLICIT=1; shift 2 ;;
    --port-panel)   PORT_PANEL="$2"; PORTS_EXPLICIT=1; shift 2 ;;
    --pip-index)    PIP_INDEX="$2"; shift 2 ;;
    --no-service)   NO_SERVICE=1; shift ;;
    --check)        CHECK_ONLY=1; shift ;;
    --upgrade)      UPGRADE=1; shift ;;
    --restore)      RESTORE_FROM="$2"; shift 2 ;;
    --service-name) SVC_NAME="$2"; shift 2 ;;
    -h|--help)      usage ;;
    *)              die "未知参数: $1（用 --help 查看用法）" ;;
  esac
done

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)


# ---------------- 文件同步 ----------------

copy_tree() {
  local src="$1" dst="$2"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$src" "$dst"
  else
    rm -rf "$dst"
    cp -a "$src" "$dst"
  fi
}

sync_code() {
  local d f
  for d in qqbot web deploy docker; do
    [[ -d "$SRC_DIR/$d" ]] && copy_tree "$SRC_DIR/$d/" "$APP_DIR/$d/"
  done
  for f in requirements.txt config.example.toml README.md LICENSE Dockerfile docker-compose.yml; do
    [[ -f "$SRC_DIR/$f" ]] && cp -f "$SRC_DIR/$f" "$APP_DIR/$f"
  done
}

# ---------------- 备份 / 回滚 ----------------

BACKUP_ROOT_DEFAULT() { echo "$APP_DIR/.backup"; }

make_backup() {
  local bk="$1"
  mkdir -p "$bk"
  # 代码与配置
  tar -czf "$bk/code.tar.gz" -C "$APP_DIR" \
    qqbot web deploy docker requirements.txt config.toml README.md 2>/dev/null || true
  # 数据库（用 sqlite3 .backup 保证一致性，没有就用复制）
  if [[ -f "$APP_DIR/data/qqbot.db" ]]; then
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$APP_DIR/data/qqbot.db" ".backup '$bk/qqbot.db'" 2>/dev/null \
        || cp -a "$APP_DIR/data/qqbot.db" "$bk/qqbot.db"
    else
      cp -a "$APP_DIR/data/qqbot.db" "$bk/qqbot.db"
    fi
  fi
  date '+%Y-%m-%d %H:%M:%S' > "$bk/CREATED"
}

prune_backups() {
  local root; root=$(BACKUP_ROOT_DEFAULT)
  [[ -d "$root" ]] || return 0
  ls -1dt "$root"/*/ 2>/dev/null | tail -n +6 | while read -r d; do rm -rf "$d"; done
}

restore_backup() {
  local bk="$1"
  [[ -f "$bk/code.tar.gz" ]] || { c_err "备份不完整：$bk"; return 1; }
  c_warn "回滚到 $bk"

  # 恢复前先把当前状态存一份，避免「回滚本身出错」时无路可退
  local safe
  safe="$(BACKUP_ROOT_DEFAULT)/restore-$(date +%Y%m%d-%H%M%S)"
  if [[ -d "$APP_DIR/qqbot" ]]; then
    make_backup "$safe" >/dev/null 2>&1 && c_ok "已为当前状态创建安全备份：$safe"
  fi

  local had_svc=0
  if unit_matches; then had_svc=1; systemctl stop "$SVC_NAME" >/dev/null 2>&1 || true; fi
  rm -rf "$APP_DIR/qqbot" "$APP_DIR/web" "$APP_DIR/deploy" "$APP_DIR/docker"
  tar -xzf "$bk/code.tar.gz" -C "$APP_DIR"
  [[ -f "$bk/qqbot.db" ]] && cp -f "$bk/qqbot.db" "$APP_DIR/data/qqbot.db"
  "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" >/dev/null 2>&1 || c_warn "旧依赖还原失败"
  if [[ $had_svc -eq 1 ]]; then
    systemctl start "$SVC_NAME" >/dev/null 2>&1 || true
    c_ok "已回滚并启动"
  else
    c_ok "已回滚（服务未托管，无需启动）"
  fi
}

unit_matches() {
  command -v systemctl >/dev/null 2>&1 || return 1
  local wd
  wd=$(systemctl show -p WorkingDirectory --value "$SVC_NAME" 2>/dev/null || true)
  [[ -n "$wd" && "$(readlink -f "$wd")" == "$(readlink -f "$APP_DIR")" ]]
}

wait_healthy() {
  local port="$1" i
  for i in $(seq 1 15); do
    if curl -fsS -m 2 "http://127.0.0.1:${port}/qqbot/webhook" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

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

if [[ -n "$RESTORE_FROM" ]]; then
  [[ -d "$RESTORE_FROM" ]] || die "备份目录不存在：$RESTORE_FROM"
  BK_REAL=$(readlink -f "$RESTORE_FROM")
  [[ -f "$BK_REAL/code.tar.gz" ]] || die "不是有效备份（缺 code.tar.gz）：$BK_REAL"
  BK_PARENT=$(basename "$(dirname "$BK_REAL")")
  [[ "$BK_PARENT" == ".backup" ]] || die "备份应位于 <应用目录>/.backup/ 下：$BK_REAL"
  OWNER_APP=$(dirname "$(dirname "$BK_REAL")")

  if [[ $DIR_EXPLICIT -eq 1 ]]; then
    [[ "$(readlink -f "$APP_DIR")" == "$OWNER_APP" ]] \
      || die "--dir 与备份归属不一致：该备份属于 $OWNER_APP"
  else
    APP_DIR="$OWNER_APP"
    c_info "由备份路径推断应用目录：$APP_DIR"
  fi
  restore_backup "$BK_REAL"
  exit 0
fi


# ---------------- 升级模式 ----------------

if [[ $UPGRADE -eq 1 ]]; then
  c_info "升级模式"
  [[ -d "$APP_DIR/qqbot" && -f "$APP_DIR/config.toml" ]] \
    || die "目标目录没有已安装的 QQBOT（$APP_DIR），首次安装请去掉 --upgrade"

  BK="$(BACKUP_ROOT_DEFAULT)/$(date +%Y%m%d-%H%M%S)"
  c_info "备份当前版本 → $BK"
  make_backup "$BK"
  [[ -f "$BK/code.tar.gz" ]] || die "备份失败，已中止（未改动任何文件）"
  c_ok "备份完成：代码 + 配置 + 数据库"

  WAS_RUNNING=0
  if unit_matches && systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
    WAS_RUNNING=1
    systemctl stop "$SVC_NAME"
    c_ok "服务已停止"
  elif systemctl is-active --quiet "$SVC_NAME" 2>/dev/null; then
    c_warn "检测到 $SVC_NAME 服务运行的是其它目录，本次不操作该服务"
  fi

  c_info "同步新版本"
  sync_code
  c_ok "文件已更新"

  c_info "更新依赖"
  UP_ARGS=(-q -r "$APP_DIR/requirements.txt")
  [[ -n "$PIP_INDEX" ]] && UP_ARGS+=(-i "$PIP_INDEX")
  if ! "$APP_DIR/venv/bin/pip" install "${UP_ARGS[@]}"; then
    c_err "依赖安装失败，开始回滚"
    restore_backup "$BK"
    exit 1
  fi
  c_ok "依赖就绪"

  # 端口沿用配置文件里的值，读回来做健康检查
  HEALTH_PORT=$(grep -A3 '^\[webhook\]' "$APP_DIR/config.toml" | grep -m1 '^port' | tr -dc '0-9')
  HEALTH_PORT=${HEALTH_PORT:-$PORT_WEBHOOK}

  if [[ $WAS_RUNNING -eq 1 ]]; then
    systemctl start "$SVC_NAME"
    if wait_healthy "$HEALTH_PORT"; then
      c_ok "服务已启动，健康检查通过（端口 $HEALTH_PORT）"
    else
      c_err "健康检查失败，自动回滚"
      restore_backup "$BK"
      c_warn "已恢复到升级前状态；备份保留在 $BK"
      exit 1
    fi
  else
    c_warn "服务原本未运行，跳过启动；可手动执行：systemctl start $SVC_NAME"
  fi

  prune_backups
  c_ok "升级完成"
  echo
  echo "回滚命令（如需）："
  echo "  sudo bash install.sh --restore $BK"
  echo
  exit 0
fi

# ---------------- 2. 拷贝文件 ----------------

c_info "安装文件到 $APP_DIR"
mkdir -p "$APP_DIR" "$APP_DIR/data"

sync_code
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

    cat > "/etc/systemd/system/${SVC_NAME}.service" <<UNIT
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
    systemctl enable --now "$SVC_NAME" >/dev/null 2>&1 \
      || c_warn "服务启动失败，查 journalctl -u $SVC_NAME"
    c_ok "已注册并启动 ${SVC_NAME}.service"
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
