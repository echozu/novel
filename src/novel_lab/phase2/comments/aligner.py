"""把读者评论对齐到 novel-lab 章节序号 + 输出每章 digest。"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from ...schema import NovelMeta
from .clean import naive_tokenize
from .schema import ChapterCommentDigest, RawComment


def align_to_chapters(
    comments: list[RawComment],
    meta: NovelMeta,
) -> dict[int, list[RawComment]]:
    """评论 → 章节序号 的映射。

    优先用 chapter_idx；缺失时按 chapter_title 模糊匹配 novel-lab 解析出的标题。
    """
    title_idx: dict[str, int] = {ch.title: ch.idx for ch in meta.chapters}
    out: dict[int, list[RawComment]] = defaultdict(list)
    for c in comments:
        if c.chapter_idx is not None and 0 <= c.chapter_idx < meta.total_chapters:
            out[c.chapter_idx].append(c)
            continue
        if c.chapter_title and c.chapter_title in title_idx:
            out[title_idx[c.chapter_title]].append(c)
            continue
        # 启发式：标题包含"第N章" 通过数字匹配
        if c.chapter_title:
            for ch in meta.chapters:
                if ch.title and ch.title.split()[0] == c.chapter_title.split()[0]:
                    out[ch.idx].append(c)
                    break
    return dict(out)


def digest_per_chapter(
    aligned: dict[int, list[RawComment]],
    meta: NovelMeta,
) -> list[ChapterCommentDigest]:
    """生成每章读者反馈摘要 — 喂给 Layer3 反向校准。"""
    if not aligned:
        return []
    avg_count = sum(len(v) for v in aligned.values()) / max(1, len(aligned))
    total_comments_n = sum(len(v) for v in aligned.values())
    avg_likes_per_comment = sum(
        c.likes for v in aligned.values() for c in v
    ) / max(1, total_comments_n)

    out: list[ChapterCommentDigest] = []
    for ch in meta.chapters:
        comments = aligned.get(ch.idx, [])
        if not comments:
            continue
        text_join = " ".join(c.text for c in comments)
        pos = sum(1 for c in comments if any(k in c.text for k in ("爽", "牛", "上头", "破防")))
        neg = sum(1 for c in comments if any(k in c.text for k in ("无聊", "水", "降智", "拖")))
        senti = (pos - neg) / max(1, len(comments))

        kw_ctr: Counter[str] = Counter()
        for c in comments:
            for tok in naive_tokenize(c.text):
                if len(tok) >= 2:
                    kw_ctr[tok] += 1
        likes_total = sum(c.likes for c in comments)
        avg_likes_here = likes_total / len(comments)
        is_peak = (
            len(comments) > avg_count * 1.3
            or avg_likes_here > avg_likes_per_comment * 1.5
        )
        is_drop = senti < -0.2 and len(comments) >= max(3, int(avg_count * 0.5))
        rep = sorted(comments, key=lambda x: -x.likes)[:3]
        out.append(
            ChapterCommentDigest(
                chapter_idx=ch.idx,
                total_comments=len(comments),
                sentiment_score=round(senti, 3),
                top_keywords=[w for w, _ in kw_ctr.most_common(8)],
                likes_total=likes_total,
                is_emotional_peak=is_peak,
                is_drop_signal=is_drop,
                representative_quotes=[r.text for r in rep],
            )
        )
    return out
