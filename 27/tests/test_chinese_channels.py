# tests/test_chinese_channels.py
"""验证中国平台通道类可以导入并具有正确的接口。"""

import importlib


def test_wecom_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.wecom")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.wecom")
    assert hasattr(mod, "WecomChannel")


def test_weixin_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.weixin")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.weixin")
    assert hasattr(mod, "WeixinChannel")


def test_feishu_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.feishu")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.feishu")
    assert hasattr(mod, "FeishuChannel")


def test_qq_channel_importable():
    spec = importlib.util.find_spec("ultrabot.channels.qq")
    assert spec is not None
    mod = importlib.import_module("ultrabot.channels.qq")
    assert hasattr(mod, "QQChannel")


def test_all_channels_extend_base():
    from ultrabot.channels.base import BaseChannel
    from ultrabot.channels.weixin import WeixinChannel

    assert issubclass(WeixinChannel, BaseChannel)


def test_weixin_message_chunking():
    """验证微信消息分割辅助函数。"""
    from ultrabot.channels.weixin import _split_message

    chunks = _split_message("A" * 10000, 4000)
    assert len(chunks) == 3
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == "A" * 10000


def test_weixin_aes_key_parsing():
    """验证 AES 密钥解析可以处理 16 字节的原始密钥。"""
    import base64
    from ultrabot.channels.weixin import _parse_aes_key

    raw_key = b"0123456789abcdef"            # 16 字节
    b64_key = base64.b64encode(raw_key).decode()
    parsed = _parse_aes_key(b64_key)
    assert parsed == raw_key
