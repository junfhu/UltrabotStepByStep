# tests/test_prompt_cache.py
"""提示词缓存和辅助客户端的测试。"""

import pytest
from ultrabot.providers.prompt_cache import PromptCacheManager, CacheStats


class TestCacheStats:
    def test_hit_rate_empty(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate(self):
        stats = CacheStats(hits=3, misses=1)
        assert stats.hit_rate == 0.75

    def test_record_hit(self):
        stats = CacheStats()
        stats.record_hit(tokens_saved=100)
        assert stats.hits == 1
        assert stats.total_tokens_saved == 100


class TestPromptCacheManager:
    def test_none_strategy_no_markers(self):
        mgr = PromptCacheManager()
        msgs = [{"role": "system", "content": "Hello"}]
        result = mgr.apply_cache_hints(msgs, strategy="none")
        assert "cache_control" not in str(result)

    def test_system_only_marks_system(self):
        mgr = PromptCacheManager()
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hi"},
        ]
        result = mgr.apply_cache_hints(msgs, strategy="system_only")
        # 系统消息内容转换为带 cache_control 的列表
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["cache_control"]["type"] == "ephemeral"
        # 用户消息未被修改
        assert isinstance(result[1]["content"], str)

    def test_system_and_3_marks_last_three(self):
        mgr = PromptCacheManager()
        msgs = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "U1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "U2"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "U3"},
        ]
        result = mgr.apply_cache_hints(msgs, strategy="system_and_3")
        # 系统消息已标记
        assert isinstance(result[0]["content"], list)
        # 最后 3 条非系统消息已标记（索引 3、4、5）
        for idx in [3, 4, 5]:
            assert isinstance(result[idx]["content"], list)
        # 前面的非系统消息未被标记
        assert isinstance(result[1]["content"], str)

    def test_original_not_mutated(self):
        mgr = PromptCacheManager()
        msgs = [{"role": "system", "content": "Hello"}]
        original_content = msgs[0]["content"]
        mgr.apply_cache_hints(msgs)
        assert msgs[0]["content"] == original_content  # 仍然是字符串

    def test_is_anthropic_model(self):
        assert PromptCacheManager.is_anthropic_model("claude-sonnet-4-20250514")
        assert not PromptCacheManager.is_anthropic_model("gpt-4o")
