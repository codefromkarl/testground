"""LLM 客户端 — 封装 OpenAI 兼容 API 调用

支持任意 OpenAI API 兼容端点。
模型自动发现优先级：
  1. 显式传入参数
  2. LLM_API_BASE / LLM_API_KEY / LLM_MODEL 环境变量
  3. CPA_API_KEY + http://127.0.0.1:8317/v1（pi 本地网关）
  4. MIMO_API_KEY + https://api.xiaomimimo.com/v1
  5. 其他已知 provider
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
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2

# 已知 provider 自动发现表
PROVIDER_DISCOVERY = [
    # (env_key_name, env_base_url, default_model)
    {"key_env": "CPA_API_KEY", "base": "http://127.0.0.1:8317/v1", "model": "mimo3"},
    {"key_env": "MIMO_API_KEY", "base": "https://api.xiaomimimo.com/v1", "model": "mimo-v2.5-pro"},
    {"key_env": "SILICONFLOW_API_KEY", "base": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-235B-A22B"},
    {"key_env": "ARK_API_KEY", "base": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-1.5-pro-256k"},
    {"key_env": "DOUBAO_API_KEY", "base": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-1.5-pro-256k"},
    {"key_env": "OPENAI_API_KEY", "base": "https://api.openai.com/v1", "model": "gpt-4o"},
]


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
        # 优先级 1: 显式传入的参数（不为空）
        # 优先级 2: LLM_* 环境变量
        # 优先级 3: 已知 provider 自动发现
        if not self.api_base:
            self.api_base = os.environ.get("LLM_API_BASE", "")
        if not self.api_key:
            self.api_key = os.environ.get("LLM_API_KEY", "")
        if not self.model:
            self.model = os.environ.get("LLM_MODEL", "")

        # 自动发现：如果没有显式配置，从已知 provider 中找
        if not self.api_key or not self.api_base:
            for provider in PROVIDER_DISCOVERY:
                key = os.environ.get(provider["key_env"], "")
                if key:
                    if not self.api_base:
                        self.api_base = provider["base"]
                    if not self.api_key:
                        self.api_key = key
                    if not self.model:
                        self.model = provider["model"]
                    break

        if not self.api_base:
            logger.warning("未找到 LLM API 配置，LLM 功能将不可用")


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
    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
            # httpx 0.28.x 的 proxy=None 仍读环境变量，SOCKS 会炸
            # 直接在创建时清理代理环境变量
            old_proxy = {}
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
                v = os.environ.pop(k, None)
                if v is not None:
                    old_proxy[k] = v
            try:
                self._client = httpx.Client(
                    base_url=self.config.api_base,
                    headers=headers,
                    timeout=self.config.timeout,
                )
            finally:
                os.environ.update(old_proxy)
        return self._client
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
    """检查 LLM 是否可用。

    自动发现逻辑：
    1. LLM_API_KEY + LLM_API_BASE 已设置
    2. 任何已知 provider 的 API key 在环境中
    3. 本地端点（无需 key）
    """
    # 显式配置
    if os.environ.get("LLM_API_KEY") or os.environ.get("LLM_API_BASE"):
        return True
    # 已知 provider
    for provider in PROVIDER_DISCOVERY:
        if os.environ.get(provider["key_env"]):
            return True
    return False


def get_default_model_info() -> Dict[str, str]:
    """返回当前自动发现的模型信息"""
    config = LLMConfig()
    return {
        "api_base": config.api_base or "(未配置)",
        "model": config.model or "(未配置)",
        "has_key": bool(config.api_key),
    }
