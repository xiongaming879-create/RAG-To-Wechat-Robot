# -*- coding: utf-8 -*-
"""WxCrypto tests — 企业微信 AES 加解密 + 签名校验."""
import base64
import hashlib
import struct

import pytest
from Crypto.Cipher import AES

from app.wx_crypto import WxCrypto

# 确定性 43 字符 EncodingAESKey: b64encode(bytes(32)) = 44 字符, 去掉 '='
TEST_AES_KEY = base64.b64encode(bytes(32)).decode().rstrip("=")
assert len(TEST_AES_KEY) == 43

TEST_TOKEN = "test-token-123"


def make_crypto():
    return WxCrypto(token=TEST_TOKEN, aes_key=TEST_AES_KEY)


PLAIN_XML = "<xml><ToUserName><![CDATA[wx123]]></ToUserName><Content><![CDATA[hello]]></Content></xml>"


def manual_encrypt(plain: str) -> str:
    """按官方算法手动构造密文, 用于验证 decrypt 剥离结构."""
    key = base64.b64decode(TEST_AES_KEY + "=")
    random16 = b"0123456789abcdef"  # 固定随机前缀便于断言
    msg = plain.encode("utf-8")
    length = struct.pack(">I", len(msg))
    to_encode = random16 + length + msg
    # 官方 sample 按 32 字节块补齐
    pad = 32 - len(to_encode) % 32
    to_encode += bytes([pad]) * pad
    cipher = AES.new(key, AES.MODE_CBC, key[:16])
    return base64.b64encode(cipher.encrypt(to_encode)).decode()


def make_signature(token: str, timestamp: str, nonce: str, encrypt_msg: str) -> str:
    items = sorted([token, timestamp, nonce, encrypt_msg])
    return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()


def test_round_trip():
    c = make_crypto()
    encrypted = c.encrypt(PLAIN_XML)
    assert c.decrypt(encrypted) == PLAIN_XML


def test_decrypt_strips_prefix_and_length():
    c = make_crypto()
    plaintext = c.decrypt(manual_encrypt(PLAIN_XML))
    assert plaintext == PLAIN_XML


def test_encrypt_output_is_valid_structure():
    """encrypt 输出应为 base64, 解开后 16B random + 4B len + msg 的结构."""
    c = make_crypto()
    encrypted = c.encrypt(PLAIN_XML)
    raw = base64.b64decode(encrypted)
    key = base64.b64decode(TEST_AES_KEY + "=")
    cipher = AES.new(key, AES.MODE_CBC, key[:16])
    raw = cipher.decrypt(raw)
    pad = raw[-1]
    assert 1 <= pad <= 32  # 官方 sample 按 32 字节块补齐
    raw = raw[:-pad]
    assert len(raw) > 20
    msg_len = struct.unpack(">I", raw[16:20])[0]
    assert raw[20 : 20 + msg_len].decode("utf-8") == PLAIN_XML
    # 剩余是 receiveid (可为空)
    assert len(raw[20 + msg_len :]) == 0


def test_verify_signature_ok():
    c = make_crypto()
    ts, nonce = "1409659813", "n123"
    encrypt_msg = manual_encrypt(PLAIN_XML)
    sig = make_signature(TEST_TOKEN, ts, nonce, encrypt_msg)
    assert c.verify_signature(ts, nonce, encrypt_msg, sig) is True


def test_verify_signature_tampered():
    c = make_crypto()
    ts, nonce = "1409659813", "n123"
    encrypt_msg = manual_encrypt(PLAIN_XML)
    sig = make_signature(TEST_TOKEN, ts, nonce, encrypt_msg)
    tampered = manual_encrypt("<xml>tampered</xml>")
    assert c.verify_signature(ts, nonce, tampered, sig) is False
    assert c.verify_signature(ts, nonce, encrypt_msg, "bad" * 10) is False


def test_round_trip_unicode():
    c = make_crypto()
    plain = "<xml><Content><![CDATA[中文消息 test]]></Content></xml>"
    assert c.decrypt(c.encrypt(plain)) == plain


def test_official_sample_vectors():
    """企业微信官方文档示例 (developer.work.weixin.qq.com/document/path/90968)."""
    official_token = "QDG6eK"
    official_key = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
    official_ts, official_nonce = "1409659813", "1372623149"
    official_encrypt = (
        "RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/0lWfrf0QADHHhGd3QczcdCUpj911L3vg3W/"
        "sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wiSpGLYL42aM6A8dTT+6k4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZ"
        "fJYuR+LtwGc2hgf5gsijff0ekUNXZiqATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQH03v+BCA9nMELNqbSf6tiWSrXJB3LA"
        "VGUcallcrw8V2t9EL4EhzJWrQUax5wLVMNS0+rUPA3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCmJU9p8g7m3fVKn28H"
        "3KDYA5Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q=="
    )
    official_signature = "477715d11cdb4164915debcba66cb864d751f3e6"
    c = WxCrypto(token=official_token, aes_key=official_key)
    assert c.verify_signature(official_ts, official_nonce, official_encrypt, official_signature) is True
    plain = c.decrypt(official_encrypt)
    # 官方文档明文样例: ToUserName=corpId, Content=hello, receiveid 已剥离
    assert plain.startswith("<xml>")
    assert "ToUserName><![CDATA[wx5823bf96d3bd56c7]]>" in plain
    assert "Content><![CDATA[hello]]>" in plain
