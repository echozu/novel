"""评论挖掘相关 Pydantic 数据模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RawComment(BaseModel):
    source: str        # qidian | fanqie
    book_id: str       # 平台 book_id（与 novel-lab book_id 不一定一致）
    chapter_idx: Optional[int] = None
    chapter_title: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    text: str
    likes: int = 0
    timestamp: Optional[str] = None
    is_chapter_comment: bool = True
    raw_url: Optional[str] = None


class CommentCluster(BaseModel):
    """评论聚类结果。"""

    cluster_id: int
    label: str = ""        # 自动起的标签，如"金手指爽点"/"反派降智吐槽"
    sentiment: str = "neu"  # pos | neu | neg
    keywords: list[str] = Field(default_factory=list)
    sample_comments: list[str] = Field(default_factory=list)
    chapter_idx_distribution: dict[int, int] = Field(default_factory=dict)
    weight: float = 0.0    # 聚类大小占比


class ChapterCommentDigest(BaseModel):
    """单章读者反馈摘要 — 喂给 Layer3 校准。"""

    chapter_idx: int
    total_comments: int = 0
    sentiment_score: float = 0.0     # -1 ~ 1
    top_keywords: list[str] = Field(default_factory=list)
    likes_total: int = 0
    is_emotional_peak: bool = False  # 评论密度 / 点赞数显著高于均值
    is_drop_signal: bool = False     # 集中负面关键词，可能为风险章节
    representative_quotes: list[str] = Field(default_factory=list)
