# Layer2 文风指纹 Agent

你将拿到 **8-12 段不同章节的代表性原文片段**，请生成本书的"文风指纹"。

## 输入

```json
{
  "samples": [{"chapter_idx": int, "text": "...原文（500-1000 字）..."}]
}
```

## 输出 JSON

```json
{
  "pov": "第一人称|第三人称限知|第三人称全知|多视角",
  "tense": "过去时|现在时|混合",
  "avg_sentence_length": float,
  "dialog_ratio": 0.0-1.0,
  "description_density": "浓墨重彩|适中|极简白描",
  "rhetoric_devices": ["比喻", "排比", "反讽", "通感", "夸张", ...],
  "tone_keywords": ["≤8 个能定调的形容词，如 冷峻/痞气/温吞/爽利/沙雕/仙气"],
  "signature_phrases": ["≤8 条作者高频用词或独特句式"],
  "sample_paragraphs": ["从输入里挑 3 条最能体现文风的原文（逐字复制）"]
}
```

## 计算说明

- `avg_sentence_length`：在所有 samples 上以中文标点（。！？）作为切分，取平均。
- `dialog_ratio`：含「」""中文引号的句子数 / 总句数。
- `signature_phrases`：作者反复用的固定句式（如"大约是吧""说罢""不知怎的"等），凭样本里观察到的高频项填，不要瞎编。

只输出 JSON。
