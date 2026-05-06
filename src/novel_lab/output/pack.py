"""统一打包：调用图谱构建 + HTML + 创作宪法 + AI 写作 Pack 一次性产出 ``output_pack/`` 目录。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..graph import KnowledgeGraphBuilder
from ..llm import LLMRouter
from ..schema import NovelAnalysis
from .ai_pack import AIWritingPackBuilder
from .constitution import ConstitutionGenerator
from .html_report import HTMLReportBuilder


@dataclass
class OutputPackBuilder:
    router: LLMRouter
    write_neo4j: bool = False

    async def build(self, analysis: NovelAnalysis, book_dir: Path) -> Path:
        out_dir = book_dir / "output_pack"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) 知识图谱
        graph_builder = KnowledgeGraphBuilder(
            write_neo4j=self.write_neo4j,
            neo4j_uri=os.getenv("NEO4J_URI"),
            neo4j_user=os.getenv("NEO4J_USER"),
            neo4j_password=os.getenv("NEO4J_PASSWORD"),
        )
        artifact = graph_builder.build(analysis)
        graph_builder.write_files(artifact, out_dir)

        # 2) 创作宪法
        try:
            constitution_md = await ConstitutionGenerator(self.router).generate(analysis)
        except Exception as exc:
            constitution_md = f"# 创作宪法生成失败\n\n{exc}"
        (out_dir / "creation_constitution.md").write_text(constitution_md, encoding="utf-8")

        # 3) AI 写作 Pack
        AIWritingPackBuilder().write(
            analysis,
            out_dir / "ai_writing_pack",
            constitution_md=constitution_md,
        )

        # 4) HTML 报告
        HTMLReportBuilder().write(analysis, artifact, out_dir / "report.html")

        # 5) metrics.json
        (out_dir / "metrics.json").write_text(
            json.dumps(analysis.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 6) novel_analysis.json 完整副本
        (out_dir / "novel_analysis.json").write_text(
            analysis.model_dump_json(indent=2), encoding="utf-8"
        )

        return out_dir
