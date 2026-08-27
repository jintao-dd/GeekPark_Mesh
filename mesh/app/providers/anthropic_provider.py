"""Anthropic 实现"""
from .base import Provider, LLMError, env


class AnthropicProvider(Provider):
    name = "anthropic"
    context_window = 200_000

    def __init__(self):
        self.model = env("MESH_LLM_MODEL") or env("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
        self.key = env("ANTHROPIC_API_KEY")

    def is_configured(self) -> bool:
        return bool(self.key) and self.key.isascii() and self.key.startswith("sk-ant")

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        if not self.is_configured():
            raise LLMError("未配置有效的 ANTHROPIC_API_KEY（在 .env 里填写以 sk-ant 开头的密钥后重启）")
        from anthropic import Anthropic  # 仅本文件可 import 厂商 SDK
        client = Anthropic(api_key=self.key)
        msg = client.messages.create(model=self.model, max_tokens=max_tokens,
                                     system=system, messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def stream(self, system: str, user: str, max_tokens: int = 4000):
        if not self.is_configured():
            raise LLMError("未配置有效的 ANTHROPIC_API_KEY（在 .env 里填写以 sk-ant 开头的密钥后重启）")
        from anthropic import Anthropic
        client = Anthropic(api_key=self.key)
        with client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                if text:
                    yield text
