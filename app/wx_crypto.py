# -*- coding: utf-8 -*-
"""企业微信消息加解密 (官方 AES-256-CBC 方案, pycryptodome 自实现).

明文结构: 16B random + 4B 网络序 msg_len + msg + receiveid
签名: sha1("".join(sorted([token, timestamp, nonce, encrypt_msg])))
"""
import base64
import hashlib
import os
import struct

from Crypto.Cipher import AES


class WxCrypto:
    def __init__(self, token: str, aes_key: str):
        """aes_key 为 43 字符 EncodingAESKey (Base64Decode 后 32 字节)."""
        if len(aes_key) != 43:
            raise ValueError(f"EncodingAESKey must be 43 chars, got {len(aes_key)}")
        self.token = token.encode("utf-8") if isinstance(token, str) else token
        self.key = base64.b64decode(aes_key + "=")
        if len(self.key) != 32:
            raise ValueError(f"AESKey must decode to 32 bytes, got {len(self.key)}")

    def verify_signature(self, timestamp: str, nonce: str, encrypt_msg: str, msg_signature: str) -> bool:
        items = sorted([self.token.decode("utf-8"), timestamp, nonce, encrypt_msg])
        expected = hashlib.sha1("".join(items).encode("utf-8")).hexdigest()
        return expected == msg_signature

    def decrypt(self, encrypt_b64: str) -> str:
        """解密, 剥离 16B random 前缀、4B 长度头和尾部 receiveid, 返回明文 XML."""
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        raw = cipher.decrypt(base64.b64decode(encrypt_b64))
        # 官方 sample 库按 32 字节块做 PKCS7 补齐, pad 合法范围 1..32
        pad = raw[-1]
        if not 1 <= pad <= 32:
            raise ValueError(f"Invalid PKCS7 padding byte: {pad}")
        raw = raw[:-pad]
        if len(raw) < 20:
            raise ValueError("Decrypted payload too short")
        msg_len = struct.unpack(">I", raw[16:20])[0]
        if 20 + msg_len > len(raw):
            raise ValueError(f"msg_len {msg_len} exceeds payload {len(raw)}")
        return raw[20 : 20 + msg_len].decode("utf-8")

    def encrypt(self, plain_xml: str) -> str:
        """加密回复 XML: 16B random + 4B len + msg, PKCS7 补齐, AES-CBC, base64."""
        msg = plain_xml.encode("utf-8")
        to_encrypt = os.urandom(16) + struct.pack(">I", len(msg)) + msg
        # 官方 sample 按 32 字节块补齐, 与官方密文互解
        pad = 32 - len(to_encrypt) % 32
        to_encrypt += bytes([pad]) * pad
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        return base64.b64encode(cipher.encrypt(to_encrypt)).decode("utf-8")
