"""差异点提炼 Agent。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import DifferentiationPoint, NovelAnalysis
from ._common import build_global_context, safe_json


@dataclass
class DifferentiationAgent:
    router: LLMRouter

    async def run(self, analysis: NovelAnalysis) -> list[DifferentiationPoint]:
        ctx = build_global_context(analysis)
        user = (
            "# 全书上下文\n```json\n"
            + json.dumps(ctx, ensure_ascii=False)
            + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("insight_differentiation") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="deep",
            json_mode=True,
            temperature=0.4,
            max_tokens=2500,
            system=sys_prompt,
        )
        try:
            data = safe_json(resp.text)
            out: list[DifferentiationPoint] = []
            for d in data.get("differentiations", []):
                d.setdefault("evidence_chapter", [])
                d.setdefault("confidence", 0.6)
                out.append(DifferentiationPoint(**d))
            return out
        except Exception:
            return []
