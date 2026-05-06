"""Gradio Web UI — 上传 / 进度 / 报告浏览 / 多书对比 / Pack 下载。

依赖：``pip install -e ".[phase2]"``（gradio）
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any, Generator

from ...orchestrator import Pipeline, PipelineConfig
from ...output import OutputPackBuilder


def _list_finished_books(workdir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not workdir.exists():
        return out
    for sub in sorted(workdir.iterdir()):
        if not sub.is_dir():
            continue
        analysis_file = sub / "novel_analysis.json"
        if not analysis_file.exists():
            continue
        try:
            data = json.loads(analysis_file.read_text(encoding="utf-8"))
            out.append(
                {
                    "book_id": sub.name,
                    "title": data["meta"]["title"],
                    "genre": data["meta"]["genre"],
                    "chapters": data["meta"]["total_chapters"],
                    "pacing_peak": data["pacing"].get("avg_peak_interval_chapters", 0),
                    "diff_score": data.get("metrics", {}).get("differentiation_score", 0),
                    "drop_risks": len(data.get("drop_risks", [])),
                    "cost": data.get("cost_usd_total", 0),
                    "report": str(sub / "output_pack" / "report.html"),
                    "pack_dir": str(sub / "output_pack"),
                }
            )
        except Exception:
            continue
    return out


def _zip_pack(pack_dir: Path) -> Path:
    zip_path = pack_dir.parent / f"{pack_dir.parent.name}_output_pack.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pack_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(pack_dir))
    return zip_path


def launch(
    workdir: Path = Path("./.workdir"),
    port: int = 7860,
    share: bool = False,
) -> None:
    import gradio as gr

    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # ---------- analyze handler ----------

    async def _run(file_path: str, title: str, genre: str, tier: str,
                   sample_ratio: float, max_chapters: int,
                   resume: bool, enable_critic: bool, write_neo4j: bool):
        config = PipelineConfig(
            book_path=Path(file_path),
            workdir=workdir,
            genre=genre,
            tier=tier,
            sample_ratio=sample_ratio,
            max_chapters=max_chapters if max_chapters > 0 else None,
            resume=resume,
            enable_critic=enable_critic,
            write_neo4j=write_neo4j,
            book_title_override=title or None,
        )
        events: list[str] = []

        def on_progress(stage: str, payload: dict) -> None:
            events.append(f"[{stage}] {payload}")

        pipeline = Pipeline(config, progress=on_progress)
        analysis = await pipeline.run()
        pack_builder = OutputPackBuilder(router=pipeline.router, write_neo4j=write_neo4j)
        out_dir = await pack_builder.build(analysis, pipeline.state.book_dir)
        return analysis, out_dir, "\n".join(events[-200:])

    def analyze(file: Any, title: str, genre: str, tier: str,
                sample_ratio: float, max_chapters: int,
                resume: bool, enable_critic: bool, write_neo4j: bool):
        if file is None:
            yield "请先上传 TXT/EPUB 文件", "", None
            return
        path = file.name if hasattr(file, "name") else str(file)
        log_acc: list[str] = []

        def append_log(msg: str) -> str:
            log_acc.append(msg)
            return "\n".join(log_acc[-200:])

        yield append_log(f"开始分析：{path}"), "", None
        try:
            analysis, out_dir, evlog = asyncio.run(
                _run(path, title, genre, tier, sample_ratio, int(max_chapters or 0),
                     resume, enable_critic, write_neo4j)
            )
        except Exception as exc:
            yield append_log(f"[ERROR] {type(exc).__name__}: {exc}"), "", None
            return

        zip_path = _zip_pack(Path(out_dir))
        report_path = Path(out_dir) / "report.html"
        summary = (
            f"### 完成\n"
            f"- 书：{analysis.meta.title}（book_id={analysis.meta.book_id}）\n"
            f"- 章节 {analysis.meta.total_chapters} / 字数 {analysis.meta.total_chars:,}\n"
            f"- 人物 {len(analysis.characters)} / 情节线 {len(analysis.plotlines)}\n"
            f"- 差异点 {len(analysis.differentiation)} / 爽点公式 {len(analysis.reader_hooks)} / 风险 {len(analysis.drop_risks)}\n"
            f"- 节奏 avg_peak {analysis.pacing.avg_peak_interval_chapters} 章\n"
            f"- 成本 ${analysis.cost_usd_total:.4f} / 用时 {analysis.runtime_seconds:.1f}s\n"
            f"- [打开报告]({report_path})\n"
        )
        yield append_log(evlog) + "\n\n" + summary, summary, str(zip_path)

    # ---------- compare handler ----------

    def compare_books():
        rows = _list_finished_books(workdir)
        if not rows:
            return [["（无）", "", 0, 0, 0, 0, 0]]
        return [
            [r["title"], r["genre"], r["chapters"], r["pacing_peak"],
             r["diff_score"], r["drop_risks"], r["cost"]]
            for r in rows
        ]

    # ---------- UI ----------

    with gr.Blocks(title="novel-lab", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# novel-lab · 长篇网文多智能体拆解流水线")

        with gr.Tab("分析"):
            with gr.Row():
                with gr.Column(scale=1):
                    file_in = gr.File(label="上传 TXT / EPUB", file_types=[".txt", ".epub"])
                    title_in = gr.Textbox(label="书名（可选，覆盖文件名）", value="")
                    genre_in = gr.Dropdown(
                        choices=["xuanhuan", "yanqing", "dushi", "generic"],
                        value="generic", label="题材",
                    )
                    tier_in = gr.Dropdown(
                        choices=["basic", "balanced", "premium", "local"],
                        value=os.getenv("NOVEL_LAB_TIER", "balanced"), label="质量档位",
                    )
                    sample_in = gr.Slider(0.05, 1.0, value=1.0, step=0.05, label="抽样比例")
                    max_in = gr.Number(value=0, precision=0, label="最多分析章节数（0 = 全本）")
                    resume_in = gr.Checkbox(value=True, label="启用断点续跑")
                    critic_in = gr.Checkbox(value=True, label="启用 Self-Critique")
                    neo4j_in = gr.Checkbox(value=False, label="同步写入 Neo4j")
                    run_btn = gr.Button("开始拆解", variant="primary")
                with gr.Column(scale=2):
                    log_out = gr.Textbox(label="实时进度", lines=20, interactive=False)
                    summary_out = gr.Markdown()
                    zip_out = gr.File(label="下载 output_pack ZIP")

            run_btn.click(
                analyze,
                inputs=[file_in, title_in, genre_in, tier_in, sample_in, max_in,
                        resume_in, critic_in, neo4j_in],
                outputs=[log_out, summary_out, zip_out],
            )

        with gr.Tab("已分析书库 / 横向对比"):
            with gr.Row():
                refresh = gr.Button("刷新")
            books_table = gr.Dataframe(
                headers=["书名", "题材", "章节", "高峰间隔(章)", "差异分", "风险数", "花费($)"],
                value=compare_books(),
                interactive=False,
            )
            refresh.click(compare_books, outputs=books_table)

        with gr.Tab("使用说明"):
            gr.Markdown(
                """
                ### 流程
                1. 上传 TXT / EPUB，选择题材和质量档位
                2. 点击「开始拆解」，等待 Layer1 → Layer2 → Layer3 → 输出 完成
                3. 下载 `output_pack.zip` 即得知识图谱 + 创作宪法 + AI 写作 Pack

                ### 下游使用
                - 把 `output_pack/ai_writing_pack/system_prompt.md` 复制到 Claude / Cursor 的 system prompt
                - `characters.yaml` + `plot_skeleton.md` + `tropes.json` 当作 context 一起喂

                ### Phase2 评论校准
                ```
                novel-lab calibrate <book>/novel_analysis.json comments.jsonl
                ```
                """
            )

    demo.launch(server_name="0.0.0.0", server_port=port, share=share)
