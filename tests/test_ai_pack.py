import json

import yaml

from novel_lab.output import AIWritingPackBuilder
from novel_lab.schema import (
    Chapter,
    ChapterAnalysis,
    CharacterArcPoint,
    CharacterProfile,
    DifferentiationPoint,
    NovelAnalysis,
    NovelMeta,
    PacingAnalysis,
    StyleFingerprint,
    TropeHit,
)


def _mk(tmp_path):
    meta = NovelMeta(book_id="b", title="T", genre="xuanhuan",
                     total_chapters=2, total_chars=20,
                     chapters=[Chapter(idx=i, title=f"ch{i}", text="x", char_count=1) for i in range(2)])
    a = NovelAnalysis(
        meta=meta,
        chapters=[ChapterAnalysis(chapter_idx=i) for i in range(2)],
        characters=[
            CharacterProfile(
                character_id="c1", name="林云", role="protagonist",
                one_liner="少年", appearance_chapters=[0, 1],
                arc=[CharacterArcPoint(stage="initial", chapter_idx=0, state_summary="s")],
                relationships=[], motivation="复仇", flaws=["执念"],
                evidence_chapter=[0],
            )
        ],
        plotlines=[],
        pacing=PacingAnalysis(small_hook_per_chapter=1.5, avg_peak_interval_chapters=2.0),
        style=StyleFingerprint(pov="第三人称限知", tense="过去时", avg_sentence_length=18,
                                dialog_ratio=0.4, description_density="适中",
                                signature_phrases=["这世道"], tone_keywords=["冷峻"],
                                sample_paragraphs=["山风很大。"]),
        differentiation=[
            DifferentiationPoint(aspect="人设", description="桀骜+克制",
                                  why_works="反差代入", evidence_chapter=[1])
        ],
        top_tropes=[TropeHit(trope_id="revenge_arc", trope_name="复仇线",
                             instance_summary="灭门", evidence_chapter=[1])],
    )
    out = tmp_path / "pack"
    AIWritingPackBuilder().write(a, out, constitution_md="# 创作宪法\n测试。")
    return out


def test_ai_pack_files_present(tmp_path):
    out = _mk(tmp_path)
    for fn in ("system_prompt.md", "style_few_shot.md", "characters.yaml",
               "tropes.json", "plot_skeleton.md", "creation_constitution.md"):
        assert (out / fn).exists(), f"{fn} missing"


def test_characters_yaml_parseable(tmp_path):
    out = _mk(tmp_path)
    data = yaml.safe_load((out / "characters.yaml").read_text(encoding="utf-8"))
    assert data["characters"][0]["name"] == "林云"


def test_tropes_json_structure(tmp_path):
    out = _mk(tmp_path)
    data = json.loads((out / "tropes.json").read_text(encoding="utf-8"))
    assert data["genre"] == "xuanhuan"
    assert data["must_use"][0]["trope_id"] == "revenge_arc"
    assert "available" in data
    assert "anti_patterns" in data
