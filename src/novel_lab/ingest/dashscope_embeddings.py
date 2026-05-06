"""阿里云 DashScope OpenAI 兼容接口 embedding（LlamaIndex BaseEmbedding）。

需要环境变量：`DASHSCOPE_API_KEY`。
默认端点：`https://dashscope.aliyuncs.com/compatible-mode/v1`
默认模型：`text-embedding-v3`（可按 `EMBEDDING_MODEL` 覆盖）。
"""

from __future__ import annotations

import asyncio
from typing import Any, List

import httpx
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.bridge.pydantic import ConfigDict, Field, PrivateAttr
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

Embedding = List[float]


def _retryable_httpx(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False


class DashScopeCompatibleEmbedding(BaseEmbedding):
    """走 ``/compatible-mode/v1/embeddings``，与 OpenAI SDK 对齐。"""

    model_config = ConfigDict(protected_namespaces=(), arbitrary_types_allowed=True)

    api_key: str = Field(description="DashScope API key")
    base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    model_name: str = Field(default="text-embedding-v3")
    request_timeout_sec: float = Field(default=60.0)
    connect_timeout_sec: float = Field(default=15.0)
    max_input_chars: int = Field(
        default=6000, description="单条输入截断上限，避免超大 payload 被服务端断开"
    )
    _httpx_client: Any = PrivateAttr(default=None)

    # ---------------- core ----------------

    def _get_httpx(self) -> httpx.Client:
        if self._httpx_client is None:
            read_sec = max(float(self.request_timeout_sec), 60.0)
            timeout = httpx.Timeout(
                connect=float(self.connect_timeout_sec),
                read=read_sec,
                write=read_sec,
                pool=30.0,
            )
            self._httpx_client = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return self._httpx_client

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_input_chars:
            return text
        return text[: self.max_input_chars]

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=1, min=2, max=90),
        retry=retry_if_exception(_retryable_httpx),
        reraise=True,
    )
    def _post_embeddings_json(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        r = self._get_httpx().post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        """单次 HTTP 请求嵌入若干条（LlamaIndex 的 batch 已按 ``embed_batch_size`` 切好）。"""
        url = self.base_url.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        truncated = [self._truncate(t) for t in texts]
        payload = {"model": self.model_name, "input": truncated, "encoding_format": "float"}
        data = self._post_embeddings_json(url, headers, payload)
        rows = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        outs: list[list[float]] = []
        for item in rows:
            emb = item.get("embedding")
            if isinstance(emb, list):
                outs.append([float(x) for x in emb])
        if len(outs) != len(texts):
            raise RuntimeError(
                f"DashScope embedding count mismatch: want {len(texts)} got {len(outs)}"
            )
        return outs

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed_many([query])[0]

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return await asyncio.to_thread(self._get_query_embedding, query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed_many([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[Embedding]:
        """覆盖默认的逐条循环，改为按批请求（与 ``get_text_embedding_batch`` 配合）。"""
        return self._embed_many(list(texts))


def build_embeddings_from_env() -> BaseEmbedding:
    """按环境变量组装 embedding."""
    import os

    backend = os.getenv("EMBEDDING_BACKEND", "").lower().strip()
    dash_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if backend == "" and dash_key:
        backend = "dashscope"

    if backend == "dashscope":
        if not dash_key:
            raise ValueError(
                "EMBEDDING_BACKEND=dashscope 但未设置 DASHSCOPE_API_KEY"
            )
        return DashScopeCompatibleEmbedding(
            api_key=dash_key,
            base_url=os.getenv(
                "DASHSCOPE_COMPAT_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            model_name=os.getenv(
                "EMBEDDING_DS_MODEL",
                os.getenv("EMBEDDING_MODEL", "text-embedding-v3"),
            ),
            request_timeout_sec=float(os.getenv("DASHSCOPE_EMBED_TIMEOUT", "180")),
            connect_timeout_sec=float(os.getenv("DASHSCOPE_EMBED_CONNECT_TIMEOUT", "15")),
            max_input_chars=int(os.getenv("DASHSCOPE_EMBED_MAX_CHARS", "6000")),
            embed_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "16")),
        )

    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    hf_model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    return HuggingFaceEmbedding(model_name=hf_model)
