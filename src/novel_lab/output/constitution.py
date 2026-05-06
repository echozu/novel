"""创作宪法 markdown 生成。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..agents.insight._common import build_global_context
from ..config import load_prompt, system_base
from ..llm import LLMRouter
from ..schema import NovelAnalysis


@dataclass
class ConstitutionGenerator:
    router: LLMRouter

    async def generate(self, analysis: NovelAnalysis) -> str:
        ctx = build_global_context(analysis)
        ctx["differentiation"] = [d.model_dump() for d in analysis.differentiation]
        ctx["reader_hooks"] = [h.model_dump() for h in analysis.reader_hooks]
        ctx["drop_risks"] = [r.model_dump() for r in analysis.drop_risks]
        ctx["top_quotes"] = [q.model_dump() for q in analysis.top_quotes[:15]]
        ctx["top_tropes"] = [t.model_dump() for t in analysis.top_tropes[:15]]

        user = (
            "# 全书完整分析\n```json\n"
            + json.dumps(ctx, ensure_ascii=False)
            + "\n```\n\n# 任务\n按 system 中的格式生成创作宪法 Markdown。"
        )
        sys_prompt = load_prompt("constitution") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="deep",
            json_mode=False,
            temperature=0.4,
            max_tokens=4500,
            system=sys_prompt,
        )
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        return text.strip()
