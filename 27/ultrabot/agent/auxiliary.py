# ultrabot/agent/auxiliary.py
"""辅助 LLM 客户端，用于辅助任务（摘要、标题生成、分类）。

基于 OpenAI 兼容聊天补全端点的轻量级异步包装器。
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class AuxiliaryClient:
    """通过 OpenAI 兼容端点执行辅助 LLM 任务的异步客户端。

    Parameters
    ----------
    provider : str
        人类可读的提供商名称（如 "openai"、"openrouter"）。
    model : str
        模型标识符（如 "gpt-4o-mini"）。
    api_key : str
        API 的 Bearer token。
    base_url : str, optional
        端点的基础 URL。默认为 OpenAI。
    timeout : float, optional
        请求超时时间（秒）。默认 30。
    """

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """延迟初始化底层 httpx 客户端。"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        """发送聊天补全请求并返回助手的文本。
        
        任何失败均返回空字符串。
        """
        if not messages:
            return ""

        client = self._get_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", "")
            return (content or "").strip()
        except Exception as exc:
            logger.debug("AuxiliaryClient.complete failed: %s", exc)
            return ""

    async def summarize(self, text: str, max_tokens: int = 256) -> str:
        """将文本摘要为简洁的一段话。"""
        if not text:
            return ""
        messages = [
            {"role": "system", "content":
             "You are a concise summarizer. Be brief."},
            {"role": "user", "content": text},
        ]
        return await self.complete(messages, max_tokens=max_tokens, temperature=0.3)

    async def generate_title(self, messages: list[dict], max_tokens: int = 32) -> str:
        """为对话生成一个简短的描述性标题。"""
        if not messages:
            return ""
        snippet_parts: list[str] = []
        for msg in messages[:4]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                snippet_parts.append(f"{role}: {content[:200]}")
        snippet = "\n".join(snippet_parts)

        title_messages = [
            {"role": "system", "content":
             "Generate a short, descriptive title (3-7 words) for this "
             "conversation. Return ONLY the title text."},
            {"role": "user", "content": snippet},
        ]
        return await self.complete(title_messages, max_tokens=max_tokens, temperature=0.3)

    async def classify(self, text: str, categories: list[str]) -> str:
        """将文本分类到给定类别之一。"""
        if not text or not categories:
            return ""
        cats_str = ", ".join(categories)
        messages = [
            {"role": "system", "content":
             f"Classify the following text into exactly one of these "
             f"categories: {cats_str}. Respond with ONLY the category name."},
            {"role": "user", "content": text},
        ]
        result = await self.complete(messages, max_tokens=20, temperature=0.1)
        result_lower = result.strip().lower()
        for cat in categories:
            if cat.lower() == result_lower:
                return cat
        for cat in categories:
            if cat.lower() in result_lower:
                return cat
        return result
