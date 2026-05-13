# 章鱼 / zhangyu 技术深挖稿：整体架构、Layer1 Map、Layer3 Insight、可视化输出

> 章鱼和 zhangyu 是同一个人，这份稿件把两部分合并：项目整体介绍、Layer1 章节级 Map Agent、Layer3 深度洞察与 Critic，以及最后的可视化与输出资产。

---

## 一、我负责什么

我负责四块内容：

1. 项目整体介绍：为什么要做 novel-lab，以及整体架构是什么。
2. Layer1：章节级 Map Agent，把每章原文转成结构化 JSON。
3. Layer3：Insight + Critic，把全书结构转成深度判断，并做原文校验。
4. 输出层：知识图谱、HTML 报告、创作宪法和 AI Writing Pack。

我的讲解主线可以概括成一句话：**我们不是让 AI 直接总结整本书，而是把长篇小说拆成“章节证据 → 全书结构 → 深度洞察 → 创作资产”的分层流水线。**

---

## 二、项目整体：为什么不能直接让大模型总结一本书

目前很多 AI 拆书工具的思路是：把整本书塞进大模型，让模型输出摘要。这个方案面对长篇中文网文会失效。

第一，输入太长。一部长篇网文可能有 100 万 token 以上，即使 200K 上下文窗口也放不下。

第二，长上下文注意力不均匀。即便勉强放进去，模型也容易出现“中间丢失”，开头和结尾记得清楚，中间大量章节被弱化。

第三，摘要不是结构。我们想知道的不只是“主角最终成功”，而是每章有什么钩子、人物弧光怎么变化、暗线在哪里推进、哪里可能导致弃书。

第四，摘要不能直接复用到写作。我们的目标是拆完一本书后，能输出一套给 AI 写同款小说用的资产。

所以 novel-lab 的核心问题是：**如何让大模型对超长小说做出有证据、有结构、可复用的深度分析。**

整体架构是分层金字塔：

```text
原文（百万字）
    ↓ Layer0：解析、切章、切块、建索引
章节级证据（每章结构化 JSON）
    ↓ Layer1：Map Agent 并发抽取
全书结构（情节线 / 人物 / 节奏 / 文风）
    ↓ Layer2：Reduce 聚合
深度洞察（差异化 / 爽点归因 / 弃书风险）
    ↓ Layer3：Insight + Critic
创作资产（报告 / 图谱 / 创作宪法 / 写作包）
```

越往下，信息量越小，但语义价值越高。每层只消费上一层的结构化输出，不需要反复把整本原文塞给模型。

---

## 三、Layer1：章节级 Map Agent 的定位

Layer1 是整个系统质量的基础。它输入的是一章原文，输出的是结构化的章节分析 JSON。

为什么要“一章一次调用”？

- 章节边界清楚，模型不会混淆多章内容。
- 一章失败不会影响其他章节。
- 每章结果可以独立缓存，方便断点续跑。
- 所有章节可以并发处理，速度从“3000 章串行”变成“按并发批次处理”。

比如一部 3000 章小说，如果并发数是 20，Map 阶段大约是 150 轮串行时间，而不是 3000 轮。

---

## 四、Layer1 的数据模型：为什么用 Pydantic

我们所有 agent 之间传递的数据都定义在 `schema.py`，使用 Pydantic 建模。

最重要的基类是 `Evidenced`。所有结论型字段都必须带 `evidence_chapter` 和 `confidence`。

```22:30:src/novel_lab/schema.py
class Evidenced(BaseModel):
    """带原文证据的结论基类。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    evidence_chapter: list[int] = Field(
        default_factory=list, description="支持本结论的章节序号（0-based）"
    )
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
```

这个设计是防幻觉的第一层：模型不能只说“这本书节奏好”，还要指出这个判断来自哪些章节。后面的 Critic 会根据这些章节号回查原文。

单章总输出是 `ChapterAnalysis`：

```148:162:src/novel_lab/schema.py
class ChapterAnalysis(BaseModel):
    """单章 Map 阶段总产出。"""

    chapter_idx: int
    summary: str = ""
    mentioned_characters: list[CharacterMention] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    hooks: list[Hook] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    tropes: list[TropeHit] = Field(default_factory=list)
    line_signals: list[ChapterLineSignal] = Field(default_factory=list)
    raw_token_in: int = 0
    raw_token_out: int = 0
    cost_usd: float = 0.0
```

这里不是只做摘要，而是一次性抽取六类结构化信息：

- `summary`：本章发生了什么。
- `mentioned_characters`：人物在本章做了什么、情绪和关系有没有变化。
- `scenes`：地点、时间线索、参与者。
- `hooks`：钩子/爽点，后续用于节奏分析。
- `quotes`：金句，要求来自原文。
- `tropes`：命中的题材套路。
- `line_signals`：章节级线路信号，为 Layer2 串主线/暗线服务。

---

## 五、Hook：爽点为什么要结构化

Hook 是网文分析里非常关键的数据。它描述一章中让读者继续读下去的节点，比如章末悬念、反转、打脸、升级、危机等。

```88:108:src/novel_lab/schema.py
class HookType(str, Enum):
    OPENING = "opening"            # 章首钩子
    CLIFFHANGER = "cliffhanger"    # 章末钩子（最关键）
    REVEAL = "reveal"              # 反转/揭秘
    POWER_UP = "power_up"          # 升级/突破
    FACE_SLAP = "face_slap"        # 打脸
    REVENGE = "revenge"            # 复仇/逆袭
    CP_PROGRESS = "cp_progress"    # CP 推进
    CRISIS = "crisis"              # 危机/绝境
    MYSTERY = "mystery"            # 悬念
    TWIST = "twist"                # 反转


class Hook(Evidenced):
    """钩子/爽点单元 — 决定读者追读率的核心。"""

    type: HookType
    intensity: int = Field(ge=1, le=5, description="爽点/钩子强度，5 最强")
    summary: str
    snippet: str = Field(default="", description="原文片段 ≤ 200 字")
    position: str = Field(default="middle", description="opening | early | middle | late | ending")
```

这里的 `intensity` 是 1 到 5 分。Layer2 的节奏分析会直接用它计算爽点曲线：

- 强度 4 以上：高峰章节。
- 强度 5：大高潮章节。
- 连续多章强度低：弃书风险区间。

所以 Hook 不是为了展示一个列表，而是后续全书节奏分析的数值基础。

---

## 六、line_signals：解决主线/暗线断裂

`line_signals` 是这次项目里很关键的技术点。它解决的问题是：如果只靠章节摘要，Layer2 很难判断每章到底推进了哪条线，尤其是经济暗线、权力暗线、情感暗线。

所以我们把线路判断前置到 Layer1：让模型在读每章原文时就标记“这一章推进了哪条线”。

```128:145:src/novel_lab/schema.py
class LineSignalKind(str, Enum):
    MAIN = "main"
    ECONOMIC = "economic"
    POWER = "power"
    EMOTIONAL = "emotional"
    SUB = "sub"
    NONE = "none"


class ChapterLineSignal(Evidenced):
    """章节级线路信号：为 Layer2 串线提供可引用锚点。"""

    line: LineSignalKind
    status: str = "advance"  # setup | advance | twist | payoff | cooldown
    event: str = ""
    impact: str = ""
    characters: list[str] = Field(default_factory=list)
    snippet: str = ""
```

这里最重要的是 `event` 和 `impact`。

- `event`：这一章发生了什么动作。
- `impact`：这个动作造成了什么变化。

比如不能只写“情感线推进”，而应该写“主角救下女主，使女主从戒备转为信任”。这样 Layer2 聚合时就能看到明确的因果节点。

---

## 七、Map Agent 如何调用 LLM

`ChapterMapAgent` 在初始化时加载题材套路库和 map prompt，然后把章节文本、章节标题、题材套路一起发给 LLM。

```23:31:src/novel_lab/agents/map/chapter_agent.py
@dataclass
class ChapterMapAgent:
    router: LLMRouter
    genre: str = "generic"

    def __post_init__(self) -> None:
        self._tropes = genre_trope_lookup(self.genre)
        self._prompt = load_prompt("map_chapter")
        self._system = system_base() + "\n\n# 你当前角色\nLayer1 章节级 Map Agent — 一次性产出 5 类结论的合一版本。"
```

真实调用时指定 `role="map"`、`json_mode=True`、低温度输出。

```44:58:src/novel_lab/agents/map/chapter_agent.py
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
```

这里的 `role="map"` 会交给 LLM Router 决定用什么模型。通常 Map 阶段量最大，所以 basic 或 balanced 档位下会优先用 DeepSeek 控制成本。

---

## 八、防幻觉：JSON 解析、Schema 校验、原文包含校验

LLM 输出不是直接相信，而是经过三层处理。

第一层是 JSON 解析。模型如果输出了代码块，系统会自动剥掉 ```json 包裹；如果前后有多余文字，会截取最大 `{...}`。

```141:158:src/novel_lab/agents/map/chapter_agent.py
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
```

第二层是字段补齐和白名单校验。比如所有结论都补 `evidence_chapter`，套路命中必须来自题材套路库。

第三层是原文包含校验。金句和 `line_signals.snippet` 必须真的出现在本章原文里，否则会被过滤。

```102:131:src/novel_lab/agents/map/chapter_agent.py
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
```

包含校验会先做直接字符串匹配，再去掉空白字符匹配，处理模型多加空格或换行的情况。

```160:168:src/novel_lab/agents/map/chapter_agent.py
@staticmethod
def _is_quote_from_chapter(quote_text: str, chapter_text: str) -> bool:
    if quote_text in chapter_text:
        return True
    norm_q = "".join(quote_text.split())
    norm_ch = "".join(chapter_text.split())
    if not norm_q:
        return False
    return norm_q in norm_ch
```

最后再用 Pydantic 构造 `ChapterAnalysis`。如果字段类型不合法，比如 `intensity` 超出 1 到 5，就会触发 `ValidationError`，返回失败摘要，避免坏数据流入 Reduce。

---

## 九、长章节处理：超过 12000 字怎么办

如果一章特别长，超过 12000 字，系统不会直接塞给模型，而是从中间附近的换行切成两段，分别分析，再合并结果。

```60:71:src/novel_lab/agents/map/chapter_agent.py
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
```

合并时会去重：人物按名字去重，Hook 按 `(type, summary 前 30 字)` 去重，`line_signals` 按 `(line, status, event 前 30 字)` 去重。如果同一个 Hook 重复出现，保留强度更高的版本。

---

## 十、并发与断点续跑

Map 阶段通过 `asyncio.Semaphore` 控制并发，默认并发数是 20。每章还有超时保护，默认 420 秒。

```67:81:src/novel_lab/agents/map/runner.py
async def run_map(
    chapters: list[Chapter],
    *,
    router: LLMRouter,
    genre: str,
    workdir: Path,
    book_id: str,
    concurrency: int = 20,
    progress_cb: Optional[Callable[[int, int, ChapterAnalysis], None]] = None,
    resume: bool = True,
) -> list[ChapterAnalysis]:
    ckpt = MapCheckpoint(workdir / book_id / "checkpoints" / "map.sqlite")
    agent = ChapterMapAgent(router=router, genre=genre)
    sem = asyncio.Semaphore(concurrency)
    chapter_timeout = float(os.getenv("MAP_CHAPTER_TIMEOUT_SEC", "420"))
```

每章执行前先查 SQLite checkpoint。如果已有有效结果，就直接跳过。

```83:104:src/novel_lab/agents/map/runner.py
async def worker(ch: Chapter) -> ChapterAnalysis:
    if resume:
        cached = ckpt.get(ch.idx)
        if cached and cached.summary and not cached.summary.startswith("<"):
            return cached
    async with sem:
        try:
            result = await asyncio.wait_for(
                agent.analyze(ch), timeout=chapter_timeout
            )
        except asyncio.TimeoutError:
            result = ChapterAnalysis(
                chapter_idx=ch.idx,
                summary=f"<map 超时：单章分析超过 {chapter_timeout:.0f}s>",
            )
        except Exception as exc:
            result = ChapterAnalysis(
                chapter_idx=ch.idx,
                summary=f"<map 失败：{type(exc).__name__}: {exc}>",
            )
    ckpt.set(result)
    return result
```

SQLite 表结构非常简单：`chapter_idx` 是主键，`payload` 保存整章分析 JSON。

```18:31:src/novel_lab/agents/map/runner.py
class MapCheckpoint:
    """SQLite 持久化每章 Map 结果，断点续跑。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS map_results ("
                "  chapter_idx INTEGER PRIMARY KEY,"
                "  payload TEXT NOT NULL"
                ")"
            )
```

这带来两个好处：

- 中途失败后可以从已完成章节继续，不重复调用 LLM。
- 章节级失败被隔离，不会拖垮整本书分析。

---

## 十一、Layer3：从结构到判断

Layer2 产出的是全书结构，比如情节线、人物弧光、节奏曲线、文风指纹。Layer3 要回答更高层的问题：这本书为什么能吸引读者？差异化在哪里？哪里容易流失？

Layer3 先做全书上下文压缩。因为 Layer2 结果也可能很长，不能直接全部塞给模型。

压缩内容包括：

- top 30 个最强 Hook。
- 情节线简化版，只保留线名、摘要、事件数量和前几个关键事件。
- 前 8 个主要人物档案。
- 节奏数据，比如平均高峰间隔、弃书风险区间。
- 文风摘要。
- 所有章节摘要的 80 字截断版。
- 题材套路库和反模式。

压缩后通常控制在 10K 到 20K token，适合做一次高质量洞察调用。

---

## 十二、Layer3 三类洞察

Layer3 输出三类洞察。

第一类是差异化分析：这本书相比同题材普通作品，独特在哪里。比如设定、人设、节奏、文风、价值观或题材融合。

第二类是读者爽点归因：不只是说“这里有打脸”，而是解释打脸为什么有效。它可能触发公平感、替代体验、智识满足、情感张力等心理机制。

第三类是弃书风险：哪些章节区间读者容易流失，原因是什么，怎么修复。修复建议必须具体，比如“在 ch200-ch230 安排一次阶段性收益”，而不是泛泛地说“节奏快一点”。

这些结论同样继承 `Evidenced` 思路，要求带 `evidence_chapter`，为后面的 Critic 做准备。

---

## 十三、Critic：基于原文的自我校验

Critic 是系统防幻觉的最后一道防线。

问题是：LLM 可能输出听起来合理、但原文不支持的结论。比如它说“主角长期处于道德两难”，但原文其实没有这种描写。

Critic 的做法是：

1. 收集 Layer3 的所有结论。
2. 每条结论读取它声明的 `evidence_chapter`。
3. 根据章节号取出原文片段。
4. 把“结论 + 原文片段”交给 Critic LLM 判断是否成立。

核心代码如下：

```29:53:src/novel_lab/agents/insight/critic.py
async def critique(
    self, claims: list[dict[str, Any]]
) -> list[InsightCritique]:
    if not claims:
        return []
    # 为每条 claim 取 evidence 章节的原文摘录
    enriched = []
    for c in claims:
        ev_chapters = list(c.get("evidence_chapter", []))[:3]
        snippets = []
        if self.index is not None:
            for ci in ev_chapters:
                text = self.index.parent_chapter_text(int(ci))
                if text:
                    snippets.append(
                        {"chapter_idx": int(ci), "text": text[: self.max_snippet_chars]}
                    )
        enriched.append(
            {
                "id": c["id"],
                "claim_type": c["claim_type"],
                "content": c["content"],
                "context_snippets": snippets,
            }
        )
```

Critic 没通过的结论不会直接删除，而是降低置信度。原因有两个：

- Critic 也可能误判，比如 evidence 章节取少了。
- 如果直接删除，某些报告模块可能变空，用户看不到任何参考。

所以我们采用“软删除”：保留结论，但降低 confidence，让用户知道它不可靠。

---

## 十四、知识图谱：把分析结果变成关系网络

输出层里我还负责知识图谱和 HTML 报告。

知识图谱的目标是把小说分析结果变成可导航的关系网络，而不是一堆孤立文本。

图谱构建不强依赖 Neo4j。系统一次构建两种格式：

- Cypher：可以导入 Neo4j，方便做图查询。
- Cytoscape.js JSON：直接嵌入 HTML 报告，在浏览器里交互查看。

```32:45:src/novel_lab/graph/builder.py
@dataclass
class GraphArtifact:
    cypher: str
    elements: dict[str, list[dict[str, Any]]]  # cytoscape.js: {nodes, edges}


@dataclass
class KnowledgeGraphBuilder:
    write_neo4j: bool = False
    neo4j_uri: Optional[str] = None
    neo4j_user: Optional[str] = None
    neo4j_password: Optional[str] = None

    def build(self, analysis: NovelAnalysis) -> GraphArtifact:
```

节点类型包括 Book、Chapter、Character、Location、Event、Trope、Quote。

其中人物弧光关系比较有价值：`EVOLVES_TO` 边连接两个章节节点，边上记录角色名、从哪个阶段到哪个阶段、心理变化是什么。

```135:153:src/novel_lab/graph/builder.py
# EVOLVES_TO 链 — 弧光状态点
arc_sorted = sorted(ch_p.arc, key=lambda a: a.chapter_idx)
for a, b in zip(arc_sorted, arc_sorted[1:]):
    if a.chapter_idx in chapter_id_map and b.chapter_idx in chapter_id_map:
        src_ch = chapter_id_map[a.chapter_idx]
        dst_ch = chapter_id_map[b.chapter_idx]
        edges.append(
            {
                "data": {
                    "source": src_ch,
                    "target": dst_ch,
                    "label": "EVOLVES_TO",
                    "character": ch_p.name,
                    "from_stage": a.stage,
                    "to_stage": b.stage,
                    "note": b.psychological_change,
                }
            }
        )
```

Cypher 里使用 `MERGE` 而不是 `CREATE`，这样重复导入不会产生重复节点。

---

## 十五、HTML 报告与 AI Writing Pack

HTML 报告使用 Jinja2 渲染成单文件 HTML，不需要启动前端项目，也不需要用户安装 Node。

报告包含：

- 概览：关键指标、质量门、差异化亮点。
- 爽点节奏：ECharts 曲线、弃书风险区间。
- 主线/暗线：多线时间轴、事件表格、分阶段总结。
- 人物档案：角色定位、动机、缺陷、弧光状态点。
- 关系图谱：Cytoscape.js 交互图。
- 深度洞察：差异化、爽点归因、弃书风险修复建议。
- 金句与套路：原文金句和套路频次。
- 原始数据：metrics 和完整章节摘要。

AI Writing Pack 是项目最产品化的输出，目标是让用户能把拆书结果直接交给 Claude 或 Cursor，用来写同款新书。

它包含：

- `system_prompt.md`：完整写作系统提示。
- `style_few_shot.md`：原著风格示例段落。
- `characters.yaml`：角色卡。
- `tropes.json`：套路和反模式。
- `plot_skeleton.md`：主线/暗线骨架。
- `creation_constitution.md`：创作宪法。

这说明 novel-lab 的输出不是一次性摘要，而是一套可复用的创作知识资产。

---

## 十六、老师可能问的问题

**Q：为什么 Map 阶段不把多章合并调用，减少 API 次数？**

A：多章合并会混淆章节边界，而且一批失败会连累多章。更重要的是，每章独立才能并发和断点续跑。长篇小说的瓶颈不是单次调用次数，而是整体吞吐和失败恢复能力。

**Q：Pydantic 在这里具体解决什么问题？**

A：它解决数据契约问题。LLM 输出的 JSON 必须符合 schema，比如 Hook 强度必须是 1 到 5，结论必须有 evidence_chapter。这样坏数据不会悄悄进入后续 Reduce 层。

**Q：line_signals 和普通摘要有什么区别？**

A：摘要只说“这一章发生了什么”，line_signals 额外说明“这一章推进了哪条线、处于什么叙事阶段、动作和影响分别是什么”。它是给 Layer2 串主线/暗线用的结构化锚点。

**Q：Critic 为什么不直接删掉不通过的结论？**

A：因为 Critic 也可能取证不足或误判。直接删除会让报告部分内容变空。降低置信度更稳妥，相当于告诉用户“这条结论需要谨慎采信”。

**Q：为什么 HTML 报告不用 React/Vue？**

A：项目目标是命令行工具输出一个可直接打开的报告。Jinja2 可以在 Python 端把数据渲染成单文件 HTML，用户不用 `npm install`，不用启动服务，可移植性更好。

**Q：知识图谱为什么同时输出 Cypher 和 Cytoscape JSON？**

A：Cytoscape JSON 用于报告内交互展示，开箱即用；Cypher 给有 Neo4j 环境的用户做更复杂查询。两者来自同一个 `NovelAnalysis`，不维护两套数据来源。

---

## 十七、一句话总结

章鱼 / zhangyu 这部分的核心贡献是：**把长篇小说从“不可直接处理的百万字原文”转成可并发抽取、可证据校验、可视化展示、可用于 AI 再创作的结构化资产。**
