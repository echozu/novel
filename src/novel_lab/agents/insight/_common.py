"""Insight agent 公用：上下文摘要构造 + JSON 解析。"""

from __future__ import annotations

import json
from typing import Any

from ...config import genre_anti_patterns, genre_trope_lookup
from ...schema import (
    ChapterAnalysis,
    NovelAnalysis,
    PacingAnalysis,
    PlotLine,
    StyleFingerprint,
)


def build_global_context(analysis: NovelAnalysis, *, top_hooks_n: int = 30) -> dict[str, Any]:
    """生成喂给深度 agent 的全书摘要字典。"""
    chapters = analysis.chapters

    # top hooks
    all_hooks = []
    for ch in chapters:
        for h in ch.hooks:
            all_hooks.append(
                {
                    "chapter_idx": ch.chapter_idx,
                    "type": h.type.value if hasattr(h.type, "value") else str(h.type),
                    "intensity": h.intensity,
                    "summary": h.summary,
                    "snippet": (h.snippet or "")[:120],
                    "position": h.position,
                }
            )
    top_hooks = sorted(all_hooks, key=lambda x: -x["intensity"])[:top_hooks_n]

    # plotlines 简化
    plotlines = []
    for line in analysis.plotlines:
        plotlines.append(
            {
                "line": line.line.value if hasattr(line.line, "value") else str(line.line),
                "name": line.name,
                "summary": line.summary,
                "events_count": len(line.events),
                "key_events": [
                    {"chapter": e.chapter_idx, "title": e.title, "summary": e.summary}
                    for e in line.events[:6]
                ],
            }
        )

    characters = []
    for c in analysis.characters[:8]:
        characters.append(
            {
                "name": c.name,
                "role": c.role,
                "one_liner": c.one_liner,
                "motivation": c.motivation,
                "flaws": c.flaws,
                "arc_summary": [a.state_summary for a in c.arc][:6],
            }
        )

    pacing = analysis.pacing
    pacing_brief = {
        "avg_peak_interval_chapters": pacing.avg_peak_interval_chapters,
        "small_hook_per_chapter": pacing.small_hook_per_chapter,
        "medium_hook_per_5_chapters": pacing.medium_hook_per_5_chapters,
        "big_climax_chapters": pacing.big_climax_chapters,
        "drop_risk_zones": pacing.drop_risk_zones,
    }

    chapter_briefs = [
        {"chapter_idx": ch.chapter_idx, "summary": ch.summary[:80]}
        for ch in chapters
    ]

    style = analysis.style.model_dump() if isinstance(analysis.style, StyleFingerprint) else {}

    return {
        "book_title": analysis.meta.title,
        "genre": analysis.meta.genre,
        "total_chapters": analysis.meta.total_chapters,
        "genre_tropes": genre_trope_lookup(analysis.meta.genre),
        "genre_anti_patterns": genre_anti_patterns(analysis.meta.genre),
        "characters": characters,
        "plotlines": plotlines,
        "pacing": pacing_brief,
        "style": style,
        "top_hooks": top_hooks,
        "chapter_briefs": chapter_briefs,
    }


def safe_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        if opener in text:
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
    return json.loads(text)
