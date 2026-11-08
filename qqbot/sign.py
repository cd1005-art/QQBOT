"""Webhook 签名：Ed25519，密钥由 AppSecret 派生。

官方算法：
    seed = AppSecret 反复翻倍补到 32 字节（不足则 seed = seed + seed）
    privateKey = Ed25519.NewKeyFromSeed(seed[:32])
回调地址校验：signature = hex(sign(event_ts + plain_token))
事件推送验签：verify(sig, timestamp + raw_body)

已用官方测试向量校验：
    secret=DG5g3B4j9X2KOErG ts=1725442341 token=Arq0D5A61EgUu4OxUvOp
    → 87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1adc2d01bcfcadc0842edac85e85205028a1132afe09280305f13aa6909ffc2d652c706
"""

from __future__ import annotations

import nacl.exceptions
import nacl.signing

SEED_SIZE = 32


def derive_seed(secret: str) -> bytes:
    seed = secret.encode("utf-8")
    while len(seed) < SEED_SIZE:
        seed = seed + seed
    return seed[:SEED_SIZE]


class Signer:
    def __init__(self, secret: str):
        if not secret:
            raise ValueError("AppSecret 为空，无法初始化签名器")
        self._key = nacl.signing.SigningKey(derive_seed(secret))
        self._verify_key = self._key.verify_key

    def sign_validation(self, plain_token: str, event_ts: str) -> str:
        message = (str(event_ts) + str(plain_token)).encode("utf-8")
        return self._key.sign(message).signature.hex()

    def sign_event(self, timestamp: str, body: bytes) -> str:
        """自测用：按事件验签的原文（timestamp + body）生成签名。"""
        return self._key.sign(str(timestamp).encode("utf-8") + body).signature.hex()

    def verify_event(self, signature_hex: str, timestamp: str, body: bytes) -> bool:
        if not signature_hex or not timestamp:
            return False
        try:
            sig = bytes.fromhex(signature_hex)
        except (TypeError, ValueError):
            return False
        try:
            self._verify_key.verify(str(timestamp).encode("utf-8") + body, sig)
            return True
        except (nacl.exceptions.BadSignatureError, ValueError):
            return False
