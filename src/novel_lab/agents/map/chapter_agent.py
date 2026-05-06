"""单章合一 Map Agent — 一次 LLM 调用产出 5 类结构化结论。

设计动机：
- 一章约 2-5k 字 ≈ 3-7k tokens，DeepSeek 完全 hold；五个任务相关性强，合并能省 token + 保持上下文一致。
- 长章节自动二次分块串联（rare）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from ...config import genre_trope_lookup, load_prompt, system_base
from ...llm import LLMRouter
from ...schema import Chapter, ChapterAnalysis


_LONG_CHAPTER_CHAR_THRESHOLD = 12000  # 超过这个字符就分段处理


@dataclass
class ChapterMapAgent:
    router: LLMRouter
    genre: str = "generic"

    def __post_init__(self) -> None:
        self._tropes = genre_trope_lookup(self.genre)
        self._prompt = load_prompt("map_chapter")
        self._system = system_base() + "\n\n# 你当前角色\nLayer1 章节级 Map Agent — 一次性产出 5 类结论的合一版本。"

    # ---------------- public ----------------

    async def analyze(self, chapter: Chapter) -> ChapterAnalysis:
        if not chapter.text.strip():
            return ChapterAnalysis(chapter_idx=chapter.idx, summary="<空章>")
        if len(chapter.text) <= _LONG_CHAPTER_CHAR_THRESHOLD:
            return await self._analyze_one(chapter, chapter.text)
        return await self._analyze_long(chapter)

    # ---------------- internals ----------------

    async def _analyze_one(self, chapter: Chapter, text: str) -> ChapterAnalysis:
        user = self._build_user_message(chapter, text)
        resp = await self.router.complete(
            messages=[{"role": "user", "content": user}],
            role="map",
            json_mode=True,
            temperature=0.2,
            max_tokens=3500,
            system=self._prompt + "\n\n" + self._system,
        )
        analysis = self._parse(resp.text, chapter)
        analysis.raw_token_in = resp.tokens_in
        analysis.raw_token_out = resp.tokens_out
        analysis.cost_usd = resp.cost_usd
        return analysis

    async def _analyze_long(self, chapter: Chapter) -> ChapterAnalysis:
        # 简单粗暴切两段，分别分析后 merge（去重 + intensity 取大）
        text = chapter.text
        mid = len(text) // 2
        # 在最近的换行处切分
        cut = text.rfind("\n", 0, mid + 200)
        if cut <= 0:
            cut = mid
        first, second = text[:cut], text[cut:]
        a = await self._analyze_one(chapter, first)
        b = await self._analyze_one(chapter, second)
        return self._merge(chapter.idx, a, b)

    def _build_user_message(self, chapter: Chapter, text: str) -> str:
        tropes_list = "\n".join(f"- {tid}: {name}" for tid, name in self._tropes.items())
        payload = {
            "chapter_idx": chapter.idx,
            "chapter_title": chapter.title,
            "genre": self.genre,
            "genre_tropes": tropes_list,
            "chapter_text": text,
        }
        # 用半结构化的 markdown，避免 JSON 字符转义 nested 把模型搞糊
        return (
            f"# 输入数据\n"
            f"- chapter_idx: {payload['chapter_idx']}\n"
            f"- chapter_title: {payload['chapter_title']}\n"
            f"- genre: {payload['genre']}\n\n"
            f"## genre_tropes（命中时 trope_id 必须从此选）\n{tropes_list}\n\n"
            f"## chapter_text\n```\n{text}\n```\n\n"
            f"# 任务\n严格按照 system 中给出的 JSON Schema 输出。\n"
        )

    def _parse(self, raw: str, chapter: Chapter) -> ChapterAnalysis:
        try:
            data = self._safe_json(raw)
        except Exception as exc:
            return ChapterAnalysis(
                chapter_idx=chapter.idx,
                summary=f"<JSON 解析失败：{exc}>",
            )

        # 强制 chapter_idx
        data["chapter_idx"] = chapter.idx
        # 兼容 evidence_chapter 缺失：填本章
        for sect in ("mentioned_characters", "scenes", "hooks", "quotes", "tropes", "line_signals"):
            for item in data.get(sect, []) or []:
                if not item.get("evidence_chapter"):
                    item["evidence_chapter"] = [chapter.idx]
                if "confidence" not in item:
                    item["confidence"] = 0.7
                # 套路 id 校验：不在白名单则丢弃
                if sect == "tropes" and item.get("trope_id") not in self._tropes:
                    item["_invalid"] = True
                # 金句必须来自本章原文（按去空白归一化做包含校验）
                if sect == "quotes":
                    text = (item.get("text") or "").strip()
                    if not text:
                        item["_invalid"] = True
                    elif not self._is_quote_from_chapter(text, chapter.text):
                        item["_invalid"] = True
                if sect == "line_signals":
                    sig_text = (item.get("snippet") or "").strip()
                    if sig_text and not self._is_quote_from_chapter(sig_text, chapter.text):
                        item["_invalid"] = True

        if "tropes" in data:
            data["tropes"] = [t for t in data["tropes"] if not t.get("_invalid")]
        if "quotes" in data:
            data["quotes"] = [q for q in data["quotes"] if not q.get("_invalid")]
        if "line_signals" in data:
            data["line_signals"] = [s for s in data["line_signals"] if not s.get("_invalid")]

        try:
            return ChapterAnalysis(**data)
        except ValidationError as exc:
            return ChapterAnalysis(
                chapter_idx=chapter.idx,
                summary=f"<schema 校验失败：{exc.errors()[0]['msg'] if exc.errors() else exc}>",
            )

    @staticmethod
    def _safe_json(text: str):
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        if text.startswith("{"):
            return json.loads(text)
        # 截取大括号包裹的最大块
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise ValueError("no JSON object found in response")

    @staticmethod
    def _is_quote_from_chapter(quote_text: str, chapter_text: str) -> bool:
        if quote_text in chapter_text:
            return True
        norm_q = "".join(quote_text.split())
        norm_ch = "".join(chapter_text.split())
        if not norm_q:
            return False
        return norm_q in norm_ch

    @staticmethod
    def _merge(idx: int, a: ChapterAnalysis, b: ChapterAnalysis) -> ChapterAnalysis:
        merged = ChapterAnalysis(
            chapter_idx=idx,
            summary=" / ".join(filter(None, [a.summary, b.summary])),
            raw_token_in=a.raw_token_in + b.raw_token_in,
            raw_token_out=a.raw_token_out + b.raw_token_out,
            cost_usd=a.cost_usd + b.cost_usd,
        )

        def merge_list(la, lb, key):
            seen = {}
            for it in la + lb:
                k = key(it)
                if k in seen:
                    # 强度取大
                    if hasattr(seen[k], "intensity") and hasattr(it, "intensity"):
                        if it.intensity > seen[k].intensity:
                            seen[k] = it
                    continue
                seen[k] = it
            return list(seen.values())

        merged.mentioned_characters = merge_list(
            a.mentioned_characters, b.mentioned_characters, lambda x: x.name
        )
        merged.scenes = a.scenes + b.scenes
        merged.hooks = merge_list(a.hooks, b.hooks, lambda h: (h.type, h.summary[:30]))
        merged.quotes = merge_list(a.quotes, b.quotes, lambda q: q.text[:30])
        merged.tropes = merge_list(a.tropes, b.tropes, lambda t: t.trope_id)
        merged.line_signals = merge_list(
            a.line_signals,
            b.line_signals,
            lambda s: (
                s.line.value if hasattr(s.line, "value") else str(s.line),
                s.status,
                (s.event or "")[:30],
            ),
        )
        return merged
