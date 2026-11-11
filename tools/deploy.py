"""部署到 Rocky9 服务器（192.168.5.48）：上传 → venv 依赖 → systemd → nginx 反代。

用法：
  python tools/deploy.py            # 全量部署
  python tools/deploy.py --check    # 只检查远端状态，不改动
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paramiko  # noqa: E402

REMOTE = "/opt/qqbot"
SERVICE = "qqbot"
NGINX_EXT = "/www/server/panel/vhost/nginx/extension/qqbot.oddpp.com"

FILES = [
    "qqbot/__init__.py", "qqbot/__main__.py", "qqbot/api.py", "qqbot/auth.py",
    "qqbot/config.py", "qqbot/ingest.py", "qqbot/sign.py", "qqbot/store.py",
    "qqbot/webhook.py", "qqbot/cli.py", "qqbot/panel.py", "qqbot/cfgedit.py", "qqbot/rules.py", "qqbot/scheduler.py", "qqbot/llm.py", "qqbot/chat.py",
    "web/panel/index.html", "web/panel/app.css", "web/panel/app.js", "web/panel/setup.html",
    "web/landing.html", "web/docs.html",
    "requirements.txt", "config.toml",
    "tests/smoke.py",
    "deploy/qqbot.service", "deploy/nginx-qqbot-webhook.conf",
]


def connect(cfg):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(cfg.ssh_host, cfg.ssh_port, cfg.ssh_user, cfg.ssh_pass,
              timeout=10, look_for_keys=False, allow_agent=False)
    return c


def run(client, cmd, timeout=300, quiet=False):
    _, out, err = client.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    if not quiet and o.strip():
        print(o.rstrip())
    if e.strip():
        print("[stderr]", e.rstrip()[:1500])
    return code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    from qqbot import config as config_mod
    cfg = config_mod.load()
    if not cfg.ssh_host:
        print("config.toml 缺少 [server].host", file=sys.stderr)
        return 2

    client = connect(cfg)
    try:
        if args.check:
            run(client, f"systemctl is-active {SERVICE}; "
                        f"curl -s -m 3 -o /dev/null -w 'local=%{{http_code}}\\n' "
                        f"http://127.0.0.1:{cfg.listen_port}{cfg.webhook_path}; "
                        f"ls -la {REMOTE} 2>/dev/null | head -6")
            return 0

        print(f"== 上传到 {cfg.ssh_host}:{REMOTE} ==")
        run(client, f"mkdir -p {REMOTE}/qqbot {REMOTE}/web/panel {REMOTE}/tests {REMOTE}/deploy {REMOTE}/data")
        run(client, "mkdir -p /www/wwwroot/qqbot.oddpp.com/panel")
        sftp = client.open_sftp()
        for rel in FILES:
            local = ROOT / rel
            remote = f"{REMOTE}/{rel}"
            sftp.put(str(local), remote)
            print(f"  {rel}")
        sftp.chmod(f"{REMOTE}/config.toml", 0o600)
        # 前端同时铺到站点目录（nginx 直接读，不经过 Python）
        webroot = "/www/wwwroot/qqbot.oddpp.com"
        for name in ("index.html", "app.css", "app.js", "setup.html"):
            sftp.put(str(ROOT / "web" / "panel" / name), f"{webroot}/panel/{name}")
        sftp.put(str(ROOT / "web" / "landing.html"), f"{webroot}/index.html")
        sftp.put(str(ROOT / "web" / "docs.html"), f"{webroot}/docs.html")
        sftp.close()
        run(client, f"mkdir -p {webroot}/docs && mv -f {webroot}/docs.html {webroot}/docs/index.html")

        print("== 依赖 ==")
        run(client, f"test -x {REMOTE}/venv/bin/python || python3 -m venv {REMOTE}/venv")
        run(client, f"{REMOTE}/venv/bin/pip install -q --upgrade pip")
        run(client, f"{REMOTE}/venv/bin/pip install -q -r {REMOTE}/requirements.txt")
        run(client, f"{REMOTE}/venv/bin/python -V; {REMOTE}/venv/bin/python -c "
                    f"'import nacl,sys;print(\"pynacl\",nacl.__version__)'")
        run(client, f"cd {REMOTE} && {REMOTE}/venv/bin/python tests/smoke.py")

        print("== systemd ==")
        run(client, f"cp {REMOTE}/deploy/qqbot.service /etc/systemd/system/{SERVICE}.service && "
                    f"systemctl daemon-reload && systemctl enable {SERVICE} && "
                    f"systemctl restart {SERVICE}")
        run(client, "sleep 2; systemctl is-active " + SERVICE)

        print("== 本机回环自检 ==")
        run(client, f"curl -s -m 5 -w '\\nlocal=%{{http_code}}\\n' "
                    f"http://127.0.0.1:{cfg.listen_port}{cfg.webhook_path}")

        print("== nginx 反代 ==")
        run(client, f"mkdir -p {NGINX_EXT} && "
                    f"cp {REMOTE}/deploy/nginx-qqbot-webhook.conf {NGINX_EXT}/qqbot-webhook.conf && "
                    f"nginx -t && nginx -s reload")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
