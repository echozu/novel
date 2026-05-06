"""主线 / 三暗线分离 — 卷级 → 全书分层聚合。

策略：
- 章节数 ≤ 80：直接全书一次性送 Claude
- 章节数 > 80：按 50 章一卷分批生成局部 plotlines，再做一次全书归并 LLM call
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import (
    ChapterAnalysis,
    NovelMeta,
    PlotEvent,
    PlotLine,
    PlotLineKind,
)


_BATCH_CHAPTERS = 50


def _briefs(items: list[ChapterAnalysis]) -> list[dict]:
    out = []
    for ch in items:
        hooks_brief = [
            {"type": h.type.value if hasattr(h.type, "value") else str(h.type),
             "intensity": h.intensity,
             "summary": h.summary[:60]}
            for h in ch.hooks
        ]
        line_signals = [
            {
                "line": s.line.value if hasattr(s.line, "value") else str(s.line),
                "status": s.status,
                "event": (s.event or "")[:72],
                "impact": (s.impact or "")[:72],
            }
            for s in (ch.line_signals or [])
        ]
        out.append(
            {
                "chapter_idx": ch.chapter_idx,
                "summary": ch.summary,
                "hooks": hooks_brief,
                "line_signals": line_signals,
            }
        )
    return out


@dataclass
class PlotlineSeparator:
    router: LLMRouter

    async def run(
        self, meta: NovelMeta, chapters_analysis: list[ChapterAnalysis]
    ) -> list[PlotLine]:
        chapters_analysis = sorted(chapters_analysis, key=lambda c: c.chapter_idx)
        if len(chapters_analysis) <= _BATCH_CHAPTERS + 30:
            return await self._one_shot(meta, chapters_analysis)
        # 分批生成局部 plotlines
        partials: list[list[PlotLine]] = []
        for i in range(0, len(chapters_analysis), _BATCH_CHAPTERS):
            batch = chapters_analysis[i : i + _BATCH_CHAPTERS]
            partials.append(await self._one_shot(meta, batch))
        return await self._merge_partials(meta, partials)

    async def _one_shot(
        self, meta: NovelMeta, chapters_analysis: list[ChapterAnalysis]
    ) -> list[PlotLine]:
        payload = {
            "total_chapters": meta.total_chapters,
            "genre": meta.genre,
            "chapter_briefs": _briefs(chapters_analysis),
        }
        user = (
            "# 输入\n```json\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("reduce_plotline") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="reduce",
            json_mode=True,
            temperature=0.3,
            max_tokens=4500,
            system=sys_prompt,
        )
        return self._parse(resp.text)

    async def _merge_partials(
        self, meta: NovelMeta, partials: list[list[PlotLine]]
    ) -> list[PlotLine]:
        # 把每个 partial 序列化成简要描述喂给 Claude，让它合并出全局 plotlines
        partial_briefs = []
        for i, lines in enumerate(partials):
            part = []
            for line in lines:
                part.append(
                    {
                        "line": line.line.value if hasattr(line.line, "value") else str(line.line),
                        "name": line.name,
                        "summary": line.summary,
                        "events": [
                            {"chapter_idx": e.chapter_idx, "title": e.title, "summary": e.summary}
                            for e in line.events[:8]
                        ],
                    }
                )
            partial_briefs.append({"batch_idx": i, "lines": part})
        user = (
            "# 输入：分卷已生成的 plotlines\n```json\n"
            + json.dumps(partial_briefs, ensure_ascii=False)
            + "\n```\n\n# 任务\n请将分卷 plotlines 合并为全书的 1 主线 + 至多 3 暗线（economic/power/emotional）+ 若干 sub。\n"
            "保留所有事件按 chapter_idx 升序合并。严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("reduce_plotline") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="reduce",
            json_mode=True,
            temperature=0.3,
            max_tokens=4500,
            system=sys_prompt,
        )
        merged = self._parse(resp.text)
        if not merged:
            # fallback 把所有 partial 的事件直接拼起来按 line 分组
            return self._fallback_merge(partials)
        return merged

    @staticmethod
    def _fallback_merge(partials: list[list[PlotLine]]) -> list[PlotLine]:
        bucket: dict[str, PlotLine] = {}
        for batch in partials:
            for line in batch:
                key = (
                    line.line.value if hasattr(line.line, "value") else str(line.line)
                ) + "|" + line.name
                if key not in bucket:
                    bucket[key] = PlotLine(
                        line=line.line, name=line.name, summary=line.summary, events=[]
                    )
                bucket[key].events.extend(line.events)
        # 按 chapter_idx 排序事件
        for line in bucket.values():
            line.events.sort(key=lambda e: e.chapter_idx)
        return list(bucket.values())

    @staticmethod
    def _parse(text: str) -> list[PlotLine]:
        try:
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            start = text.find("{")
            end = text.rfind("}")
            data = json.loads(text[start : end + 1] if start != -1 else text)
        except Exception:
            return []

        out: list[PlotLine] = []
        for item in data.get("plotlines", []):
            kind = item.get("line", "main")
            try:
                kind_enum = PlotLineKind(kind)
            except ValueError:
                kind_enum = PlotLineKind.SUB
            events: list[PlotEvent] = []
            for e in item.get("events", []) or []:
                e.setdefault("evidence_chapter", [e.get("chapter_idx", 0)])
                e.setdefault("confidence", 0.7)
                e["line"] = kind_enum.value
                try:
                    events.append(PlotEvent(**e))
                except Exception:
                    continue
            out.append(
                PlotLine(
                    line=kind_enum,
                    name=item.get("name", "未命名"),
                    summary=item.get("summary", ""),
                    events=events,
                    intersections=item.get("intersections", []),
                )
            )
        return out
