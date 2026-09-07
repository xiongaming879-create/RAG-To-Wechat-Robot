# -*- coding: utf-8 -*-
"""企业微信回调通路测试: GET 校验 + POST 接收 + 秒回 200."""
import hashlib

import pytest
from fastapi.testclient import TestClient

import app.wx_callback as wx_callback_module
from app.config import settings
from app.main import app
from app.wx_crypto import WxCrypto

TOKEN = "test-token"
AES_KEY = "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopq"  # 43 chars, b64 -> 32B
TIMESTAMP = "1409659813"
NONCE = "1372623149"
ECHO_STR = "echo-123"

PLAIN_MSG = (
    "<xml><ToUserName><![CDATA[toUser]]></ToUserName>"
    "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
    "<CreateTime>1348831860</CreateTime>"
    "<MsgType><![CDATA[text]]></MsgType>"
    "<Content><![CDATA[hello world]]></Content>"
    "<MsgId>1234567890123456</MsgId>"
    "<AgentID>1</AgentID>"
    "<ChatId><![CDATA[chatid123]]></ChatId>"
    "<AtUserIdList><![CDATA[u1001,u1002]]></AtUserIdList>"
    "</xml>"
)


def sign(crypto: WxCrypto, encrypt_msg: str) -> str:
    items = sorted([TOKEN, TIMESTAMP, NONCE, encrypt_msg])
    return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()


def enc_body(crypto: WxCrypto, plain_xml: str) -> tuple[str, str]:
    """加密明文 XML -> (POST body, msg_signature)."""
    enc = crypto.encrypt(plain_xml)
    body = f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt></xml>"
    return body, sign(crypto, enc)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "WX_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "WX_AES_KEY", AES_KEY)
    return TestClient(app)


@pytest.fixture()
def plaintext_client(monkeypatch):
    monkeypatch.setattr(settings, "WX_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "WX_AES_KEY", "")
    return TestClient(app)


class TestGet:
    def test_encrypted_ok_returns_decrypted_echostr(self, client):
        crypto = WxCrypto(TOKEN, AES_KEY)
        enc = crypto.encrypt(ECHO_STR)
        r = client.get(
            "/wx/callback",
            params={
                "msg_signature": sign(crypto, enc),
                "timestamp": TIMESTAMP,
                "nonce": NONCE,
                "echostr": enc,
            },
        )
        assert r.status_code == 200
        assert r.text == ECHO_STR

    def test_encrypted_bad_signature_403_no_echostr(self, client):
        crypto = WxCrypto(TOKEN, AES_KEY)
        enc = crypto.encrypt(ECHO_STR)
        r = client.get(
            "/wx/callback",
            params={
                "msg_signature": "0" * 40,
                "timestamp": TIMESTAMP,
                "nonce": NONCE,
                "echostr": enc,
            },
        )
        assert r.status_code == 403
        assert ECHO_STR not in r.text

    def test_plaintext_mode_returns_echostr_as_is(self, plaintext_client):
        r = plaintext_client.get(
            "/wx/callback",
            params={"echostr": ECHO_STR},
        )
        assert r.status_code == 200
        assert r.text == ECHO_STR


class TestPost:
    def test_encrypted_msg_calls_handler_and_returns_200(self, client, monkeypatch):
        received = []

        async def fake_handler(msg):
            received.append(msg)

        monkeypatch.setattr(wx_callback_module, "message_handler", fake_handler)
        crypto = WxCrypto(TOKEN, AES_KEY)
        body, msg_signature = enc_body(crypto, PLAIN_MSG)
        r = client.post(
            "/wx/callback",
            params={"msg_signature": msg_signature, "timestamp": TIMESTAMP, "nonce": NONCE},
            content=body.encode("utf-8"),
        )
        assert r.status_code == 200
        assert r.text == ""
        assert len(received) == 1
        msg = received[0]
        assert msg["msgid"] == "1234567890123456"
        assert msg["msg_type"] == "text"
        assert msg["from_user"] == "zhangsan"
        assert msg["chat_id"] == "chatid123"
        assert msg["content"] == "hello world"
        assert msg["at_userids"] == ["u1001", "u1002"]

    def test_handler_exception_still_200(self, client, monkeypatch):
        async def bad_handler(msg):
            raise RuntimeError("boom")

        monkeypatch.setattr(wx_callback_module, "message_handler", bad_handler)
        crypto = WxCrypto(TOKEN, AES_KEY)
        body, msg_signature = enc_body(crypto, PLAIN_MSG)
        r = client.post(
            "/wx/callback",
            params={"msg_signature": msg_signature, "timestamp": TIMESTAMP, "nonce": NONCE},
            content=body.encode("utf-8"),
        )
        assert r.status_code == 200

    def test_plaintext_msg_calls_handler(self, plaintext_client, monkeypatch):
        received = []

        async def fake_handler(msg):
            received.append(msg)

        monkeypatch.setattr(wx_callback_module, "message_handler", fake_handler)
        r = plaintext_client.post(
            "/wx/callback",
            params={"msg_signature": "x", "timestamp": TIMESTAMP, "nonce": NONCE},
            content=PLAIN_MSG.encode("utf-8"),
        )
        assert r.status_code == 200
        assert received[0]["content"] == "hello world"
        assert received[0]["chat_id"] == "chatid123"

    def test_bad_signature_403(self, client, monkeypatch):
        called = []

        async def fake_handler(msg):
            called.append(msg)

        monkeypatch.setattr(wx_callback_module, "message_handler", fake_handler)
        crypto = WxCrypto(TOKEN, AES_KEY)
        body, _ = enc_body(crypto, PLAIN_MSG)
        r = client.post(
            "/wx/callback",
            params={"msg_signature": "0" * 40, "timestamp": TIMESTAMP, "nonce": NONCE},
            content=body.encode("utf-8"),
        )
        assert r.status_code == 403
        assert not called
