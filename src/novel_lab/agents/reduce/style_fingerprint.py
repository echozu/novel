"""文风指纹 Agent — 抽 8-12 段代表性原文走 Claude。"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import Chapter, StyleFingerprint as StyleSchema


@dataclass
class StyleFingerprint:
    router: LLMRouter
    sample_count: int = 10

    async def run(self, chapters: list[Chapter]) -> StyleSchema:
        chapters = [c for c in chapters if c.text.strip()]
        if not chapters:
            return StyleSchema()
        # 均匀抽样：开头/中/末尾各占
        n = len(chapters)
        if n <= self.sample_count:
            picks = chapters
        else:
            step = n / self.sample_count
            indices = sorted({int(i * step) for i in range(self.sample_count)})
            picks = [chapters[i] for i in indices if i < n]

        samples = []
        for ch in picks:
            text = ch.text.strip().replace("\n", " ")
            if len(text) > 1200:
                # 截一段中间区域
                start = random.randint(0, max(0, len(text) - 1000))
                text = text[start : start + 800]
            samples.append({"chapter_idx": ch.idx, "text": text})

        user = (
            "# 输入\n```json\n"
            + json.dumps({"samples": samples}, ensure_ascii=False, indent=2)
            + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
        )
        sys_prompt = load_prompt("reduce_style") + "\n\n" + system_base()
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="reduce",
            json_mode=True,
            temperature=0.3,
            max_tokens=2200,
            system=sys_prompt,
        )
        try:
            data = resp.parse_json()
            return StyleSchema(**data)
        except Exception:
            return StyleSchema(tone_keywords=["<解析失败>"])
