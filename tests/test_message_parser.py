# -*- coding: utf-8 -*-
"""消息解析测试: @判定 + 幂等去重 + 指令识别."""
import pytest

from app.config import settings
from app.message_parser import MsgIdDedup, extract_question, is_admin, is_at_me, parse_command


def make_msg(content="hello", at_userids=None):
    return {"msgid": "1", "content": content, "at_userids": at_userids or []}


@pytest.mark.anyio
async def run_is_at_me(monkeypatch, bot_id, msg):
    monkeypatch.setattr(settings, "WX_BOT_USERID", bot_id)
    return await is_at_me(msg)


class TestIsAtMe:
    @pytest.mark.anyio
    async def test_bot_in_at_userids_true(self, monkeypatch):
        msg = make_msg(at_userids=["u1", "bot001"])
        assert await run_is_at_me(monkeypatch, "bot001", msg) is True
        assert msg["is_at_me"] is True

    @pytest.mark.anyio
    async def test_bot_not_in_at_userids_false(self, monkeypatch):
        msg = make_msg(at_userids=["u1", "u2"])
        assert await run_is_at_me(monkeypatch, "bot001", msg) is False

    @pytest.mark.anyio
    async def test_at_all_true(self, monkeypatch):
        msg = make_msg(content="请大家看一下 @all")
        assert await run_is_at_me(monkeypatch, "bot001", msg) is True

    @pytest.mark.anyio
    async def test_empty_bot_userid_no_false_positive(self, monkeypatch):
        msg = make_msg(at_userids=["u1", "u2"])
        assert await run_is_at_me(monkeypatch, "", msg) is False


class TestExtractQuestion:
    def test_removes_at_tag(self):
        assert extract_question(make_msg("@张三 怎么退货")) == "怎么退货"

    def test_multiple_at_tags_and_spaces(self):
        assert extract_question(make_msg("  @张三 @李四 怎么退货  ")) == "怎么退货"

    def test_no_at_tag(self):
        assert extract_question(make_msg("怎么退货")) == "怎么退货"

    def test_at_all_removed(self):
        assert extract_question(make_msg("@all 怎么退货")) == "怎么退货"


class TestMsgIdDedup:
    def test_first_true_repeat_false(self):
        clock = [1000.0]
        d = MsgIdDedup(ttl=600, now=lambda: clock[0])
        assert d.dedup("m1") is True
        assert d.dedup("m1") is False

    def test_expired_entry_passes_again(self):
        clock = [1000.0]
        d = MsgIdDedup(ttl=600, now=lambda: clock[0])
        assert d.dedup("m1") is True
        clock[0] += 601
        assert d.dedup("m1") is True

    def test_within_ttl_still_blocked(self):
        clock = [1000.0]
        d = MsgIdDedup(ttl=600, now=lambda: clock[0])
        d.dedup("m1")
        clock[0] += 599
        assert d.dedup("m1") is False


class TestParseCommand:
    def test_upload(self):
        assert parse_command("#kb:upload") == {"action": "upload", "arg": ""}

    def test_delete_with_filename(self):
        assert parse_command("#kb:delete 文件.md") == {"action": "delete", "arg": "文件.md"}

    def test_list(self):
        assert parse_command("#kb:list") == {"action": "list", "arg": ""}

    def test_leading_trailing_spaces(self):
        assert parse_command("  #kb:list  ") == {"action": "list", "arg": ""}

    def test_not_a_command(self):
        assert parse_command("怎么退货") is None
        assert parse_command("#kb:foo bar") is None
        assert parse_command("") is None

    def test_list_prefix_not_swallow_extra_text(self):
        # "#kb:listxxx" 不应被识别为 list 指令
        assert parse_command("#kb:listxxx") is None


class TestIsAdmin:
    def test_in_whitelist(self, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_USER_LIST", ["admin1", "admin2"])
        assert is_admin("admin1") is True

    def test_not_in_whitelist(self, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_USER_LIST", ["admin1"])
        assert is_admin("nobody") is False

    def test_empty_whitelist(self, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_USER_LIST", [])
        assert is_admin("admin1") is False
