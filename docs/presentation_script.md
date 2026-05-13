# novel-lab 演讲分工与讲解稿

> **分工总览：**
> - 章鱼：①项目整体介绍 → ③Layer3 Insight & Critic → ④收尾：可视化（知识图谱 + HTML 报告模板），与 jj 联合说明
> - jj：Layer0 —— 文本解析、语义切块、RAG 向量索引（与章鱼共同实现）
> - zhangyu：Layer1 —— 章节级 Map Agent
> - 潘子：Layer2 Reduce 聚合（情节线、人物弧光、节奏、文风）**+ Finalize 质量门**

---

## 分工表格

| 顺序 | 讲解人 | 负责模块 | 核心内容 | 预估时长 |
|:---:|:---:|---|---|:---:|
| 1 | 章鱼 | 项目整体介绍 | 背景痛点、整体架构（6 层金字塔）、输出资产清单 | 5 分钟 |
| 2 | jj | Layer0：预处理与 RAG | TXT/EPUB 解析 → 章节切分 → 语义切块（RecursiveCharacterTextSplitter）→ ChromaDB 父子向量索引 | 5 分钟 |
| 3 | zhangyu | Layer1：Map Agent | 单章并发抽取（Hook / Quote / line_signals）、原文包含校验、SQLite 断点续跑 | 6 分钟 |
| 4 | 潘子 | Layer2：Reduce + Finalize 质量门 | 四个并行子任务（节奏/弧光/情节线/文风）+ 情节线连续性修复 + 确定性兜底 | 7 分钟 |
| 5 | 章鱼 | Layer3：Insight & Critic | 全书上下文压缩、三类洞察（差异化/爽点归因/弃书风险）、Critic 原文交叉校验 | 4 分钟 |
| 6 | 章鱼 + jj | 可视化：知识图谱 + HTML 报告 | Cytoscape.js + Neo4j 双格式图谱、ECharts 爽点曲线 + 情节线时间轴、Jinja2 模板 | 5 分钟 |

---

## 一、【章鱼】项目整体介绍（约 5 分钟）

### 讲解稿

大家好，我是章鱼，我来介绍一下我们这个项目——novel-lab，一套面向长篇中文网络小说的自动拆解与创作资产生成系统。

首先说一下我们为什么做这个项目，或者说它解决的问题是什么。

目前市面上大部分 AI 拆书工具，本质上就是"把整本书塞给大模型，让它输出摘要"。这在短文本没有问题，但长篇网文平均 200 万字，换算过来是接近 100 万 token，即使最大的 Claude 200K 窗口也只能放五分之一。塞不进去就更别谈分析了。

更关键的问题是，即便塞进去，结果也很差。大模型在超长输入时有一个明显的"中间丢失"现象——开头和结尾处理正常，但中间大段内容被模型降权。一部 3000 章的小说，有 2500 章的细节实际上是失效的。

第三个问题是：摘要是给人看的，不是给下游 AI 创作用的。我们想要的是，拆完一本书之后，能直接拿着结果让 Claude 写同款新书，而不是再花几个小时手工整理提示词。

所以 novel-lab 解决的是：**如何让大模型对超长小说做出有深度、有证据、可复用的结构化分析**。

---

**架构一句话总结**：我们采用"分层金字塔 + Map-Reduce + RAG + 自我校验 + 输出资产化"的技术路线。整个系统分六层：

```
原文（百万字）
    ↓  Layer0：解析与切块，建 RAG 向量库
章节级证据（每章结构化 JSON）
    ↓  Layer1：Map Agent 并发提取
全书结构（情节线/人物/节奏/文风）
    ↓  Layer2：Reduce 聚合
深度洞察（差异化/爽点/弃书风险）
    ↓  Layer3：Insight + Critic 校验
创作资产（报告 / 图谱 / 创作宪法 / 写作包）
    ↓  Output Pack
```

越往下信息量越小，意义越大。每一层只消费上一层的输出，不回溯原文，这样我们就把百万字的问题拆成了每层可处理的规模。

---

**输出的东西**：我们最终输出的不是一份报告，而是一套可复用的创作知识资产：
- 交互式 HTML 报告（含知识图谱、爽点曲线、情节线时间轴）
- `knowledge_graph.cypher`：可以直接导入 Neo4j
- `creation_constitution.md`：写同款小说的规则书
- AI Writing Pack：包含 system prompt、示例段落、角色卡、套路表

下面分别由我们几位同学介绍各层的具体实现。先请 jj 介绍 Layer0。

---

### 老师可能问的问题及回答

**Q：为什么不直接用更大的上下文窗口？**

A：上下文窗口大了成本成倍增加，而且大模型对超长输入本身有注意力衰减的问题。分层 Map-Reduce 的本质是把信息压缩到每层可处理的粒度，同时保留了可追溯性——每个结论都附有 evidence_chapter，可以定位到原文。这比"喂进去、摘出来"的思路质量高得多。

**Q：这是做了 RAG 吗，和普通 RAG 有什么区别？**

A：是的，但我们不是把 RAG 当成唯一的检索机制，而是用在两个关键位置：一个是 Layer3 的 Critic 校验（根据 evidence_chapter 精确取对应章节原文做交叉验证）；另一个是情节线连续性复核（用语义查询检索中后段关键章节，修复暗线断裂）。RAG 用在刀刃上，不是为了炫技。

---

## 二、【jj】Layer0：文本预处理与 RAG 构建（约 5 分钟）

> 说明：jj 和章鱼共同实现了 Layer0 这一部分。

### 讲解稿

我是 jj，我来介绍 Layer0，也就是文本预处理和 RAG 向量索引的建立部分。

---

**第一部分：文件解析**

Layer0 首先要解决的问题是"如何把一本小说读进来"。这听起来很简单，但中文网文的编码历史比较乱——很多老文件是 GBK 或 GB18030 编码的。

我们的做法是：对 TXT 文件按顺序尝试 `utf-8`、`utf-8-sig`、`gbk`、`gb18030`、`big5` 五种编码，取第一个成功解码的结果。如果全部失败，用 utf-8 忽略错误字符强制解码，保证流程不崩溃。

```22:55:src/novel_lab/ingest/parser.py
# 中文数字 + 阿拉伯数字 通用章节正则
_CN_NUM = r"[零〇一二三四五六七八九十百千万两\d]+"
# 章节：章 / 回 / 节 / 折（不含 卷）
_CHAPTER_RE = regex.compile(
    rf"^[\s　]*(?:第\s*{_CN_NUM}\s*[章回节折])"
    rf"(?:[\s　]+|[:：、\.\-—_]+)?"
    rf"(.{{0,80}}?)\s*$",
    flags=regex.MULTILINE,
)
```

章节识别用正则，支持"第N章/回/节/折"，其中 N 支持阿拉伯数字和中文数字（零一二三四五六七八九十百千万两都覆盖了）。同时识别卷信息，比如"第一卷"这类标记。

章节切完之后，每本书的元信息也在这里生成：

```38:40:src/novel_lab/ingest/parser.py
def _book_id(text: str, title: str) -> str:
    h = hashlib.sha1((title + text[:5000]).encode("utf-8", errors="ignore")).hexdigest()
    return f"book_{h[:12]}"
```

`book_id` 是书名加正文前 5000 字的 SHA1 哈希，保证同一本书多次处理得到相同 ID，方便后面断点续跑复用缓存。

---

**第二部分：语义切块**

章节切分是粗粒度的，一章通常 2000-5000 字，直接做向量检索粒度太粗，所以要进一步切成语义块。

我们用 LangChain 的 `RecursiveCharacterTextSplitter`，分隔符优先级从高到低：双换行（段落）、单换行、句号、感叹号、问号、分号、逗号、空格——这样切出来的块优先在段落和句子边界分割，保留语义完整性。

```49:70:src/novel_lab/ingest/chunker.py
_DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def split_chapter(
    chapter: Chapter,
    *,
    chunk_size: int = 384,
    chunk_overlap: int = 64,
) -> list[ChildChunk]:
    """把一章切成若干 child chunk。"""
    if not chapter.text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size * 2,        # length_function 用字符数代理
        chunk_overlap=chunk_overlap * 2,
        length_function=len,
        separators=_DEFAULT_SEPARATORS,
        keep_separator=True,
    )
```

Token 数估算用 tiktoken 的 `cl100k_base` 词表，离线环境下按中文字符数 × 0.6 做兜底：

```41:46:src/novel_lab/ingest/chunker.py
def estimate_tokens(text: str) -> int:
    enc = _enc()
    if enc is None:
        # 中文兜底估算：每个字符 ≈ 0.6 token
        return int(len(text) * 0.6)
    return len(enc.encode(text))
```

---

**第三部分：向量索引**

这里用了一个父子（Parent-Child）架构。

```1:11:src/novel_lab/ingest/indexer.py
"""LlamaIndex Hierarchical Parent-Child 向量库。

设计：
- Parent = 整章（含 chapter_idx 元数据）
- Child  = chunker 切的小块（指向 parent）
- 检索时：先 child 命中 → 取 parent 上下文（RAG 兜底原文细节查询）
- 默认本地 ``BAAI/bge-small-zh-v1.5`` embedding（中文友好且小）
- 持久化到 ChromaDB（便于断点续跑）
"""
```

向量库用 **ChromaDB** 做持久化存储，不需要额外起服务，直接以文件夹形式落盘。向量化用阿里云百炼的 `text-embedding-v4` 模型，通过 OpenAI-compatible 接口调用，支持 1024 维向量。

检索逻辑是：先用 child chunk 做语义相似度命中，再取其 parent（整章原文）作为上下文返回。这保证了检索精度（小块语义聚焦）和上下文完整性（大块返回）两者兼顾：

```144:163:src/novel_lab/ingest/indexer.py
    def retrieve_with_parents(self, query: str, top_k: int = 6) -> list[dict]:
        """兼顾精度（child 命中）+ 上下文（parent 章节）。"""
        hits = self.retrieve(query, top_k=top_k)
        out: list[dict] = []
        seen: set[int] = set()
        for h in hits:
            cidx = int(h.node.metadata.get("chapter_idx", -1))
            if cidx in seen or cidx < 0:
                continue
            seen.add(cidx)
            out.append(
                {
                    "chapter_idx": cidx,
                    "chapter_title": h.node.metadata.get("chapter_title", ""),
                    "snippet": h.node.get_content(),
                    "score": float(h.score or 0.0),
                    "parent_text": self.parent_chapter_text(cidx),
                }
            )
        return out
```

如果用户不想跑 RAG（比如纯调试），系统会用 `ChapterTextOnlyIndex` 作为 fallback，只存章节原文，不建向量库：

```25:43:src/novel_lab/ingest/indexer.py
class ChapterTextOnlyIndex:
    """不构建向量库：仅保留章节原文，供 Critic / 其它需 ``parent_chapter_text`` 的阶段使用。"""
```

---

### 老师可能问的问题及回答

**Q：为什么要用父子（Parent-Child）结构，直接把整章做向量不行吗？**

A：整章做向量有两个问题：第一，一章几千字的 embedding 会把很多不同主题的内容混合在一起，语义向量会变得很"模糊"，检索时容易召回不相关的章节；第二，chunk 越小，向量越精确，能找到真正相关的段落。但如果只返回小 chunk，上下文不够，LLM 看不到足够背景。父子结构就是用小块检索精度，用大块提供上下文，两者兼顾。

**Q：ChromaDB 是什么，为什么用它？**

A：ChromaDB 是一个轻量的本地向量数据库，无需启动独立服务，以文件夹方式持久化。我们选它的原因是：对中小规模数据（几千个 chunk）性能足够，集成 LlamaIndex 简单，而且支持断点续跑——向量已经建好的书不需要重新 embedding，省时省钱。

**Q：text-embedding-v4 有什么特别的注意事项？**

A：有，DashScope 的 text-embedding-v4 每次请求最多接受 10 条输入，我们在代码里做了 batch size 上限控制。另外它的请求不支持 `encoding_format` 参数，我们调试时踩过这个坑（会报 400 Bad Request），最终把这个参数去掉了。

---

## 三、【zhangyu】Layer1：章节级 Map Agent（约 6 分钟）

### 讲解稿

大家好，我是 zhangyu，我来讲 Layer1，也就是章节级 Map Agent 的实现。

Layer1 是整个系统质量的基础。它的任务是：把每一章原始文本，通过一次 LLM 调用，转化成结构化的"读书笔记"。

---

**为什么要"一章一次调用"？**

首先解释一下设计动机。把多章合并送给 LLM 会有几个问题：章节边界容易混淆，一章失败连累整批，也没法做章节级断点续跑。更重要的是，每章独立处理可以**完全并发**——一部 3000 章的小说，20 个并发任务同时跑，Map 阶段只需要约 150 次串行时间，而不是 3000 次。

---

**数据模型：每章产出什么**

所有数据结构都定义在 `schema.py` 里，我们用 Pydantic 做模型：

```22:31:src/novel_lab/schema.py
class Evidenced(BaseModel):
    """带原文证据的结论基类。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    evidence_chapter: list[int] = Field(
        default_factory=list, description="支持本结论的章节序号（0-based）"
    )
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
```

所有结论型字段都继承 `Evidenced`，强制携带 `evidence_chapter`——也就是说，LLM 的每条结论都必须指出"这个结论在哪几章有原文依据"。这是整个系统防幻觉的基础。

`ChapterAnalysis` 是单章的总产出：

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
```

其中 `hooks`（钩子/爽点）和 `line_signals`（线路信号）是最关键的字段，我分别讲一下。

---

**钩子/爽点（Hook）**

Hook 记录了这章最重要的"让读者不想停下来"的节点：

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

强度 1-5，5 代表大爽点。后面 Layer2 的节奏分析就是在这个字段上算的——强度 ≥ 4 视为高峰，连续若干章没有中等以上爽点就标记为弃书风险区间。

---

**线路信号（line_signals）——这是这次新增的核心功能**

这是我们这次最重要的升级。原来主线/暗线的分析是在 Layer2 全凭摘要猜，效果很差（"没头没尾"就是这个原因）。现在我们在 Layer1 就让 LLM 给每章标注"这章推进了哪条线"：

```128:146:src/novel_lab/schema.py
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

`event` 是这章发生了什么动作，`impact` 是造成了什么影响——两者形成因果对，而不是只说"情感推进"这种没有信息量的描述。

---

**数据校验：防止 LLM 编造**

LLM 输出的 JSON 经过严格校验，特别是两个原文包含校验：

```113:124:src/novel_lab/agents/map/chapter_agent.py
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

金句（quotes）的文本必须能在原章节文本中找到，否则直接丢弃——避免 LLM 改写或编造金句。line_signals 的 snippet 证据片段同样做这个校验。

包含校验的逻辑：

```161:168:src/novel_lab/agents/map/chapter_agent.py
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

先做直接包含检查，如果失败，去掉所有空白字符再做一次——这是为了处理 LLM 输出的引号里多了一个空格这类细节问题。

---

**并发与断点续跑**

并发调度在 `runner.py` 里：

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

用 `asyncio.Semaphore` 控制最大并发数（默认 20），每章有超时保护（默认 420 秒）。

断点续跑：每章完成后立刻写入 `map.sqlite`，下次启动先查数据库，有结果就跳过：

```83:87:src/novel_lab/agents/map/runner.py
    async def worker(ch: Chapter) -> ChapterAnalysis:
        if resume:
            cached = ckpt.get(ch.idx)
            if cached and cached.summary and not cached.summary.startswith("<"):
                return cached
```

这是最省钱的设计之一——中断后续跑，已完成的章节不重新花钱。

---

### 老师可能问的问题及回答

**Q：为什么用 Pydantic，有什么好处？**

A：Pydantic 提供两个核心价值：第一，Schema 即文档——数据结构的字段、类型、约束都写在模型里，LLM 输出什么格式、后续 Reduce 消费什么格式，一目了然；第二，自动校验——LLM 的 JSON 输出经过 `ChapterAnalysis(**data)` 解析时，如果字段类型不对（比如 intensity 不是 1-5 的整数），直接抛 ValidationError，我们可以降级处理，不让错误数据悄悄流到下一层。

**Q：line_signals 里的 status 字段（setup/advance/twist 等）有什么用？**

A：status 描述这章在这条线上处于哪个叙事阶段：setup（铺垫）、advance（推进）、twist（转折）、payoff（兑现）、cooldown（收束）。在 Layer2 聚合情节线时，这些状态可以帮助 LLM 判断整条线的结构完整性——比如一条情感线只有 advance 没有 payoff，说明这条线在书里没有完结，这是一个质量问题。

**Q：超过 12000 字的长章节怎么处理？**

A：我们会把长章节从最近的换行处切成两段，分别分析，再调用 `_merge` 方法合并结果：

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

合并时，钩子按 `(type, summary前30字)` 去重、强度取大；line_signals 按 `(line, status, event前30字)` 去重。

---

## 四、【潘子】Layer2：Reduce 聚合（约 6 分钟）

### 讲解稿

我是潘子，我来介绍 Layer2，也就是 Reduce 聚合阶段。

Layer1 完成后，我们手里有每一章的结构化证据。Layer2 的任务是把这些分散的章节级证据汇聚成全书级的结构。

---

**四个并行的 Reduce 子任务**

Layer2 同时启动四个子任务：节奏分析、人物弧光、情节线、文风指纹。除了节奏分析是纯算法的，其余三个都要调用 LLM。这几个子任务用 `asyncio.gather` 并发执行，互不阻塞。

---

**节奏分析（PacingAnalyzer）——纯算法，不依赖 LLM**

这个模块完全用确定性算法计算，稳定可复现。

做法是：遍历所有章节的 hooks，提取每章最大爽点强度，得到一条强度曲线。然后计算：
- 平均高峰间隔：找出所有 intensity ≥ 4 的章节，相邻两个高峰的章节间隔均值。网文 top10% 爆款基准是每 1.8 章一个高峰。
- 弃书风险区间：连续若干章 maxIntensity ≤ 2，标记为风险区。

---

**人物弧光（ArcTracker）**

先做别名归并：如果两个名字互相包含对方，认为是同一人物（比如"猴哥"包含"哥"，"孙悟空"和"猴哥"可以靠更明确的规则合并）。然后按出现频率取前 12 个人物重点分析。

弧光输出五状态点：`initial → catalyst → turn_25 → turn_50 → turn_75 → final`，每个状态点附章节号、状态描述和心理变化说明。这来自网文写作方法论——一个好人物弧光在全书的 25%/50%/75%/100% 处都有明显变化。

---

**情节线（PlotlineSeparator）——重点**

这是 Layer2 最复杂的模块，我重点讲。

输入：所有章节的摘要、钩子、以及 zhangyu 刚才介绍的 `line_signals`。

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

**分批策略**：对于超过 80 章的小说，不一次性把所有章节摘要送给 LLM（prompt 会很长）。而是每 50 章一批生成局部情节线，再用 LLM 合并局部线为全书线索。这是 Map-Reduce 模式在子模块内部的体现：

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

---

**情节线连续性修复（这是最难的部分）**

即使有了 line_signals，LLM 输出的情节线仍然可能断裂。我们做了两层修复：

**第一层：RAG 辅助 LLM 复核**

用语义查询（如"后半段关键转折 伏笔回收 终局"）检索向量库里最相关的章节片段，把这些原文片段注入给 refine 阶段的 LLM，让它有实际证据补完断裂的情节线。

**第二层：确定性修复**

LLM 复核之后，还做一遍确定性扫描——这是纯算法，保证不受 LLM 质量影响：

```706:779:src/novel_lab/orchestrator/pipeline.py
    def _stitch_plotline_continuity(
        self,
        *,
        meta: NovelMeta,
        chapters_a: list[ChapterAnalysis],
        plotlines: list[PlotLine],
    ) -> list[PlotLine]:
        """修复情节线断裂：去重、补桥接事件、补尾段回收。"""
```

具体逻辑：
1. **去重**：同一章节出现多个事件，保留置信度最高的那个
2. **桥接**：相邻事件间隔超过阈值（`gap_threshold = max(18, min(80, total_chapters // 7))`），在中间章节插入一个"承接推进"节点
3. **尾段回收**：最后一个事件在全书前 75% 之前，补一个"阶段回收"锚点到最后一章
4. **主线起势**：主线第一个事件超过全书前 25%，在第一章补"主线起势"

这四步保证了情节线在时间轴上不会出现明显断裂，不管 LLM 输出质量如何。

---

**文风指纹（StyleFingerprint）**

从书的开头、中段、末尾均匀抽样 10 段原文，每段 800 字。让 LLM 分析这 10 段，输出：POV、时态、平均句长、对白比例、描写浓度、修辞手法、调性关键词、标志性句式、示例段落。

均匀抽样是关键——不能只看开头（早期写作质量可能不稳定），也不能全部喂进去（太长）。这样既控制了 token 数，又覆盖了全书不同阶段的文风。

---

### 老师可能问的问题及回答

**Q：情节线修复里的 gap_threshold 怎么确定的？**

A：`max(18, min(80, total_chapters // 7))`——这个公式是根据书的长度动态计算的。对于 70 章的西游记，threshold 约 18 章；对于 700 章的长篇网文，threshold 约 100 章。这样短篇和长篇的修复粒度是相匹配的，避免对短篇过度补桥接、对长篇插得太少的问题。

**Q：为什么用 `asyncio.gather` 并发四个 Reduce 任务？**

A：这四个任务相互独立，不需要等待彼此的结果。节奏分析、人物弧光、情节线、文风指纹各自输入不同（节奏只需要 hooks，文风只需要抽样段落），可以同时跑。并发执行把 Layer2 的总时间从"四个任务串行"缩短到"最慢那个任务的时间"。

**Q：别名归并用"名字包含关系"会不会出错？**

A：确实是一个启发式方法，不完美。对于同名不同人、或者名字没有包含关系的别名（比如"贾环"和"贾三爷"），这个方法就无效了。但对于大部分网文主角（比如"叶凡"、"叶小凡"这种变体），这个方法足够用。更精确的做法是用 NLP 实体消解模型，这是我们后续可以优化的方向。

---

**Finalize 质量门（接 Layer2 之后）**

Layer2 的四个 Reduce 子任务全部完成之后，流程进入 Finalize 阶段。这是整个分析链的"兜底层"，核心作用是：**保证无论哪个 LLM 调用出了问题，最终报告都不会出现关键模块空白**。

兜底逻辑全部是确定性算法，不依赖任何 LLM：

- **情节线为空** → 从所有章节摘要中按均匀间隔抽取若干章节摘要作为主线事件，自动回填一条基础主线
- **差异化亮点不足 3 条** → 根据情节线结构和节奏数据自动生成，比如"高强度爽点间隔短"、"主线与三条暗线并驱形成复合冲突"
- **弃书风险为空** → 把节奏分析里 `drop_risk_zones` 的低强度区间直接转成 `DropRisk` 对象
- **读者爽点公式不足 5 条** → 统计所有章节 hooks 的类型频次，把出现最多的类型按预设的心理机制映射表转化为爽点公式

```973:1009:src/novel_lab/orchestrator/pipeline.py
    def _recover_reader_hooks(self, analysis: NovelAnalysis) -> list[ReaderHookCausation]:
        hook_map = {
            "reveal": ("规则破解 + 真相揭示", "智识满足"),
            "cliffhanger": ("章末断点 + 高悬念收束", "猎奇好奇"),
            "face_slap": ("压制后反打脸", "公平感"),
            "power_up": ("阶段升级 + 即时反馈", "即时反馈"),
```

这种设计意味着：即使 API 网络中断、LLM 报错、JSON 解析失败，流程也能走完，产出一份有基础内容的报告。

---

**Q：质量门的兜底是不是又调了一次 LLM？**

A：不是，全部是确定性算法。弃书风险兜底是直接把节奏分析里已有的风险区间转换格式；读者爽点兜底是统计 hooks 频次做映射；差异化兜底是根据当前已有的情节线结构和节奏数值生成固定模板描述。这样即使 API 完全不可用，流程也不会卡住。

**Q：分线总结为空的情况怎么兜底？**

A：分线总结（"长篇智能体总结"）有两个来源：优先用情节线复核阶段 LLM 顺带生成的版本；如果这个版本为空（JSON 解析失败或 LLM 没有输出），就用确定性算法：把每条线的事件按时间轴四等分（铺垫/触发/升级/回收），每段取前几个事件标题拼成焦点描述，再均匀抽样几个里程碑事件组合成总结文本。报告里不会出现"暂无长篇总结"的空状态。

---

## 五、【章鱼】Layer3 Insight + Critic（约 4 分钟）

### 讲解稿

Layer2 和质量门完成之后，接下来由我来介绍 Layer3——深度洞察与自我校验。

---

**全书上下文压缩**

Layer3 的输入是 Layer2 的大量结果，在做深度洞察之前需要先压缩成一个可以放进 LLM 的 prompt 里。

压缩策略：top 30 个最强钩子、情节线简化版（只保留每条线的名称、描述和前 6 个事件）、前 8 个主要人物档案、节奏数据、文风摘要、所有章节摘要的 80 字截断版、题材套路库和反模式。

压缩后的上下文通常在 10-20K token 之间。

---

**三类洞察**

同样并发执行：
- **差异化分析**：这本书相比同题材普通作品有什么独特之处。每条附 aspect（设定/人设/节奏/文风/价值观/题材融合）、具体描述、心理机制说明，以及 evidence_chapter。
- **读者爽点归因**：不只说"有打脸"，要说清楚打脸触发了读者哪种心理机制——公平感、替代体验、智识满足等。
- **弃书风险**：哪些章节区间读者最容易流失，原因和修复建议。修复建议要足够具体，比如"在 ch200-ch230 安排一次阶段性收益"，不能只说"节奏要快一点"。

---

**Critic：基于原文的自我校验**

这是防幻觉的最后一道防线。

问题背景：LLM 可能会生成听起来合理但原文没有支撑的结论。Critic 的做法是：

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
```

每条结论有 `evidence_chapter` 字段，Critic 根据这个取对应章节原文，把"结论 + 原文"一起送给 Critic LLM 判断：结论有没有原文支撑？

没通过的结论**不删除，而是降低 confidence**。这是一个有意识的设计决策：如果粗暴删除，报告某个模块可能完全空白；保留但标低置信度，让用户自己判断。

---

### 老师可能问的问题及回答

**Q：Critic 为什么不直接删除低置信度结论？**

A：有两个原因。第一，Critic 本身也可能判断错——如果一个结论确实存在但 Critic 的取证章节选错了，就可能误删有效结论。第二，如果粗暴删除，某个分析模块可能完全空白，报告质量反而更差。降低置信度是"软删除"，用户可以根据置信度自己决定是否采信，比强制删除更合理。

**Q：三类洞察的 evidence_chapter 是 LLM 自己填的吗？**

A：是的，LLM 在输出每条洞察结论时，必须在 JSON 里填 `evidence_chapter` 字段，指定哪几章原文支持这个结论。这是 Pydantic `Evidenced` 基类强制要求的字段，不填会在 schema 校验时被捕获。Critic 校验时就根据这个字段取原文——所以 LLM 如果随便填章节号，Critic 回查原文时就会发现原文对不上，从而降低该条结论的置信度，形成闭环。

---

## 六、【章鱼 + jj 联合】可视化：知识图谱与 HTML 报告（约 5 分钟）

> 这部分由章鱼和 jj 共同完成实现。

### 讲解稿

最后我和 jj 来介绍可视化部分——知识图谱和交互式 HTML 报告。

---

**知识图谱（builder.py）**

知识图谱的设计思想是：把整本书的分析结果变成一个可导航的关系网络，而不是孤立的文字结论。

技术选型：
- 图谱构建完全在 Python 里完成，不依赖 Neo4j（无 driver 也能运行）
- 输出两种格式：**Cypher 脚本**（给 Neo4j 用）和 **JSON**（给前端 Cytoscape.js 用）
- 采用统一的 `KnowledgeGraphBuilder` 类，输入是 `NovelAnalysis`，输出是 `GraphArtifact`

节点类型共 7 种：Book / Chapter / Character / Location / Event / Trope / Quote

关系类型共 9 种：

```87:93:src/novel_lab/graph/builder.py
            cypher_lines.append(
                f"MATCH (b:Book {{id:'{book_id}'}}), (c:Chapter {{id:'{cid}'}}) "
                f"MERGE (b)-[:CONTAINS_CHAPTER {{order:{ch.idx}}}]->(c);"
            )
```

我们用 `MERGE` 而不是 `CREATE`，这样重复导入不会产生重复节点。每条 Cypher 语句独立执行，部分失败不影响整体。

弧光关系是图谱里最有价值的部分——`EVOLVES_TO` 边连接的是两个章节节点，边上记录了人物名称、起始阶段和目标阶段，以及心理变化说明：

```136:153:src/novel_lab/graph/builder.py
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

**大图降噪**：长篇小说图谱可能有上千个节点，全量渲染很卡。我们在渲染前按度数排序，Chapter 节点保留最重要的 120 个，非章节节点保留前 300 个，总边数不超过 1800 条。完整数据仍然保存在 `graph.json` 里。

---

**HTML 报告**

报告用 Jinja2 模板渲染，是一个完全自包含的单 HTML 文件，不依赖外部服务。

三个主要可视化组件：

1. **ECharts**（百度出品的开源图表库）：
   - 爽点强度曲线：X 轴章节序号，Y 轴强度，红色背景标注弃书风险区间，⭐ 标注强度 5 的高峰
   - 情节线时间轴：多线 scatter 图，每种线不同颜色，点击节点弹出事件详情
   - 钩子类型分布饼图

2. **Cytoscape.js**（开源图谱可视化库）：
   - 渲染知识图谱，支持点击节点聚焦邻域、隐藏章节节点、重新布局

3. **原生 HTML details/table**：
   - 人物档案、情节线事件表格、弃书风险、金句列表

报告中情节线事件表格的设计是新近优化的，每个节点展示三列：**节点事件**（标题）、**发生了什么**（action）、**造成什么变化**（impact），从 Line Signal 的 event/impact 字段提取，让因果关系一目了然。

---

### 老师可能问的问题及回答

**Q：Cytoscape.js 和 Neo4j 的数据格式是一样的吗？**

A：不一样，但我们写了统一的 builder，一次构建同时输出两种格式。Cytoscape.js 要求 `{nodes: [...], edges: [...]}` 结构，每个节点 `{data: {id, label, ...}}`；Neo4j 使用 Cypher 查询语言，MERGE/SET/MATCH 语句。两者的节点 ID 规则是一样的（用 `safe_id` 函数生成），所以导入 Neo4j 之后可以用 Cypher 查询的结果和前端图谱做对照。

**Q：为什么要同时输出这两种格式？**

A：Cytoscape.js 的图谱是嵌在 HTML 报告里的，任何人不需要安装任何东西就可以在浏览器里交互地看图谱。Neo4j 格式是给有图数据库环境的用户用的——可以写复杂查询，比如"找出所有和某角色在同一章出现的事件节点"。两种格式覆盖不同需求，数据来源是同一份 `NovelAnalysis`，不需要维护两套逻辑。

**Q：为什么用 Jinja2 而不是 React/Vue 这种前端框架？**

A：这是一个刻意的选择——我们要输出一个**单文件 HTML**，用户拿到就能打开，不需要 npm install，不需要启服务器，不需要网络。Jinja2 是 Python 生态的模板引擎，在构建时把所有数据直接渲染到 HTML 里，ECharts 和 Cytoscape.js 的 JS 通过 CDN 加载（或者也可以 inline 进 HTML）。这对于一个命令行工具的输出来说，可移植性是最重要的。

**Q：报告里的爽点强度曲线数据是怎么来的？**

A：直接从 `PacingAnalysis.curve` 字段读取，每章一个 `PacingPoint` 对象，包含章节序号和该章最大 intensity。这个数据在 Layer2 节奏分析阶段就计算好了，报告只是把它喂给 ECharts 渲染。弃书风险区间对应的红色背景，是把 `drop_risk_zones` 里的 `[start, end]` 区间用 ECharts 的 `markArea` 功能画出来的。

---

## 附录：可能被问到的技术细节速查

### LLM 路由与成本

```52:57:src/novel_lab/llm/router.py
_TIER_MATRIX: dict[str, dict[Role, Backend]] = {
    "basic":     {"map": "deepseek", "reduce": "deepseek", "critic": "deepseek", "deep": "deepseek"},
    "balanced":  {"map": "deepseek", "reduce": "claude",   "critic": "claude",   "deep": "claude"},
    "premium":   {"map": "claude",   "reduce": "claude",   "critic": "claude",   "deep": "claude"},
    "local":     {"map": "local",    "reduce": "local",    "critic": "local",    "deep": "claude"},
}
```

- basic 全 DeepSeek：100 章西游记约 $0.03
- balanced：Map 用 DeepSeek，Reduce/Insight 用 Claude，适合高质量报告

### 数据落盘位置

| 数据 | 位置 |
|---|---|
| 每章 Map 结果 | `checkpoints/map.sqlite` |
| Pipeline 阶段结果 | `checkpoints/stages.sqlite` |
| LLM 响应缓存 | `.workdir/llm_cache.sqlite` |
| 向量库 | `chroma/` |
| 最终分析 | `novel_analysis.json` |
| 报告等产物 | `output_pack/` |

### Key 预检查（运行前提示）

```60:65:src/novel_lab/llm/router.py
def _is_probably_placeholder_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k or k == "missing":
        return True
    placeholder_tokens = ("xxxx", "your_", "placeholder", "replace_me", "test_key")
    return any(t in k for t in placeholder_tokens)
```

如果用了 balanced 档位但 ANTHROPIC_API_KEY 是占位值，运行前会红色警告，不会等任务运行到一半才崩溃。

### 完整运行命令

```bash
novel-lab analyze ./book.txt \
  --genre generic \
  --tier basic \
  --rag \
  --resume
```
