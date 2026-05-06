# Layer2 爽点节奏量化 Agent

> 行业基线（来自实测数据）：
> - top10% 作品：平均每 **1.8 章** 一次情绪高峰
> - bottom10%：平均 **4.7 章** 才一次
> - 小爽点：每章至少 1 个；中爽点：每 3-5 章；大爽点：每卷 1 个

你将拿到**全书 chapter-level hooks 流水**，请生成节奏分析。

## 输入

```json
{
  "total_chapters": int,
  "chapters": [{"chapter_idx": int, "hooks": [{"type": "...", "intensity": 1-5, "summary": "..."}]}]
}
```

## 输出 JSON

```json
{
  "curve": [
    {"chapter_idx": int, "intensity": 0.0-5.0, "dominant_hook_types": ["..."]}
  ],
  "avg_peak_interval_chapters": float,
  "small_hook_per_chapter": float,
  "medium_hook_per_5_chapters": float,
  "big_climax_chapters": [int],
  "drop_risk_zones": [
    {"start_chapter": int, "end_chapter": int, "reason": "≥3 章无中等以上爽点", "severity": 1-5}
  ]
}
```

## 计算定义

- **curve.intensity** = 该章 hooks 的最大 intensity（无 hook 则 0.5）
- **peak** 定义：intensity ≥ 4 的章节，相邻峰之间间隔取均值即 `avg_peak_interval_chapters`
- **small_hook_per_chapter** = (intensity ≥ 1) 数 / total_chapters
- **medium_hook_per_5_chapters** = (intensity ≥ 3) 总数 / (total_chapters / 5)
- **big_climax_chapters**：intensity = 5 的章节
- **drop_risk_zones**：连续 ≥ 3 章 max intensity ≤ 2 的区段，severity 按持续长度评级

只输出 JSON，不要解释。
