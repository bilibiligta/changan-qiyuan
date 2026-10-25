"""纯 Python RSA（PKCS#1 v1.5）工具。

为什么不用 cryptography / pycryptodome：
    HA 的 requirements 里加依赖会拖慢启动，且这里只需要「用服务端公钥加密 6 位 PIN」
    这一件事。服务端 /app2/api/security/key 返回的是 base64(DER SubjectPublicKeyInfo)，
    1024 位、e=65537，自己解析只要 30 行。

踩坑记录：
  - 解析 BIT STRING 之后要跳过第 1 个字节（未使用位数），再按 SEQUENCE 解 RSAPublicKey；
  - RSAPublicKey 内部的偏移必须重新从 0 开始，沿用外层偏移会 IndexError: index out of range；
  - 填充必须是 0x00 || 0x02 || PS(非零随机) || 0x00 || M，PS 至少 8 字节。
"""
from __future__ import annotations

import base64
import secrets


def _read_tlv(buf: bytes, i: int) -> tuple[int, bytes, int]:
    """读一个 DER TLV，返回 (tag, value, next_offset)。"""
    tag = buf[i]
    i += 1
    ln = buf[i]
    i += 1
    if ln & 0x80:                       # 长格式长度
        nbytes = ln & 0x7F
        ln = int.from_bytes(buf[i:i + nbytes], "big")
        i += nbytes
    return tag, buf[i:i + ln], i + ln


def parse_rsa_pubkey(b64: str) -> tuple[int, int]:
    """base64(DER SubjectPublicKeyInfo) -> (n, e)。"""
    der = base64.b64decode(b64)
    _, spki, _ = _read_tlv(der, 0)
    i = 0
    _, _, i = _read_tlv(spki, i)        # AlgorithmIdentifier
    _, bits, i = _read_tlv(spki, i)     # BIT STRING
    _, rsa, _ = _read_tlv(bits[1:], 0)  # 跳过「未使用位数」字节
    j = 0
    _, n_bytes, j = _read_tlv(rsa, j)
    _, e_bytes, j = _read_tlv(rsa, j)
    return int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big")


def rsa_pkcs1v15_encrypt(msg: bytes, n: int, e: int) -> str:
    """PKCS#1 v1.5 加密，返回 base64 字符串。"""
    k = (n.bit_length() + 7) // 8
    if len(msg) > k - 11:
        raise ValueError("消息过长")
    pad = bytes(secrets.choice(range(1, 256)) for _ in range(k - 3 - len(msg)))
    c = pow(int.from_bytes(b"\x00\x02" + pad + b"\x00" + msg, "big"), e, n)
    return base64.b64encode(c.to_bytes(k, "big")).decode()
