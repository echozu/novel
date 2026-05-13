# 潘子技术深挖稿：Layer2 Reduce 聚合与 Finalize 质量门

> 适合潘子答辩使用。重点讲清楚：Layer2 如何把章节级证据汇聚成全书结构，以及 Finalize 如何保证 LLM 失败时报告仍然完整。

---

## 一、这部分负责什么

潘子负责的是 **Layer2 Reduce 聚合 + Finalize 质量门**。

Layer1 已经把每一章变成了结构化 JSON，包括摘要、人物提及、场景、Hook、金句、套路和 `line_signals`。但是这些还是分散的章节级证据。

Layer2 的任务是把这些证据汇聚成全书级结构：

```text
每章 ChapterAnalysis
    ↓
Layer2 Reduce
    ├── 节奏分析 PacingAnalyzer
    ├── 人物弧光 ArcTracker
    ├── 主线/暗线 PlotlineSeparator
    └── 文风指纹 StyleFingerprint
    ↓
情节线连续性修复
    ↓
Finalize 质量门兜底
    ↓
稳定可展示的 NovelAnalysis
```

这一层的关键词是 **聚合、压缩、修复、兜底**。

---

## 二、Layer2 为什么叫 Reduce

Layer1 是 Map：每章独立分析，输出一堆章节级结构。

Layer2 是 Reduce：把很多章节结果合并成全书理解。

比如：

- Hook 经过 Reduce 变成爽点曲线和弃书风险区间。
- 人物提及经过 Reduce 变成主要人物弧光。
- `line_signals` 经过 Reduce 变成主线、经济暗线、权力暗线、情感暗线。
- 原文抽样经过 Reduce 变成文风指纹。

这个设计的价值是：全书分析不再直接面对百万字原文，而是面对 Layer1 压缩后的结构化证据。

---

## 三、四个 Reduce 子任务如何并发执行

Layer2 里有四个子任务：

1. 节奏分析：纯算法，不调用 LLM。
2. 人物弧光：调用 LLM，分析主要人物成长线。
3. 主线/暗线：调用 LLM，聚合 plotlines。
4. 文风指纹：调用 LLM，分析叙事风格。

在 pipeline 里，节奏分析先做，因为它是确定性计算，很快完成。之后人物、情节线、文风三个任务用 `asyncio.gather` 并发执行。

```263:293:src/novel_lab/orchestrator/pipeline.py
# 1) 节奏（纯计算，先做，便于其他 agent 引用）
self.progress("reduce_agent_start", {"agent": "pacing", "task": "计算爽点曲线与弃书风险"})
pacing = PacingAnalyzer().run(chapters_a)
self.progress("reduce_pacing", {
    "avg_peak_interval_chapters": pacing.avg_peak_interval_chapters,
    "drop_zones": len(pacing.drop_risk_zones),
})

# 2) Arc / 情节线 / 文风（并发；目标模型由 ``tier`` 决定，basic=全 DeepSeek）
async def run_reduce_agent(agent: str, task: str, coro):
    self.progress("reduce_agent_start", {"agent": agent, "task": task})
    try:
        result = await coro
        self.progress("reduce_agent_done", {"agent": agent})
    except Exception as exc:
        self.progress("reduce_agent_error", {"agent": agent, "err": repr(exc)})
        raise
    return result

arc_task = run_reduce_agent(
    "arc", "追踪人物弧光与关系变化", ArcTracker(self.router).run(meta, chapters_a)
)
plot_task = run_reduce_agent(
    "plotline", "聚合主线/暗线/副线", PlotlineSeparator(self.router).run(meta, chapters_a)
)
style_task = run_reduce_agent(
    "style", "抽样分析文风指纹", StyleFingerprint(self.router).run(meta.chapters)
)
characters, plotlines, style = await asyncio.gather(
    arc_task, plot_task, style_task, return_exceptions=True
)
```

这里 `return_exceptions=True` 很关键。它表示某个子任务失败时，不会让整个 Layer2 崩溃。比如文风分析失败，人物弧光和情节线仍然可以保留。

---

## 四、节奏分析：为什么不用 LLM

节奏分析是 Layer2 中唯一完全不依赖 LLM 的模块。

原因是它的输入和计算逻辑都很明确：Layer1 已经给每章抽取了 Hook，并且每个 Hook 有 `intensity`。节奏分析只需要遍历所有章节，取每章最大 Hook 强度，形成一条曲线。

它主要计算：

- `curve`：每章最大爽点强度。
- 平均高峰间隔：强度大于等于 4 的章节之间平均隔多少章。
- 大高潮章节：强度等于 5 的章节。
- 弃书风险区间：连续多章强度低于阈值。

为什么不用 LLM？

因为节奏分析要求稳定、可复现。如果同一本书每次跑出来的爽点曲线不一样，报告就不可信。而这里的规则很清楚，用算法比用 LLM 更合适。

答辩时可以这样说：

> LLM 适合做语言理解和归纳，但不适合做本来就确定的统计计算。节奏分析依赖的是 Hook 强度字段，所以用确定性算法更稳定，也更便宜。

---

## 五、人物弧光：从章节提及到人物传记

人物弧光模块的输入是 Layer1 的 `mentioned_characters`。单章里只知道“这个人物在本章做了什么”，Layer2 要把它变成全书维度的人物档案。

处理流程是：

1. 收集所有章节的人物提及。
2. 做简单别名归并，比如名字包含关系。
3. 按出现频率排序，只取前 12 个主要人物。
4. 分批送给 LLM，每批默认 4 人，控制 prompt 长度。
5. 输出人物定位、动机、缺陷、关系演化、五状态弧光。

五状态弧光是：

```text
initial → catalyst → turn_25 → turn_50 → turn_75 → final
```

这个结构的意义是把人物成长从“泛泛总结”变成时间轴上的状态变化。老师如果问为什么要这样设计，可以回答：

> 长篇小说里人物是否立得住，不是看人物标签，而是看他在关键阶段有没有状态变化。25%、50%、75%、结尾这些点能帮助我们检查人物弧光是否完整。

别名归并目前是启发式的，不是完美实体消解。答辩时可以主动说明这是当前限制：对于复杂别名、同名人物，后续可以接入更强的实体消解模型或人工别名字典。

---

## 六、主线/暗线分离：Layer2 最复杂的模块

情节线聚合是 Layer2 最难的部分，因为长篇小说不是只有一条主线，通常还会有经济暗线、权力暗线、情感暗线、副线等。

输入来自每章的三个核心字段：

- `summary`：本章摘要。
- `hooks`：本章爽点和关键节点。
- `line_signals`：本章推进了哪条线、事件是什么、影响是什么。

代码里 `_briefs` 会把每章压缩成更适合 LLM 聚合的简要结构：

```27:53:src/novel_lab/agents/reduce/plotline_separator.py
def _briefs(items: list[ChapterAnalysis]) -> list[dict]:
    out = []
    for ch in items:
        hooks_brief = [
            {"type": h.type.value if hasattr(h.type, "value") else str(h.type),
             "intensity": h.intensity,
             "summary": h.summary[:60]}
            for h in ch.hooks
        ]
        line_signals = [
            {
                "line": s.line.value if hasattr(s.line, "value") else str(s.line),
                "status": s.status,
                "event": (s.event or "")[:72],
                "impact": (s.impact or "")[:72],
            }
            for s in (ch.line_signals or [])
        ]
        out.append(
            {
                "chapter_idx": ch.chapter_idx,
                "summary": ch.summary,
                "hooks": hooks_brief,
                "line_signals": line_signals,
            }
        )
    return out
```

这里特别要讲 `line_signals` 的作用。以前如果只给 LLM 章节摘要，它可能只能总结大概剧情，很容易漏掉暗线。现在每章都提前标了线路信号，Layer2 就有了章节级锚点，不再完全靠模型猜。

---

## 七、长篇分批：避免一次性 prompt 过长

如果小说章节数不多，`PlotlineSeparator` 可以一次性处理。

但超过一定章节数时，系统不会把所有章节摘要一次性送给 LLM，而是按 50 章一批，先生成局部情节线，再合并成全书情节线。

```56:71:src/novel_lab/agents/reduce/plotline_separator.py
@dataclass
class PlotlineSeparator:
    router: LLMRouter

    async def run(
        self, meta: NovelMeta, chapters_analysis: list[ChapterAnalysis]
    ) -> list[PlotLine]:
        chapters_analysis = sorted(chapters_analysis, key=lambda c: c.chapter_idx)
        if len(chapters_analysis) <= _BATCH_CHAPTERS + 30:
            return await self._one_shot(meta, chapters_analysis)
        # 分批生成局部 plotlines
        partials: list[list[PlotLine]] = []
        for i in range(0, len(chapters_analysis), _BATCH_CHAPTERS):
            batch = chapters_analysis[i : i + _BATCH_CHAPTERS]
            partials.append(await self._one_shot(meta, batch))
        return await self._merge_partials(meta, partials)
```

这里体现的是 Map-Reduce 的二次使用：

- 第一层 Map-Reduce：章节 Map → 全书 Reduce。
- 情节线内部再做一次：50 章局部 Reduce → 全书合并 Reduce。

老师如果问“为什么是 50 章一批”，可以回答：

> 50 章是 prompt 长度和结构完整性的折中。太少会让局部线太碎，太多会让 prompt 过长并增加中段注意力衰减风险。这个值目前是工程经验参数，后续可以根据 token 预算动态调整。

---

## 八、LLM 结果解析与 fallback

情节线 LLM 输出仍然可能解析失败，所以 `_parse` 方法会尽量从回复中截取 JSON，再逐条构造 `PlotLine` 和 `PlotEvent`。

```156:197:src/novel_lab/agents/reduce/plotline_separator.py
@staticmethod
def _parse(text: str) -> list[PlotLine]:
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        data = json.loads(text[start : end + 1] if start != -1 else text)
    except Exception:
        return []

    out: list[PlotLine] = []
    for item in data.get("plotlines", []):
        kind = item.get("line", "main")
        try:
            kind_enum = PlotLineKind(kind)
        except ValueError:
            kind_enum = PlotLineKind.SUB
        events: list[PlotEvent] = []
        for e in item.get("events", []) or []:
            e.setdefault("evidence_chapter", [e.get("chapter_idx", 0)])
            e.setdefault("confidence", 0.7)
            e["line"] = kind_enum.value
            try:
                events.append(PlotEvent(**e))
            except Exception:
                continue
        out.append(
            PlotLine(
                line=kind_enum,
                name=item.get("name", "未命名"),
                summary=item.get("summary", ""),
                events=events,
                intersections=item.get("intersections", []),
            )
        )
    return out
```

如果分批合并阶段失败，还有 `_fallback_merge`：把所有 partial 的事件按线名分桶，再按章节号排序。这不是最智能的结果，但能保证不完全为空。

```132:154:src/novel_lab/agents/reduce/plotline_separator.py
merged = self._parse(resp.text)
if not merged:
    # fallback 把所有 partial 的事件直接拼起来按 line 分组
    return self._fallback_merge(partials)
return merged
```

---

## 九、情节线连续性修复：为什么还要 Layer2.5

即使有 `line_signals`，LLM 输出的情节线仍然可能断裂。比如经济暗线前 30 章有事件，后面突然跳到第 200 章，中间推进消失，这样报告里的时间轴就会“没头没尾”。

所以 pipeline 在拿到 plotlines 之后，会调用 `_stitch_plotline_continuity` 做确定性修复。

```328:333:src/novel_lab/orchestrator/pipeline.py
if isinstance(plotlines, list) and plotlines:
    plotlines = self._stitch_plotline_continuity(
        meta=meta, chapters_a=chapters_a, plotlines=plotlines
    )
```

这个修复函数的目标是：让每条线在章节时间轴上尽量连续，不出现明显断层。

核心策略有四个：

1. 去重：同一章节多个事件时，只保留置信度最高的事件。
2. 桥接：相邻事件间隔太大时，在中间章节插入“承接推进”节点。
3. 尾段回收：如果最后一个事件太靠前，在最后一章补一个“阶段回收”锚点。
4. 主线起势：如果主线开头太晚，在第一章补“主线起势”。

答辩时可以这样讲：

> 我们没有把情节线质量完全交给 LLM，而是把“连续性”变成可检测、可修复的工程问题。LLM 负责理解，确定性算法负责兜底结构完整性。

---

## 十、gap_threshold 怎么理解

情节线桥接需要判断“相邻两个事件间隔多少章算断裂”。这个阈值不能固定。

如果一本书只有 70 章，间隔 30 章已经很大；如果一本书有 3000 章，间隔 30 章可能很正常。

所以系统使用动态阈值，思路是随书的总章节数变化，同时设置上下界，避免过小或过大。

讲给老师听时可以这样表达：

> gap threshold 是按总章节数动态估计的。短篇阈值更小，长篇阈值更大，这样不会对短篇补太少，也不会对长篇补太密。它的作用不是创造新剧情，而是在已有章节摘要和 line_signals 中找一个合理的承接锚点。

---

## 十一、文风指纹：为什么要均匀抽样

文风分析的难点是：不能把整本书原文都喂给模型，但只看开头也不可靠。

开头可能文风还不稳定，或者作者前期没有进入状态；只看结尾也可能偏向高潮段落，不代表日常叙事风格。

所以文风模块采用均匀抽样：

- 从开头、中段、末尾等位置抽样。
- 默认抽 10 段。
- 每段约 800 字。

让 LLM 输出：

- POV：第一人称、第三人称限知、第三人称全知等。
- 时态。
- 平均句长。
- 对白比例。
- 描写浓度。
- 修辞手法。
- 调性关键词。
- 标志性句式。
- 示例段落。

这个结果后续会进入 AI Writing Pack，用来约束下游模型模仿原著语感。

---

## 十二、Finalize：为什么还需要质量门

Layer2 和 Layer3 都有 LLM 调用，只要有 LLM，就可能出现：

- 网络失败。
- JSON 解析失败。
- 模型输出字段为空。
- 某个子任务超时。

如果没有兜底，最终 HTML 报告可能出现关键模块空白。Finalize 质量门就是为了解决这个问题。

在 pipeline 中，最后会调用 `_quality_gate_recover`：

```463:469:src/novel_lab/orchestrator/pipeline.py
async def _finalize(self) -> None:
    analysis = self.state.analysis
    if analysis is None:
        return
    self.progress("finalize_start", {"task": "体检关键结果、抽取金句与套路、写入 analysis JSON"})
    self._quality_gate_recover(analysis)
```

质量门检查四类关键结果：

```866:874:src/novel_lab/orchestrator/pipeline.py
def _quality_gate_recover(self, analysis: NovelAnalysis) -> None:
    """结果体检：关键模块为空时自动补齐，避免报告核心区块空白。"""
    self._recover_plotlines_if_empty(analysis)
    if len(analysis.differentiation) < 3:
        analysis.differentiation = self._recover_differentiation(analysis)
    if len(analysis.reader_hooks) < 5:
        analysis.reader_hooks = self._recover_reader_hooks(analysis)
    if not analysis.drop_risks:
        analysis.drop_risks = self._recover_drop_risks(analysis)
```

这几个 recover 都是确定性算法，不再调用 LLM。

---

## 十三、Finalize 的四类兜底

第一类：情节线为空。

如果 plotlines 为空，系统会从章节摘要里按均匀间隔抽取若干节点，自动构造基础主线。这样报告至少能展示一条主线时间轴。

第二类：差异化亮点不足。

如果 Layer3 没生成足够差异点，系统会从现有情节线、节奏数据、人物数据中生成基础差异点，比如“高强度爽点间隔短”“主线和暗线并驱形成复合冲突”。

第三类：读者爽点公式不足。

如果爽点归因太少，系统会统计所有章节 Hook 类型，把高频 Hook 映射成心理机制。

```973:981:src/novel_lab/orchestrator/pipeline.py
def _recover_reader_hooks(self, analysis: NovelAnalysis) -> list[ReaderHookCausation]:
    hook_map = {
        "reveal": ("规则破解 + 真相揭示", "智识满足"),
        "cliffhanger": ("章末断点 + 高悬念收束", "猎奇好奇"),
        "face_slap": ("压制后反打脸", "公平感"),
        "power_up": ("阶段升级 + 即时反馈", "即时反馈"),
```

第四类：弃书风险为空。

如果 Layer3 没有输出弃书风险，系统直接把节奏分析里的 `drop_risk_zones` 转成 `DropRisk` 对象。因为弃书风险本来就可以从爽点曲线里确定性推导。

---

## 十四、质量门不是“造假”

老师可能会问：兜底是不是在编造内容？

可以这样回答：

> 质量门不是编造，它只基于已有结构化数据做保守回填。比如弃书风险来自节奏曲线，爽点公式来自 Hook 类型频次，基础主线来自章节摘要抽样。这些数据都已经在前面产生了，质量门只是把它们转换成报告需要的格式，避免空白。

也就是说，LLM 是增强质量；质量门保证最低可用。

---

## 十五、分线总结为空怎么处理

报告里有“长篇智能体总结”，用于展示每条情节线的分阶段概述。

它有两个来源：

1. 优先使用 LLM 在情节线复核时生成的版本。
2. 如果 LLM 版本为空，就用确定性算法生成。

确定性生成的方法是：

- 把每条线的事件按时间轴四等分。
- 分成铺垫、触发、升级、回收几个阶段。
- 每段取前几个事件标题拼成焦点描述。
- 再抽样若干里程碑事件作为总结。

这样即使 LLM JSON 解析失败，报告里也不会出现“暂无总结”的空状态。

---

## 十六、这部分的技术亮点

第一，Layer2 子任务解耦并发。节奏、人物、情节线、文风互不等待，提升整体速度。

第二，确定性和 LLM 分工明确。节奏分析、质量门、连续性扫描用算法；人物弧光、情节线归纳、文风分析用 LLM。

第三，主线/暗线不是直接让 LLM 猜，而是基于 Layer1 的 `line_signals` 聚合。

第四，情节线连续性有工程修复，不完全依赖模型输出质量。

第五，Finalize 保证报告最低可用，不因为某个 LLM 子任务失败导致最终产物崩溃。

---

## 十七、老师可能问的问题

**Q：为什么 Layer2 不是直接读原文？**

A：因为原文太长，直接读会回到上下文窗口不够的问题。Layer1 已经把原文压缩成章节级结构化证据，Layer2 基于这些证据聚合全书结构，信息量更小、噪音更少、成本更低。

**Q：节奏分析为什么不用 LLM？**

A：节奏分析来自 Hook 强度，是明确的统计问题。确定性算法更稳定、可复现，也不会因为模型随机性导致同一本书每次曲线不同。

**Q：`line_signals` 对情节线有什么实际帮助？**

A：它给每章打上“推进了哪条线”的标签，并记录 event 和 impact。Layer2 聚合时可以沿着这些章节锚点串线，尤其能减少经济、权力、情感等暗线被漏掉的问题。

**Q：情节线修复会不会插入不存在的剧情？**

A：不会凭空写新剧情。桥接节点基于已有章节摘要和 line_signals 找承接点，作用是补结构锚点，不是创造原文没有的事件。

**Q：为什么用 `asyncio.gather`？**

A：人物弧光、情节线、文风三个任务相互独立，可以并发执行。总耗时取决于最慢的那个任务，而不是三个任务耗时相加。

**Q：如果某个 Reduce 子任务失败怎么办？**

A：pipeline 使用 `return_exceptions=True` 隔离失败。失败的模块会被置为空结构，后面 Finalize 质量门再做确定性兜底，保证最终报告能生成。

**Q：Finalize 会不会掩盖错误？**

A：不会。Finalize 的目标是保证报告可用，但它也会把质量门状态写入 metrics，报告可以看到哪些模块是 recover 出来的。它不是隐藏失败，而是把失败变成可控降级。

---

## 十八、一句话总结

潘子这部分的核心贡献是：**把分散的章节证据聚合成全书结构，并通过连续性修复和质量门兜底，把 LLM 的不稳定输出变成可展示、可解释、可恢复的工程结果。**
