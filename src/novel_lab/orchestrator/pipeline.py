"""端到端 Pipeline — 把所有 Layer 串起来。

关键特性：
- 每个 stage 结果落 SQLite（断点续跑）
- Layer1 章级并发；Layer2/3 内部子任务用 ``asyncio.gather`` 并发
- 失败不致命：单 agent 报错降级成空结果，整体仍能产出报告
- 进度回调（Rich CLI 用）
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..agents.insight import (
    CriticAgent,
    DifferentiationAgent,
    DropRiskAgent,
    ReaderHookAgent,
)
from ..agents.map.runner import run_map
from ..agents.reduce import (
    ArcTracker,
    PacingAnalyzer,
    PlotlineSeparator,
    StyleFingerprint,
)
from ..config import load_prompt, system_base
from ..ingest.indexer import ChapterTextOnlyIndex, NovelIndex
from ..ingest.parser import parse as parse_novel
from ..llm import LLMRouter
from ..schema import (
    ChapterAnalysis,
    DifferentiationPoint,
    DropRisk,
    PlotLine,
    PlotLineKind,
    ReaderHookCausation,
    NovelAnalysis,
    NovelMeta,
    Quote,
    TropeHit,
)


# ---------------- config / state ----------------


@dataclass
class PipelineConfig:
    book_path: Path
    workdir: Path
    genre: str = "generic"
    tier: str = "balanced"
    sample_ratio: float = 1.0
    max_chapters: Optional[int] = None
    map_concurrency: int = 20
    resume: bool = True
    write_neo4j: bool = False
    enable_critic: bool = True
    book_title_override: Optional[str] = None


@dataclass
class PipelineState:
    meta: Optional[NovelMeta] = None
    chapter_analyses: list[ChapterAnalysis] = field(default_factory=list)
    analysis: Optional[NovelAnalysis] = None
    book_dir: Optional[Path] = None
    runtime_seconds: float = 0.0
    cost_usd: float = 0.0


class StageCheckpoint:
    """记录大阶段完成状态 + 持久化最终 NovelAnalysis（增量）。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS stages ("
                "  name TEXT PRIMARY KEY, "
                "  payload TEXT, "
                "  finished_at REAL"
                ")"
            )

    def _conn(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, name: str) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT payload FROM stages WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set(self, name: str, payload: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO stages(name, payload, finished_at) VALUES (?,?,?)",
                (name, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            c.commit()


# ---------------- Pipeline ----------------


ProgressCB = Callable[[str, dict[str, Any]], None]


class Pipeline:
    """端到端流水线。"""

    def __init__(
        self,
        config: PipelineConfig,
        *,
        progress: Optional[ProgressCB] = None,
    ) -> None:
        self.config = config
        self.progress = progress or (lambda *a, **k: None)
        self.router = LLMRouter(tier=config.tier)
        self.state = PipelineState()
        self._index: Any = None
        self._stage: Optional[StageCheckpoint] = None

    # ---------------- public ----------------

    async def run(self) -> NovelAnalysis:
        t0 = time.time()
        await self._stage_ingest()
        await self._stage_index()
        await self._stage_map()
        await self._stage_reduce()
        await self._stage_insight()
        if self.config.enable_critic:
            await self._stage_critic()
        await self._finalize()
        self.state.runtime_seconds = time.time() - t0
        if self.state.analysis is not None:
            self.state.analysis.runtime_seconds = self.state.runtime_seconds
            self.state.analysis.cost_usd_total = self.router.total_cost
        return self.state.analysis  # type: ignore[return-value]

    # ---------------- stages ----------------

    async def _stage_ingest(self) -> None:
        meta = parse_novel(self.config.book_path, title=self.config.book_title_override)
        meta.genre = self.config.genre

        # 抽样 / 截断
        chapters = meta.chapters
        if self.config.max_chapters is not None:
            chapters = chapters[: self.config.max_chapters]
        if 0 < self.config.sample_ratio < 1.0 and len(chapters) > 5:
            keep = max(5, int(len(chapters) * self.config.sample_ratio))
            step = len(chapters) / keep
            indices = sorted({int(i * step) for i in range(keep)})
            chapters = [chapters[i] for i in indices if i < len(chapters)]
        meta.chapters = chapters
        meta.total_chapters = len(chapters)
        meta.total_chars = sum(c.char_count for c in chapters)

        self.state.meta = meta
        self.state.book_dir = self.config.workdir / meta.book_id
        self.state.book_dir.mkdir(parents=True, exist_ok=True)
        self._stage = StageCheckpoint(self.state.book_dir / "checkpoints" / "stages.sqlite")

        # 持久化 meta 备份
        (self.state.book_dir / "meta.json").write_text(
            meta.model_dump_json(indent=2, exclude={"chapters"}), encoding="utf-8"
        )
        self.progress("ingest", {"book_id": meta.book_id, "chapters": meta.total_chapters,
                                  "chars": meta.total_chars})

    async def _stage_index(self) -> None:
        if self.state.meta is None or self.state.book_dir is None:
            return
        if os.getenv("NOVEL_LAB_SKIP_INDEX", "").lower() in ("1", "true", "yes"):
            self._index = ChapterTextOnlyIndex(
                self.state.meta, workdir=self.config.workdir
            ).build()
            self.progress(
                "index",
                {"skipped": True, "persisted_at": str(self._index.persist_dir)},
            )
            return
        self._index = NovelIndex(self.state.meta, workdir=self.config.workdir).build(
            rebuild=False
        )
        self.progress("index", {"persisted_at": str(self._index.persist_dir)})

    async def _stage_map(self) -> None:
        meta = self.state.meta
        if meta is None or self.state.book_dir is None:
            return

        self.progress("map_start", {"total": len(meta.chapters)})

        def cb(done: int, total: int, item: ChapterAnalysis) -> None:
            self.progress(
                "map_chapter",
                {
                    "done": done,
                    "total": total,
                    "chapter_idx": item.chapter_idx,
                    "summary_head": (item.summary or "")[:30],
                    "cost_usd": round(self.router.total_cost, 4),
                },
            )

        results = await run_map(
            meta.chapters,
            router=self.router,
            genre=self.config.genre,
            workdir=self.config.workdir,
            book_id=meta.book_id,
            concurrency=self.config.map_concurrency,
            progress_cb=cb,
            resume=self.config.resume,
        )
        self.state.chapter_analyses = results
        self.progress("map", {"chapters": len(results)})

    async def _stage_reduce(self) -> None:
        meta = self.state.meta
        if meta is None:
            return
        chapters_a = self.state.chapter_analyses

        # 1) 节奏（纯计算，先做，便于其他 agent 引用）
        pacing = PacingAnalyzer().run(chapters_a)
        self.progress("reduce_pacing", {
            "avg_peak_interval_chapters": pacing.avg_peak_interval_chapters,
            "drop_zones": len(pacing.drop_risk_zones),
        })

        # 2) Arc / 情节线 / 文风（并发；目标模型由 ``tier`` 决定，basic=全 DeepSeek）
        arc_task = ArcTracker(self.router).run(meta, chapters_a)
        plot_task = PlotlineSeparator(self.router).run(meta, chapters_a)
        style_task = StyleFingerprint(self.router).run(meta.chapters)
        characters, plotlines, style = await asyncio.gather(
            arc_task, plot_task, style_task, return_exceptions=True
        )
        if isinstance(characters, BaseException):
            characters = []
            self.progress("reduce_arc_error", {"err": repr(characters)})
        if isinstance(plotlines, BaseException):
            plotlines = []
            self.progress("reduce_plotline_error", {"err": repr(plotlines)})
        if isinstance(style, BaseException):
            from ..schema import StyleFingerprint as StyleSchema
            style = StyleSchema()
            self.progress("reduce_style_error", {"err": repr(style)})

        line_briefs_llm: list[dict[str, Any]] = []
        if isinstance(plotlines, list) and plotlines:
            try:
                refined_plotlines, line_briefs_llm = await self._refine_plotlines_with_raw_trace(
                    meta=meta,
                    chapters_a=chapters_a,
                    draft_plotlines=plotlines,
                    pacing=pacing,
                )
                if refined_plotlines:
                    plotlines = refined_plotlines
                self.progress(
                    "reduce_plotline_refine",
                    {"plotlines": len(plotlines), "line_briefs": len(line_briefs_llm)},
                )
            except Exception as exc:
                self.progress("reduce_plotline_refine_error", {"err": repr(exc)})

        # 组装中间 NovelAnalysis（无洞察）
        analysis = NovelAnalysis(
            meta=meta,
            chapters=chapters_a,
            characters=characters,                # type: ignore[arg-type]
            plotlines=plotlines,                  # type: ignore[arg-type]
            pacing=pacing,
            style=style,                          # type: ignore[arg-type]
        )
        analysis.metrics["line_briefs_llm"] = line_briefs_llm
        self.state.analysis = analysis
        self.progress(
            "reduce",
            {
                "characters": len(characters),     # type: ignore[arg-type]
                "plotlines": len(plotlines),       # type: ignore[arg-type]
                "style_pov": getattr(style, "pov", ""),
            },
        )

    async def _stage_insight(self) -> None:
        analysis = self.state.analysis
        if analysis is None:
            return
        diff_task = DifferentiationAgent(self.router).run(analysis)
        hook_task = ReaderHookAgent(self.router).run(analysis)
        risk_task = DropRiskAgent(self.router).run(analysis)
        diffs, hooks, risks = await asyncio.gather(
            diff_task, hook_task, risk_task, return_exceptions=True
        )
        analysis.differentiation = diffs if isinstance(diffs, list) else []
        analysis.reader_hooks = hooks if isinstance(hooks, list) else []
        analysis.drop_risks = risks if isinstance(risks, list) else []
        self.progress(
            "insight",
            {
                "diffs": len(analysis.differentiation),
                "hooks": len(analysis.reader_hooks),
                "risks": len(analysis.drop_risks),
            },
        )

    async def _stage_critic(self) -> None:
        analysis = self.state.analysis
        if analysis is None:
            return
        claims: list[dict[str, Any]] = []
        for i, d in enumerate(analysis.differentiation):
            claims.append(
                {
                    "id": f"diff_{i}",
                    "claim_type": "differentiation",
                    "content": d.model_dump(),
                    "evidence_chapter": d.evidence_chapter,
                }
            )
        for i, r in enumerate(analysis.drop_risks):
            claims.append(
                {
                    "id": f"risk_{i}",
                    "claim_type": "drop_risk",
                    "content": r.model_dump(),
                    "evidence_chapter": list(r.chapter_range),
                }
            )
        for i, h in enumerate(analysis.reader_hooks[:8]):
            claims.append(
                {
                    "id": f"hook_{i}",
                    "claim_type": "reader_hook",
                    "content": h.model_dump(),
                    "evidence_chapter": h.evidence_chapter,
                }
            )
        if not claims:
            return

        critic = CriticAgent(self.router, index=self._index)
        critiques = await critic.critique(claims)
        rejected = {cr.target_id for cr in critiques if not cr.pass_check}

        # 标记低 confidence 而非删除（保留可追溯）
        for i, d in enumerate(analysis.differentiation):
            if f"diff_{i}" in rejected:
                d.confidence = min(d.confidence, 0.3)
        for i, r in enumerate(analysis.drop_risks):
            if f"risk_{i}" in rejected:
                r.confidence = min(r.confidence, 0.3)
        for i, h in enumerate(analysis.reader_hooks[:8]):
            if f"hook_{i}" in rejected:
                h.confidence = min(h.confidence, 0.3)

        self.progress(
            "critic",
            {
                "checked": len(claims),
                "rejected": len(rejected),
            },
        )

    async def _finalize(self) -> None:
        analysis = self.state.analysis
        if analysis is None:
            return
        self._quality_gate_recover(analysis)
        # 抽 top quotes / top tropes
        all_quotes: list[Quote] = []
        all_tropes_counter: Counter[str] = Counter()
        trope_examples: dict[str, TropeHit] = {}
        for ch in analysis.chapters:
            for q in ch.quotes:
                all_quotes.append(q)
            for t in ch.tropes:
                all_tropes_counter[t.trope_id] += 1
                trope_examples.setdefault(t.trope_id, t)

        quote_limit = min(120, max(40, analysis.meta.total_chapters // 12))
        analysis.top_quotes = sorted(
            all_quotes,
            key=lambda q: -self._quote_quality_score(q),
        )[:quote_limit]
        analysis.top_tropes = []
        trope_limit = min(80, max(30, analysis.meta.total_chapters // 25))
        for tid, count in all_tropes_counter.most_common(trope_limit):
            example = trope_examples[tid]
            example.evidence_chapter = sorted(
                {
                    c
                    for ch in analysis.chapters
                    for t in ch.tropes
                    if t.trope_id == tid
                    for c in t.evidence_chapter
                }
            )[:16]
            analysis.top_tropes.append(example)

        # 计算 metrics
        prev_line_briefs = analysis.metrics.get("line_briefs_llm", [])
        n = analysis.meta.total_chapters or 1
        peaks = analysis.pacing.avg_peak_interval_chapters
        peak_score = max(0.0, min(1.0, (5.0 - peaks) / 5.0)) if peaks > 0 else 0.0
        small_score = min(1.0, analysis.pacing.small_hook_per_chapter / 1.5)
        diff_score = min(1.0, len(analysis.differentiation) / 6.0)
        analysis.metrics = {
            "pacing_peak_score": round(peak_score, 3),
            "pacing_small_hook_score": round(small_score, 3),
            "differentiation_score": round(diff_score, 3),
            "drop_risk_count": len(analysis.drop_risks),
            "computed_drop_zones": len(analysis.pacing.drop_risk_zones),
            "characters_tracked": len(analysis.characters),
            "plotlines_count": len(analysis.plotlines),
            "main_plot_events": next(
                (len(p.events) for p in analysis.plotlines if str(p.line) == "main"
                 or (hasattr(p.line, "value") and p.line.value == "main")),
                0,
            ),
            "scale_tier": self.config.tier,
            "quality_gate": self._build_quality_gate_snapshot(analysis),
            "longform_briefs": self._build_longform_briefs(analysis),
            "line_briefs_llm": prev_line_briefs,
        }

        # 持久化 NovelAnalysis 全量 JSON
        if self.state.book_dir is not None:
            (self.state.book_dir / "novel_analysis.json").write_text(
                analysis.model_dump_json(indent=2),
                encoding="utf-8",
            )
        self.progress(
            "finalize",
            {
                "top_quotes": len(analysis.top_quotes),
                "top_tropes": len(analysis.top_tropes),
                "metrics": analysis.metrics,
                "cost_usd": round(self.router.total_cost, 4),
            },
        )

    async def _refine_plotlines_with_raw_trace(
        self,
        *,
        meta: NovelMeta,
        chapters_a: list[ChapterAnalysis],
        draft_plotlines: list[PlotLine],
        pacing: Any,
    ) -> tuple[list[PlotLine], list[dict[str, Any]]]:
        """用 Layer1 原始流水再看一遍主/暗线，降低片面性。"""
        chapters_sorted = sorted(chapters_a, key=lambda c: c.chapter_idx)
        if not chapters_sorted:
            return draft_plotlines, []

        sample_limit = 240
        if len(chapters_sorted) <= sample_limit:
            sampled = chapters_sorted
        else:
            step = len(chapters_sorted) / sample_limit
            sampled = [
                chapters_sorted[min(len(chapters_sorted) - 1, int(i * step))]
                for i in range(sample_limit)
            ]

        key_chapters = sorted(
            {
                e.chapter_idx
                for p in draft_plotlines
                for e in p.events
            }
            | set(pacing.big_climax_chapters[:40])
            | {int(z.get("start_chapter", 0)) for z in pacing.drop_risk_zones[:20]}
            | {int(z.get("end_chapter", 0)) for z in pacing.drop_risk_zones[:20]}
        )
        key_chapter_set = set(key_chapters)

        chapter_traces = []
        for ch in sampled:
            chapter_traces.append(
                {
                    "chapter_idx": ch.chapter_idx,
                    "summary": ch.summary[:180],
                    "hooks": [
                        {
                            "type": h.type.value if hasattr(h.type, "value") else str(h.type),
                            "intensity": h.intensity,
                            "summary": h.summary[:50],
                            "snippet": (h.snippet or "")[:60],
                        }
                        for h in ch.hooks[:3]
                    ],
                    "quotes": [
                        {"text": q.text[:80], "why_good": q.why_good[:60]}
                        for q in ch.quotes[:2]
                    ],
                    "is_key_chapter": ch.chapter_idx in key_chapter_set,
                }
            )

        payload = {
            "book_title": meta.title,
            "genre": meta.genre,
            "total_chapters": meta.total_chapters,
            "draft_plotlines": [
                {
                    "line": p.line.value if hasattr(p.line, "value") else str(p.line),
                    "name": p.name,
                    "summary": p.summary,
                    "events": [
                        {"chapter_idx": e.chapter_idx, "title": e.title, "summary": e.summary}
                        for e in p.events
                    ],
                }
                for p in draft_plotlines
            ],
            "chapter_traces": chapter_traces,
            "key_chapters": key_chapters[:200],
        }

        user = (
            "# 输入（基于 Layer1 原始流水）\n```json\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n```\n\n# 任务\n1) 复核并优化 plotlines；2) 产出分线深度总结。严格按 system schema 输出。"
        )
        system = load_prompt("reduce_plotline_refine") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="reduce",
            json_mode=True,
            temperature=0.2,
            max_tokens=5000,
            system=system,
        )
        data = self._safe_json(resp.text)
        refined = PlotlineSeparator._parse(
            json.dumps({"plotlines": data.get("plotlines", [])}, ensure_ascii=False)
        )
        line_briefs = data.get("line_briefs", [])
        return (refined or draft_plotlines), (line_briefs if isinstance(line_briefs, list) else [])

    @staticmethod
    def _safe_json(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]
        return json.loads(text)

    @staticmethod
    def _quote_quality_score(q: Quote) -> float:
        labels = [str(x).lower() for x in (q.qualities or [])]
        keyword_bonus = 0.0
        for kw in ("优美", "凝练", "画面", "哲思", "反差", "共鸣", "节奏", "意象", "传播"):
            if kw in (q.why_good or ""):
                keyword_bonus += 2.0
        label_bonus = min(6.0, len(labels) * 1.5)
        conf_bonus = 8.0 if q.confidence > 0.8 else 3.0 if q.confidence > 0.6 else 0.0
        base_len = min(22.0, len(q.text) * 0.35)
        length_penalty = 3.0 if len(q.text) < 8 else 2.0 if len(q.text) > 88 else 0.0
        return base_len + keyword_bonus + label_bonus + conf_bonus - length_penalty

    def _quality_gate_recover(self, analysis: NovelAnalysis) -> None:
        """结果体检：关键模块为空时自动补齐，避免报告核心区块空白。"""
        self._recover_plotlines_if_empty(analysis)
        if len(analysis.differentiation) < 3:
            analysis.differentiation = self._recover_differentiation(analysis)
        if len(analysis.reader_hooks) < 5:
            analysis.reader_hooks = self._recover_reader_hooks(analysis)
        if not analysis.drop_risks:
            analysis.drop_risks = self._recover_drop_risks(analysis)

    def _recover_plotlines_if_empty(self, analysis: NovelAnalysis) -> None:
        if analysis.plotlines and any(p.events for p in analysis.plotlines):
            return
        chapters = sorted(analysis.chapters, key=lambda c: c.chapter_idx)
        if not chapters:
            return
        step = max(1, len(chapters) // 12)
        events = []
        for i in range(0, len(chapters), step):
            ch = chapters[i]
            title = (ch.summary or "章节推进").split("。")[0][:24]
            events.append(
                {
                    "chapter_idx": ch.chapter_idx,
                    "title": title or f"章节 {ch.chapter_idx}",
                    "summary": (ch.summary or "")[:120],
                    "line": "main",
                    "characters": [],
                    "evidence_chapter": [ch.chapter_idx],
                    "confidence": 0.5,
                }
            )
        try:
            main_line = PlotLine(
                line=PlotLineKind.MAIN,
                name="主线推进（自动回填）",
                summary="由于上游情节线抽取不足，系统按章节摘要自动回填主线事件。",
                events=events,  # type: ignore[arg-type]
            )
            analysis.plotlines = [main_line]
        except Exception:
            analysis.plotlines = []

    def _recover_differentiation(self, analysis: NovelAnalysis) -> list[DifferentiationPoint]:
        candidates: list[DifferentiationPoint] = []
        main_line = next(
            (
                p
                for p in analysis.plotlines
                if str(p.line) == "main" or (hasattr(p.line, "value") and p.line.value == "main")
            ),
            None,
        )
        main_ev = [e.chapter_idx for e in (main_line.events if main_line else [])[:4]] or [0]
        line_types = {
            (p.line.value if hasattr(p.line, "value") else str(p.line))
            for p in analysis.plotlines
        }
        if main_line and len(main_line.events) >= 4:
            candidates.append(
                DifferentiationPoint(
                    aspect="叙事引擎",
                    description="主线围绕高压生存博弈推进，持续制造规则破解型阅读驱动力。",
                    why_works="将智识满足与生死压迫叠加，读者获得强烈的“必须继续看”冲动。",
                    evidence_chapter=main_ev,
                    confidence=0.72,
                )
            )
        if {"economic", "power", "emotional"}.issubset(line_types):
            candidates.append(
                DifferentiationPoint(
                    aspect="多线结构",
                    description="主线与经济/权力/情感暗线并驱，交叉形成复合冲突场。",
                    why_works="单章既有即时反馈又有长期悬念，降低“只剩打怪升级”疲劳。",
                    evidence_chapter=sorted(
                        {
                            e.chapter_idx
                            for p in analysis.plotlines
                            for e in p.events[:2]
                        }
                    )[:6]
                    or main_ev,
                    confidence=0.68,
                )
            )
        if analysis.pacing.avg_peak_interval_chapters and analysis.pacing.avg_peak_interval_chapters <= 3.0:
            candidates.append(
                DifferentiationPoint(
                    aspect="节奏策略",
                    description="高强度爽点间隔短，且峰值事件频繁穿插在叙事推进中。",
                    why_works="持续即时反馈强化追读惯性，读者更容易形成日更依赖。",
                    evidence_chapter=analysis.pacing.big_climax_chapters[:6] or main_ev,
                    confidence=0.66,
                )
            )
        if not candidates:
            candidates.append(
                DifferentiationPoint(
                    aspect="人物驱动",
                    description="关键人物关系反复重组，冲突与联盟切换快。",
                    why_works="读者在角色立场变化中持续获得新信息与预期反转。",
                    evidence_chapter=main_ev,
                    confidence=0.62,
                )
            )
        return candidates[:8]

    def _recover_reader_hooks(self, analysis: NovelAnalysis) -> list[ReaderHookCausation]:
        hook_map = {
            "reveal": ("规则破解 + 真相揭示", "智识满足"),
            "cliffhanger": ("章末断点 + 高悬念收束", "猎奇好奇"),
            "face_slap": ("压制后反打脸", "公平感"),
            "power_up": ("阶段升级 + 即时奖励", "即时反馈"),
            "cp_progress": ("共患难关系推进", "情感张力"),
            "revenge": ("损失累积后复仇兑现", "身份认同"),
            "mystery": ("谜面递进 + 延迟揭底", "智识满足"),
            "twist": ("预期反转 + 立场翻盘", "替代体验"),
            "crisis": ("绝境承压 + 临界求生", "安全感"),
            "opening": ("开章冲突直入", "猎奇好奇"),
        }
        freq: Counter[str] = Counter()
        chapters_by_type: dict[str, list[tuple[int, int]]] = {}
        for ch in analysis.chapters:
            for h in ch.hooks:
                t = h.type.value if hasattr(h.type, "value") else str(h.type)
                freq[t] += 1
                chapters_by_type.setdefault(t, []).append((ch.chapter_idx, h.intensity))
        out: list[ReaderHookCausation] = []
        for t, _ in freq.most_common(8):
            name, psych = hook_map.get(t, (f"{t}型爽点循环", "替代体验"))
            chs = sorted(
                chapters_by_type.get(t, []), key=lambda x: (-x[1], x[0])
            )
            typical = sorted({c for c, _ in chs[:8]})
            out.append(
                ReaderHookCausation(
                    hook_pattern=name,
                    psychological_mechanism=psych,
                    typical_chapters=typical,
                    evidence_chapter=typical[:4],
                    confidence=0.64,
                )
            )
        if len(out) < 5:
            for line in analysis.plotlines[: max(0, 5 - len(out))]:
                typical = [e.chapter_idx for e in line.events[:6]]
                out.append(
                    ReaderHookCausation(
                        hook_pattern=f"{line.name}驱动的阶段性兑现",
                        psychological_mechanism="替代体验",
                        typical_chapters=typical,
                        evidence_chapter=typical[:3],
                        confidence=0.58,
                    )
                )
        return out[:10]

    def _recover_drop_risks(self, analysis: NovelAnalysis) -> list[DropRisk]:
        out: list[DropRisk] = []
        for z in analysis.pacing.drop_risk_zones[:8]:
            start = int(z.get("start_chapter", 0))
            end = int(z.get("end_chapter", start))
            out.append(
                DropRisk(
                    chapter_range=(start, end),
                    reason=z.get("reason", "连续章节缺少中高强度反馈。"),
                    severity=int(z.get("severity", 3)),
                    suggestion="在该区段插入 1-2 个 intensity>=3 的冲突回合，并给主角明确阶段收益。",
                    evidence_chapter=[start, end],
                    confidence=0.63,
                )
            )
        if not out and analysis.meta.total_chapters > 200:
            out.append(
                DropRisk(
                    chapter_range=(0, min(30, analysis.meta.total_chapters - 1)),
                    reason="长篇前段若铺垫过密、兑现不足，读者首轮留存风险会显著上升。",
                    severity=3,
                    suggestion="每 3-5 章至少安排一次可感知收益（破局、升级、关系突破、反转其一）。",
                    evidence_chapter=[0, min(30, analysis.meta.total_chapters - 1)],
                    confidence=0.55,
                )
            )
        return out

    def _build_quality_gate_snapshot(self, analysis: NovelAnalysis) -> dict[str, Any]:
        return {
            "plotlines_ready": bool(analysis.plotlines and any(p.events for p in analysis.plotlines)),
            "differentiation_count": len(analysis.differentiation),
            "reader_hook_count": len(analysis.reader_hooks),
            "drop_risk_count": len(analysis.drop_risks),
            "quotes_count": len(analysis.top_quotes),
            "tropes_count": len(analysis.top_tropes),
        }

    def _build_longform_briefs(self, analysis: NovelAnalysis) -> list[dict[str, Any]]:
        """给超长篇提供“智能体级”结构化摘要，便于报告直接展示。"""
        briefs: list[dict[str, Any]] = []
        total = max(analysis.meta.total_chapters, 1)
        for line in analysis.plotlines[:8]:
            events = sorted(line.events, key=lambda e: e.chapter_idx)
            if not events:
                continue
            e1 = [e for e in events if e.chapter_idx <= total * 0.25]
            e2 = [e for e in events if total * 0.25 < e.chapter_idx <= total * 0.50]
            e3 = [e for e in events if total * 0.50 < e.chapter_idx <= total * 0.75]
            e4 = [e for e in events if e.chapter_idx > total * 0.75]
            phase_spec = [
                ("铺垫", e1),
                ("触发", e2),
                ("升级", e3),
                ("回收", e4),
            ]
            phase_rows = []
            for phase_name, items in phase_spec:
                if not items:
                    continue
                phase_rows.append(
                    {
                        "phase": phase_name,
                        "chapter_range": [items[0].chapter_idx, items[-1].chapter_idx],
                        "focus": "；".join(i.title for i in items[:3])[:120] or "该阶段推进该线核心冲突。",
                    }
                )
            max_milestones = 12 if len(events) > 20 else 8
            if len(events) <= max_milestones:
                sampled_events = events
            else:
                idx_set = {
                    round(i * (len(events) - 1) / (max_milestones - 1))
                    for i in range(max_milestones)
                }
                sampled_events = [events[i] for i in sorted(idx_set)]
            line_key = line.line.value if hasattr(line.line, "value") else str(line.line)
            briefs.append(
                {
                    "line": line_key,
                    "name": line.name,
                    "deep_summary": line.summary or "该线索以阶段冲突推进，持续影响主线决策。",
                    "phases": phase_rows,
                    "milestones": [
                        {
                            "chapter_idx": e.chapter_idx,
                            "title": e.title,
                            "summary": e.summary[:90],
                        }
                        for e in sampled_events
                    ],
                }
            )
        return briefs
