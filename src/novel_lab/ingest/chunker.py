"""章节内语义切块。

策略：
- 章节级 = parent chunk（整章，给 Reduce 用）
- 章节内 = child chunks（256-512 tokens，10-20% overlap，给 RAG 检索用）
- 用 tiktoken 估算 token；中文按字符约 1 char ≈ 0.6 token 兜底
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..schema import Chapter


@dataclass
class ChildChunk:
    chapter_idx: int
    chunk_idx: int
    text: str
    char_count: int
    token_estimate: int


_ENC = None


def _enc() -> tiktoken.Encoding:
    global _ENC
    if _ENC is None:
        try:
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENC = None  # 离线兜底
    return _ENC  # type: ignore[return-value]


def estimate_tokens(text: str) -> int:
    enc = _enc()
    if enc is None:
        # 中文兜底估算：每个字符 ≈ 0.6 token
        return int(len(text) * 0.6)
    return len(enc.encode(text))


_DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def split_chapter(
    chapter: Chapter,
    *,
    chunk_size: int = 384,
    chunk_overlap: int = 64,
) -> list[ChildChunk]:
    """把一章切成若干 child chunk。"""
    if not chapter.text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size * 2,        # length_function 用字符数代理
        chunk_overlap=chunk_overlap * 2,
        length_function=len,
        separators=_DEFAULT_SEPARATORS,
        keep_separator=True,
    )
    pieces = splitter.split_text(chapter.text)
    out: list[ChildChunk] = []
    for ci, piece in enumerate(pieces):
        out.append(
            ChildChunk(
                chapter_idx=chapter.idx,
                chunk_idx=ci,
                text=piece,
                char_count=len(piece),
                token_estimate=estimate_tokens(piece),
            )
        )
    return out


def split_all(chapters: list[Chapter]) -> list[ChildChunk]:
    out: list[ChildChunk] = []
    for ch in chapters:
        out.extend(split_chapter(ch))
    return out
