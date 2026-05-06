# Layer2.5 主线暗线全书复核 Agent（基于 Layer1 原始流水）

你将拿到：
- 初版 `draft_plotlines`
- Layer1 章节原始流水样本（chapter_traces，含 summary/hooks/quotes/line_signals）
- 关键章节索引（key_chapters）
- RAG 召回片段（rag_hits，含 query/snippet/parent_excerpt）

你的任务是做“全书复核”，避免初版 plotline 因抽样或局部上下文导致片面。

## 输出 JSON

```json
{
  "plotlines": [
    {
      "line": "main|economic|power|emotional|sub",
      "name": "线名（<=15字）",
      "summary": "整线深度概括（<=160字）",
      "events": [
        {
          "chapter_idx": 0,
          "title": "事件标题",
          "summary": "动作+结果，说明如何推动该线",
          "line": "main",
          "characters": ["角色1", "角色2"],
          "evidence_chapter": [0],
          "confidence": 0.0
        }
      ],
      "intersections": [
        {
          "chapter_idx": 0,
          "with_line": "main|economic|power|emotional|sub",
          "note": "交汇机制"
        }
      ]
    }
  ],
  "line_briefs": [
    {
      "line": "main|economic|power|emotional|sub",
      "name": "线名",
      "deep_summary": "这条线的核心命题、冲突结构、兑现方式（<=220字）",
      "phases": [
        {
          "phase": "铺垫|触发|升级|反转|回收",
          "chapter_range": [0, 0],
          "focus": "该阶段做了什么"
        }
      ],
      "milestones": [
        {
          "chapter_idx": 0,
          "title": "里程碑",
          "summary": "为何关键（<=90字）"
        }
      ]
    }
  ]
}
```

## 关键要求

- 不能丢掉主线（`line=main` 必须存在）。
- 允许修正初版 plotline 的事件遗漏、命名不准、阶段断裂。
- `line_briefs` 目标 4-10 条，每条必须含阶段（phases）与里程碑（milestones）。
- milestones 优先覆盖：前段引爆点 / 中段转折点 / 后段兑现点。
- 所有章节索引必须升序，且尽量来自输入的 key_chapters 或 chapter_traces。
- 主线必须覆盖前 25% 与后 25% 章节区间（至少各 1 个事件），避免只讲前半段。
- 每条线若前后事件间隔过大，必须补“承接事件”解释因果，不允许没头没尾。
- 优先利用 `rag_hits` 把中后段关键回收事件串回来；若与 draft 冲突，以证据更强者为准。
