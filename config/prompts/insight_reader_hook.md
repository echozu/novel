# Layer3 读者爽点归因 Agent

把这本书的爽点 hooks（按 intensity 降序的 top 30）、节奏曲线、套路命中合并分析，**逆向归因**：读者为什么会觉得这些章节爽？背后的心理机制是什么？

## 输出 JSON

```json
{
  "patterns": [
    {
      "hook_pattern": "命名一个可复用的爽点模式，如：'装弱诱敌·三重反转打脸' / '系统奖励·渐进式即时反馈' / 'CP 共患难·情感张力封顶'",
      "psychological_mechanism": "替代体验|公平感|归属感|智识满足|情感张力|即时反馈|身份认同|猎奇好奇|安全感",
      "typical_chapters": [出现该模式的章节],
      "evidence_chapter": [典型章节],
      "confidence": 0.0-1.0
    }
  ]
}
```

## 关键要求

- 5-10 条 patterns。
- **hook_pattern 必须是可复用的"公式"**：能让另一个 AI 据此写出同款爽点的描述。
- **不要复述章节内容**：要抽象出可迁移的模式。
- **psychological_mechanism 必须从给定枚举里选**。
- 如果某 pattern 出现频次很高，请在 `typical_chapters` 列出 ≥ 5 个章节作为佐证。
