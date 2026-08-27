"""Provider 注册表。切换模型只改 .env 里的 MESH_LLM_PROVIDER。"""
from .base import Provider, LLMError, env
from .anthropic_provider import AnthropicProvider
from .openai_compat_provider import OpenAICompatProvider

REGISTRY = {
    "anthropic": AnthropicProvider,
    "openai_compat": OpenAICompatProvider,
    # 豆包 / DeepSeek / 通义 / Kimi 均走 openai_compat，别名方便填写
    "doubao": OpenAICompatProvider,
    "deepseek": OpenAICompatProvider,
    "qwen": OpenAICompatProvider,
}


def get_provider() -> Provider:
    name = (env("MESH_LLM_PROVIDER") or "anthropic").lower()
    cls = REGISTRY.get(name)
    if not cls:
        raise LLMError(f"未知的 MESH_LLM_PROVIDER={name}；可选：{', '.join(sorted(REGISTRY))}")
    return cls()
