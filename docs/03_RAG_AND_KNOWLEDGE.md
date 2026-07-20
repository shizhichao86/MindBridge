<!--
  文档定位: 面试用技术深度分析文档
  面试权重: ★★★★☆ (高频考点, 涉及检索策略/降级设计/中文分词/评测体系)
  前置阅读: 无 (从 0 开始, 假设读者第一次接触多智能体系统的知识模块)
-->

# 混合检索系统深度分析

> 你是否想过——一个校园心理对话机器人, 当学生说"我最近好丧"和"我最近情绪低落"时, 检索到的知识条目一样吗? 这就是 RAG (Retrieval-Augmented Generation) 检索系统的核心挑战。

## 1. 为什么需要混合检索

### 1.1 纯向量的局限

向量检索 (如 Chroma + text-embedding-3-small) 擅长捕捉语义相似性: "丧"和"情绪低落"在向量空间中距离很近。但当你查找"HIGH 风险处置流程"时, 纯向量可能返回一堆语义相关但不含关键词"Excel台账"的 chunk —— 因为向量空间里"风险处置"的语义邻居是"风险评估"而不是"Excel 表格"。

### 1.2 纯关键词的局限

BM25 等关键词匹配倒过来: "自杀"命中"自杀", 但"不想活了"这种口语化表达可能完全漏掉 —— 因为中文字面上没有交集, 但语义上高度危险。

### 1.3 校园心理场景的特殊性

| 维度 | 特征 | 挑战 |
|------|------|------|
| 口语 vs 术语 | "想不开" vs "自杀意念" | 纯向量需要同义词覆盖 |
| 专业文档 | 风险策略、处置流程 | 纯关键词需要精确命中 |
| 安全敏感 | 高危用语必须不能漏 | 两种召回路径缺一不可 |
| 可部署性 | 学校机房可能无网/无 GPU | 必须支持纯本地降级 |

**设计决策**: 混合检索 = 向量语义分 (0.65) + BM25 关键词分 (0.35), 互为补充, 覆盖同一需要检索的 chunk 的两个正交维度。对应代码见 `app/services/knowledge.py:127-138`.

## 2. 架构总览

```
                        ┌─────────────────────┐
                        │   用户输入 query      │
                        └─────────┬───────────┘
                                  │
                 ┌────────────────┼────────────────┐
                 ▼                                 ▼
    ┌────────────────────────┐        ┌────────────────────────┐
    │   向量分支 (Chroma)     │        │   BM25 分支 (纯 Python) │
    │   OpenAI embedding      │        │   tokenize + IDF 算分   │
    │   candidate_k = 16      │        │   candidate_k = 16      │
    └───────────┬─────────────┘        └───────────┬────────────┘
                │                                  │
                │    vector_results                  │   bm25_results
                │    (SearchResult[])                │   (SearchResult[])
                └────────────────┬──────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Min-Max 归一化          │
                    │   normalize_scores()      │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   分数融合                 │
                    │   vector*0.65+bm25*0.35  │
                    │   去重 + 裁断 candidate_k │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Rerank (本地)           │
                    │   base*0.55 + hybrid*0.25│
                    │   + coverage*0.15        │
                    │   + phrase*0.05          │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   上下文扩展               │
                    │   _expand_best()         │
                    │   最优 chunk ±1 邻居拼接  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   top_k = 4 (默认)       │
                    └─────────────────────────┘

        当 can_embed = False 时:
        ┌─────────────────────────────────────────────┐
        │  向量分支短路 → 纯 BM25 + hybrid_score 重排  │
        │  (无 OpenAI key / chromadb 未装 /            │
        │   KNOWLEDGE_VECTOR_ENABLED=false)            │
        └─────────────────────────────────────────────┘
```

降级路径代码在 `app/services/vector_store.py:38-52` 的 `__init__` 中: 一旦 `openai_api_key` 缺失或 `chromadb` 未安装, `can_embed` 置为 `False`, 后续 `_retrieve_vector()` 直接返回空列表 (`app/services/knowledge.py:208-209`)。

## 3. 向量检索分支: ChromaKnowledgeStore

### 3.1 Chroma 持久化配置

`app/services/vector_store.py:31-63` 实现向量存储的核心类:

```python
# vector_store.py:54-62
persist_dir = self._resolve_path(settings.chroma_persist_dir)
self.client = chromadb.PersistentClient(path=str(persist_dir))
self.collection = self.client.get_or_create_collection(
    name=settings.chroma_collection_name,  # "mindbridge_knowledge"
    embedding_function=None,                # 不依赖 Chroma 内置 embedding
    metadata={"hnsw:space": "cosine", "embedding_model": ...},
)
```

关键设计:
- **`embedding_function=None`**: 不把 embedding 计算交给 Chroma, 而是由应用侧调用 OpenAI API 得到向量后再 Upsert。这样 embedding 逻辑完全可控、可替换。
- **`hnsw:space=cosine`**: 向量相似度用余弦距离, 与 OpenAI embedding 的训练目标一致。
- **持久化路径**: 默认 `data/chroma`, 可通过配置覆盖。

### 3.2 OpenAI text-embedding-3-small 嵌入

`app/services/vector_store.py:121-160` 的 `_embed()` 方法:

- 通过 HTTPX 调用 OpenAI 兼容的 `/embeddings` 端点
- 使用 `text-embedding-3-small` 模型 (1536 维)
- 支持任何 OpenAI 兼容服务 (通过 `OPENAI_BASE_URL` 切换)
- 返回后按 `index` 排序保证顺序

### 3.3 candidate_k=16 的设计哲学

为什么向量分支取 16 个候选, 而不是直接取 `top_k=4`?

```
┌──────────────────────────────────────────────────────┐
│  语义检索: 宽召回 → 重排精筛                            │
│                                                      │
│  candidate_k=16 (粗筛)                               │
│    → fuse + rerank                                   │
│      → top_k=4 (精筛)                                │
│                                                      │
│  如果直接 top_k=4:                                    │
│  - 向量纬度可能漏掉关键词精确匹配的 chunk               │
│  - 融合时没有足够的候选池做 cross-signal score          │
│  - 重排只能在更小集合上操作, 精度下降                   │
└──────────────────────────────────────────────────────┘
```

代码见 `app/services/knowledge.py:204-205`:
```python
def _candidate_k(self, top_k: int) -> int:
    return max(top_k, self.settings.knowledge_candidate_k)  # max(4, 16) = 16
```

### 3.4 按需建索引

`_ensure_vector_index()` (`knowledge.py:230-240`) 实现惰性建索引:
- 首次检索时才检查 Chroma 是否与 MySQL chunk 表一致
- 不一致则调用 `_sync_vector_chunks()` 同步
- 对于已有 `embedding_json` 的 chunk, 直接用缓存避免重复调用 OpenAI API
- 仅对缺失 embedding 的 chunk 发起新请求 (`_embeddings_for_chunks`, knowledge.py:273-290)

### 3.5 快照备份机制

`vector_store.py:126-136` 的 `snapshot()` + `upsert_chunks` 末尾自动触发:
- 每次 Upsert 后自动生成时间戳快照 (`YYYYMMDD-HHMMSS-微秒`)
- 保留最近 `chroma_snapshot_keep=5` 份, 自动清理旧的
- 使用 `shutil.copytree` 完整复制 Chroma 持久化目录

## 4. BM25 关键词分支 (纯 Python 实现)

没有依赖 Elasticsearch 或 Whoosh, 全部裸写 Python。

### 4.1 BM25 算法公式

`app/services/knowledge.py:348-384` 的 `bm25_scores()`:

```
SCORE(q, d) = Σ IDF(t) × query_boost(t) × TF_saturated(t, d)

其中:
  IDF(t)        = ln(1 + (N - df + 0.5) / (df + 0.5))
  query_boost(t) = 1 + ln(query_freq(t))
  TF_saturated   = f(t,d) × (k1 + 1) / (f(t,d) + length_norm)

  length_norm = k1 × (1 - b + b × doc_len / avg_len)

  k1 = 1.5 (term saturation),  b = 0.75 (长度归一化)
```

`k1=1.5` 控制词频饱和速度 —— 比默认 1.2 略高, 给高频匹配词更多权重, 适合短查询。`b=0.75` 接近标准值, 抑制长文档的天然优势。

### 4.2 中文分词: 正则 + 2-gram

`app/services/knowledge.py:452-457` 的 `tokenize()`:

```python
def tokenize(text: str) -> list[str]:
    # 第一层: 英文词/数字/单个汉字
    words = re.findall(r"[a-zA-Z0-9_]+|[一-鿿]", text.lower())
    grams = words[:]

    # 第二层: 2-gram 汉字组合 (补偿单字粒度不足)
    compact = "".join(ch for ch in text.lower() if "一" <= ch <= "鿿")
    grams.extend(compact[i:i + 2] for i in range(max(0, len(compact) - 1)))

    return [item for item in grams if item.strip()]
```

为什么这样做?

| 方案 | 优点 | 缺点 |
|------|------|------|
| jieba 分词 | 词级准确度高 | 额外依赖, 词汇表大小, 心理领域新词未收录 |
| 单字匹配 | 零依赖 | 信息丢失严重, "心理"拆成"心"+"理"失去语义 |
| **正则+2-gram** | **零依赖+字符级覆盖** | "不想活了"→"不想"/"想活"/"活了", 每个 2-gram 都是检索入口 |

这是典型的 **"够用就好 (KISS)"** 决策: 校园心理 RAG 的知识库规模是 11 篇 Markdown (约几百个 chunk), 不需要工业级分词器, 字符级 2-gram 足以覆盖召回需求。

### 4.3 token_cosine 与 hybrid_score

`knowledge.py:344-345` 的 `hybrid_score()`:
```
hybrid_score = token_cosine × 0.75 + keyword_score × 0.25
```

`token_cosine` (`knowledge.py:460-468`) 计算两个文本 token 集合的余弦相似度, `keyword_score` (`knowledge.py:471-477`) 按 ≥2 字的查询词在文档中的命中比例打分。这是降级检索路径 (无向量) 的重排算子。

## 5. 分数融合 (_fuse_and_rerank)

`app/services/knowledge.py:151-192`:

### 5.1 Min-Max 归一化

`normalize_scores()` (`knowledge.py:416-427`):

```
normalized(x) = (x - min) / (max - min)  (仅对 >0 的分数)
```

向量分数 (cosine 相似度 0~1) 和 BM25 分数 (无上限) 量纲完全不同 —— 不归一化的话, BM25 分数会主导融合结果。归一化后两者都在 [0, 1] 区间可比。

### 5.2 融合公式

```
fused_score = (vector_norm × 0.65 + bm25_norm × 0.35) / total_weight
```

代码细节 (`knowledge.py:176-191`):

```python
# 如果向量分支无结果, 向量权重归零, 纯靠 BM25
vector_weight = max(0.0, self.settings.knowledge_hybrid_vector_weight) if vector_results else 0.0
bm25_weight = max(0.0, self.settings.knowledge_hybrid_bm25_weight)
# 防止除以零: 如果两个权重都为 0, 回退到 bm25_weight=1.0
if vector_weight == 0.0 and bm25_weight == 0.0:
    bm25_weight = 1.0
```

融合后裁断到 `candidate_k` 再送入 rerank。

### 5.3 为什么 0.65 / 0.35?

```
┌─────────────────────────────────────────────────────────┐
│  Trade-off:                                             │
│                                                         │
│  向量 0.65 > BM25 0.35 的原因:                          │
│  1. 心理对话以语义为主 ("想不开" 不能靠关键词)            │
│  2. Embedding 召回质量经过大规模语料训练, 通用性好        │
│  3. 但 BM25 作为"安全网"不可为零: 专业术语                   │
│     (如 "PHQ-9" / "CBT" / "DSM-5") 需精确命中           │
│                                                         │
│  调优方式: 通过环境变量 KNOLWEDGE_HYBRID_VECTOR_WEIGHT   │
│  和 KNOWLEDGE_HYBRID_BM25_WEIGHT 覆盖, 配合 RAG 评测     │
│  报告观察 recall@K 和 MRR 变化。                         │
└─────────────────────────────────────────────────────────┘
```

## 6. 本地 Rerank

### 6.1 公式分解

`app/services/knowledge.py:387-391`:

```
rerank_score = base × 0.55 + lexical × 0.25 + coverage × 0.15 + phrase × 0.05
```

| 因子 | 含义 | 实现 |
|------|------|------|
| `base` (0.55) | 上游融合分数 | 直接传入, 保持召回信号 |
| `lexical` (0.25) | 词法相似度 | `hybrid_score()` = token_cosine×0.75 + keyword×0.25 |
| `coverage` (0.15) | query token 覆盖率 | `query_token_coverage()`: 查询 token 在文档中出现的比例 |
| `phrase` (0.05) | 短语精确匹配 | 去空格小写后子串包含 → 1.0; 否则回退 keyword |

权重设计思路: **上游信号占大头** (融合分数已经是向量+BM25 的 double evidence), 词法相似度提供二次校验, 覆盖率防止只匹配部分词的"片面 related"结果, 短语是锦上添花的精确信号。

### 6.2 为什么不用 Cross-encoder?

```
┌──────────────┬─────────────────────┬──────────────────────┐
│              │  本地公式 Rerank    │  Cross-encoder (如    │
│              │  (当前方案)         │  BGE-Reranker)       │
├──────────────┼─────────────────────┼──────────────────────┤
│ 延迟         │  ~1ms (纯计算)      │  ~50-200ms (模型推理) │
│ 依赖         │  零额外依赖          │  需 GPU / ONNX Runtime│
│ 准确性       │  中等 (线性加权)     │  高 (深度语义)        │
│ 可解释性      │  高 (每个因子可追踪) │  低 (黑盒)            │
│ 适用规模      │  < 1000 chunks     │  任意规模             │
└──────────────┴─────────────────────┴──────────────────────┘
```

设计决策: 知识库仅约 500 chunks, `candidate_k=16` 重排集合很小, 本地公式足以区分; 且项目目标部署环境是学校机房 (可能无 GPU), **零依赖是硬约束**。如果知识库扩展到万级, 可插入 Cross-encoder 作为可选增强。

## 7. 上下文扩展 (_expand_best)

`app/services/knowledge.py:303-329`:

```
检索结果的第一个 chunk (得分最高) → 查同 source 下的 ±1 邻居 → 拼接
```

```python
# knowledge.py:321-327
neighbors = (
    self.db.query(KnowledgeChunk)
    .filter(KnowledgeChunk.source == chunk.source)
    .filter(KnowledgeChunk.source_index >= max(0, chunk.source_index - 1))
    .filter(KnowledgeChunk.source_index <= chunk.source_index + 1)
    .order_by(KnowledgeChunk.source_index.asc())
    .all()
)
return SearchResult(chunk.id, chunk.source,
    "\n\n".join(item.content for item in neighbors), result.score)
```

为什么要扩展? chunk 默认 512 字符带 64 字符 overlap, 但"前因后果"可能跨越两个 chunk —— 把 neighbors 拼接进来保证 LLM 看到完整上下文。

注意: 扩展只对第一个结果做, 因为后面的 chunk 不会作为 RAG 的主证据送入 prompt。

## 8. 降级策略设计 (面试亮点)

### 8.1 三级降级路径

`app/services/vector_store.py:36-63` + `app/services/knowledge.py:207-209`:

```
Level 1: KNOWLEDGE_VECTOR_ENABLED=true, OPENAI_API_KEY 已配置, chromadb 已安装
         → 完整混合检索 (Chroma + BM25 + 融合 + Rerank + 扩展)

Level 2: KNOWLEDGE_VECTOR_ENABLED=true, 但 OPENAI_API_KEY 缺失 或 chromadb 未安装
         → can_embed=False, 向量分支短路, 纯 BM25 + hybrid_score rerank
         → 对应检索路径: _retrieve_vector() 直接 return []
         → _fuse_and_rerank 检测到 vector_results 为空后 vector_weight 置零

Level 3: KNOWLEDGE_VECTOR_REQUIRED=true, 但向量条件不满足
         → 直接 raise VectorStoreUnavailable, 拒绝启动
```

### 8.2 触发条件汇总

| 条件 | can_embed | 行为 |
|------|-----------|------|
| `KNOWLEDGE_VECTOR_ENABLED=false` | `False` | BM25 only, 不尝试加载 chromadb |
| `OPENAI_API_KEY` 为空 + `KNOWLEDGE_VECTOR_REQUIRED=false` | `False` | BM25 only |
| `OPENAI_API_KEY` 为空 + `KNOWLEDGE_VECTOR_REQUIRED=true` | 抛异常 | 启动即拒绝 |
| `chromadb` 未安装 + `KNOWLEDGE_VECTOR_REQUIRED=false` | `False` | BM25 only |
| `chromadb` 未安装 + `KNOWLEDGE_VECTOR_REQUIRED=true` | 抛异常 | 启动即拒绝 |
| 向量检索抛异常 | 已为 `True` | `_handle_vector_error()`: REQUIRED → raise, 否则 log warning + 空列表 |

### 8.3 为什么自动降级?

> "学校心理咨询室的一台旧电脑, 没配 OpenAI Key, 系统还能用吗?"

这就是 `KNOWLEDGE_VECTOR_REQUIRED=false` (默认) 的设计动机: **可用性优先于最佳检索质量**。纯 BM25 在 11 篇知识库上的检索质量虽然不如混合方案, 但足以让对话系统正常运行 —— 丢失的是语义泛化能力, 保留的是关键词精确召回能力。

## 9. 知识库管理 (CRUD)

### 9.1 Seed 同步机制

`app/core/bootstrap.py:33-36`:

```python
service = KnowledgeService(db, get_settings())
for file in sorted((root / "knowledge").glob("*.md")):
    service.ensure_source(file.name, file.read_text(encoding="utf-8"))
```

`ensure_source()` (`knowledge.py:45-56`) 的设计:

```
读取文件 → 按当前分块规则分块 (chunk_size=512, overlap=64)
         → 比对 DB 中已有 chunk 列表
         → 一致? 跳过 (避免重建索引)
         → 不一致? 先删旧的 DB+向量记录, 再 ingest 新内容
```

这个"变更检测"使得: 每次重启只对内容有变化的文件重新分块和索引, 不变的不动。

### 9.2 分块算法

`knowledge.py:331-341` 的 `chunk_text()`: 滑动窗口, step = size - overlap = 512 - 64 = 448。朴素但够用 —— 心理常识文档结构简单 (段落为主), 不需要 RecursiveCharacterTextSplitter。

### 9.3 管理员 API

- `POST /api/knowledge/rebuild-vector-index` → `rebuild_vector_index()`: 全量重建 Chroma 索引
- `POST /api/knowledge/backup-vector-index` → `backup_vector_index()`: 生成 Chroma 快照
- 上传文件 `ingest_file()`: 支持 `.md` / `.txt` / `.pdf`
- `GET /api/knowledge/status` → `status()`: 返回检索链路状态全貌

### 9.4 KnowledgeChunk 的 embedding_json 缓存

`KnowledgeChunk` 实体中有 `embedding_json` 列 (`knowledge.py:257`): 一旦调过 OpenAI embedding, 向量序列化 JSON 存入 MySQL。下次 `_ensure_vector_index` 发现 `embedding_json` 非空就直接用, 不重复调用 API —— 简单但有效的 Embedding 缓存。

## 10. RAG 评测体系

### 10.1 评测数据集

`app/rag_eval/mindbridge-rag-eval.json` (当前约 30+ 条):

```json
{
  "id": "risk-high-self-harm-direct",
  "question": "学生明确说想自杀或者伤害自己时, 系统应该怎么处理?",
  "expectedSources": ["risk-policy.md"],
  "expectedTerms": ["HIGH", "self-harm", "suicide", "alert"]
}
```

每条 case 指定:
- `expectedSources`: 期望检索到的知识文件
- `expectedTerms`: 期望命中内容的关键词 (≥2 字匹配即判 relevant)

### 10.2 评测指标

`app/rag_eval/runner.py:24-35`:

| 指标 | 公式 | 含义 |
|------|------|------|
| **Recall@K** | 1 或 0 (binary) | 是否有 relevant chunk 在前 K 个结果中 |
| **Precision@K** | relevant_count / K | 前 K 个中 relevant 的比例 |
| **MRR** | 1 / first_relevant_rank | 第一个 relevant 的排位倒数, 早出现得分高 |
| **NDCG@K** | DCG / IDCG | 考虑排位加权的归一化折损累计增益 |
| **HitRate** | hits / total | 至少命中一条的比例 (与 Recall@K 等价) |
| **AvgFirstRelevantRank** | mean(first_relevant_rank) | 第一个相关结果平均排位 |

### 10.3 运行方式

```bash
AI_PROVIDER=mock python -m app.rag_eval.runner
# 输出 target/rag-eval-report.json
```

评测使用 `mock` provider, 向量检索部分依赖真实的 OpenAI embedding (需要 KEY), 降级路径下只用 BM25 也能出报告。

## 11. 面试追问

### Q1: 为什么不用 Elasticsearch 而是自己实现 BM25?

**答**: 三个原因。1) **零运维**: 学校机房不需要额外部署 ES 集群, 一个 Python 进程搞定全部。2) **规模**: 知识库 11 篇 Markdown, ~500 chunks, ES 的倒排索引优势在百级规模不明显。3) **可控性**: 自己实现 BM25 可以细调 `k1`/`b` 参数和分词策略, 也能方便地融合向量分。如果知识库扩展到万级以上, 引入 ES 作为关键词分支是合理的重构方向。

### Q2: 权重 0.65 是怎么来的?

**答**: 坦白说, 是经验值, 不是网格搜索出来的。设计逻辑是"语义权重 > 关键词权重", 因为场景是心理对话, 口语化表达多。如果要从工程上严谨调优, 应该做: 用 RAG 评测数据集遍历 (0.5, 0.5) → (0.9, 0.1) 的权重组合, 对比 Recall@K/MRR 挑最优; 或者让不同权重跑人工标注的相关性判断。当前通过环境变量已支持覆盖, 见 `app/core/config.py:43-44`。

### Q3: 怎么评估检索质量?

**答**: 通过 `app/rag_eval/runner.py` 的自动化评测: 30+ 条标注了预期 source 和预期 term 的 case, 每次检索出 top_k=4 结果后自动计算 Recall@K/Precision@K/MRR/NDCG@K/HitRate, 输出 JSON 报告。评测数据集覆盖 HIGH 风险、MEDIUM 风险、日常对话三类场景。不足是目前没有人工标注的相关性分 (只有 0/1), 无法算 precision@1 以外的细粒度排序质量。

### Q4: 中文分词的挑战和方案?

**答**: 最大的挑战是心理领域术语 (如"自杀意念"、"习得性无助"、"创伤后应激障碍") 和口语表达 (如"想不开"、"活着没意思") 的匹配鸿沟。当前方案是 2-gram 字符级覆盖, 避免引入 jieba 等分词库的额外依赖和词汇表问题。代价是 2-gram 会产生噪音 ("不想活了" → "不想"/"想活"/"活了" 中 "想活" 单独看偏正面, 但组合语境是负面的)。缓解策略: BM25 的 `k1` 稍高 (1.5) 给多词共现的文档加分, 配合向量分支捕捉语义。未来可考虑: 加入心理领域自定义词典 (如"自杀意念"作为一个 token), 或引入 jieba + 自定义词典作为可选的增强分词。

---

**文件索引**

| 文件 | 职责 |
|------|------|
| `app/services/knowledge.py` | 混合检索核心: 向量+BM25+融合+Rerank+扩展 |
| `app/services/vector_store.py` | Chroma 向量存储: 嵌入/Upsert/查询/快照 |
| `app/rag_eval/runner.py` | RAG 评测执行器 |
| `app/rag_eval/mindbridge-rag-eval.json` | 评测数据集 |
| `app/core/bootstrap.py` | 启动时 seed 知识库 |
| `app/core/config.py:38-56` | 知识库/Chroma/RAG 配置项 |
