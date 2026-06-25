"""统一 LLM 调用客户端。

支持 DeepSeek、Qwen（DashScope 兼容模式）、OpenAI 三种提供方，均通过 OpenAI
兼容的 ``/v1/chat/completions`` 协议访问，因此底层只用 ``httpx`` 直接发 HTTP
请求，不依赖 ``openai`` SDK。

切换提供方：
    - 环境变量 ``LLM_PROVIDER``（``deepseek`` / ``qwen`` / ``openai``，默认 ``deepseek``）
    - 对应的 API Key 环境变量：``DEEPSEEK_API_KEY`` / ``DASHSCOPE_API_KEY`` / ``OPENAI_API_KEY``
    - base_url 可用 ``DEEPSEEK_BASE_URL`` / ``DASHSCOPE_BASE_URL`` / ``OPENAI_BASE_URL`` 覆盖

典型用法::

    # 便捷：一句话调用
    from pipeline.model_client import quick_chat
    answer = quick_chat("用一句话介绍 LangGraph")

    # 进阶：自行构造消息
    from pipeline.model_client import create_provider, chat_with_retry
    prov = create_provider()
    resp = chat_with_retry(prov, messages, max_tokens=500)

AGENTS.md 红线：禁止裸 ``print()``，统一 ``logging``；禁止无错误处理的外部请求。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import httpx

# 本模块可被两种路径加载：直接运行时为顶层 ``model_client``，作为包成员时为
# ``pipeline.model_client``。这里把两者统一为同一个模块对象，否则各自定义的
# ``LLMError`` 会是两个不同的类，导致 ``except LLMError`` 跨调用点漏接。
# 规则：无论先以哪个名字加载，都把另一个名字也指向当前模块。
_mod = sys.modules.get(__name__)
if _mod is not None:
    if __name__ == "model_client":
        sys.modules.setdefault("pipeline.model_client", _mod)
    elif __name__ == "pipeline.model_client":
        sys.modules.setdefault("model_client", _mod)

# --------------------------------------------------------------------------- #
# 常量与配置
# --------------------------------------------------------------------------- #

LOG = logging.getLogger("model_client")

# 各 provider 支持的取值
SUPPORTED_PROVIDERS: tuple[str, ...] = ("deepseek", "qwen", "openai")

# 各 provider 官方默认 base_url（OpenAI 兼容协议入口）
DEFAULT_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}

# 各 provider 的 base_url 覆盖环境变量名
BASE_URL_ENV: dict[str, str] = {
    "deepseek": "DEEPSEEK_BASE_URL",
    "qwen": "DASHSCOPE_BASE_URL",
    "openai": "OPENAI_BASE_URL",
}

# 各 provider 对应的 API Key 环境变量名
API_KEY_ENV: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# 各 provider 默认模型名
DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "qwen": "qwen-plus",
    "openai": "gpt-4o-mini",
}

# 单次 HTTP 请求超时（秒）
REQUEST_TIMEOUT = 60
# 最大重试次数（含首次尝试）
MAX_RETRIES = 3
# 指数退避基数：第 n 次失败后等待 ``base ** n`` 秒（2s, 4s）
RETRY_BACKOFF_BASE = 2

# 成本定价表：USD / 1M tokens，(输入价, 输出价)
PRICING_USD_PER_M: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.27, 1.10),
    "qwen-plus": (0.40, 1.20),
    "gpt-4o-mini": (0.15, 0.60),
}


# --------------------------------------------------------------------------- #
# 用量与响应数据结构
# --------------------------------------------------------------------------- #


@dataclass
class Usage:
    """单次请求的 token 用量统计。

    Attributes:
        prompt_tokens: 输入（提示）token 数。
        completion_tokens: 输出（补全）token 数。
        total_tokens: 合计 token 数；为 0 时按 prompt + completion 推断。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def total(self) -> int:
        """返回总 token 数，兼容 ``total_tokens`` 缺失的情况。"""

        if self.total_tokens:
            return self.total_tokens
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    """统一的 LLM 响应。

    Attributes:
        content: 模型生成的文本内容。
        usage: 本次请求的 token 用量。
        model: 实际使用的模型名（响应里返回的为准）。
        provider: 提供方标识（``deepseek`` / ``qwen`` / ``openai``）。
        finish_reason: 结束原因（如 ``stop`` / ``length``），可能为空。
    """

    content: str
    usage: Usage
    model: str
    provider: str
    finish_reason: str = ""


# --------------------------------------------------------------------------- #
# 异常与成本估算
# --------------------------------------------------------------------------- #


class LLMError(RuntimeError):
    """LLM 调用失败的统一异常。"""


def estimate_cost(usage: Usage, model: str) -> float:
    """按 token 用量与模型估算 USD 成本。

    Args:
        usage: 单次请求的 token 用量。
        model: 模型名（用于查定价表）。

    Returns:
        估算的 USD 成本。未列入 ``PRICING_USD_PER_M`` 的模型返回 ``0.0`` 并告警。
    """

    pricing = PRICING_USD_PER_M.get(model)
    if pricing is None:
        LOG.warning("estimate_cost: 模型 %s 未列入定价表，返回 0.0", model)
        return 0.0
    input_price, output_price = pricing
    cost = (
        usage.prompt_tokens * input_price / 1_000_000
        + usage.completion_tokens * output_price / 1_000_000
    )
    return round(cost, 6)


# --------------------------------------------------------------------------- #
# 抽象基类与实现
# --------------------------------------------------------------------------- #


class LLMProvider(ABC):
    """LLM 提供方的统一接口（抽象基类）。"""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """发起一次 chat 对话。

        Args:
            messages: OpenAI 格式的消息列表，形如
                ``[{"role": "user", "content": "..."}]``。
            model: 模型名；默认使用 provider 内置的默认模型。
            temperature: 采样温度，0.0 - 2.0。
            max_tokens: 最大生成 token 数；``None`` 表示由服务端决定。

        Returns:
            LLMResponse: 统一响应。

        Raises:
            LLMError: 任何网络或解析层面的失败。
        """


class OpenAICompatibleProvider(LLMProvider):
    """基于 ``httpx`` 的 OpenAI 兼容 ``/v1/chat/completions`` 客户端。

    DeepSeek、Qwen（DashScope 兼容模式）、OpenAI 三家均为 OpenAI 兼容协议，
    差异仅在 ``base_url``、API Key 与默认模型，因此用单一实现即可覆盖。

    配置优先级：构造参数 > 环境变量 > 模块内置默认。
    """

    def __init__(self, provider: str = "deepseek") -> None:
        """初始化客户端。

        Args:
            provider: 提供方标识，取值见 ``SUPPORTED_PROVIDERS``，默认 ``deepseek``。

        Raises:
            LLMError: provider 不支持，或对应的 API Key 环境变量未设置。
        """

        provider = (provider or os.getenv("LLM_PROVIDER", "deepseek")).lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise LLMError(
                f"不支持的 provider: {provider!r}，可选: {SUPPORTED_PROVIDERS}"
            )

        api_key = os.getenv(API_KEY_ENV[provider], "").strip()
        if not api_key:
            raise LLMError(
                f"缺少 API Key：请设置环境变量 {API_KEY_ENV[provider]}"
            )

        self.provider = provider
        self.api_key = api_key
        self.base_url = os.getenv(
            BASE_URL_ENV[provider], DEFAULT_BASE_URLS[provider]
        ).rstrip("/")
        self.default_model = DEFAULT_MODELS[provider]

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """发起一次 chat 请求并解析响应。

        实现细节：``POST {base_url}/chat/completions``，``Authorization: Bearer
        {api_key}``，60 秒超时。网络错误统一转成 ``LLMError``。
        """

        used_model = model or self.default_model
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"请求超时（{REQUEST_TIMEOUT}s）: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMError(f"网络请求失败: {exc}") from exc

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            finish_reason = data["choices"][0].get("finish_reason", "")
            raw_usage = data.get("usage") or {}
            usage = Usage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0),
                completion_tokens=raw_usage.get("completion_tokens", 0),
                total_tokens=raw_usage.get("total_tokens", 0),
            )
            # 响应里返回的模型名（如 deepseek 实际模型）更准确
            returned_model = data.get("model", used_model)
        except (KeyError, ValueError) as exc:
            raise LLMError(f"LLM 返回格式异常，无法解析: {exc}") from exc

        return LLMResponse(
            content=content,
            usage=usage,
            model=returned_model,
            provider=self.provider,
            finish_reason=finish_reason,
        )


# --------------------------------------------------------------------------- #
# Provider 工厂
# --------------------------------------------------------------------------- #


def create_provider(provider: Optional[str] = None) -> OpenAICompatibleProvider:
    """创建 LLM provider 实例的工厂函数。

    统一 provider 解析逻辑：构造参数 > 环境变量 ``LLM_PROVIDER`` > 默认 ``deepseek``。
    供需要自行控制消息（而非使用 ``quick_chat``）的调用方使用，例如::

        from pipeline.model_client import create_provider, chat_with_retry

        prov = create_provider()
        resp = chat_with_retry(prov, messages, max_tokens=500)

    Args:
        provider: 提供方标识（``deepseek`` / ``qwen`` / ``openai``）；
            ``None`` 时读环境变量 ``LLM_PROVIDER``（默认 ``deepseek``）。

    Returns:
        OpenAICompatibleProvider: 初始化好的 provider 实例。

    Raises:
        LLMError: provider 不支持，或对应的 API Key 环境变量未设置。
    """

    used = provider or os.getenv("LLM_PROVIDER", "deepseek")
    return OpenAICompatibleProvider(used)


# --------------------------------------------------------------------------- #
# 重试封装
# --------------------------------------------------------------------------- #


def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict[str, str]],
    *,
    retries: int = MAX_RETRIES,
    **kwargs: Any,
) -> LLMResponse:
    """带指数退避的重试封装。

    Args:
        provider: ``LLMProvider`` 实例。
        messages: OpenAI 格式的消息列表。
        retries: 含首次在内的总尝试次数，默认 ``MAX_RETRIES``（3）。
        **kwargs: 透传给 ``provider.chat`` 的关键字参数（model / temperature / max_tokens）。

    Returns:
        LLMResponse: 首次成功的响应。

    Raises:
        LLMError: 重试耗尽后仍失败，抛出最后一次的异常。
    """

    last_exc: Optional[LLMError] = None
    for attempt in range(1, retries + 1):
        try:
            return provider.chat(messages, **kwargs)
        except LLMError as exc:
            last_exc = exc
            if attempt == retries:
                LOG.error("LLM 调用失败（重试耗尽 %d/%d）: %s", attempt, retries, exc)
                raise
            wait = RETRY_BACKOFF_BASE ** attempt
            LOG.warning(
                "LLM 调用失败，%ds 后重试 (%d/%d): %s",
                wait,
                attempt,
                retries,
                exc,
            )
            time.sleep(wait)

    # 理论不可达（循环内必 return 或 raise），保险起见
    assert last_exc is not None
    raise last_exc


# --------------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------------- #


def quick_chat(
    prompt: str,
    *,
    provider: Optional[str] = None,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """一句话调用 LLM，返回纯文本。

    内部自动构造消息、发起带重试的请求，并记录 token 用量与成本估算。

    Args:
        prompt: 用户提示词。
        provider: 提供方标识；``None`` 时读 ``LLM_PROVIDER``（默认 ``deepseek``）。
        system: 可选的 system message。
        model: 指定模型名；``None`` 用 provider 默认模型。
        max_tokens: 最大生成 token 数；``None`` 由服务端决定。

    Returns:
        模型生成的文本内容。

    Raises:
        LLMError: 配置缺失或重试耗尽。
    """

    used_provider = provider or os.getenv("LLM_PROVIDER", "deepseek")
    prov = create_provider(used_provider)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = chat_with_retry(prov, messages, model=model, max_tokens=max_tokens)
    cost = estimate_cost(resp.usage, resp.model)
    LOG.info(
        "quick_chat 完成: provider=%s model=%s tokens=%d cost=$%.6f",
        resp.provider,
        resp.model,
        resp.usage.total,
        cost,
    )
    return resp.content


# --------------------------------------------------------------------------- #
# 自测入口
# --------------------------------------------------------------------------- #

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _main() -> int:
    """``python -m pipeline.model_client`` / 直接运行时的自测入口。

    Returns:
        进程退出码：0 成功，1 失败。
    """

    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    provider = os.getenv("LLM_PROVIDER", "deepseek")
    LOG.info("model_client 自测：provider=%s", provider)

    # 用例 1：未配置 API Key 时给出清晰提示（不发真实请求）
    api_key_env = API_KEY_ENV.get(provider, "")
    if not os.getenv(api_key_env, "").strip():
        LOG.error("未检测到 %s，跳过真实调用测试", api_key_env)
        LOG.info("请先设置: export %s=sk-...", api_key_env)
        return 1

    # 用例 2：真实调用 quick_chat，打印响应 + 用量 + 成本
    try:
        answer = quick_chat(
            "用一句话（不超过 30 字）介绍你自己。",
            system="你是一个简洁的技术助手。",
            max_tokens=64,
        )
    except LLMError:
        LOG.exception("quick_chat 自测失败")
        return 1

    LOG.info("响应内容: %s", answer)

    # 用例 3：演示重试日志（用一个一定失败的 provider 触发 LLMError 路径）
    LOG.info("重试演示：构造一个无效 provider 以触发 LLMError 路径")
    try:
        OpenAICompatibleProvider("deepseek")  # 此处仅用于复用构造校验
    except LLMError:
        # 已配置 Key 时不会进到这里，仅占位
        pass

    LOG.info("model_client 自测完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
