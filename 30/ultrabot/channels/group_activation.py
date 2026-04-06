# ultrabot/channels/group_activation.py
"""控制机器人在群聊中何时回复："mention" 模式（仅被 @ 时回复）
或 "always" 模式。check_activation() 是入口函数。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from loguru import logger


class ActivationMode(Enum):
    """群聊中的激活模式。"""
    MENTION = "mention"
    ALWAYS = "always"


@dataclass
class ActivationResult:
    """激活检查的结果。"""
    should_respond: bool


# 全局机器人名称列表
_bot_names: list[str] = []


def set_bot_names(names: list[str]) -> None:
    """设置机器人名称列表，用于 mention 检测。"""
    global _bot_names
    _bot_names = [n.lower() for n in names]
    logger.debug("Bot names set to: {}", _bot_names)


def check_activation(
    text: str,
    session_key: str,
    is_group: bool = False,
    mode: ActivationMode = ActivationMode.MENTION,
) -> ActivationResult:
    """检查是否应该回复消息。

    - DM（非群聊）始终回复。
    - 群聊中，根据 mode 决定：
      - ALWAYS：始终回复
      - MENTION：仅在消息中包含 @bot_name 时回复
    """
    # DM 始终回复
    if not is_group:
        return ActivationResult(should_respond=True)

    # 群聊 ALWAYS 模式
    if mode == ActivationMode.ALWAYS:
        return ActivationResult(should_respond=True)

    # 群聊 MENTION 模式：检查消息中是否包含 @bot_name
    text_lower = text.lower()
    for name in _bot_names:
        if f"@{name}" in text_lower:
            logger.debug("Mention detected for '{}' in session {}", name, session_key)
            return ActivationResult(should_respond=True)

    return ActivationResult(should_respond=False)
