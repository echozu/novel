from novel_lab.phase2.comments.aligner import align_to_chapters, digest_per_chapter
from novel_lab.phase2.comments.calibrator import CommentCalibrator
from novel_lab.phase2.comments.clean import clean_stream
from novel_lab.phase2.comments.schema import RawComment
from novel_lab.schema import (
    Chapter,
    ChapterAnalysis,
    NovelAnalysis,
    NovelMeta,
    PacingAnalysis,
    PacingPoint,
)


def _mk_analysis():
    chapters = [
        Chapter(idx=i, title=f"第{i+1}章", text="x", char_count=1)
        for i in range(5)
    ]
    meta = NovelMeta(book_id="b", title="T", genre="generic", total_chapters=5, total_chars=5,
                     chapters=chapters)
    pacing = PacingAnalysis(curve=[
        PacingPoint(chapter_idx=i, intensity=2.0, dominant_hook_types=[]) for i in range(5)
    ])
    return NovelAnalysis(
        meta=meta,
        chapters=[ChapterAnalysis(chapter_idx=i) for i in range(5)],
        pacing=pacing,
    )


def test_clean_filter_garbage():
    comments = [
        RawComment(source="qidian", book_id="b1", chapter_idx=0, text="爽爽爽好看！", likes=10),
        RawComment(source="qidian", book_id="b1", chapter_idx=0, text="...", likes=0),
        RawComment(source="qidian", book_id="b1", chapter_idx=0, text="爽爽爽好看！", likes=10),
        RawComment(source="qidian", book_id="b1", chapter_idx=0, text="催更", likes=1),
    ]
    out = clean_stream(comments)
    assert len(out) == 1


def test_calibrator_boosts_peak_and_adds_drop():
    analysis = _mk_analysis()
    comments = (
        # ch1：高赞情绪高峰
        [RawComment(source="qidian", book_id="b", chapter_idx=1, text="太爽了上头！", likes=200) for _ in range(8)]
        + [RawComment(source="qidian", book_id="b", chapter_idx=1, text="破防牛！", likes=100) for _ in range(4)]
        # ch3：负面集中（弃书风险）
        + [RawComment(source="qidian", book_id="b", chapter_idx=3,
                      text=f"无聊水更降智 #{i}", likes=5) for i in range(8)]
    )
    aligned = align_to_chapters(comments, analysis.meta)
    digests = digest_per_chapter(aligned, analysis.meta)
    chapters_with_peak = [d.chapter_idx for d in digests if d.is_emotional_peak]
    assert 1 in chapters_with_peak
    chapters_with_drop = [d.chapter_idx for d in digests if d.is_drop_signal]
    assert 3 in chapters_with_drop

    before_drops = len(analysis.drop_risks)
    new_pt_intensity = next(p.intensity for p in analysis.pacing.curve if p.chapter_idx == 1)
    calibrated = CommentCalibrator().apply(analysis, digests)
    after_drops = len(calibrated.drop_risks)
    assert after_drops > before_drops, "应至少新增 1 条 drop_risk"
    boosted = next(p.intensity for p in calibrated.pacing.curve if p.chapter_idx == 1)
    assert boosted > new_pt_intensity, "ch1 强度应被读者反馈推高"
