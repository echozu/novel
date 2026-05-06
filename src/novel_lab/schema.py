"""Pydantic 数据模型 — 所有 agent 之间流转的结构化结论都基于此。

设计原则：
- 任何**结论性字段**必须带 `evidence_chapter: list[int]`，便于 Critic 回溯原文
- ID 一律用 stable string（如 ``ch_0042``、``char_主角``）方便跨阶段聚合
- 时间用 chapter_idx（int）而非真实时间，网文不一定有日期
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ConfigDict


# =====================================================================
# 通用基类
# =====================================================================


class Evidenced(BaseModel):
    """带原文证据的结论基类。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    evidence_chapter: list[int] = Field(
        default_factory=list, description="支持本结论的章节序号（0-based）"
    )
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


# =====================================================================
# Layer0 输入 / 切分
# =====================================================================


class Chapter(BaseModel):
    """切分后的单章。"""

    idx: int
    title: str
    text: str
    char_count: int
    token_estimate: int = 0
    volume: Optional[str] = None
    volume_idx: Optional[int] = None


class NovelMeta(BaseModel):
    book_id: str
    title: str
    author: Optional[str] = None
    genre: str = "generic"
    total_chapters: int
    total_chars: int
    chapters: list[Chapter] = Field(default_factory=list)


# =====================================================================
# Layer1 Map：单章产出
# =====================================================================


class CharacterMention(Evidenced):
    """单章里的人物提及。"""

    name: str
    aliases: list[str] = Field(default_factory=list)
    role_hint: Optional[str] = None  # 主角 / 反派 / 配角 / 路人
    actions: list[str] = Field(default_factory=list, description="本章中该人物做了什么")
    emotional_state: Optional[str] = None
    relationship_updates: list[dict[str, str]] = Field(
        default_factory=list,
        description="本章中关系变化，例如 [{'with': '李四', 'change': '由敌转盟'}]",
    )


class Scene(Evidenced):
    """场景片段。"""

    location: str
    time_clue: Optional[str] = None
    summary: str
    participants: list[str] = Field(default_factory=list)


class HookType(str, Enum):
    OPENING = "opening"            # 章首钩子
    CLIFFHANGER = "cliffhanger"    # 章末钩子（最关键）
    REVEAL = "reveal"              # 反转/揭秘
    POWER_UP = "power_up"          # 升级/突破
    FACE_SLAP = "face_slap"        # 打脸
    REVENGE = "revenge"            # 复仇/逆袭
    CP_PROGRESS = "cp_progress"    # CP 推进
    CRISIS = "crisis"              # 危机/绝境
    MYSTERY = "mystery"            # 悬念
    TWIST = "twist"                # 反转


class Hook(Evidenced):
    """钩子/爽点单元 — 决定读者追读率的核心。"""

    type: HookType
    intensity: int = Field(ge=1, le=5, description="爽点/钩子强度，5 最强")
    summary: str
    snippet: str = Field(default="", description="原文片段 ≤ 200 字")
    position: str = Field(default="middle", description="opening | early | middle | late | ending")


class Quote(Evidenced):
    """金句。"""

    text: str
    speaker: Optional[str] = None
    why_good: str = Field(default="", description="为什么是金句：简洁/反差/价值观/有共鸣等")
    qualities: list[str] = Field(default_factory=list, description="句子质量标签：优美/凝练/画面感等")


class TropeHit(Evidenced):
    """命中的套路。"""

    trope_id: str = Field(description="对应 config/genres/*.yaml 的 trope id")
    trope_name: str
    instance_summary: str


class LineSignalKind(str, Enum):
    MAIN = "main"
    ECONOMIC = "economic"
    POWER = "power"
    EMOTIONAL = "emotional"
    SUB = "sub"
    NONE = "none"


class ChapterLineSignal(Evidenced):
    """章节级线路信号：为 Layer2 串线提供可引用锚点。"""

    line: LineSignalKind
    status: str = "advance"  # setup | advance | twist | payoff | cooldown
    event: str = ""
    impact: str = ""
    characters: list[str] = Field(default_factory=list)
    snippet: str = ""


class ChapterAnalysis(BaseModel):
    """单章 Map 阶段总产出。"""

    chapter_idx: int
    summary: str = ""
    mentioned_characters: list[CharacterMention] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    hooks: list[Hook] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    tropes: list[TropeHit] = Field(default_factory=list)
    line_signals: list[ChapterLineSignal] = Field(default_factory=list)
    raw_token_in: int = 0
    raw_token_out: int = 0
    cost_usd: float = 0.0


# =====================================================================
# Layer2 Reduce：卷/全书聚合
# =====================================================================


class CharacterArcPoint(BaseModel):
    """人物弧光 5 状态点之一。"""

    stage: str  # initial | catalyst | turn_25 | turn_50 | turn_75 | final
    chapter_idx: int
    state_summary: str
    psychological_change: str = ""


class CharacterProfile(Evidenced):
    """全书人物档案。"""

    character_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str = "side"  # protagonist | antagonist | side | mentor | love_interest
    one_liner: str = ""
    appearance_chapters: list[int] = Field(default_factory=list)
    arc: list[CharacterArcPoint] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(
        default_factory=list,
        description="[{target, type, evolution: [{chapter, note}]}]",
    )
    motivation: str = ""
    flaws: list[str] = Field(default_factory=list)


class PlotLineKind(str, Enum):
    MAIN = "main"
    ECONOMIC = "economic"   # 暗线：经济 / 资源
    POWER = "power"         # 暗线：权力 / 势力
    EMOTIONAL = "emotional"  # 暗线：情感
    SUB = "sub"             # 其他副线


class PlotEvent(Evidenced):
    chapter_idx: int
    title: str
    summary: str
    line: PlotLineKind
    characters: list[str] = Field(default_factory=list)


class PlotLine(BaseModel):
    line: PlotLineKind
    name: str
    summary: str = ""
    events: list[PlotEvent] = Field(default_factory=list)
    intersections: list[dict[str, Any]] = Field(
        default_factory=list,
        description="与主线的交汇点 [{chapter, with_line, note}]",
    )


class PacingPoint(BaseModel):
    chapter_idx: int
    intensity: float = Field(ge=0.0, le=5.0)
    dominant_hook_types: list[str] = Field(default_factory=list)


class PacingAnalysis(BaseModel):
    curve: list[PacingPoint] = Field(default_factory=list)
    avg_peak_interval_chapters: float = 0.0  # 越小越好，top10% ≈ 1.8
    small_hook_per_chapter: float = 0.0
    medium_hook_per_5_chapters: float = 0.0
    big_climax_chapters: list[int] = Field(default_factory=list)
    drop_risk_zones: list[dict[str, Any]] = Field(
        default_factory=list, description="可能造成弃书的低强度区段"
    )


class StyleFingerprint(BaseModel):
    pov: str = ""                       # 第一人称 / 第三人称限知 / 全知
    tense: str = ""                     # 过去时 / 现在时
    avg_sentence_length: float = 0.0
    dialog_ratio: float = 0.0
    description_density: str = ""       # 浓墨重彩 / 极简白描 / 适中
    rhetoric_devices: list[str] = Field(default_factory=list)
    tone_keywords: list[str] = Field(default_factory=list)
    signature_phrases: list[str] = Field(default_factory=list)
    sample_paragraphs: list[str] = Field(default_factory=list)


# =====================================================================
# Layer3 深度洞察
# =====================================================================


class DifferentiationPoint(Evidenced):
    """与同题材常规作品的差异点。"""

    aspect: str           # 设定 / 人设 / 节奏 / 文风 / 价值观 / 题材融合
    description: str
    why_works: str


class ReaderHookCausation(Evidenced):
    """读者爽点归因。"""

    hook_pattern: str
    psychological_mechanism: str   # 替代体验 / 公平感 / 归属感 / 智识满足 / 情感张力
    typical_chapters: list[int] = Field(default_factory=list)


class DropRisk(Evidenced):
    chapter_range: tuple[int, int]
    reason: str
    severity: int = Field(ge=1, le=5)
    suggestion: str = ""


class InsightCritique(BaseModel):
    """Critic agent 对一条洞察的评判。"""

    target_id: str
    pass_check: bool
    issues: list[str] = Field(default_factory=list)
    revised_text: Optional[str] = None


# =====================================================================
# 最终汇总：分析报告
# =====================================================================


class NovelAnalysis(BaseModel):
    """全书分析最终产出 — 写盘 + 喂下游 AI。"""

    meta: NovelMeta
    chapters: list[ChapterAnalysis] = Field(default_factory=list)
    characters: list[CharacterProfile] = Field(default_factory=list)
    plotlines: list[PlotLine] = Field(default_factory=list)
    pacing: PacingAnalysis = Field(default_factory=PacingAnalysis)
    style: StyleFingerprint = Field(default_factory=StyleFingerprint)

    differentiation: list[DifferentiationPoint] = Field(default_factory=list)
    reader_hooks: list[ReaderHookCausation] = Field(default_factory=list)
    drop_risks: list[DropRisk] = Field(default_factory=list)

    top_quotes: list[Quote] = Field(default_factory=list)
    top_tropes: list[TropeHit] = Field(default_factory=list)

    metrics: dict[str, Any] = Field(default_factory=dict)
    cost_usd_total: float = 0.0
    runtime_seconds: float = 0.0
