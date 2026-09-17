"""OpenAI 兼容接口实现 —— 覆盖豆包（火山方舟）、DeepSeek、通义、Kimi 等。

只需在 .env 配 BASE_URL / MODEL / API_KEY，不需要改代码。
"""
import json
import time
import requests
from .base import Provider, LLMError, env


def _transient(status: int, body: str) -> bool:
    """Modelink/Bedrock 偶发拒流：短请求常成功，批量抽取时会抖 400。"""
    if status in (408, 409, 425, 429, 500, 502, 503, 504, 524):
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
        try:
            self.timeout = int(env("MESH_LLM_TIMEOUT") or 600)
        except ValueError:
            self.timeout = 600

    def is_configured(self) -> bool:
        return bool(self.key and self.model and self.base)

    def _request_payload(self, system: str, user: str, max_tokens: int) -> dict:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    def complete_detail(self, system: str, user: str, max_tokens: int = 4000) -> dict:
        """完整 API 响应（诊断用）：content / finish_reason / usage / raw_response。"""
        if not self.is_configured():
            raise LLMError("未配置 MESH_LLM_API_KEY / MESH_LLM_MODEL / MESH_LLM_BASE_URL（见 .env）")
        req = self._request_payload(system, user, max_tokens)
        payload = json.dumps(req, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(1, 4):
            t0 = time.monotonic()
            try:
                r = requests.post(
                    self.base.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                    data=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logging.getLogger("mesh.llm").info(
                    "openai_compat.request_failed attempt=%s elapsed_ms=%s error=%s",
                    attempt, elapsed_ms, e,
                )
                last_err = LLMError(f"模型接口网络异常：{e}")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise last_err
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if r.status_code < 400:
                body = r.json()
                choice = (body.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                usage = body.get("usage") or {}
                logging.getLogger("mesh.llm").info(
                    "openai_compat.request_ok attempt=%s elapsed_ms=%s status=%s "
                    "model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                    attempt,
                    elapsed_ms,
                    r.status_code,
                    body.get("model") or self.model,
                    choice.get("finish_reason"),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                )
                return {
                    "content": msg.get("content") or "",
                    "finish_reason": choice.get("finish_reason"),
                    "usage": body.get("usage"),
                    "model": body.get("model") or self.model,
                    "raw_response": body,
                    "request_payload": req,
                }
            logging.getLogger("mesh.llm").info(
                "openai_compat.request_error attempt=%s elapsed_ms=%s status=%s body=%s",
                attempt, elapsed_ms, r.status_code, r.text[:300],
            )
            last_err = LLMError(f"模型接口返回 {r.status_code}：{r.text[:500]}")
            if attempt < 3 and _transient(r.status_code, r.text):
                time.sleep(2 * attempt)
                continue
            raise last_err
        raise last_err or LLMError("模型接口调用失败")

    def complete(self, system: str, user: str, max_tokens: int = 4000) -> str:
        return self.complete_detail(system, user, max_tokens=max_tokens)["content"]

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
                timeout=self.timeout,
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
