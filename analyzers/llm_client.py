"""LLM 客户端 — 封装 OpenAI 兼容 API 调用

支持任意 OpenAI API 兼容端点（OpenAI、vLLM、Ollama、LocalAI 等）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_API_BASE = "http://localhost:6011/v1"
DEFAULT_MODEL = "gpt"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2


@dataclass
class LLMConfig:
    """LLM 客户端配置"""

    api_base: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    temperature: float = 0.1

    def __post_init__(self) -> None:
        # 从环境变量填充默认值
        if not self.api_base:
            self.api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE)
        if not self.api_key:
            self.api_key = os.environ.get("LLM_API_KEY", "")
        if not self.model:
            self.model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)


class LLMClient:
    """OpenAI 兼容 LLM 客户端。

    使用 httpx 同步调用，支持重试和错误处理。
    """

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or LLMConfig()
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
            self._client = httpx.Client(
                base_url=self.config.api_base,
                headers=headers,
                timeout=self.config.timeout,
            )
        return self._client

    def chat(self, prompt: str, system: str = "") -> str:
        """发送聊天请求，返回模型回复文本。

        Args:
            prompt: 用户消息
            system: 系统提示（可选）

        Returns:
            模型回复文本

        Raises:
            LLMError: 调用失败时抛出
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                last_error = LLMError(
                    f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                )
                logger.warning("LLM 调用失败 (attempt %d): %s", attempt + 1, last_error)
            except httpx.RequestError as e:
                last_error = LLMError(f"请求失败: {e}")
                logger.warning("LLM 请求错误 (attempt %d): %s", attempt + 1, last_error)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                last_error = LLMError(f"响应解析失败: {e}")
                logger.warning("LLM 响应解析失败 (attempt %d): %s", attempt + 1, last_error)
                break  # 解析错误不重试

        raise last_error or LLMError("未知错误")

    def chat_json(self, prompt: str, system: str = "") -> Dict[str, Any]:
        """发送聊天请求，解析 JSON 响应。

        Returns:
            解析后的 JSON 字典

        Raises:
            LLMError: 调用或解析失败时抛出
        """
        raw = self.chat(prompt, system)
        # 尝试从 markdown code block 中提取 JSON
        text = raw.strip()
        if text.startswith("```"):
            # 去掉 ```json 和 ```
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON 解析失败: {e}, 原始响应: {raw[:200]}")

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class LLMError(Exception):
    """LLM 调用异常"""
    pass


def is_llm_available() -> bool:
    """检查 LLM 是否可用（环境变量已配置）。

    用于 SemanticEvaluator 决定是否降级到规则引擎。
    """
    api_key = os.environ.get("LLM_API_KEY", "")
    # 有 API key 或者使用本地端点（无需 key）都认为可用
    if api_key:
        return True
    api_base = os.environ.get("LLM_API_BASE", "")
    # 本地端点默认可用
    if api_base and ("localhost" in api_base or "127.0.0.1" in api_base):
        return True
    return False
