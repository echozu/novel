"""弃书风险点 Agent。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import DropRisk, NovelAnalysis
from ._common import build_global_context, safe_json


@dataclass
class DropRiskAgent:
    router: LLMRouter

    async def run(self, analysis: NovelAnalysis) -> list[DropRisk]:
        ctx = build_global_context(analysis)
        # 多塞一份 pacing 已识别的低强度区段，便于交叉印证
        ctx["computed_drop_zones"] = analysis.pacing.drop_risk_zones
        user = (
            "# 全书上下文（含 pacing 已识别的低强度区段）\n```json\n"
            + json.dumps(ctx, ensure_ascii=False)
            + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("insight_drop_risk") + "\n\n" + system_base()
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
            out: list[DropRisk] = []
            for r in data.get("risks", []):
                rng = r.get("chapter_range") or [0, 0]
                if isinstance(rng, list) and len(rng) >= 2:
                    r["chapter_range"] = (int(rng[0]), int(rng[1]))
                else:
                    continue
                r.setdefault("evidence_chapter", list(r["chapter_range"]))
                r.setdefault("confidence", 0.6)
                out.append(DropRisk(**r))
            return out
        except Exception:
            return []
