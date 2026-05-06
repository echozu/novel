# Layer1 章节级 Map Agent（合一版本）

你将拿到**单章原文**，请同时完成 5 项任务，**一次性输出**一份结构化 JSON。

## 输入
- `chapter_idx`：本章序号（0-based）
- `chapter_title`：本章标题
- `chapter_text`：本章正文
- `genre`：题材（xuanhuan | yanqing | dushi | generic）
- `genre_tropes`：题材套路库 id→name 速查表（命中时 `trope_id` 必须从此列表选）

## 输出 JSON Schema（严格遵守，字段不可缺）

```json
{
  "chapter_idx": int,
  "summary": "本章 80-150 字精炼摘要，含主要事件 + 状态变化",
  "mentioned_characters": [
    {
      "name": "人物名",
      "aliases": ["可能的别名/绰号"],
      "role_hint": "主角|反派|配角|路人",
      "actions": ["≤5 条该角色本章关键动作"],
      "emotional_state": "本章心理/情感状态",
      "relationship_updates": [{"with": "对方名", "change": "由敌转盟/暧昧升级/反目..."}],
      "evidence_chapter": [chapter_idx],
      "confidence": 0.0-1.0
    }
  ],
  "scenes": [
    {
      "location": "场景地点",
      "time_clue": "时间线索 可空",
      "summary": "场景 1 句话",
      "participants": ["参与者"],
      "evidence_chapter": [chapter_idx],
      "confidence": 0.0-1.0
    }
  ],
  "hooks": [
    {
      "type": "opening|cliffhanger|reveal|power_up|face_slap|revenge|cp_progress|crisis|mystery|twist",
      "intensity": 1-5,
      "summary": "≤30 字爽点/钩子描述",
      "snippet": "原文片段 ≤120 字（直接复制原文）",
      "position": "opening|early|middle|late|ending",
      "evidence_chapter": [chapter_idx],
      "confidence": 0.0-1.0
    }
  ],
  "quotes": [
    {
      "text": "原文金句（≤80 字，必须是原文逐字）",
      "speaker": "说话人或'叙述者'",
      "why_good": "为什么是金句：反差/价值观/共鸣/凝练/音律/节奏/意象",
      "qualities": ["从下列枚举中选 1-3 个：优美|凝练|有画面感|有哲思|情绪冲击|反差强|角色辨识度高|可传播"],
      "evidence_chapter": [chapter_idx],
      "confidence": 0.0-1.0
    }
  ],
  "tropes": [
    {
      "trope_id": "必须来自 genre_tropes 速查表",
      "trope_name": "对应名称",
      "instance_summary": "本章中如何具体体现",
      "evidence_chapter": [chapter_idx],
      "confidence": 0.0-1.0
    }
  ],
  "line_signals": [
    {
      "line": "main|economic|power|emotional|sub|none",
      "status": "setup|advance|twist|payoff|cooldown",
      "event": "本章该线节点发生了什么（动作）",
      "impact": "该节点造成了什么结果/影响",
      "characters": ["相关角色，可空"],
      "snippet": "原文证据片段 ≤120 字（直接复制原文）",
      "evidence_chapter": [chapter_idx],
      "confidence": 0.0-1.0
    }
  ]
}
```

## 关键要求

- **hooks 是核心**：哪怕本章没有大爽点，也要找出哪怕 1-2 个微小钩子（章末悬念、伏笔等），并标 intensity=1-2。一章 hooks 数量目标 1-4 个。
- **章末钩子（cliffhanger）必须捕获**：读者决定看不看下一章看的就是末尾 3 行，请重点检查最后 200 字。
- **quotes 必须来自原文句级证据**：按“句子质量”提取，不依赖 summary。优先选语言本身有审美或传播价值的句子。
- **quotes 宁缺毋滥**：没有真正闪光的就空数组。不要把普通对话当金句。
- **snippet 必须是原文**：不要改写、不要总结、直接复制（≤120 字截断）。
- **没有命中的套路就空数组**，绝不硬凑。
- **line_signals 是后续串主线的核心锚点**：每章建议 1-4 条，优先 main/economic/power/emotional/sub，实在无推进再用 none。
- line_signals 的 `event` 与 `impact` 必须区分清楚：前者写“发生了什么动作”，后者写“导致了什么变化”。
- **confidence 默认 0.7**，原文证据非常清晰时 0.9+，模糊推测 0.4。
