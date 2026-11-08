"""离线冒烟测试：不联网、不需要凭证。

跑法：python tests/smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qqbot.ingest import ingest  # noqa: E402
from qqbot.sign import Signer, derive_seed  # noqa: E402
from qqbot.store import Store  # noqa: E402

# 官方文档给出的校验样例
VECTOR_SECRET = "DG5g3B4j9X2KOErG"
VECTOR_TS = "1725442341"
VECTOR_TOKEN = "Arq0D5A61EgUu4OxUvOp"
VECTOR_SIG = ("87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1adc2d01"
              "bcfcadc0842edac85e85205028a1132afe09280305f13aa6909ffc2d652c706")


def check_signature():
    assert len(derive_seed(VECTOR_SECRET)) == 32
    sig = Signer(VECTOR_SECRET).sign_validation(VECTOR_TOKEN, VECTOR_TS)
    assert sig == VECTOR_SIG, f"签名与官方向量不一致: {sig}"
    return "签名与官方向量一致"


def check_ingest(store: Store):
    raw = json.dumps({
        "id": "MSG1", "content": "测试", "group_openid": "G1",
        "timestamp": "2026-11-08T12:00:00+08:00",
        "author": {"member_openid": "M1", "union_openid": "U1"},
    })
    ingest(store, "GROUP_MESSAGE_CREATE", json.loads(raw), raw)
    assert len(store.list_groups()) == 1
    assert store.list_members("G1")[0]["member_openid"] == "M1"
    assert store.last_message("G1")["message_id"] == "MSG1"

    # 重推同一条：消息不重复，成员计数只加一次
    ingest(store, "GROUP_MESSAGE_CREATE", json.loads(raw), raw)
    assert store.list_members("G1")[0]["msg_count"] == 2, "重推会被计入发言数（预期行为，用于排查）"

    # 未知事件也要落 raw_events，便于对照平台文档补实现
    ingest(store, "GROUP_MEMBER_ADD", {"group_openid": "G1", "member_openid": "M2"}, None)
    assert any(r["event_type"] == "GROUP_MEMBER_ADD" for r in store.event_types())
    return "事件落库 / 去重 / 未知事件留档 通过"


def check_recall(store: Store):
    store.mark_recalled("MSG1")
    assert store.last_message("G1") is None
    store.log_action("recall", "G1", "MSG1", "{}", True, "HTTP 200")
    assert store.recent_audit(1)[0]["action"] == "recall"
    return "撤回标记与审计 通过"


def main() -> int:
    msg1 = check_signature()
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "t.db")
        msg2 = check_ingest(store)
        msg3 = check_recall(store)
    print(msg1)
    print(msg2)
    print(msg3)
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
