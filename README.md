# novel-lab — 长篇网文多智能体拆解流水线

> 把一部 100-500 万字的中文网文，拆解成可被下游 AI 直接用于创作的「知识图谱 + 创作宪法 + 写作 Pack」。
>
> 核心目标：**真正抓住爆款规律，不是玩具**。

## 它解决什么问题

直接把整本小说塞进 LLM 会丢失绝大部分细节，市面上的"AI 拆书"普遍是**一次性摘要型玩具**：

- 文风复刻只在句式表面
- 不懂中文网文真实爽点（每 1.8 章一个情绪高峰是 top10% 基准）
- 人物弧光、伏笔、暗线全部丢失
- 输出是一份 PDF 报告，**下游 AI 没法直接用来写新书**

novel-lab 用「**分层金字塔 + Map-Reduce + 多智能体 + 自我反思**」解决长文本难题，并把输出做成**可执行的下游写作包**。

## 架构总览

```
TXT/EPUB
   │
   ▼
[Layer0] 章节切分 → 语义切块 → LlamaIndex Hierarchical Parent-Child 向量库
   │
   ▼
[Layer1] Map 章节级并行（DeepSeek-V3，便宜大批量）
   ├─ scene  · character · hook · quote · trope
   ▼
[Layer2] Reduce 卷/全书聚合（Claude Sonnet）
   ├─ 主线/三暗线分离（经济/权力/情感）
   ├─ 人物弧光 5 状态点
   ├─ 爽点节奏量化
   └─ 文风指纹
   ▼
[Layer3] 深度洞察 + 2 pass Self-Critique
   ├─ 差异点提炼
   ├─ 读者爽点归因
   └─ 弃书风险点
   ▼
[Layer4] 输出
   ├─ Neo4j 知识图谱
   ├─ creation_constitution.md（创作宪法）
   ├─ 交互式 HTML 报告（关系网/爽点曲线/时间轴）
   └─ ai_writing_pack/（下游 AI 直接 import）
```

## 快速开始

```bash
# 1. 安装依赖（推荐 uv）
uv sync           # 或 pip install -e ".[dev]"

# 2. 配置 API key
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK / ANTHROPIC keys

# 3. 起 Neo4j（可选，只想要本地 JSON 图谱可跳过）
docker compose up -d neo4j

# 4. 拆解一本小说，无向量模式，继续模式
cd /Users/zhangyu/code/project/novel
NOVEL_LAB_SKIP_INDEX=1 .venv/bin/novel-lab analyze "/Users/zhangyu/code/project/novel/novel合集/西游记（原文版）.txt" --tier basic --genre generic --resume

# 有向量模式
cd /Users/zhangyu/code/project/novel
export NOVEL_LAB_FORCE_REINDEX=1
# 可选：显式声明后端（你已有 DASHSCOPE_API_KEY 时不写也会自动走 dashscope）
export EMBEDDING_BACKEND=dashscope
.venv/bin/novel-lab analyze "/Users/zhangyu/code/project/novel/novel合集/西游记（原文版）.txt" --tier basic --genre generic --rag --resume

# 5. 查看输出
open ./.workdir/<book_id>/output_pack/report.html
```

### 完整重跑同一本书（不沿用旧 map / 旧报告）

同一 txt 的路径与内容哈希固定 → **永远落在 `.workdir/<book_id>/`**。继续用 **`--resume`** 时，会读取 **`checkpoints/map.sqlite`**，所以会误以为「和以前同一条命令、没重头跑」。可选：

```bash
cd /Users/zhangyu/code/project/novel

# 推荐：删掉该书整条工作目录（map / checkpoints / chroma / output_pack 全清）
rm -rf .workdir/book_<该书book_id>   # book_id：ls .workdir/ 或对应该书的 meta.json / 上轮日志

# 无向量 + 不重读 map 校验点（仍会写回 map.sqlite）。注意：下文 LLM 缓存。
NOVEL_LAB_SKIP_INDEX=1 .venv/bin/novel-lab analyze "/path/to/小说.txt" --tier basic --genre generic --no-resume

# 有向量 + 强制重算嵌入 + 不重读 map 校验点
NOVEL_LAB_FORCE_REINDEX=1 EMBEDDING_BACKEND=dashscope .venv/bin/novel-lab analyze "/path/to/小说.txt" --tier balanced --genre generic --rag --no-resume
```

**全局** LLM 缓存在 `.workdir/llm_cache.sqlite`。即使用 `--no-resume`，只要 prompt 完全一致仍可能 **命中缓存、不再调用 API**。若要连模型输出都强制刷新（会影响**其他书**）：先备份再删掉该文件：

```bash
mv .workdir/llm_cache.sqlite .workdir/llm_cache.sqlite.bak
```

## 关键 CLI

```bash
novel-lab analyze <path>
  --genre {xuanhuan|yanqing|dushi|generic}     # 题材，决定套路库
  --tier {basic|balanced|premium|local}        # 质量/成本档位
  --sample-ratio 0.3                           # 抽样模式快速预览
  --resume                                     # 断点续跑
  --no-resume                                  # 忽略 map 校验点，全书重跑 Map API（仍可能命中 llm_cache.sqlite）
  --rag / --no-rag                             # 启用/关闭向量检索参与主线复核
  --no-graph                                   # 不写 Neo4j（只产 graph.json）
  --max-chapters 200                           # 限制章节数（调试用）
```

## 输出物

```
.workdir/<book_id>/output_pack/
├── report.html                    # 交互式可视化报告
├── creation_constitution.md       # 创作宪法（核心爽点公式 / 必避雷区 / 章末钩子库）
├── knowledge_graph.cypher         # 可导入 Neo4j
├── graph.json                     # 前端渲染用
├── metrics.json                   # 爽点密度、追读风险、对标百分位
└── ai_writing_pack/
    ├── system_prompt.md           # 给下游 LLM 的 system prompt
    ├── style_few_shot.md          # 风格 few-shot 例
    ├── characters.yaml            # 角色卡
    └── tropes.json                # 套路库
```

## 设计决策

- **LLM 路由**：Layer1 用 DeepSeek-V3（$0.14/1M），Layer2/3 用 Claude Sonnet 4.5。可在 `.env` 切到全本地 Qwen3。
- **无幻觉证据链**：所有结构化结论强制带 `evidence_chapter` 字段，Critic agent 会校对原文。
- **断点续跑**：每个节点结果写 SQLite checkpoint，断电重跑不烧钱。
- **题材套路库**：`config/genres/` 下 YAML 可扩展，玄幻/言情/都市内置。
- **本地优先**：默认本地 `sentence-transformers` 做 embedding，不外发原文。

## Phase 2 路线图

- 评论挖掘插件（Scrapy + Playwright 爬起点/番茄章评 → BERT 聚类 → 反向校准 Layer3）
- Gradio Web UI（上传 / 进度可视化 / 多书横向对比 / Pack 一键下载）
- 同题材横向对比库（跑过的书入库，新书自动对标 top10% 爆款基线）

## 参考资料

- NexusSum (ACL 2025) — 分层多 LLM 智能体长叙事摘要
- LlamaIndex Hierarchical Parent-Child Indexing
- Neo4j LLM Knowledge Graph Builder
- 网文 666《人物弧光设计》、马良《三级大纲体系》、六神磊磊文本细读法
