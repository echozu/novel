"""统一 LLM 路由 — DeepSeek-V3 / Claude Sonnet 4.5 / 本地。

特性：
- Role-based 路由：``map`` / ``reduce`` / ``critic`` / ``deep`` 各走最合适的模型
- Tier：basic | balanced | premium | local，决定每个 role 的目标 backend
- aiolimiter RateLimiter（按 backend）
- tenacity 指数退避重试
- token 计费 + SQLite 缓存
- JSON 结构化输出（OpenAI/Anthropic 各自机制）
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from aiolimiter import AsyncLimiter
import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from .cache import LLMCache

Role = Literal["map", "reduce", "critic", "deep"]
Backend = Literal["deepseek", "claude", "openai", "local"]


# 价格表（USD per 1M tokens）；可在 .env 里覆盖。仅用于计费展示。
_PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-haiku-4": (0.80, 4.0),
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "qwen3-32b": (0.0, 0.0),
}


# Tier × Role → backend 映射
_TIER_MATRIX: dict[str, dict[Role, Backend]] = {
    "basic":     {"map": "deepseek", "reduce": "deepseek", "critic": "deepseek", "deep": "deepseek"},
    "balanced":  {"map": "deepseek", "reduce": "claude",   "critic": "claude",   "deep": "claude"},
    "premium":   {"map": "claude",   "reduce": "claude",   "critic": "claude",   "deep": "claude"},
    "local":     {"map": "local",    "reduce": "local",    "critic": "local",    "deep": "claude"},
}


def _is_probably_placeholder_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k or k == "missing":
        return True
    placeholder_tokens = ("xxxx", "your_", "placeholder", "replace_me", "test_key")
    return any(t in k for t in placeholder_tokens)


def _openai_http_timeout() -> httpx.Timeout:
    """避免 LLM 调用无限挂死（map 单章可能上下文较大，read 默认拉长）。"""
    read = float(os.getenv("OPENAI_HTTP_READ_TIMEOUT", os.getenv("OPENAI_HTTP_TIMEOUT", "360")))
    connect = float(os.getenv("OPENAI_HTTP_CONNECT_TIMEOUT", "30"))
    return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)


@dataclass
class LLMResponse:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str = ""
    cached: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def parse_json(self) -> Any:
        """容错 JSON 解析。"""
        text = self.text.strip()
        # 砍掉 ```json fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[: -3]
            text = text.strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        # 提取第一个 JSON 对象/数组
        for opener, closer in (("{", "}"), ("[", "]")):
            if opener in text and closer in text:
                start = text.find(opener)
                end = text.rfind(closer)
                if start != -1 and end != -1 and end > start:
                    candidate = text[start : end + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        continue
        return json.loads(text)


# ---------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------


class LLMRouter:
    """全局 LLM 路由 / 缓存 / 计费 / 限流。"""

    def __init__(
        self,
        *,
        tier: str = "balanced",
        cache_path: Optional[Path] = None,
        deepseek_rpm: int = 60,
        claude_rpm: int = 50,
        openai_rpm: int = 60,
    ) -> None:
        self.tier = tier
        if tier not in _TIER_MATRIX:
            raise ValueError(f"unknown tier {tier}")
        self._role_map = _TIER_MATRIX[tier]
        _timeout = _openai_http_timeout()

        self._deepseek = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", "MISSING"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            timeout=_timeout,
        )
        self._anthropic = AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "MISSING"),
            timeout=_timeout,
        )
        self._openai = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY", "MISSING"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            timeout=_timeout,
        )

        self._models: dict[Backend, str] = {
            "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "claude": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            "openai": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            "local": os.getenv("LOCAL_MODEL", "qwen3-32b"),
        }

        self._limiters: dict[Backend, AsyncLimiter] = {
            "deepseek": AsyncLimiter(deepseek_rpm, 60),
            "claude": AsyncLimiter(claude_rpm, 60),
            "openai": AsyncLimiter(openai_rpm, 60),
            "local": AsyncLimiter(120, 60),
        }

        self._cache = (
            LLMCache(cache_path)
            if cache_path is not None
            else LLMCache(Path(os.getenv("NOVEL_LAB_WORKDIR", "./.workdir")) / "llm_cache.sqlite")
        )

        # 累计计费
        self._total_cost = 0.0
        self._total_in = 0
        self._total_out = 0
        self._cost_lock = asyncio.Lock()

    # ---------------- public ----------------

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def total_tokens(self) -> tuple[int, int]:
        return self._total_in, self._total_out

    def model_for(self, role: Role) -> str:
        backend = self._role_map[role]
        return self._models[backend]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        role: Role = "map",
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        system: Optional[str] = None,
        cache: bool = True,
    ) -> LLMResponse:
        """统一对话补全入口。messages 不含 system；system 单独传。"""
        backend = self._role_map[role]
        model = self._models[backend]
        self._validate_backend_auth(backend)

        cache_payload = {
            "messages": messages,
            "system": system,
            "json_mode": json_mode,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        cache_key = LLMCache.make_key(model, cache_payload)
        if cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return LLMResponse(
                    text=cached["text"],
                    tokens_in=cached.get("tokens_in", 0),
                    tokens_out=cached.get("tokens_out", 0),
                    cost_usd=0.0,
                    model=model,
                    cached=True,
                    raw=cached.get("raw", {}),
                )

        async with self._limiters[backend]:
            async for attempt in AsyncRetrying(
                wait=wait_random_exponential(multiplier=2, max=30),
                stop=stop_after_attempt(5),
                retry=retry_if_exception_type(Exception),
                reraise=True,
            ):
                with attempt:
                    if backend == "deepseek":
                        resp = await self._call_openai_compat(
                            self._deepseek, model, messages, system,
                            json_mode, temperature, max_tokens,
                        )
                    elif backend == "claude":
                        resp = await self._call_claude(
                            model, messages, system, json_mode, temperature, max_tokens,
                        )
                    elif backend == "openai":
                        resp = await self._call_openai_compat(
                            self._openai, model, messages, system,
                            json_mode, temperature, max_tokens,
                        )
                    elif backend == "local":
                        # 本地模型走 OpenAI 兼容端点（vLLM / llama.cpp 都支持）
                        local_client = AsyncOpenAI(
                            api_key="EMPTY",
                            base_url=os.getenv("LOCAL_BASE_URL", "http://localhost:8000/v1"),
                            timeout=_openai_http_timeout(),
                        )
                        resp = await self._call_openai_compat(
                            local_client, model, messages, system,
                            json_mode, temperature, max_tokens,
                        )
                    else:
                        raise RuntimeError(f"unknown backend {backend}")

        # 计费
        in_p, out_p = _PRICES.get(model, (0.0, 0.0))
        cost = (resp.tokens_in * in_p + resp.tokens_out * out_p) / 1_000_000
        resp.cost_usd = cost
        async with self._cost_lock:
            self._total_cost += cost
            self._total_in += resp.tokens_in
            self._total_out += resp.tokens_out

        if cache:
            self._cache.set(
                cache_key, model,
                {
                    "text": resp.text,
                    "tokens_in": resp.tokens_in,
                    "tokens_out": resp.tokens_out,
                    "raw": {},
                },
            )
        return resp

    def _validate_backend_auth(self, backend: Backend) -> None:
        if backend == "claude":
            key = os.getenv("ANTHROPIC_API_KEY", "")
            if _is_probably_placeholder_key(key):
                raise RuntimeError(
                    "Anthropic API key 未配置或仍是占位值；当前 tier 会调用 Claude。"
                    "请设置 ANTHROPIC_API_KEY，或改用 --tier basic。"
                )
        elif backend == "deepseek":
            key = os.getenv("DEEPSEEK_API_KEY", "")
            if _is_probably_placeholder_key(key):
                raise RuntimeError("DeepSeek API key 未配置或仍是占位值。")
        elif backend == "openai":
            key = os.getenv("OPENAI_API_KEY", "")
            if _is_probably_placeholder_key(key):
                raise RuntimeError("OPENAI_API_KEY 未配置或仍是占位值。")

    # ---------------- backend impls ----------------

    async def _call_openai_compat(
        self,
        client: AsyncOpenAI,
        model: str,
        messages: list[dict[str, str]],
        system: Optional[str],
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        full_msgs: list[dict[str, str]] = []
        if system:
            full_msgs.append({"role": "system", "content": system})
        full_msgs.extend(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": full_msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        result = await client.chat.completions.create(**kwargs)
        choice = result.choices[0]
        text = choice.message.content or ""
        usage = getattr(result, "usage", None)
        return LLMResponse(
            text=text,
            tokens_in=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_out=getattr(usage, "completion_tokens", 0) if usage else 0,
            model=model,
        )

    async def _call_claude(
        self,
        model: str,
        messages: list[dict[str, str]],
        system: Optional[str],
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        # Claude 不需要 response_format，直接在 system 里要求 JSON
        sys_text = system or ""
        if json_mode:
            sys_text = (sys_text + "\n\n严格要求：只输出合法 JSON，不要任何解释或 markdown 代码块。").strip()
        result = await self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=sys_text or None,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        )
        text_parts = []
        for block in result.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        return LLMResponse(
            text="".join(text_parts),
            tokens_in=result.usage.input_tokens if result.usage else 0,
            tokens_out=result.usage.output_tokens if result.usage else 0,
            model=model,
        )
