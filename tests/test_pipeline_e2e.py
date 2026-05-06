"""端到端 pipeline 测试 — 用 FakeRouter 跑通全流程。

目的：验证管线水电 + Schema 校验 + evidence 可追溯 + 输出文件齐全。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_lab.orchestrator import Pipeline, PipelineConfig
from novel_lab.output import OutputPackBuilder


class _ArcStubRouter:
    """子类化 FakeRouter 行为（避免循环依赖）。"""


@pytest.mark.asyncio
async def test_pipeline_end_to_end_with_fake_llm(tmp_path: Path, sample_novel_path: Path, monkeypatch):
    # 用 FakeRouter 替换真 LLMRouter
    from tests.conftest import FakeRouter
    from novel_lab.orchestrator import pipeline as pipeline_mod
    from novel_lab.output import pack as pack_mod

    fake = FakeRouter()
    # 让 Pipeline.__init__ 实例化的 LLMRouter 也指向 fake
    monkeypatch.setattr(pipeline_mod, "LLMRouter", lambda **kw: fake)
    monkeypatch.setattr(pack_mod, "LLMRouter", lambda **kw: fake)

    # 走通 Layer0 索引也耗时，通过 monkeypatch 跳过 sentence-transformers
    class _FakeIndex:
        def __init__(self, meta, workdir, embed_model_name=None):
            self.meta = meta
            self.persist_dir = workdir / meta.book_id / "chroma"
            self._chapter_text = {ch.idx: ch.text for ch in meta.chapters}
        def build(self, *, rebuild=False):
            return self
        def parent_chapter_text(self, idx):
            return self._chapter_text.get(idx, "")
        def retrieve(self, *a, **k):
            return []
        def retrieve_with_parents(self, *a, **k):
            return []

    monkeypatch.setattr(pipeline_mod, "NovelIndex", _FakeIndex)

    config = PipelineConfig(
        book_path=sample_novel_path,
        workdir=tmp_path,
        genre="xuanhuan",
        tier="basic",
        sample_ratio=1.0,
        map_concurrency=4,
        resume=False,
        write_neo4j=False,
    )
    pipeline = Pipeline(config)
    analysis = await pipeline.run()

    # 基础断言
    assert analysis.meta.total_chapters >= 7
    assert len(analysis.chapters) == analysis.meta.total_chapters
    # 每章 hooks 至少 1（FakeRouter 每章给 2 个）
    assert all(len(c.hooks) >= 1 for c in analysis.chapters)
    # 至少识别到 1 个角色
    assert len(analysis.characters) >= 1
    # plotlines 至少 1 个 main
    kinds = {p.line.value if hasattr(p.line, "value") else str(p.line) for p in analysis.plotlines}
    assert "main" in kinds
    # 节奏曲线长度 = 章节数
    assert len(analysis.pacing.curve) == analysis.meta.total_chapters
    # 差异点存在
    assert len(analysis.differentiation) >= 1
    # 所有 evidence_chapter 必须是合法章节序号
    for d in analysis.differentiation:
        for ci in d.evidence_chapter:
            assert 0 <= ci < analysis.meta.total_chapters

    # 跑输出 pack
    pack_builder = OutputPackBuilder(router=fake, write_neo4j=False)
    out_dir = await pack_builder.build(analysis, pipeline.state.book_dir)
    expected = [
        "report.html",
        "creation_constitution.md",
        "metrics.json",
        "graph.json",
        "knowledge_graph.cypher",
        "novel_analysis.json",
    ]
    for fn in expected:
        assert (out_dir / fn).exists(), f"missing {fn}"
    # writing pack
    pack_dir = out_dir / "ai_writing_pack"
    for fn in ("system_prompt.md", "characters.yaml", "tropes.json",
               "style_few_shot.md", "plot_skeleton.md"):
        assert (pack_dir / fn).exists(), f"missing pack/{fn}"

    # report.html 应该是合法的、含 ECharts CDN 的 HTML
    html = (out_dir / "report.html").read_text(encoding="utf-8")
    assert "echarts" in html.lower()
    assert "cytoscape" in html.lower()
    assert analysis.meta.title in html
