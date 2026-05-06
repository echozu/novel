from novel_lab.agents.reduce import PacingAnalyzer
from novel_lab.schema import ChapterAnalysis, Hook, HookType


def _ch(idx, *intensities):
    return ChapterAnalysis(
        chapter_idx=idx,
        summary=f"ch{idx}",
        hooks=[
            Hook(type=HookType.CLIFFHANGER, intensity=i, summary=f"i={i}")
            for i in intensities
        ],
    )


def test_pacing_basic():
    chapters = [
        _ch(0, 4),
        _ch(1, 1),
        _ch(2, 5),
        _ch(3, 1),
        _ch(4, 4),
    ]
    res = PacingAnalyzer().run(chapters)
    assert len(res.curve) == 5
    assert res.big_climax_chapters == [2]
    assert res.avg_peak_interval_chapters == 2.0  # peaks at 0,2,4 → diff [2,2]
    # small hook count = 5 (每章都有 hook intensity ≥1)
    assert res.small_hook_per_chapter == 1.0


def test_pacing_drop_zone_detection():
    # 5 章连续低强度
    chapters = [_ch(i, 1) for i in range(5)]
    chapters.append(_ch(5, 4))
    res = PacingAnalyzer().run(chapters)
    assert any(z["start_chapter"] == 0 for z in res.drop_risk_zones)


def test_pacing_empty():
    res = PacingAnalyzer().run([])
    assert res.curve == []
