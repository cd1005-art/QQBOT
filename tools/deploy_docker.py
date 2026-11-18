"""在 192.168.5.17 上更新 QQBOT（Docker + 宝塔容器编排）。

流程：拉最新镜像 → 重建容器 → 等健康检查 → 打印日志。
QQBOT 已从 192.168.5.48 迁走，那台机不再用于本项目。

用法：
  python tools/deploy_docker.py            # 更新到 latest 并重启
  python tools/deploy_docker.py --check    # 只看当前状态
  python tools/deploy_docker.py --tag v1.0.3   # 临时指定版本
  python tools/deploy_docker.py --logs     # 顺带打印最近日志
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paramiko  # noqa: E402

HOST = os.getenv("QQBOT_DEPLOY_HOST", "192.168.5.17")
PORT = int(os.getenv("QQBOT_DEPLOY_PORT", "22"))
USER = os.getenv("QQBOT_DEPLOY_USER", "root")
PASS = os.getenv("QQBOT_DEPLOY_PASS", "cd870623")

# 宝塔「容器编排」创建的项目目录
COMPOSE_DIR = "/www/server/panel/data/compose/QQBOT"
CONTAINER = "qqbot"
PANEL_PORT = 18901
WEBHOOK_PORT = 18900


def connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, PORT, USER, PASS, timeout=10,
              look_for_keys=False, allow_agent=False)
    return c


def run(c, cmd: str, timeout: int = 300, quiet: bool = False) -> tuple[int, str]:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    body = out.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    text = (body + err.read().decode("utf-8", "replace")).strip()
    if text and not quiet:
        print(text)
    return code, text


def state(c) -> dict:
    _, img = run(c, f"docker inspect {CONTAINER} --format '{{{{.Config.Image}}}}'", quiet=True)
    _, status = run(c, f"docker inspect {CONTAINER} --format '{{{{.State.Status}}}}'", quiet=True)
    _, health = run(c, f"docker inspect {CONTAINER} --format '{{{{.State.Health.Status}}}}'", quiet=True)
    _, panel = run(c, f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{PANEL_PORT}/panel/", quiet=True)
    _, hook = run(c, f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{WEBHOOK_PORT}/qqbot/webhook", quiet=True)
    return {"image": img, "status": status, "health": health, "panel": panel, "webhook": hook}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只查看状态")
    ap.add_argument("--tag", default="", help="指定镜像标签，默认 latest")
    ap.add_argument("--logs", action="store_true", help="结束后打印日志")
    args = ap.parse_args()

    c = connect()
    print(f"== 目标 {HOST} ==")

    if args.check:
        s = state(c)
        print(f"  镜像   {s['image']}")
        print(f"  状态   {s['status']} / {s['health']}")
        print(f"  面板   HTTP {s['panel']}")
        print(f"  回调   HTTP {s['webhook']}")
        c.close()
        return 0

    if not args.tag:
        # 保持编排文件里的 latest 不变，直接拉
        code, _ = run(c, f"cd {COMPOSE_DIR} && docker compose pull", timeout=600)
        if code != 0:
            print("拉取失败")
            c.close()
            return 1
        code, _ = run(c, f"cd {COMPOSE_DIR} && docker compose up -d", timeout=300)
    else:
        tag = args.tag
        code, _ = run(c, f"docker pull ghcr.io/cd1005-art/qqbot:{tag}", timeout=600)
        if code != 0:
            print("拉取失败")
            c.close()
            return 1
        # 临时改镜像标签跑起来，再改回配置由编排文件决定
        run(c, f"docker rm -f {CONTAINER} >/dev/null 2>&1 || true", quiet=True)
        code, _ = run(
            c,
            "docker run -d --name qqbot --restart unless-stopped "
            "-e TZ=Asia/Shanghai -e QQBOT_BIND=0.0.0.0 "
            "-e QQBOT_CONFIG=/app/config/config.toml "
            f"-p 127.0.0.1:{WEBHOOK_PORT}:18900 -p 127.0.0.1:{PANEL_PORT}:18901 "
            "-v /opt/qqbot/config:/app/config -v /opt/qqbot/data:/app/data "
            f"ghcr.io/cd1005-art/qqbot:{tag}",
            timeout=180)

    if code != 0:
        print("启动失败")
        c.close()
        return 1

    # 等健康检查
    ok = False
    for i in range(20):
        s = state(c)
        if s["panel"] == "200" and s["webhook"] == "200":
            ok = True
            break
        time.sleep(2)

    s = state(c)
    print()
    print(f"  镜像   {s['image']}")
    print(f"  健康   {s['health']}")
    print(f"  面板   HTTP {s['panel']}")
    print(f"  回调   HTTP {s['webhook']}")

    if args.logs or not ok:
        print()
        run(c, f"docker logs --tail 15 {CONTAINER} 2>&1")

    c.close()
    if not ok:
        print("\n未通过健康检查，可用 --logs 查看原因")
        return 1
    print("\n更新完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
