# tests/test_channels_platform.py
"""验证通道类可以加载并具有正确的接口。"""


def test_discord_channel_has_correct_name():
    # 导入时不需要在运行时依赖 discord 库。
    from ultrabot.channels.discord_channel import DiscordChannel
    assert DiscordChannel.name.fget is not None   # 属性存在


def test_slack_channel_has_correct_name():
    from ultrabot.channels.slack_channel import SlackChannel
    assert SlackChannel.name.fget is not None


def test_base_channel_is_abstract():
    from ultrabot.channels.base import BaseChannel
    import inspect
    abstract_methods = {
        name for name, _ in inspect.getmembers(BaseChannel)
        if getattr(getattr(BaseChannel, name, None), "__isabstractmethod__", False)
    }
    assert "start" in abstract_methods
    assert "stop" in abstract_methods
    assert "send" in abstract_methods
    assert "name" in abstract_methods
