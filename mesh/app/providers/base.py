"""模型适配层 · 抽象接口

业务代码（llm.py 及其调用方）只依赖本接口，**不得 import 任何厂商 SDK**。
新增一家模型 = 新增一个 Provider 子类 + 在 __init__.py 注册，业务代码零改动。
"""
from __future__ import annotations
import os


class LLMError(RuntimeError):
    pass


class Provider:
    name = "base"
    #: 上下文窗口（token 估算用）。切分策略必须读它，不许写死"一次塞完"。
    context_window = 128_000

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        raise NotImplementedError

    def stream(self, system: str, user: str, max_tokens: int = 4000):
        """流式生成：yield 文本片段。默认回退为整段 complete（兼容未实现流式的 provider）。"""
        text = self.complete(system, user, max_tokens=max_tokens)
        if text:
            yield text

    # 便于诊断：后台"规则提示词"页会显示当前 provider 与模型
    def describe(self) -> dict:
        return {"provider": self.name, "model": getattr(self, "model", "?"),
                "context_window": self.context_window, "configured": self.is_configured()}

    def is_configured(self) -> bool:
        return False


def env(key: str, default: str = "") -> str:
    return (os.environ.get(key, default) or "").strip()
