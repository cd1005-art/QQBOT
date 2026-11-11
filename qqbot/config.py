"""配置加载：config.toml + 环境变量覆盖。

Python 3.11+ 用标准库 tomllib；3.9/3.10 回落到 tomli（requirements 已带）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # 3.9 / 3.10
    import tomli as _toml  # type: ignore

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    app_id: str
    app_secret: str
    sandbox: bool = False
    dry_run: bool = True
    allowed_groups: list[str] = field(default_factory=list)
    timeout: int = 15
    db_path: Path = ROOT / "data" / "qqbot.db"
    source_path: Path = ROOT / "config.toml"
    at_all_template: str = "<qqbot-at-everyone />"
    listen_host: str = "127.0.0.1"
    listen_port: int = 18900
    webhook_path: str = "/qqbot/webhook"
    max_body: int = 1 << 20
    log_events: bool = True
    panel_enabled: bool = True
    panel_host: str = "127.0.0.1"
    panel_port: int = 18901
    panel_password: str = ""
    # 大模型对话
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_system: str = "你是 QQ 群里的助手，回答简洁，控制在 80 字以内，不要使用 markdown。"
    llm_max_tokens: int = 300
    llm_temperature: float = 0.7
    llm_context_turns: int = 6
    llm_cooldown: int = 3
    llm_daily_limit: int = 200
    llm_trigger: str = "at"          # at | all
    llm_keywords: list[str] = field(default_factory=list)
    # 回复时是否 @ 提问者（仅 markdown 消息支持）
    reply_at_user: bool = False
    # 图片审核（视觉模型）
    vision_enabled: bool = False
    vision_chat_enabled: bool = False
    vision_model: str = ""
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_prompt: str = ""
    vision_action: str = "recall_mute"
    vision_minutes: int = 10
    vision_notice: str = ""
    vision_max_bytes: int = 8 * 1024 * 1024
    vision_max_tokens: int = 800
    # 网络找图
    image_search_enabled: bool = False
    image_max_bytes: int = 5 * 1024 * 1024
    image_categories: list[str] = field(
        default_factory=lambda: ['二次元', '动漫', '漫画', '插画', '壁纸', '搞笑', '风景', '宠物', '游戏', '表情包'])
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_pass: str = ""

    def has_credentials(self) -> bool:
        return bool(self.app_id and self.app_secret)


def load(path: str | Path | None = None) -> Config:
    cfg_path = Path(path or os.getenv("QQBOT_CONFIG") or ROOT / "config.toml")
    if not cfg_path.exists():
        raise ConfigError(
            f"缺少配置文件：{cfg_path}\n复制 config.example.toml 为 config.toml 后填写"
        )
    with cfg_path.open("rb") as fh:
        raw = _toml.load(fh)

    bot = raw.get("bot", {})
    safety = raw.get("safety", {})
    hook = raw.get("webhook", {})
    paths = raw.get("paths", {})
    msg = raw.get("message", {})
    srv = raw.get("server", {})
    panel = raw.get("panel", {})
    llm = raw.get("llm", {})
    reply = raw.get("reply", {})
    vision = raw.get("vision", {})
    image = raw.get("image", {})

    app_id = os.getenv("QQBOT_APP_ID") or bot.get("app_id", "")
    app_secret = os.getenv("QQBOT_APP_SECRET") or bot.get("app_secret", "")
    env_sandbox = os.getenv("QQBOT_SANDBOX")
    if env_sandbox is not None:
        sandbox = env_sandbox.strip().lower() in ("1", "true", "yes", "on")
    else:
        sandbox = bool(bot.get("sandbox", False))

    bind = os.getenv("QQBOT_BIND")  # 容器内需要 0.0.0.0
    db_rel = paths.get("db", "data/qqbot.db")
    db_path = Path(db_rel) if Path(db_rel).is_absolute() else ROOT / db_rel

    return Config(
        app_id=app_id,
        app_secret=app_secret,
        sandbox=sandbox,
        dry_run=bool(safety.get("dry_run", True)),
        allowed_groups=[str(x) for x in safety.get("allowed_groups", [])],
        timeout=int(safety.get("timeout", 15)),
        db_path=db_path,
        source_path=cfg_path,
        at_all_template=str(msg.get("at_all_template", "<qqbot-at-everyone />")),
        listen_host=bind or str(hook.get("host", "127.0.0.1")),
        listen_port=int(hook.get("port", 18900)),
        webhook_path=str(hook.get("path", "/qqbot/webhook")),
        max_body=int(hook.get("max_body", 1 << 20)),
        log_events=bool(hook.get("log_events", True)),
        panel_enabled=bool(panel.get("enabled", True)),
        panel_host=bind or str(panel.get("host", "127.0.0.1")),
        panel_port=int(panel.get("port", 18901)),
        panel_password=os.getenv("QQBOT_PANEL_PASSWORD") or str(panel.get("password", "")),
        llm_enabled=bool(llm.get("enabled", False)),
        llm_base_url=str(llm.get("base_url", "https://api.openai.com/v1")),
        llm_api_key=os.getenv("QQBOT_LLM_KEY") or str(llm.get("api_key", "")),
        llm_model=str(llm.get("model", "gpt-4o-mini")),
        llm_system=str(llm.get("system", "你是 QQ 群里的助手，回答简洁，控制在 80 字以内，不要使用 markdown。")),
        llm_max_tokens=int(llm.get("max_tokens", 300)),
        llm_temperature=float(llm.get("temperature", 0.7)),
        llm_context_turns=int(llm.get("context_turns", 6)),
        llm_cooldown=int(llm.get("cooldown", 3)),
        llm_daily_limit=int(llm.get("daily_limit", 200)),
        llm_trigger=str(llm.get("trigger", "at")),
        llm_keywords=[str(x) for x in llm.get("keywords", [])],
        reply_at_user=bool(reply.get("at_user", False)),
        vision_enabled=bool(vision.get("enabled", False)),
        vision_chat_enabled=bool(vision.get("chat_enabled", False)),
        vision_model=str(vision.get("model", "")),
        vision_base_url=str(vision.get("base_url", "")),
        vision_api_key=str(vision.get("api_key", "")),
        vision_prompt=str(vision.get("prompt", "")),
        vision_action=str(vision.get("action", "recall_mute")),
        vision_minutes=int(vision.get("minutes", 10)),
        vision_notice=str(vision.get("notice", "")),
        vision_max_bytes=int(vision.get("max_bytes", 8 * 1024 * 1024)),
        vision_max_tokens=int(vision.get("max_tokens", 800)),
        image_search_enabled=bool(image.get("search_enabled", False)),
        image_max_bytes=int(image.get("max_bytes", 5 * 1024 * 1024)),
        image_categories=[str(x) for x in image.get("categories", ['二次元', '动漫', '漫画', '插画', '壁纸', '搞笑', '风景', '宠物', '游戏', '表情包'])],
        ssh_host=os.getenv("QQBOT_SSH_HOST") or str(srv.get("host", "")),
        ssh_port=int(srv.get("port", 22)),
        ssh_user=os.getenv("QQBOT_SSH_USER") or str(srv.get("user", "root")),
        ssh_pass=os.getenv("QQBOT_SSH_PASS") or str(srv.get("password", "")),
    )
