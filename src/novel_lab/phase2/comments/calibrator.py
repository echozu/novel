"""用读者评论 digest 反向校准 Layer3 的 pacing / drop_risks / reader_hooks。

校准规则：
1. 真实情绪高峰章节如果不在 pacing 的 big_climax_chapters 中，提升其 intensity。
2. 真实弃书风险信号章节（评论高频负面）补充到 drop_risks。
3. 读者关键词与 reader_hooks 关键词对齐打分（pattern 是否被读者验证）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...schema import DropRisk, NovelAnalysis, PacingPoint
from .schema import ChapterCommentDigest


@dataclass
class CommentCalibrator:
    boost_intensity: float = 0.5
    drop_severity: int = 3

    def apply(
        self,
        analysis: NovelAnalysis,
        digests: list[ChapterCommentDigest],
    ) -> NovelAnalysis:
        if not digests:
            return analysis

        digest_by_chapter = {d.chapter_idx: d for d in digests}

        # 1) pacing 强度提升
        peaks_added: set[int] = set()
        for p in analysis.pacing.curve:
            d = digest_by_chapter.get(p.chapter_idx)
            if not d:
                continue
            if d.is_emotional_peak:
                p.intensity = min(5.0, p.intensity + self.boost_intensity)
                if "reader_peak" not in p.dominant_hook_types:
                    p.dominant_hook_types.append("reader_peak")
                if p.intensity >= 4:
                    peaks_added.add(p.chapter_idx)

        for cidx in peaks_added:
            if cidx not in analysis.pacing.big_climax_chapters and cidx in {
                p.chapter_idx for p in analysis.pacing.curve if p.intensity >= 5
            }:
                analysis.pacing.big_climax_chapters.append(cidx)
        analysis.pacing.big_climax_chapters = sorted(set(analysis.pacing.big_climax_chapters))

        # 2) 新增 drop risks
        for d in digests:
            if not d.is_drop_signal:
                continue
            already = any(
                r.chapter_range[0] <= d.chapter_idx <= r.chapter_range[1]
                for r in analysis.drop_risks
            )
            if already:
                continue
            quote = d.representative_quotes[0] if d.representative_quotes else ""
            analysis.drop_risks.append(
                DropRisk(
                    chapter_range=(d.chapter_idx, d.chapter_idx),
                    reason=f"读者评论负面集中（关键词 {d.top_keywords[:3]}，代表反馈：{quote[:30]}…）",
                    severity=self.drop_severity,
                    suggestion="下游 AI 创作时主动规避此类节奏 / 桥段，尤其避免该章相似情节",
                    evidence_chapter=[d.chapter_idx],
                    confidence=0.8,
                )
            )

        # 3) reader_hooks 与读者关键词对齐打分
        all_kw: set[str] = set()
        for d in digests:
            all_kw.update(d.top_keywords)
        for h in analysis.reader_hooks:
            hits = sum(1 for kw in all_kw if kw in h.hook_pattern)
            if hits >= 1:
                h.confidence = min(1.0, h.confidence + 0.15)

        # 4) 写到 metrics
        analysis.metrics.setdefault("comments", {})
        analysis.metrics["comments"] = {
            "covered_chapters": len(digest_by_chapter),
            "peak_chapters_added": list(peaks_added),
            "new_drop_risks": sum(1 for d in digests if d.is_drop_signal),
        }
        return analysis
