"""基于 LLM 的长对话上下文压缩。

通过辅助客户端对对话中间部分进行摘要压缩，
同时保护头部（系统提示 + 首轮对话）和尾部（最近消息）。
"""

import logging
from typing import Optional

from ultrabot.agent.auxiliary import AuxiliaryClient

logger = logging.getLogger(__name__)

# 粗略估算：1 token ≈ 4 个字符（广泛使用的启发式方法）
_CHARS_PER_TOKEN = 4

# 当估算 token 数超过上下文限制的 80% 时触发压缩
_DEFAULT_THRESHOLD_RATIO = 0.80

# 摘要输入中每个工具结果保留的最大字符数
_MAX_TOOL_RESULT_CHARS = 3000

# 裁剪后的工具输出占位符
_PRUNED_TOOL_PLACEHOLDER = "[Tool output truncated to save context space]"

# 摘要前缀，让模型知道上下文已被压缩
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier turns in this conversation were compacted "
    "to save context space. The summary below describes work that was "
    "already completed. Use it to continue without repeating work:"
)

# LLM 需要填写的结构化模板
_SUMMARY_TEMPLATE = """\
## Conversation Summary
**Goal:** [what the user is trying to accomplish]
**Progress:** [what has been done so far]
**Key Decisions:** [important choices made]
**Files Modified:** [files touched, if any]
**Next Steps:** [what remains to be done]"""

_SUMMARIZE_SYSTEM_PROMPT = f"""\
You are a context compressor. Given conversation turns, produce a structured \
summary using EXACTLY this template:

{_SUMMARY_TEMPLATE}

Be specific: include file paths, commands, error messages, and concrete values. \
Write only the summary — no preamble."""

class ContextCompressor:
    """当接近模型上下文限制时压缩对话上下文。

    Parameters
    ----------
    auxiliary : AuxiliaryClient
        用于生成摘要的 LLM 客户端（廉价模型）。
    threshold_ratio : float
        触发压缩的 context_limit 比例（0.80）。
    protect_head : int
        开头需要保护的消息数（默认 3：系统消息、第一条用户消息、第一条助手消息）。
    protect_tail : int
        末尾需要保护的最近消息数（默认 6）。
    max_summary_tokens : int
        摘要响应的最大 token 数（默认 1024）。
    """

    def __init__(
        self,
        auxiliary: AuxiliaryClient,
        threshold_ratio: float = _DEFAULT_THRESHOLD_RATIO,
        protect_head: int = 3,
        protect_tail: int = 6,
        max_summary_tokens: int = 1024,
    ) -> None:
        self.auxiliary = auxiliary
        self.threshold_ratio = threshold_ratio
        self.protect_head = max(1, protect_head)
        self.protect_tail = max(1, protect_tail)
        self.max_summary_tokens = max_summary_tokens
        self._previous_summary: Optional[str] = None  # 跨多次压缩堆叠
        self.compression_count: int = 0

    @staticmethod
    def estimate_tokens(messages: list[dict]) -> int:
        """粗略 token 估算：总字符数 / 4。"""
        if not messages:
            return 0
        total_chars = 0
        for msg in messages:
            content = msg.get("content") or ""
            total_chars += len(content) + 4   # 每条消息约 4 字符开销
            # 计入 tool_calls 参数
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict):
                    args = tc.get("function", {}).get("arguments", "")
                    total_chars += len(args)
        return total_chars // _CHARS_PER_TOKEN

    def should_compress(self, messages: list[dict], context_limit: int) -> bool:
        """当估算 token 数超过阈值时返回 True。"""
        if not messages or context_limit <= 0:
            return False
        estimated = self.estimate_tokens(messages)
        threshold = int(context_limit * self.threshold_ratio)
        return estimated >= threshold

    @staticmethod
    def prune_tool_output(
        messages: list[dict], max_chars: int = _MAX_TOOL_RESULT_CHARS,
    ) -> list[dict]:
        """截断过长的工具结果消息以节省 token。
        
        返回一个新列表 — 非工具消息原样传递。
        """
        if not messages:
            return []
        result: list[dict] = []
        for msg in messages:
            if msg.get("role") == "tool" and len(msg.get("content", "")) > max_chars:
                truncated = msg.copy()
                original = truncated["content"]
                truncated["content"] = (
                    original[:max_chars] + f"\n...{_PRUNED_TOOL_PLACEHOLDER}"
                )
                result.append(truncated)
            else:
                result.append(msg)
        return result

    async def compress(self, messages: list[dict], max_tokens: int = 0) -> list[dict]:
        """通过摘要中间部分进行压缩。
        
        返回：头部 + [摘要消息] + 尾部
        """
        if not messages:
            return []
        n = len(messages)

        # 如果所有消息都在保护范围内，则无需压缩
        if n <= self.protect_head + self.protect_tail:
            return list(messages)

        head = messages[: self.protect_head]
        tail = messages[-self.protect_tail :]
        middle = messages[self.protect_head : n - self.protect_tail]

        if not middle:
            return list(messages)

        # 在摘要之前先裁剪中间部分的工具输出
        pruned_middle = self.prune_tool_output(middle)
        serialized = self._serialize_turns(pruned_middle)

        # 构建摘要提示 — 如果存在之前的摘要则合并
        if self._previous_summary:
            user_prompt = (
                f"Previous summary:\n{self._previous_summary}\n\n"
                f"New turns to incorporate:\n{serialized}\n\n"
                f"Update the summary using the structured template. "
                f"Preserve all relevant previous information."
            )
        else:
            user_prompt = f"Summarize these conversation turns:\n{serialized}"

        summary_messages = [
            {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        summary_text = await self.auxiliary.complete(
            summary_messages,
            max_tokens=self.max_summary_tokens,
            temperature=0.3,
        )

        if not summary_text:
            summary_text = (
                f"(Summary generation failed. {len(middle)} messages were "
                f"removed to save context space.)"
            )

        # 为多轮压缩堆叠摘要
        self._previous_summary = summary_text
        self.compression_count += 1

        summary_message = {
            "role": "system",
            "content": f"{SUMMARY_PREFIX}\n\n{summary_text}",
        }

        return head + [summary_message] + tail

    @staticmethod
    def _serialize_turns(turns: list[dict]) -> str:
        """将消息转换为带标签的文本供摘要器使用。"""
        parts: list[str] = []
        for msg in turns:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content") or ""

            # 截断过长的单条内容
            if len(content) > _MAX_TOOL_RESULT_CHARS:
                content = content[:2000] + "\n...[truncated]...\n" + content[-800:]

            if role == "TOOL":
                tool_id = msg.get("tool_call_id", "")
                parts.append(f"[TOOL RESULT {tool_id}]: {content}")
            elif role == "ASSISTANT":
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    tc_parts: list[str] = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            name = fn.get("name", "?")
                            args = fn.get("arguments", "")
                            if len(args) > 500:
                                args = args[:400] + "..."
                            tc_parts.append(f"  {name}({args})")
                    content += "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
                parts.append(f"[ASSISTANT]: {content}")
            else:
                parts.append(f"[{role}]: {content}")

        return "\n\n".join(parts)
