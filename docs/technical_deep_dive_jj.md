# jj 技术深挖稿：Layer0 文本预处理与 RAG 构建

> 适合 jj 答辩使用。重点讲清楚：一本中文网文如何被读入、切章、切块、建索引，以及为什么这里要做 Parent-Child RAG。

---

## 一、这部分负责什么

jj 负责的是 **Layer0：文本预处理与 RAG 构建**。

这一层是整个系统的入口，目标不是让大模型直接读书，而是先把一本长篇小说变成后续智能体可以稳定消费的数据结构：

```text
原始文件（TXT / EPUB）
    ↓
编码兼容读取
    ↓
章节切分（Chapter）
    ↓
语义切块（ChildChunk）
    ↓
ChromaDB 向量索引
    ↓
给 Critic / 情节线复核提供原文检索能力
```

这层做得好，后面的 Layer1 Map Agent 才能按章节并发分析；这层做不好，比如章节切错、编码乱码、向量索引无法回查原文，后面的所有分析都会被污染。

---

## 二、文件解析：先保证“能读进来”

中文网文的第一个工程问题是编码混乱。很多旧 TXT 文件不是 UTF-8，而是 GBK、GB18030 或 Big5。如果直接 `open(..., encoding="utf-8")`，有些书会直接报错。

所以我们在解析 TXT 时做了鲁棒读取：按顺序尝试多种编码，任何一种成功就使用；如果都失败，就用 UTF-8 忽略错误字符兜底，保证主流程不中断。

```48:55:src/novel_lab/ingest/parser.py
def _read_text_robust(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")
```

EPUB 的处理方式是先用 `ebooklib` 读取 EPUB 包里的 HTML 文档，再用 `BeautifulSoup` 转成纯文本，最后拼成完整文本，复用 TXT 的章节切分逻辑。

这个设计的好处是：TXT 和 EPUB 后面走同一套 Chapter 数据结构，不需要为后续层维护两套逻辑。

---

## 三、章节切分：把长文本变成 Chapter

章节切分是 Layer0 里最关键的一步。后续 Layer1 是“一章一次 LLM 调用”，所以章节边界必须尽量准确。

我们使用中文章节正则，支持常见格式：

- `第1章`
- `第一章`
- `第十二回`
- `第123节`
- `第十折`
- 以及卷标识，如 `第一卷`

对应代码里用 `_CN_NUM` 同时覆盖中文数字和阿拉伯数字。

```22:35:src/novel_lab/ingest/parser.py
_CN_NUM = r"[零〇一二三四五六七八九十百千万两\d]+"
_CHAPTER_RE = regex.compile(
    rf"^[\s　]*(?:第\s*{_CN_NUM}\s*[章回节折])"
    rf"(?:[\s　]+|[:：、\.\-—_]+)?"
    rf"(.{{0,80}}?)\s*$",
    flags=regex.MULTILINE,
)
_VOLUME_RE = regex.compile(
    rf"^[\s　]*(?:第\s*{_CN_NUM}\s*卷"
    rf"(?:[\s　]+|[:：])?(.{{0,80}}?))\s*$",
    flags=regex.MULTILINE,
)
```

如果完全找不到章节标题，系统不会崩溃，而是把全文作为一章处理：

```58:69:src/novel_lab/ingest/parser.py
def _iter_chapter_spans(text: str) -> Iterator[tuple[int, int, str]]:
    """yield (start_pos, end_pos, title)."""
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        # 没有章节标识，整本作为一章
        yield 0, len(text), "全文"
        return
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = m.group(0).strip()
        yield start, end, title
```

解析完成后，每章会变成 `Chapter` 对象，包含章节序号、标题、正文、字数、卷信息等。整本书则形成 `NovelMeta`，其中 `book_id` 是根据书名和正文前 5000 字计算出的 SHA1。

```38:40:src/novel_lab/ingest/parser.py
def _book_id(text: str, title: str) -> str:
    h = hashlib.sha1((title + text[:5000]).encode("utf-8", errors="ignore")).hexdigest()
    return f"book_{h[:12]}"
```

`book_id` 的作用是稳定定位一本书。同一本书重复分析时，`book_id` 不变，后续的缓存、向量库、checkpoint 都能复用。

---

## 四、语义切块：为什么不能整章直接检索

章节切分之后，一章通常有 2000 到 5000 字。如果直接把整章做 embedding，会有两个问题：

第一，整章内容太杂。一个章节里可能同时有对话、打斗、伏笔、人物关系变化，整章向量会把这些语义混在一起，检索精度下降。

第二，RAG 检索需要命中具体片段。如果用户或后续 agent 查“后半段伏笔回收”，我们希望先命中最相关的小片段，而不是让整章向量粗略匹配。

所以我们用 `RecursiveCharacterTextSplitter` 做语义切块。分隔符优先级是：段落、换行、句号、感叹号、问号、分号、逗号、空格。这样优先在自然语义边界切开，避免把一句话从中间截断。

```49:68:src/novel_lab/ingest/chunker.py
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

这里默认 `chunk_size=384`、`chunk_overlap=64`。overlap 的作用是保留跨块语义，比如伏笔句子在上一个块末尾、解释在下一个块开头，如果没有重叠，检索时可能丢掉上下文。

Token 数估算使用 `tiktoken`，如果离线环境拿不到编码器，则按中文字符数乘以 0.6 兜底。

```41:46:src/novel_lab/ingest/chunker.py
def estimate_tokens(text: str) -> int:
    enc = _enc()
    if enc is None:
        # 中文兜底估算：每个字符 ≈ 0.6 token
        return int(len(text) * 0.6)
    return len(enc.encode(text))
```

---

## 五、RAG 索引：Parent-Child 结构

我们的 RAG 不是简单地把整本书所有文本切块后塞进向量库，而是采用 Parent-Child 结构：

- Parent：整章原文
- Child：章节内切出来的小语义块
- 检索时：先用 child chunk 做相似度匹配，再返回它所属的 parent 章节原文

这样兼顾两个目标：

- child 小块保证检索精度
- parent 整章保证上下文完整

代码里 `NovelIndex` 会把每个 child chunk 写成 `TextNode`，并在 metadata 中记录 `chapter_idx`、`chapter_title`、`chunk_idx` 等信息。

```102:130:src/novel_lab/ingest/indexer.py
nodes: list[TextNode] = []
for ch in self.meta.chapters:
    chunks: list[ChildChunk] = split_chapter(ch)
    for c in chunks:
        node = TextNode(
            text=c.text,
            id_=f"{self.meta.book_id}::ch{c.chapter_idx:05d}::p{c.chunk_idx:03d}",
            metadata={
                "book_id": self.meta.book_id,
                "chapter_idx": c.chapter_idx,
                "chapter_title": ch.title,
                "chunk_idx": c.chunk_idx,
                "char_count": c.char_count,
            },
        )
        nodes.append(node)

self._index = VectorStoreIndex(
    nodes=nodes, storage_context=self._storage, embed_model=self.embed_model
)
```

向量库使用 ChromaDB，本地持久化，不需要额外启动服务。

如果 Chroma collection 已经有数据，并且没有强制重建，系统会直接复用已有索引。这对长篇小说很重要，因为 embedding 是有成本和时间开销的。

```88:100:src/novel_lab/ingest/indexer.py
def build(self, *, rebuild: bool = False) -> "NovelIndex":
    from llama_index.core import VectorStoreIndex
    from llama_index.core.schema import TextNode
    from llama_index.vector_stores.chroma import ChromaVectorStore

    if os.getenv("NOVEL_LAB_FORCE_REINDEX", "").lower() in ("1", "true", "yes"):
        rebuild = True

    if self._collection.count() > 0 and not rebuild:
        self._index = VectorStoreIndex.from_vector_store(
            vector_store=self._vstore, embed_model=self.embed_model
        )
        return self
```

检索时，先拿 child 命中结果，再根据 `chapter_idx` 回到 parent 章节原文：

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

---

## 六、RAG 在系统里的两个用途

这里要特别说明：本项目不是“所有问题都靠 RAG 问答”，而是只在关键位置使用 RAG。

第一，Critic 校验时根据 `evidence_chapter` 精确取对应章节原文。这个场景不需要语义检索，因为结论已经声明了证据章节，直接回查 parent chapter 即可。

第二，情节线复核时使用语义检索。比如系统会查询“后半段关键转折 伏笔回收 终局”“人物关系逆转 阵营变化”等，把命中的章节片段提供给 LLM，帮助修复主线/暗线断裂问题。

如果用户调试时不想构建向量库，也可以使用 `ChapterTextOnlyIndex`。它不做 embedding，只保存章节原文，仍然能支持 Critic 根据章节号回查原文。

```25:45:src/novel_lab/ingest/indexer.py
class ChapterTextOnlyIndex:
    """不构建向量库：仅保留章节原文，供 Critic / 其它需 ``parent_chapter_text`` 的阶段使用。"""

    def __init__(self, meta: NovelMeta, workdir: Path) -> None:
        self.meta = meta
        self.workdir = workdir
        self.persist_dir = workdir / meta.book_id / "index_skipped"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._chapter_text: dict[int, str] = {ch.idx: ch.text for ch in meta.chapters}
```

---

## 七、老师可能问的问题

**Q：为什么不直接把整章做向量？**

A：整章向量会把多个语义主题混在一起，检索结果比较粗。我们用 child chunk 做精确召回，再返回 parent chapter 做完整上下文，这样精度和完整性都兼顾。

**Q：为什么用 ChromaDB？**

A：ChromaDB 是轻量本地向量数据库，不需要单独部署服务，直接持久化到工作目录。对几千到几万个 chunk 的项目规模足够，而且可以断点复用，避免每次都重新 embedding。

**Q：如果章节标题格式很奇怪怎么办？**

A：当前正则覆盖常见中文网文格式，包括中文数字、阿拉伯数字、章/回/节/折。如果完全识别不到章节，系统会把全文作为一章处理，保证流程不中断。后续可以继续扩展更多标题启发式规则。

**Q：RAG 是不是贯穿所有分析？**

A：不是。主流程是分层 Map-Reduce，RAG 只是辅助原文回查和情节线复核。这样可以避免把所有分析都变成不稳定的“检索 + 问答”，同时保留必要的证据能力。

**Q：text-embedding-v4 有什么工程注意点？**

A：DashScope 的 `text-embedding-v4` 每次请求最多 10 条输入，所以 batch size 需要控制在 10 以内；同时请求参数要遵循它的 OpenAI-compatible 接口限制，不能随便加不支持的参数。

---

## 八、一句话总结

jj 这部分的核心贡献是：**把混乱的原始小说文件变成可复用、可检索、可断点续跑的结构化输入层**。后面的 Map、Reduce、Critic 和报告生成，都是建立在这一层提供的章节结构和原文回查能力之上的。
