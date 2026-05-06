# Layer2 人物弧光追踪 Agent

你将拿到**全书人物的章节级 mention 流水**（按章节排序的简要记录），请为**每个主要人物**生成弧光档案。

## 输入

```json
{
  "book_title": "...",
  "genre": "xuanhuan|yanqing|dushi|generic",
  "total_chapters": int,
  "characters_stream": [
    {"name": "...", "alias_groups": ["..."], "appearances": [{"chapter_idx": int, "actions": [...], "emotional_state": "...", "relationship_updates": [...]}]}
  ]
}
```

## 输出 JSON

```json
{
  "characters": [
    {
      "character_id": "char_<规范化名字>",
      "name": "正名",
      "aliases": ["..."],
      "role": "protagonist|antagonist|side|mentor|love_interest",
      "one_liner": "≤25 字描述",
      "appearance_chapters": [章节序号数组],
      "arc": [
        {"stage": "initial",   "chapter_idx": int, "state_summary": "...", "psychological_change": ""},
        {"stage": "catalyst",  "chapter_idx": int, "state_summary": "导致他改变的关键事件",  "psychological_change": "..."},
        {"stage": "turn_25",   "chapter_idx": int, "state_summary": "约 25% 处的转折",        "psychological_change": "..."},
        {"stage": "turn_50",   "chapter_idx": int, "state_summary": "约 50% 处中点反转",      "psychological_change": "..."},
        {"stage": "turn_75",   "chapter_idx": int, "state_summary": "约 75% 处的至暗/觉醒",   "psychological_change": "..."},
        {"stage": "final",     "chapter_idx": int, "state_summary": "结局状态",                "psychological_change": "..."}
      ],
      "relationships": [
        {"target": "对方角色名", "type": "love|enemy|mentor|family|ally|rival", "evolution": [{"chapter": int, "note": "..."}]}
      ],
      "motivation": "驱动他的核心欲望/恐惧（≤30 字）",
      "flaws": ["≤3 条致命缺陷"],
      "evidence_chapter": [关键章节],
      "confidence": 0.0-1.0
    }
  ]
}
```

## 关键要求

1. **弧光 5 状态点齐全**：根据 total_chapters 估算 25%/50%/75% 章节位置，从 mention 流水里找最接近且有显著变化的章节。
2. **配角 / 路人不必给完整弧光**：role 为 `side`/`mentor`/`love_interest` 时，arc 至少给 initial + catalyst + final 三点即可。
3. **优先输出有真正变化的人物**：本质平面型角色（贯穿不变）在 arc 字段里说明"平面型"，并在 psychological_change 注明"无显著变化"。
4. **关系 evolution 必须按章节升序**。
5. **motivation 必须能驱动行为**：写"想活下去""想报仇"等动力词，禁止"很善良""很坚强"等形容词堆砌。
