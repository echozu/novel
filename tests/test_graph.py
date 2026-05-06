from novel_lab.graph import KnowledgeGraphBuilder
from novel_lab.schema import (
    Chapter,
    ChapterAnalysis,
    CharacterArcPoint,
    CharacterProfile,
    NovelAnalysis,
    NovelMeta,
    PlotEvent,
    PlotLine,
    PlotLineKind,
    Quote,
    Scene,
    TropeHit,
)


def _make_analysis():
    chapters = [
        Chapter(idx=i, title=f"第{i+1}章", text="林云很冷酷。", char_count=10)
        for i in range(3)
    ]
    meta = NovelMeta(book_id="book_test", title="剑出山门", genre="xuanhuan",
                     total_chapters=3, total_chars=30, chapters=chapters)
    chapter_analyses = [
        ChapterAnalysis(
            chapter_idx=i, summary=f"ch{i}",
            scenes=[Scene(location="山下", summary="...", evidence_chapter=[i])],
            tropes=[TropeHit(trope_id="revenge_arc", trope_name="复仇线",
                             instance_summary="复仇", evidence_chapter=[i])],
            quotes=[Quote(text="这世道，该有人收拾。", speaker="林云",
                          why_good="冷峻", evidence_chapter=[i])],
        )
        for i in range(3)
    ]
    chars = [
        CharacterProfile(
            character_id="char_lin",
            name="林云",
            aliases=["少年"],
            role="protagonist",
            one_liner="少年剑客",
            appearance_chapters=[0, 1, 2],
            arc=[
                CharacterArcPoint(stage="initial", chapter_idx=0, state_summary="下山"),
                CharacterArcPoint(stage="final", chapter_idx=2, state_summary="再下山"),
            ],
            relationships=[{"target": "苏婉", "type": "love", "evolution": []}],
            motivation="复仇",
            flaws=["太执"],
            evidence_chapter=[0, 2],
        ),
        CharacterProfile(
            character_id="char_su",
            name="苏婉",
            role="love_interest",
            one_liner="师妹",
            appearance_chapters=[0, 2],
            arc=[],
            relationships=[],
            motivation="守候",
            evidence_chapter=[0, 2],
        ),
    ]
    plotlines = [
        PlotLine(line=PlotLineKind.MAIN, name="复仇主线",
                 events=[PlotEvent(chapter_idx=2, title="灭门",
                                   summary="灭秦府", line=PlotLineKind.MAIN,
                                   characters=["林云"], evidence_chapter=[2])])
    ]
    analysis = NovelAnalysis(
        meta=meta,
        chapters=chapter_analyses,
        characters=chars,
        plotlines=plotlines,
        top_quotes=[chapter_analyses[0].quotes[0]],
        top_tropes=[chapter_analyses[0].tropes[0]],
    )
    return analysis


def test_graph_builder_emits_nodes_and_edges():
    analysis = _make_analysis()
    artifact = KnowledgeGraphBuilder().build(analysis)
    elements = artifact.elements
    labels = {n["data"]["label"] for n in elements["nodes"]}
    assert "Book" in labels
    assert "Chapter" in labels
    assert "Character" in labels
    assert "Trope" in labels
    assert "Quote" in labels
    # 至少有 RELATIONSHIP_WITH 边（林云-苏婉）
    edge_labels = {e["data"]["label"] for e in elements["edges"]}
    assert "RELATIONSHIP_WITH" in edge_labels
    # cypher 非空
    assert "MERGE" in artifact.cypher
