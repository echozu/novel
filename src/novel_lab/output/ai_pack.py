"""AI Writing Pack — 给下游 Claude/GPT 直接 import 使用。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import load_genre
from ..schema import NovelAnalysis


@dataclass
class AIWritingPackBuilder:
    """生成 4 个文件，下游 LLM 一并 import 即可创作同款 / 同人。"""

    def write(self, analysis: NovelAnalysis, out_dir: Path, *, constitution_md: str | None = None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) system_prompt.md
        (out_dir / "system_prompt.md").write_text(
            self._system_prompt(analysis), encoding="utf-8"
        )
        # 2) style_few_shot.md
        (out_dir / "style_few_shot.md").write_text(
            self._style_few_shot(analysis), encoding="utf-8"
        )
        # 3) characters.yaml
        (out_dir / "characters.yaml").write_text(
            yaml.safe_dump(self._characters_payload(analysis), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        # 4) tropes.json — 命中的 + 推荐的
        (out_dir / "tropes.json").write_text(
            json.dumps(self._tropes_payload(analysis), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 5) plot_skeleton.md — 主线骨架
        (out_dir / "plot_skeleton.md").write_text(
            self._plot_skeleton(analysis), encoding="utf-8"
        )
        # 6) creation_constitution.md（如已有）
        if constitution_md:
            (out_dir / "creation_constitution.md").write_text(constitution_md, encoding="utf-8")

    # ---------------- builders ----------------

    def _system_prompt(self, a: NovelAnalysis) -> str:
        style = a.style.model_dump() if a.style else {}
        avoid = "\n".join(f"- {r.reason} → {r.suggestion}" for r in a.drop_risks[:6])
        diffs = "\n".join(f"- **{d.aspect}**：{d.description}（{d.why_works}）" for d in a.differentiation[:6])
        hooks = "\n".join(f"- {h.hook_pattern}（{h.psychological_mechanism}）" for h in a.reader_hooks[:8])

        peak = a.pacing.avg_peak_interval_chapters
        pacing_target = "1.8" if peak < 2.5 else f"{max(1.8, peak * 0.7):.1f}"

        return (
            f"# 你是 《{a.meta.title}》的同款 / 同人创作助手\n\n"
            "## 你的目标\n"
            "写一部能让网文读者**追读不弃书**的小说，要求节奏紧凑、爽点密集、文风稳定。\n\n"
            "## 题材定位\n"
            f"- 题材：{a.meta.genre}\n"
            f"- 节奏目标：每 {pacing_target} 章一个情绪高峰（intensity ≥ 4）\n"
            f"- 小爽点目标：每章 ≥ {max(1.0, a.pacing.small_hook_per_chapter or 1.0):.1f} 个\n\n"
            "## 文风约束\n"
            f"- POV：{style.get('pov', '第三人称限知')}\n"
            f"- 时态：{style.get('tense', '过去时')}\n"
            f"- 句长：约 {style.get('avg_sentence_length', 25):.0f} 字\n"
            f"- 对白比：约 {style.get('dialog_ratio', 0.4):.0%}\n"
            f"- 描写浓度：{style.get('description_density', '适中')}\n"
            f"- 标志性句式（必须模仿至少 2 条）：{', '.join(style.get('signature_phrases', [])[:5])}\n"
            f"- 调性关键词：{', '.join(style.get('tone_keywords', []))}\n\n"
            "## 必须命中的核心爽点公式（按重要性）\n"
            f"{hooks if hooks else '（见 tropes.json + creation_constitution.md）'}\n\n"
            "## 该书相比同题常规的差异化（必须保留）\n"
            f"{diffs if diffs else '（请优先参考 creation_constitution.md 第 1-2 节）'}\n\n"
            "## 必须避免的雷区（来自弃书风险分析）\n"
            f"{avoid if avoid else '（无显著风险，但仍需注意基础雷区）'}\n\n"
            "## 写作流程\n"
            "1. 写章节前先设计 1-3 个本章爽点 / 钩子（intensity 1-5）\n"
            "2. 章末必有 cliffhanger（intensity ≥ 2）\n"
            "3. 描写 + 对白 + 心理 + 反馈四要素至少占 3 项\n"
            "4. 单章 2500-3500 字，长章必拆\n\n"
            "## 你已配套获得\n"
            "- `creation_constitution.md` — 完整创作宪法（务必先读）\n"
            "- `style_few_shot.md` — 3+ 段示范文风\n"
            "- `characters.yaml` — 角色卡 + 弧光节点\n"
            "- `tropes.json` — 套路使用清单\n"
            "- `plot_skeleton.md` — 主线骨架\n"
        )

    def _style_few_shot(self, a: NovelAnalysis) -> str:
        samples = (a.style.sample_paragraphs or [])[:5]
        if not samples:
            samples = []
            for ch in a.chapters[:80]:
                if ch.quotes:
                    snippet = ch.quotes[0].text
                    samples.append(snippet)
                if len(samples) >= 5:
                    break
        body = "\n\n---\n\n".join(f"### 示范段落 {i+1}\n\n> {s}" for i, s in enumerate(samples))
        return (
            "# 文风示范段落（写新章节前请先重读这些段落进入语感）\n\n"
            f"{body}\n"
        )

    def _characters_payload(self, a: NovelAnalysis) -> dict:
        return {
            "characters": [
                {
                    "id": c.character_id,
                    "name": c.name,
                    "aliases": c.aliases,
                    "role": c.role,
                    "one_liner": c.one_liner,
                    "motivation": c.motivation,
                    "flaws": c.flaws,
                    "appearance_chapters": c.appearance_chapters[:30],
                    "arc": [
                        {
                            "stage": p.stage,
                            "chapter_idx": p.chapter_idx,
                            "state": p.state_summary,
                            "psychological_change": p.psychological_change,
                        }
                        for p in c.arc
                    ],
                    "relationships": c.relationships,
                }
                for c in a.characters
            ]
        }

    def _tropes_payload(self, a: NovelAnalysis) -> dict:
        genre_cfg = load_genre(a.meta.genre)
        all_tropes = {t["id"]: t for t in genre_cfg.get("tropes", [])}
        used_ids = {t.trope_id for t in a.top_tropes}
        return {
            "genre": a.meta.genre,
            "must_use": [
                {
                    "trope_id": t.trope_id,
                    "trope_name": t.trope_name,
                    "instance_summary": t.instance_summary,
                    "evidence_chapters": t.evidence_chapter[:5],
                }
                for t in a.top_tropes[:10]
            ],
            "available": [
                {"trope_id": tid, "trope_name": v["name"], "desc": v.get("desc", "")}
                for tid, v in all_tropes.items()
                if tid not in used_ids
            ],
            "anti_patterns": genre_cfg.get("anti_patterns", []),
        }

    def _plot_skeleton(self, a: NovelAnalysis) -> str:
        lines = ["# 主线 / 暗线骨架\n"]
        for line in a.plotlines:
            kind = line.line.value if hasattr(line.line, "value") else str(line.line)
            lines.append(f"\n## {kind.upper()} — {line.name}\n")
            if line.summary:
                lines.append(f"{line.summary}\n")
            for e in line.events:
                lines.append(f"- ch{e.chapter_idx}：**{e.title}** — {e.summary}")
            if line.intersections:
                lines.append("\n**与他线交汇点：**")
                for ix in line.intersections:
                    lines.append(
                        f"- ch{ix.get('chapter_idx', '?')} ↔ {ix.get('with_line', '')}：{ix.get('note', '')}"
                    )
        return "\n".join(lines)
