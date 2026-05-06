"""novel-lab CLI 入口（Typer + Rich 进度条）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .orchestrator import Pipeline, PipelineConfig
from .output import OutputPackBuilder

load_dotenv()

app = typer.Typer(add_completion=False, no_args_is_help=True, help="novel-lab — 长篇网文多智能体拆解流水线")
console = Console()


def _is_probably_placeholder_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k or k == "missing":
        return True
    return any(tok in k for tok in ("xxxx", "your_", "placeholder", "replace_me", "test_key"))


def _print_preflight_warnings(config: PipelineConfig) -> None:
    tier = (config.tier or "").strip().lower()
    if tier in {"balanced", "premium"}:
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if _is_probably_placeholder_key(anthropic_key):
            console.print(
                "[bold red]⚠ 配置预检查：当前 tier 会调用 Claude，但 ANTHROPIC_API_KEY 未配置或仍是占位值。[/]"
            )
            console.print(
                "[yellow]  影响：reduce 的 arc / plotline / style 子任务会失败。[/]"
            )
            console.print(
                "[yellow]  处理：设置真实 ANTHROPIC_API_KEY，或改用 --tier basic（全 DeepSeek）。[/]"
            )

    if config.enable_rag and os.getenv("NOVEL_LAB_SKIP_INDEX", "").lower() in ("1", "true", "yes"):
        console.print(
            "[bold yellow]⚠ 配置预检查：你开启了 --rag，但 NOVEL_LAB_SKIP_INDEX=1，向量索引会被跳过。[/]"
        )
        console.print(
            "[yellow]  处理：如需真正开启检索，请设 NOVEL_LAB_SKIP_INDEX=0 并建议 NOVEL_LAB_FORCE_REINDEX=1。[/]"
        )


@app.command()
def analyze(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="TXT / EPUB 路径"),
    genre: str = typer.Option("generic", "--genre", "-g",
                              help="题材：xuanhuan|yanqing|dushi|generic"),
    tier: str = typer.Option(os.getenv("NOVEL_LAB_TIER", "balanced"), "--tier", "-t",
                             help="质量档位：basic|balanced|premium|local"),
    sample_ratio: float = typer.Option(1.0, "--sample-ratio", "-s",
                                       min=0.05, max=1.0, help="抽样比例 0-1，调试用"),
    max_chapters: Optional[int] = typer.Option(None, "--max-chapters", "-m",
                                               help="最多分析多少章（调试）"),
    map_concurrency: int = typer.Option(
        int(os.getenv("NOVEL_LAB_MAP_CONCURRENCY", "20")), "--map-concurrency", "-c",
        help="Map 阶段最大并发"
    ),
    workdir: Path = typer.Option(
        Path(os.getenv("NOVEL_LAB_WORKDIR", "./.workdir")),
        "--workdir", "-w", help="工作目录"
    ),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="启用 SQLite 断点续跑"),
    write_neo4j: bool = typer.Option(False, "--neo4j", help="同步写入 Neo4j（需 docker compose up）"),
    enable_critic: bool = typer.Option(True, "--critic/--no-critic", help="启用 Self-Critique 反思"),
    enable_rag: bool = typer.Option(True, "--rag/--no-rag", help="启用向量检索（RAG）参与主线复核"),
    title: Optional[str] = typer.Option(None, "--title", help="覆盖书名"),
) -> None:
    """拆解一本小说，输出 output_pack/ 目录。"""
    workdir.mkdir(parents=True, exist_ok=True)
    config = PipelineConfig(
        book_path=path.resolve(),
        workdir=workdir.resolve(),
        genre=genre,
        tier=tier,
        sample_ratio=sample_ratio,
        max_chapters=max_chapters,
        map_concurrency=map_concurrency,
        resume=resume,
        write_neo4j=write_neo4j,
        enable_critic=enable_critic,
        enable_rag=enable_rag,
        book_title_override=title,
    )

    asyncio.run(_run(config))


async def _run(config: PipelineConfig) -> None:
    _print_preflight_warnings(config)
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    map_task_id: Optional[int] = None
    task_ids: dict[str, int] = {}

    def ensure_task(key: str, desc: str, total: int = 1) -> int:
        if key not in task_ids:
            task_ids[key] = progress.add_task(desc, total=total)
        else:
            progress.update(task_ids[key], description=desc)
        return task_ids[key]

    def finish_task(key: str, desc: Optional[str] = None) -> None:
        tid = task_ids.get(key)
        if tid is None:
            return
        kwargs = {"completed": 1}
        if desc:
            kwargs["description"] = desc
        progress.update(tid, **kwargs)

    def on_progress(stage: str, payload: dict) -> None:
        nonlocal map_task_id
        if stage == "ingest":
            console.print(f"[bold green]✓ ingest[/]  book_id={payload['book_id']}  "
                          f"章节 {payload['chapters']}  字数 {payload['chars']:,}")
        elif stage == "index_start":
            desc = (
                f"Layer0 Index  backend={payload.get('backend')} "
                f"model={payload.get('model') or '-'}"
            )
            ensure_task("index", desc)
            console.print(f"[cyan]→[/] index: 构建/复用向量库 · {desc}")
        elif stage == "index":
            finish_task("index", "Layer0 Index  done")
            console.print(f"[bold green]✓ index[/]  vector store @ {payload['persisted_at']}")
        elif stage == "map_start":
            console.print(f"[cyan]→[/] Layer1 Map 开始 · 共 {payload['total']} 章")
        elif stage == "map_chapter":
            if map_task_id is None:
                map_task_id = progress.add_task("Layer1 Map (DeepSeek)", total=payload["total"])
            progress.update(
                map_task_id, completed=payload["done"],
                description=f"Layer1 Map  ch{payload['chapter_idx']}  ${payload['cost_usd']}"
            )
        elif stage == "map":
            progress.refresh()
            console.print(f"[bold green]✓ map[/]  {payload['chapters']} 章已分析")
        elif stage == "reduce_start":
            ensure_task("reduce", f"Layer2 Reduce  model={payload.get('model')} tasks={','.join(payload.get('tasks', []))}")
            console.print(
                f"[cyan]→[/] Layer2 Reduce 开始 · model={payload.get('model')} · "
                f"任务={', '.join(payload.get('tasks', []))}"
            )
        elif stage == "reduce_agent_start":
            key = f"reduce:{payload.get('agent')}"
            ensure_task(key, f"Reduce/{payload.get('agent')}  {payload.get('task')}")
            console.print(f"[cyan]·[/] Reduce/{payload.get('agent')}: {payload.get('task')}")
        elif stage == "reduce_agent_done":
            key = f"reduce:{payload.get('agent')}"
            finish_task(key, f"Reduce/{payload.get('agent')}  done")
            console.print(f"[green]✓[/] Reduce/{payload.get('agent')} 完成")
        elif stage == "reduce_agent_error":
            key = f"reduce:{payload.get('agent')}"
            finish_task(key, f"Reduce/{payload.get('agent')}  failed")
            console.print(f"[bold red]✗ Reduce/{payload.get('agent')}[/]  {payload.get('err')}")
        elif stage == "reduce_pacing":
            finish_task("reduce:pacing", "Reduce/pacing  done")
            console.print(f"[cyan]·[/] pacing: avg_peak={payload['avg_peak_interval_chapters']} 章, "
                          f"风险段 {payload['drop_zones']}")
        elif stage == "reduce_plotline_refine_start":
            ensure_task("reduce:refine", f"Reduce/refine  {payload.get('task')}")
            console.print(
                f"[cyan]·[/] Reduce/refine: {payload.get('task')} · 初版情节线 {payload.get('plotlines')}"
            )
        elif stage == "reduce_plotline_refine":
            finish_task("reduce:refine", "Reduce/refine  done")
            console.print(
                f"[green]✓[/] Reduce/refine 完成 · 情节线 {payload.get('plotlines')} · "
                f"分线总结 {payload.get('line_briefs')}"
            )
        elif stage == "reduce":
            finish_task("reduce", "Layer2 Reduce  done")
            console.print(f"[bold green]✓ reduce[/]  人物 {payload['characters']}, "
                          f"情节线 {payload['plotlines']}, POV {payload['style_pov']}")
        elif stage == "insight_start":
            ensure_task("insight", f"Layer3 Insight  model={payload.get('model')}")
            console.print(
                f"[cyan]→[/] Layer3 Insight 开始 · model={payload.get('model')} · "
                f"任务={', '.join(payload.get('tasks', []))}"
            )
        elif stage == "insight_agent_start":
            key = f"insight:{payload.get('agent')}"
            ensure_task(key, f"Insight/{payload.get('agent')}  {payload.get('task')}")
            console.print(f"[cyan]·[/] Insight/{payload.get('agent')}: {payload.get('task')}")
        elif stage == "insight_agent_done":
            key = f"insight:{payload.get('agent')}"
            finish_task(key, f"Insight/{payload.get('agent')}  done")
            console.print(f"[green]✓[/] Insight/{payload.get('agent')} 完成")
        elif stage == "insight_agent_error":
            key = f"insight:{payload.get('agent')}"
            finish_task(key, f"Insight/{payload.get('agent')}  failed")
            console.print(f"[bold red]✗ Insight/{payload.get('agent')}[/]  {payload.get('err')}")
        elif stage == "insight":
            finish_task("insight", "Layer3 Insight  done")
            console.print(f"[bold green]✓ insight[/]  差异点 {payload['diffs']}, "
                          f"爽点公式 {payload['hooks']}, 风险 {payload['risks']}")
        elif stage == "critic_start":
            ensure_task("critic", f"Layer3 Critic  claims={payload.get('claims')} model={payload.get('model')}")
            console.print(
                f"[cyan]→[/] Critic 开始 · 校对 {payload.get('claims')} 条 · model={payload.get('model')}"
            )
        elif stage == "critic":
            finish_task("critic", "Layer3 Critic  done")
            console.print(f"[bold yellow]✓ critic[/]  校对 {payload['checked']} 条, "
                          f"驳回 {payload['rejected']}")
        elif stage == "finalize_start":
            ensure_task("finalize", f"Finalize  {payload.get('task')}")
            console.print(f"[cyan]→[/] Finalize: {payload.get('task')}")
        elif stage == "finalize":
            finish_task("finalize", "Finalize  done")
            console.print(f"[bold green]✓ finalize[/]  金句 {payload['top_quotes']}, "
                          f"套路 {payload['top_tropes']}, 总成本 ${payload['cost_usd']}")
        elif stage.endswith("_error"):
            console.print(f"[bold red]✗ {stage}[/]  {payload}")

    pipeline = Pipeline(config, progress=on_progress)
    with progress:
        analysis = await pipeline.run()
    console.print()

    book_dir = pipeline.state.book_dir
    if book_dir is None or analysis is None:
        console.print("[bold red]pipeline produced no analysis[/]")
        raise typer.Exit(1)

    # 输出包
    console.print("[bold cyan]→ output[/] 生成输出包（HTML 报告 + 创作宪法 + AI 写作 Pack + 知识图谱）...")
    pack_builder = OutputPackBuilder(router=pipeline.router, write_neo4j=config.write_neo4j)
    out_dir = await pack_builder.build(analysis, book_dir)
    console.print(f"[bold green]✓ output[/]  输出包已生成 @ {out_dir}")

    # 总结
    table = Table(title=f"《{analysis.meta.title}》拆解完成", show_header=True, header_style="bold magenta")
    table.add_column("指标", style="cyan")
    table.add_column("数值", justify="right")
    table.add_row("章节数", str(analysis.meta.total_chapters))
    table.add_row("总字数", f"{analysis.meta.total_chars:,}")
    table.add_row("追踪人物", str(len(analysis.characters)))
    table.add_row("情节线", str(len(analysis.plotlines)))
    table.add_row("差异化亮点", str(len(analysis.differentiation)))
    table.add_row("读者爽点公式", str(len(analysis.reader_hooks)))
    table.add_row("弃书风险段", str(len(analysis.drop_risks)))
    table.add_row("金句", str(len(analysis.top_quotes)))
    table.add_row("套路命中", str(len(analysis.top_tropes)))
    table.add_row("爽点高峰间隔", f"{analysis.pacing.avg_peak_interval_chapters} 章")
    table.add_row("总成本 (USD)", f"${analysis.cost_usd_total:.4f}")
    table.add_row("总耗时 (s)", f"{analysis.runtime_seconds:.1f}")
    console.print(table)
    console.print(f"\n[bold green]→ 输出目录：[/] {out_dir}")
    console.print(f"[bold green]→ 打开报告：[/] open {out_dir/'report.html'}")


@app.command()
def info(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="TXT / EPUB 路径"),
) -> None:
    """快速查看小说基本信息（章节数 / 字数 / 卷），不调用 LLM。"""
    from .ingest.parser import parse as parse_novel
    meta = parse_novel(path)
    console.print(f"[bold]书名[/]: {meta.title}")
    console.print(f"[bold]book_id[/]: {meta.book_id}")
    console.print(f"[bold]章节[/]: {meta.total_chapters}")
    console.print(f"[bold]总字数[/]: {meta.total_chars:,}")
    if meta.chapters:
        avg = meta.total_chars / max(1, meta.total_chapters)
        console.print(f"[bold]平均章长[/]: {avg:.0f} 字")
        console.print("\n[bold]前 5 章[/]:")
        for ch in meta.chapters[:5]:
            console.print(f"  · ch{ch.idx} 《{ch.title}》 ({ch.char_count} 字)")


@app.command("calibrate")
def calibrate(
    analysis_json: Path = typer.Argument(..., exists=True, dir_okay=False,
                                          help="已产出的 novel_analysis.json"),
    comments_jsonl: Path = typer.Argument(..., exists=True, dir_okay=False,
                                           help="评论 JSONL（每行 RawComment）"),
    out: Path = typer.Option(None, "--out", "-o", help="输出 calibrated_analysis.json"),
) -> None:
    """[Phase2] 用读者评论反向校准 Layer3 输出。"""
    import json

    from .phase2.comments.aligner import align_to_chapters, digest_per_chapter
    from .phase2.comments.calibrator import CommentCalibrator
    from .phase2.comments.clean import clean_stream
    from .phase2.comments.scraper import load_jsonl
    from .schema import NovelAnalysis

    raw_analysis = json.loads(analysis_json.read_text(encoding="utf-8"))
    analysis = NovelAnalysis(**raw_analysis)
    comments_raw = list(load_jsonl(comments_jsonl))
    comments = clean_stream(comments_raw)
    aligned = align_to_chapters(comments, analysis.meta)
    digests = digest_per_chapter(aligned, analysis.meta)
    calibrated = CommentCalibrator().apply(analysis, digests)
    out_path = out or analysis_json.with_name("calibrated_analysis.json")
    out_path.write_text(calibrated.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"[bold green]✓ 已校准[/]  评论 {len(comments)} 条 → 章节 {len(aligned)} 个")
    console.print(f"[bold green]→[/] {out_path}")


@app.command("ui")
def launch_ui(
    workdir: Path = typer.Option(
        Path(os.getenv("NOVEL_LAB_WORKDIR", "./.workdir")),
        "--workdir", "-w",
    ),
    port: int = typer.Option(7860, "--port", "-p"),
    share: bool = typer.Option(False, "--share"),
) -> None:
    """[Phase2] 启动 Gradio Web UI（需安装 phase2 extras）。"""
    try:
        from .phase2.ui.app import launch
    except ImportError as exc:
        console.print(f"[bold red]缺少 phase2 依赖[/]：pip install -e \".[phase2]\"  ({exc})")
        raise typer.Exit(1)
    launch(workdir=workdir, port=port, share=share)


if __name__ == "__main__":
    app()
