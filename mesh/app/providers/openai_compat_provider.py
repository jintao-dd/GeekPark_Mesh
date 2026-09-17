"""OpenAI 兼容接口实现 —— 覆盖豆包（火山方舟）、DeepSeek、通义、Kimi 等。

只需在 .env 配 BASE_URL / MODEL / API_KEY，不需要改代码。
"""
import json
import logging
import time
import requests
from .base import Provider, LLMError, env

log = logging.getLogger("uvicorn.error")


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

    def _request_payload(self, system: str, user: str, max_tokens: int, *, stream: bool = True) -> dict:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

    def complete_detail(self, system: str, user: str, max_tokens: int = 4000) -> dict:
        """完整 API 响应（诊断用）：content / finish_reason / usage / raw_response。

        使用 SSE 流式接收，便于精确记录 TTFB 和 first_token_ms。
        """
        if not self.is_configured():
            raise LLMError("未配置 MESH_LLM_API_KEY / MESH_LLM_MODEL / MESH_LLM_BASE_URL（见 .env）")
        req = self._request_payload(system, user, max_tokens)
        payload = json.dumps(req, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(1, 4):
            t0 = time.monotonic()
            first_byte_ms: int = -1
            first_token_ms: int = -1
            try:
                r = requests.post(
                    self.base.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                    data=payload,
                    timeout=self.timeout,
                    stream=True,
                )
                # TTFB：从发请求到收到第一个响应字节（状态行/header）的时间
                first_byte_ms = int((time.monotonic() - t0) * 1000)
            except requests.RequestException as e:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                log.info(
                    "openai_compat.request_failed attempt=%s elapsed_ms=%s first_byte_ms=%s first_token_ms=%s error=%s",
                    attempt, elapsed_ms, -1, -1, e,
                )
                last_err = LLMError(f"模型接口网络异常：{e}")
                if attempt < 3:
                    time.sleep(2 * attempt)
                    continue
                raise last_err

            if r.status_code >= 400:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                log.info(
                    "openai_compat.request_error attempt=%s elapsed_ms=%s first_byte_ms=%s first_token_ms=%s status=%s body=%s",
                    attempt, elapsed_ms, first_byte_ms, -1, r.status_code, r.text[:300],
                )
                last_err = LLMError(f"模型接口返回 {r.status_code}：{r.text[:500]}")
                if attempt < 3 and _transient(r.status_code, r.text):
                    time.sleep(2 * attempt)
                    continue
                raise last_err

            # 部分服务端在 stream=True 时仍一次性返回完整 JSON（无 early flush）。
            # 先尝试按完整 JSON 解析；若看起来像 SSE 则回退到 SSE 解析。
            r.encoding = "utf-8"
            text = ""
            finish_reason: str | None = None
            usage: dict[str, Any] = {}
            raw_snippet = ""
            try:
                body = r.json()
                choice = (body.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = str(msg.get("content") or "").strip()
                finish_reason = choice.get("finish_reason")
                usage = body.get("usage") or {}
                model = body.get("model") or self.model
                # 一次性返回时 first_token 不可测量，标记为 -1
                first_token_ms = -1
                raw_snippet = json.dumps(body, ensure_ascii=False)[:400]
            except Exception:
                # 非完整 JSON，尝试 SSE 解析
                chunks: list[dict[str, Any]] = []
                raw_samples: list[str] = []
                last_obj: dict[str, Any] = {}
                chunk_timestamps: list[int] = []
                chunk_content_len: list[int] = []
                chunk_count = 0
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    line = str(raw)
                    if len(raw_samples) < 3:
                        raw_samples.append(line[:500])
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
                    last_obj = obj
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if delta:
                        chunks.append(delta)
                        now_ms = int((time.monotonic() - t0) * 1000)
                        chunk_timestamps.append(now_ms)
                        content_len = len(str(delta.get("content") or ""))
                        reasoning_len = len(str(delta.get("reasoning_content") or ""))
                        chunk_content_len.append(content_len + reasoning_len)
                        chunk_count += 1
                        if first_token_ms < 0 and (delta.get("content") or delta.get("reasoning_content")):
                            first_token_ms = now_ms
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0].get("finish_reason")
                text_parts: list[str] = []
                reasoning_parts: list[str] = []
                for c in chunks:
                    if c.get("content"):
                        text_parts.append(str(c["content"]))
                    if c.get("reasoning_content"):
                        reasoning_parts.append(str(c["reasoning_content"]))
                text = "".join(text_parts)
                model = self.model
                raw_snippet = " | ".join(raw_samples)[:400]
                # 部分厂商把 usage 放在最后一个 chunk 的顶层
                usage = last_obj.get("usage") or {}

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            usage = usage or {}
            log.info(
                "openai_compat.request_ok attempt=%s elapsed_ms=%s first_byte_ms=%s first_token_ms=%s status=%s "
                "model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s "
                "chunk_count=%s chunk_timestamps=%s chunk_content_len=%s raw_snippet=%s",
                attempt,
                elapsed_ms,
                first_byte_ms,
                first_token_ms,
                r.status_code,
                model,
                finish_reason,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
                chunk_count,
                chunk_timestamps,
                chunk_content_len,
                raw_snippet,
            )
            return {
                "content": text,
                "finish_reason": finish_reason,
                "usage": usage,
                "model": model,
                "raw_response": {
                    "snippet": raw_snippet,
                    "finish_reason": finish_reason,
                    "chunk_count": chunk_count,
                    "chunk_timestamps": chunk_timestamps,
                    "chunk_content_len": chunk_content_len,
                },
                "request_payload": req,
            }
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
