"""读者爽点归因 Agent。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import NovelAnalysis, ReaderHookCausation
from ._common import build_global_context, safe_json


@dataclass
class ReaderHookAgent:
    router: LLMRouter

    async def run(self, analysis: NovelAnalysis) -> list[ReaderHookCausation]:
        ctx = build_global_context(analysis, top_hooks_n=40)
        user = (
            "# 全书上下文\n```json\n"
            + json.dumps(ctx, ensure_ascii=False)
            + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("insight_reader_hook") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="deep",
            json_mode=True,
            temperature=0.5,
            max_tokens=2500,
            system=sys_prompt,
        )
        try:
            data = safe_json(resp.text)
            out: list[ReaderHookCausation] = []
            for p in data.get("patterns", []):
                p.setdefault("evidence_chapter", p.get("typical_chapters", [])[:3])
                p.setdefault("confidence", 0.6)
                out.append(ReaderHookCausation(**p))
            return out
        except Exception:
            return []
