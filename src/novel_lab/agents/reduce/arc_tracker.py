"""人物弧光追踪 — 把全书 character mention 流水做归并 + LLM 生成 5 状态点。"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from ...config import load_prompt, system_base
from ...llm import LLMRouter
from ...schema import ChapterAnalysis, CharacterProfile, NovelMeta


# 同一人物的别名归并的简单启发式：完全同名 → 合并；包含关系 → 合并。
def _normalize_name(name: str) -> str:
    return name.strip().replace(" ", "").replace("\u3000", "")


def _merge_alias_groups(streams: dict[str, list]) -> dict[str, list]:
    """根据名字归并同一角色（包含关系 + 别名集合）。"""
    keys = sorted(streams.keys(), key=lambda k: -len(k))  # 长名优先做 canonical
    groups: dict[str, list[str]] = {}
    parent: dict[str, str] = {}

    for k in keys:
        n = _normalize_name(k)
        chosen = None
        for existing in list(groups.keys()):
            e = _normalize_name(existing)
            if n == e or n in e or e in n:
                chosen = existing
                break
        if chosen is None:
            groups[k] = [k]
            parent[k] = k
        else:
            groups[chosen].append(k)
            parent[k] = chosen

    merged: dict[str, list] = {}
    for canonical, alias_list in groups.items():
        appearances = []
        for alias in alias_list:
            appearances.extend(streams[alias])
        appearances.sort(key=lambda a: a["chapter_idx"])
        merged[canonical] = [{"alias_groups": alias_list, "appearances": appearances}]
    return merged


@dataclass
class ArcTracker:
    router: LLMRouter
    top_k_characters: int = 12  # 仅对出现最多的 N 个人物做完整弧光（成本控制）
    batch_size: int = 4         # 一次让 Claude 处理几个人

    async def run(
        self, meta: NovelMeta, chapters_analysis: list[ChapterAnalysis]
    ) -> list[CharacterProfile]:
        # 1) 收集 mention 流水
        streams: dict[str, list[dict]] = defaultdict(list)
        for ch in chapters_analysis:
            for m in ch.mentioned_characters:
                streams[m.name].append(
                    {
                        "chapter_idx": ch.chapter_idx,
                        "actions": m.actions,
                        "emotional_state": m.emotional_state or "",
                        "relationship_updates": m.relationship_updates,
                        "role_hint": m.role_hint or "",
                    }
                )

        if not streams:
            return []

        # 2) 别名归并
        merged = _merge_alias_groups(streams)
        # 3) 取 top K
        ranked = sorted(merged.items(), key=lambda kv: -len(kv[1][0]["appearances"]))
        primary = ranked[: self.top_k_characters]

        # 4) 分 batch 喂 Claude
        profiles: list[CharacterProfile] = []
        for i in range(0, len(primary), self.batch_size):
            batch = primary[i : i + self.batch_size]
            characters_stream = []
            for name, payload in batch:
                stream = payload[0]
                characters_stream.append(
                    {
                        "name": name,
                        "alias_groups": stream["alias_groups"],
                        "appearances": stream["appearances"][:60],  # 截断
                    }
                )

            user = (
                "# 输入\n```json\n"
                + json.dumps(
                    {
                        "book_title": meta.title,
                        "genre": meta.genre,
                        "total_chapters": meta.total_chapters,
                        "characters_stream": characters_stream,
                    },
                    ensure_ascii=False,
                )
                + "\n```\n\n# 任务\n严格按 system 中的 schema 输出。"
            )
            sys_prompt = load_prompt("reduce_arc") + "\n\n" + system_base()
            resp = await self.router.complete(
                messages=[{"role": "user", "content": user}],
                role="reduce",
                json_mode=True,
                temperature=0.25,
                max_tokens=4096,
                system=sys_prompt,
            )
            try:
                data = resp.parse_json()
                for c in data.get("characters", []):
                    if not c.get("evidence_chapter"):
                        c["evidence_chapter"] = c.get("appearance_chapters", [])[:5]
                    profiles.append(CharacterProfile(**c))
            except Exception as exc:
                # 退而求其次：写一个最小 profile 不丢这一批
                for name, payload in batch:
                    stream = payload[0]
                    profiles.append(
                        CharacterProfile(
                            character_id=f"char_{_normalize_name(name)}",
                            name=name,
                            aliases=stream["alias_groups"],
                            role="side",
                            one_liner=f"<弧光生成失败：{exc}>",
                            appearance_chapters=[a["chapter_idx"] for a in stream["appearances"]],
                            evidence_chapter=[a["chapter_idx"] for a in stream["appearances"][:3]],
                            confidence=0.3,
                        )
                    )
        return profiles
