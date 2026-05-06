"""Self-Critique — 跨 agent 交叉校对。

输入若干 claim + 它声称的 evidence_chapter 真实原文片段，让 Critic 判断是否成立。
不通过的 claim 会在第 2 pass 用 revised_text 重生成或保留为低优先级。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import InsightCritique
from ._common import safe_json


class _ChapterTextIndex(Protocol):
    def parent_chapter_text(self, chapter_idx: int) -> str: ...


@dataclass
class CriticAgent:
    router: LLMRouter
    index: _ChapterTextIndex | None = None
    max_snippet_chars: int = 600

    async def critique(
        self, claims: list[dict[str, Any]]
    ) -> list[InsightCritique]:
        if not claims:
            return []
        # 为每条 claim 取 evidence 章节的原文摘录
        enriched = []
        for c in claims:
            ev_chapters = list(c.get("evidence_chapter", []))[:3]
            snippets = []
            if self.index is not None:
                for ci in ev_chapters:
                    text = self.index.parent_chapter_text(int(ci))
                    if text:
                        snippets.append(
                            {"chapter_idx": int(ci), "text": text[: self.max_snippet_chars]}
                        )
            enriched.append(
                {
                    "id": c["id"],
                    "claim_type": c["claim_type"],
                    "content": c["content"],
                    "context_snippets": snippets,
                }
            )

        user = (
            "# 输入\n```json\n"
            + json.dumps({"claims": enriched}, ensure_ascii=False)
            + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("critic") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="critic",
            json_mode=True,
            temperature=0.2,
            max_tokens=2500,
            system=sys_prompt,
        )
        try:
            data = safe_json(resp.text)
            out: list[InsightCritique] = []
            for cr in data.get("critiques", []):
                out.append(InsightCritique(**cr))
            return out
        except Exception:
            return []
