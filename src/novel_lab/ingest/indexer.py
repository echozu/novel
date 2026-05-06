"""LlamaIndex Hierarchical Parent-Child 向量库。

设计：
- Parent = 整章（含 chapter_idx 元数据）
- Child  = chunker 切的小块（指向 parent）
- 检索时：先 child 命中 → 取 parent 上下文（RAG 兜底原文细节查询）
- 默认本地 ``BAAI/bge-small-zh-v1.5`` embedding（中文友好且小）
- 持久化到 ChromaDB（便于断点续跑）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..schema import Chapter, NovelMeta
from .chunker import ChildChunk, split_chapter


if TYPE_CHECKING:
    from llama_index.core.schema import NodeWithScore  # noqa: F401


class ChapterTextOnlyIndex:
    """不构建向量库：仅保留章节原文，供 Critic / 其它需 ``parent_chapter_text`` 的阶段使用。"""

    def __init__(self, meta: NovelMeta, workdir: Path) -> None:
        self.meta = meta
        self.workdir = workdir
        self.persist_dir = workdir / meta.book_id / "index_skipped"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._chapter_text: dict[int, str] = {ch.idx: ch.text for ch in meta.chapters}

    def build(self, *, rebuild: bool = False) -> ChapterTextOnlyIndex:
        return self

    def retrieve(self, query: str, top_k: int = 8) -> list[Any]:
        return []

    def parent_chapter_text(self, chapter_idx: int) -> str:
        return self._chapter_text.get(chapter_idx, "")

    def retrieve_with_parents(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        return []


class NovelIndex:
    """对单本书构建/复用向量索引。"""

    def __init__(
        self,
        meta: NovelMeta,
        workdir: Path,
        embed_model_name: Optional[str] = None,
    ) -> None:
        # 惰性导入，避免无 chromadb/llama-index 时 import 链整体失败
        import chromadb
        from llama_index.core import StorageContext
        from llama_index.vector_stores.chroma import ChromaVectorStore

        self._chromadb = chromadb
        self._StorageContext = StorageContext

        self.meta = meta
        self.workdir = workdir
        self.persist_dir = workdir / meta.book_id / "chroma"
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        if embed_model_name:
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding

            self.embed_model = HuggingFaceEmbedding(model_name=embed_model_name)
        else:
            from .dashscope_embeddings import build_embeddings_from_env

            self.embed_model = build_embeddings_from_env()

        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._collection = self._client.get_or_create_collection(meta.book_id)
        self._vstore = ChromaVectorStore(chroma_collection=self._collection)
        self._storage = StorageContext.from_defaults(vector_store=self._vstore)
        self._index: Optional[Any] = None
        self._chapter_text: dict[int, str] = {ch.idx: ch.text for ch in meta.chapters}

    # ---------------- build ----------------

    def build(self, *, rebuild: bool = False) -> "NovelIndex":
        from llama_index.core import VectorStoreIndex
        from llama_index.core.schema import TextNode
        from llama_index.vector_stores.chroma import ChromaVectorStore

        if os.getenv("NOVEL_LAB_FORCE_REINDEX", "").lower() in ("1", "true", "yes"):
            rebuild = True

        if self._collection.count() > 0 and not rebuild:
            self._index = VectorStoreIndex.from_vector_store(
                vector_store=self._vstore, embed_model=self.embed_model
            )
            return self

        nodes: list[TextNode] = []
        for ch in self.meta.chapters:
            chunks: list[ChildChunk] = split_chapter(ch)
            for c in chunks:
                node = TextNode(
                    text=c.text,
                    id_=f"{self.meta.book_id}::ch{c.chapter_idx:05d}::p{c.chunk_idx:03d}",
                    metadata={
                        "book_id": self.meta.book_id,
                        "chapter_idx": c.chapter_idx,
                        "chapter_title": ch.title,
                        "chunk_idx": c.chunk_idx,
                        "char_count": c.char_count,
                    },
                )
                nodes.append(node)

        if rebuild:
            try:
                self._client.delete_collection(self.meta.book_id)
            except Exception:
                pass
            self._collection = self._client.get_or_create_collection(self.meta.book_id)
            self._vstore = ChromaVectorStore(chroma_collection=self._collection)
            self._storage = self._StorageContext.from_defaults(vector_store=self._vstore)

        self._index = VectorStoreIndex(
            nodes=nodes, storage_context=self._storage, embed_model=self.embed_model
        )
        return self

    # ---------------- query ----------------

    def retrieve(self, query: str, top_k: int = 8) -> list[Any]:
        if self._index is None:
            self.build()
        retriever = self._index.as_retriever(similarity_top_k=top_k)  # type: ignore[union-attr]
        return retriever.retrieve(query)

    def parent_chapter_text(self, chapter_idx: int) -> str:
        return self._chapter_text.get(chapter_idx, "")

    def retrieve_with_parents(self, query: str, top_k: int = 6) -> list[dict]:
        """兼顾精度（child 命中）+ 上下文（parent 章节）。"""
        hits = self.retrieve(query, top_k=top_k)
        out: list[dict] = []
        seen: set[int] = set()
        for h in hits:
            cidx = int(h.node.metadata.get("chapter_idx", -1))
            if cidx in seen or cidx < 0:
                continue
            seen.add(cidx)
            out.append(
                {
                    "chapter_idx": cidx,
                    "chapter_title": h.node.metadata.get("chapter_title", ""),
                    "snippet": h.node.get_content(),
                    "score": float(h.score or 0.0),
                    "parent_text": self.parent_chapter_text(cidx),
                }
            )
        return out
