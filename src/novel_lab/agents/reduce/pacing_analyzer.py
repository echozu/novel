"""节奏分析 — 纯计算（确定性）+ 风险区段识别。

不依赖 LLM 即可工作；下游解读由 deep insight agent 完成。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...schema import ChapterAnalysis, PacingAnalysis, PacingPoint


@dataclass
class PacingAnalyzer:
    drop_window: int = 3        # 连续 N 章无中等以上爽点 → 风险
    medium_intensity: int = 3   # 中等爽点起点 intensity ≥ 3
    big_intensity: int = 5      # 大爽点 intensity = 5

    def run(self, chapters: list[ChapterAnalysis]) -> PacingAnalysis:
        if not chapters:
            return PacingAnalysis()

        chapters_sorted = sorted(chapters, key=lambda c: c.chapter_idx)
        curve: list[PacingPoint] = []
        peak_chapters: list[int] = []
        big_chapters: list[int] = []
        small_count = 0
        medium_count = 0

        for c in chapters_sorted:
            max_int = 0.5  # 兜底，没有 hook 时给 0.5 让曲线不空白
            types: list[str] = []
            for h in c.hooks:
                if h.intensity > max_int:
                    max_int = float(h.intensity)
                if h.intensity >= 1:
                    small_count += 1
                if h.intensity >= self.medium_intensity:
                    medium_count += 1
                types.append(h.type.value if hasattr(h.type, "value") else str(h.type))
            if max_int >= 4:
                peak_chapters.append(c.chapter_idx)
            if max_int >= self.big_intensity:
                big_chapters.append(c.chapter_idx)
            curve.append(
                PacingPoint(
                    chapter_idx=c.chapter_idx,
                    intensity=max_int,
                    dominant_hook_types=list(dict.fromkeys(types))[:3],
                )
            )

        n = len(chapters_sorted)
        # 平均 peak 间隔
        if len(peak_chapters) >= 2:
            diffs = [peak_chapters[i + 1] - peak_chapters[i] for i in range(len(peak_chapters) - 1)]
            avg_peak = sum(diffs) / len(diffs)
        elif len(peak_chapters) == 1:
            avg_peak = float(n)  # 全书只有 1 个峰，糟糕
        else:
            avg_peak = float(n)

        # 风险区段：连续 ≥ drop_window 章 max intensity ≤ 2
        risks: list[dict] = []
        run_start: int | None = None
        for p in curve:
            if p.intensity <= 2:
                if run_start is None:
                    run_start = p.chapter_idx
            else:
                if run_start is not None and p.chapter_idx - run_start >= self.drop_window:
                    risks.append(
                        {
                            "start_chapter": run_start,
                            "end_chapter": p.chapter_idx - 1,
                            "reason": f"连续 {p.chapter_idx - run_start} 章无中等以上爽点",
                            "severity": min(5, (p.chapter_idx - run_start) // self.drop_window + 1),
                        }
                    )
                run_start = None
        if run_start is not None and curve and curve[-1].chapter_idx - run_start >= self.drop_window:
            risks.append(
                {
                    "start_chapter": run_start,
                    "end_chapter": curve[-1].chapter_idx,
                    "reason": f"末尾连续 {curve[-1].chapter_idx - run_start + 1} 章无中等以上爽点",
                    "severity": min(5, (curve[-1].chapter_idx - run_start) // self.drop_window + 1),
                }
            )

        return PacingAnalysis(
            curve=curve,
            avg_peak_interval_chapters=round(avg_peak, 2),
            small_hook_per_chapter=round(small_count / max(n, 1), 2),
            medium_hook_per_5_chapters=round(medium_count / max(n / 5, 1), 2),
            big_climax_chapters=big_chapters,
            drop_risk_zones=risks,
        )
