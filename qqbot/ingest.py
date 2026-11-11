"""事件归一处理：Webhook 收到的原始事件 → 落库 + 一行日志。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .store import Store
from .textutil import clean

# 群消息（含全量模式与 @ 模式，字段结构一致）
MESSAGE_EVENTS = {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"}
GROUP_LIFECYCLE = {"GROUP_ADD_ROBOT", "GROUP_DEL_ROBOT"}
MEMBER_EVENTS = {"GROUP_MEMBER_ADD", "GROUP_MEMBER_REMOVE"}
JOIN_EVENTS = {"GROUP_JOIN_REQUEST"}
# 按钮/菜单交互：群里点了机器人的按钮会推，payload 里带 group_openid 但不带消息体
INTERACTION_EVENTS = {"INTERACTION_CREATE"}

HANDLED = MESSAGE_EVENTS | GROUP_LIFECYCLE | MEMBER_EVENTS | JOIN_EVENTS | INTERACTION_EVENTS


def to_epoch(ts: str | None) -> int:
    if not ts:
        return 0
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def event_key(event: str, data: dict, raw: str) -> str:
    ident = data.get("id") or data.get("message_id")
    if ident:
        return f"{event}:{ident}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"{event}:{digest}"


def cfg_name(store: Store) -> str:
    """机器人自称，用于把 <@bot> 显示成名字而不是尾号。"""
    return store.get_kv("bot_name", "") or "机器人"


def ingest(store: Store, event: str, data: dict, raw: str | None = None) -> str | None:
    """写库并返回可打印日志；未知事件也落 raw_events，返回 None 表示不处理。"""
    raw = raw or json.dumps(data, ensure_ascii=False)
    group_openid = data.get("group_openid") or ""
    key = event_key(event, data, raw)

    if event not in HANDLED:
        store.record_event(key, event, group_openid, raw)
        return None

    first = store.record_event(key, event, group_openid, raw)
    if group_openid:
        store.touch_group(group_openid)

    if event in MESSAGE_EVENTS:
        author = data.get("author") or {}
        member_openid = author.get("member_openid") or author.get("id") or ""
        if member_openid and group_openid:
            store.touch_member(group_openid, member_openid,
                               author.get("union_openid"), author.get("username"))
        if data.get("id") and group_openid:
            bots = {m.get("id"): (m.get("username") or "")
                    for m in (data.get("mentions") or []) if m.get("bot") and m.get("id")}
            if bots:
                try:
                    known = json.loads(store.get_kv("bot_names", "{}") or "{}")
                except Exception:
                    known = {}
                for k, v in bots.items():
                    known.setdefault(k, v or "机器人")
                store.set_kv("bot_names", json.dumps(known, ensure_ascii=False))
            text = clean(data.get("content") or "",
                         lambda oid: store.member_nickname(group_openid, oid),
                         bot_ids=json.loads(store.get_kv("bot_names", "{}") or "{}"),
                         bot_name=cfg_name(store))
            store.add_message(data["id"], group_openid, member_openid,
                              text, to_epoch(data.get("timestamp")))
        tag = "新" if first else "重复"
        return (f"[{tag}消息] {event} 群={group_openid} 成员={member_openid} "
                f"内容={(data.get('content') or '')[:60]}")

    if event in MEMBER_EVENTS:
        member_openid = (data.get("member_openid") or data.get("op_member_openid")
                         or (data.get("member") or {}).get("member_openid") or "")
        if member_openid and group_openid:
            store.touch_member(group_openid, member_openid)
        return f"[成员变动] {event} 群={group_openid} 成员={member_openid}"

    if event in JOIN_EVENTS:
        member_openid = (data.get("member_openid") or data.get("applicant_openid")
                         or (data.get("member") or {}).get("member_openid") or "")
        return f"[入群申请] 群={group_openid} 申请人={member_openid}"

    if event in INTERACTION_EVENTS:
        member_openid = (data.get("group_member_openid") or data.get("member_openid")
                         or (data.get("author") or {}).get("member_openid") or "")
        if member_openid and group_openid:
            store.touch_member(group_openid, member_openid)
        return (f"[交互] 群={group_openid} 成员={member_openid or '-'} "
                f"type={data.get('type')} scene={data.get('scene')}")

    return f"[群事件] {event} 群={group_openid} 首次={first}"
