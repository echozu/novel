"""评论清洗：去重 / 长度过滤 / 去机灌水 / 简单分词。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Iterable

import regex

from .schema import RawComment


_BAD_PATTERNS = [
    re.compile(r"^[\s\.。…!！?？~～♡♥💕]*$"),
    re.compile(r"^催更.{0,3}$"),
    re.compile(r"^.{0,2}$"),
    re.compile(r"^更新更新更.+"),
]
_LIKE_FLOOD_THRESHOLD = 200    # 短评赞数飙高视为可疑


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def is_garbage(c: RawComment) -> bool:
    txt = (c.text or "").strip()
    if not txt or len(txt) < 4:
        return True
    if any(p.match(txt) for p in _BAD_PATTERNS):
        return True
    return False


def clean_stream(comments: Iterable[RawComment]) -> list[RawComment]:
    seen: set[str] = set()
    out: list[RawComment] = []
    for c in comments:
        if is_garbage(c):
            continue
        key = f"{c.source}:{c.book_id}:{c.chapter_idx}:{_hash(c.text)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


_PUNCT_RE = regex.compile(r"[\p{P}\s]+", regex.UNICODE)


def naive_tokenize(text: str) -> list[str]:
    """退而求其次的中文分词：按标点切，再以连续 2-4 字为粒度。

    适用于无 jieba 时的兜底；推荐安装 jieba 后替换。
    """
    text = _PUNCT_RE.sub(" ", text)
    parts = text.split()
    grams: list[str] = []
    for p in parts:
        if len(p) <= 4:
            grams.append(p)
        else:
            for i in range(0, len(p) - 1, 2):
                grams.append(p[i : i + 2])
    return [g for g in grams if g]


def keyword_freq(comments: Iterable[RawComment], top: int = 30) -> list[tuple[str, int]]:
    ctr: Counter[str] = Counter()
    for c in comments:
        for tok in naive_tokenize(c.text):
            if len(tok) >= 2:
                ctr[tok] += 1
    return ctr.most_common(top)
