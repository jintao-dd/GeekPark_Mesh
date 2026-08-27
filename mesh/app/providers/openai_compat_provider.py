"""OpenAI 兼容接口实现 —— 覆盖豆包（火山方舟）、DeepSeek、通义、Kimi 等。

只需在 .env 配 BASE_URL / MODEL / API_KEY，不需要改代码。
"""
import json
import time
import requests
from .base import Provider, LLMError, env


def _transient(status: int, body: str) -> bool:
    """Modelink/Bedrock 偶发拒流：短请求常成功，批量抽取时会抖 400。"""
    if status in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    b = (body or "").lower()
    return any(x in b for x in (
        "bedrockexception",
        "access to bedrock models is not allowed",
        "temporarily unavailable",
        "rate limit",
        "timeout",
        "overloaded",
    ))


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    def __init__(self):
        self.base = env("MESH_LLM_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
        self.model = env("MESH_LLM_MODEL")
        self.key = env("MESH_LLM_API_KEY")
        try:
            self.context_window = int(env("MESH_LLM_CONTEXT_WINDOW") or 128000)
        except ValueError:
            self.context_window = 128_000

    def is_configured(self) -> bool:
        return bool(self.key and self.model and self.base)

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        if not self.is_configured():
            raise LLMError("未配置 MESH_LLM_API_KEY / MESH_LLM_MODEL / MESH_LLM_BASE_URL（见 .env）")
        payload = json.dumps({
            "model": self.model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    self.base.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                    data=payload,
                    timeout=300,
                )
            except requests.RequestException as e:
                last_err = LLMError(f"模型接口网络异常：{e}")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise last_err
            if r.status_code < 400:
                return r.json()["choices"][0]["message"]["content"]
            last_err = LLMError(f"模型接口返回 {r.status_code}：{r.text[:500]}")
            if attempt < 3 and _transient(r.status_code, r.text):
                time.sleep(2 * attempt)
                continue
            raise last_err
        raise last_err or LLMError("模型接口调用失败")

    def stream(self, system: str, user: str, max_tokens: int = 4000):
        if not self.is_configured():
            raise LLMError("未配置 MESH_LLM_API_KEY / MESH_LLM_MODEL / MESH_LLM_BASE_URL（见 .env）")
        payload = json.dumps({
            "model": self.model, "max_tokens": max_tokens, "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, ensure_ascii=False).encode("utf-8")
        try:
            r = requests.post(
                self.base.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                data=payload,
                timeout=300,
                stream=True,
            )
        except requests.RequestException as e:
            raise LLMError(f"模型接口网络异常：{e}")
        if r.status_code >= 400:
            # 错误体也强制按 utf-8，避免乱码进异常信息
            try:
                err_body = r.content.decode("utf-8", errors="replace")[:500]
            except Exception:
                err_body = (r.text or "")[:500]
            raise LLMError(f"模型接口返回 {r.status_code}：{err_body}")
        # 关键：stream 响应若未声明 charset，requests 的 decode_unicode 会按 ISO-8859-1
        # 解 UTF-8 中文，造成「基于 N 条」正常、模型正文乱码。
        r.encoding = "utf-8"
        for raw in r.iter_lines(decode_unicode=False):
            if not raw:
                continue
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw.decode("utf-8", errors="replace")
            if line.startswith("data:"):
                data = line[5:].strip()
            else:
                continue
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                yield text
