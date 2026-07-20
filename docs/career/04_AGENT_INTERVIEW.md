# AI Agent 应用开发面试题

> 面向「AI Agent 应用开发工程师」岗位的面试准备文档。结合 MindBridge Python 项目(事件驱动多智能体校园心理支持系统)的实际源码,每问附代码引用位置,面试时可精准举证。

---

## 1. Agent 架构基础 (5 问)

### Q1: 什么是 AI Agent?和传统 ChatBot 有什么区别?

| 维度 | 传统 ChatBot | AI Agent |
|------|-------------|----------|
| 控制流 | 单次请求-响应,无状态 | 多步决策循环(观察-思考-行动) |
| 记忆 | 无或仅 session 变量 | 短期/长期/工作记忆三层 |
| 工具能力 | 无 | 可调用 API、数据库、外部服务 |
| 自主性 | 被动响应 | 自主推理、规划、修错 |
| 安全 | 输入校验 | 多层护栏+独立审查 Agent |

**项目印证**: MindBridge 的 5 个 Agent 通过 `CollaborationBlackboard` (app/agents/events.py:121) 协作,不是简单问答对。SafetyAgent 独立审查 ResponseAgent 的输出 (app/agents/autonomous.py:262),这远超 ChatBot 的能力边界。

### Q2: ReAct / Plan-Execute / Reflection 三种 Agent 模式的区别和适用场景?

| 模式 | 流程 | 适用 | 本项目对应 |
|------|------|------|-----------|
| **ReAct** | Reason-Act-Observe 循环,每步交替推理/行动/观察 | 工具调用密集场景(搜索、API) | ContextAgent 的检索循环 (autonomous.py:348-373) |
| **Plan-Execute** | 先生成完整计划,再逐步执行 | 多步复杂任务(代码生成、报告) | Coordinator 的 `_derive_missing_work` 推导任务链 (coordinator.py:92) |
| **Reflection** | 执行后自我审视,修正错误 | 需要质量保证的场景(客服、安全) | SafetyAgent 的 `_review_response` 审核机制 (autonomous.py:262-307) |

**关键洞见**: MindBridge 不是单一模式,而是在同一运行时内混合了三种模式 — Coordinator 做 Plan,各 Agent 做 ReAct 式认领,SafetyAgent 做 Reflection 审查。这是成熟 Agent 系统的典型设计。

### Q3: 单 Agent vs 多 Agent 的 trade-off?

| 维度 | 单 Agent | 多 Agent |
|------|---------|---------|
| 复杂度 | 低,一个 prompt + 工具调用 | 高,需要协调/通信/冲突解决 |
| 可靠性 | 单点故障,无制衡 | 互相制衡,一个挂了其他兜底 |
| 安全 | LLM 自己审查自己(不可靠) | 独立安全 Agent 有否决权 |
| 成本 | 低(一次 LLM 调用) | 高(多次调用,但可分层用不同模型) |
| 延迟 | 低 | 更高(但本项目中 CHAT 模式只 3 轮) |

**本项目选择多 Agent 的原因** (见 `docs/tech/02_AGENT_RUNTIME.md` 第 1.1 节): 心理健康场景下有"不可漏报"的硬约束 — 单 LLM 无法给自己当安全审查员。`SafetyAgent` 通过 `SAFETY_OVERRIDE` 事件 (events.py:18) 可强制覆盖其他 Agent 的判断。

### Q4: Agent 的记忆系统怎么设计(短期/长期/工作记忆)?

三个层次:

| 类型 | 存储 | 周期 | 本项目实现 |
|------|------|------|-----------|
| **短期记忆** | Redis | 24h TTL,40 条上限 | `RedisShortTermMemoryStore`,key=`mindbridge:short-term-memory:{sid}` |
| **长期记忆** | MySQL | 永久 | `chat_messages` 表,全量持久化 |
| **工作记忆** | 黑板(内存) | 单次对话 | `CollaborationBlackboard` artifacts/tasks/messages (events.py:121-131) |

**额外设计 — 私有记忆**: 每个 Agent 拥有独立的 Redis key (`agent:{AgentName}:{session_id}`),`AgentPrivateMemory` (autonomous.py:53-68)。这确保 SafetyAgent 的风险判断记忆不偏向 ResponseAgent 的策略记忆,实现**记忆隔离**。

### Q5: Function Calling / Tool Use 的设计原则?怎么处理工具调用失败?

**设计原则**:

1. **最小权限** — 每个 Agent 只有其任务需要的工具权限 frozenset (registry.py:25),如 ContextAgent 只能 `rag.retrieve`,不能写 Excel
2. **治理前置** — 执行前 `ToolPolicyRegistry.authorize()` 检查风险等级是否匹配 (tool_governance.py:52-61),生成 `ToolAuditRecord`
3. **解耦执行** — 工具执行与流式回复完全异步,学生不会等 Excel 写完才看到回复

**失败处理** (tool_queue.py:237-262):
- `attempts < max_attempts` → 延迟重试(指数退避)
- `attempts >= max_attempts` → 进死信队列 (`DeadLetterRecord`),管理员人工排查
- `ALERT_SEND` 依赖 `CASE_CREATE` 先成功,依赖不满足 → `_requeue()` 回 PENDING 带 2s 延迟 (tool_queue.py:156-157)
- 邮件投递 `mode="log"` 时永远 SUCCESS — 开发环境没 SMTP 也能正常工作

---

## 2. 多智能体协作 (5 问,结合本项目)

### Q6: 多 Agent 协作有哪些模式(黑板/消息传递/层级/市场)?

| 模式 | 机制 | 代表框架 | 本项目对应 |
|------|------|---------|-----------|
| **黑板** | 共享数据区,Agent 读/写,协调器调度 | 本项目、早期的 Hearsay-II | `CollaborationBlackboard` (events.py:121) |
| **消息传递** | Agent 间点对点发消息 | AutoGen、CrewAI | 本项目的 `AgentMessage` (events.py:80-88) |
| **层级** | 主 Agent 分解任务给子 Agent | LangGraph Supervisor | CoordinatorAgent 推导 + 分配 (coordinator.py:92) |
| **市场/竞标** | Agent 对任务出价,价高者得 | AG2、Mixture-of-Agents | 认领制 (`_claim_candidates`,coordinator.py:201-228) |

**本项目组合了黑板 + 市场两种模式**: 黑板是通信载体,市场(认领)是调度机制。Agent 不直接调另一个 Agent 的方法 — 各自读黑板上的 artifact,发布自己的 artifact。

### Q7: 为什么本项目选黑板+认领制?有什么 trade-off?

**选择原因**:

1. **安全制衡第一** — 固定 DAG 中 SafetyAgent 只是个节点;认领制下 SafetyAgent 可以主动认领+发布 `SAFETY_OVERRIDE`,任何阶段都能介入
2. **条件跳过** — CHAT 模式自动跳过 ContextAgent (coordinator.py:107-110),减少不必要的 LLM 调用
3. **优先级天然支持** — 高风险关键词命中 → task 优先级 CRITICAL → 排到认领队列最前面 (coordinator.py:275-277)

**Trade-off**:

| 优点 | 缺点 |
|------|------|
| 灵活,工作流按需推导而非预定义 | 调试难,事件序列不固定 |
| Agent 间零耦合,加/减 Agent 不改其他 Agent | 可能出现无 Agent 认领(已通过 `force_response` 兜底) |
| 事件溯源,完整审计 | 每轮都要重新 check 所有 Agent 的 decide,有开销 |

**面试话术**: "固定 DAG 适合确定性强的工作流(如 RAG 管线),但安全敏感场景需要 Agent 能随时超控。我们的 `SAFETY_OVERRIDE` 事件表明:安全不是工作流的一个节点,而是贯穿全程的横切面。"

### Q8: Agent 之间如何避免冲突?(结合 SafetyAgent SAFETY_OVERRIDE)

**冲突场景**: ResponseAgent 提出乐观回复,但 SafetyAgent 认为不够安全。

**解决机制** (3 层):

```
Layer 1: 架构制衡 — SafetyAgent 独立评估,不共享 ResponseAgent 记忆 (autonomous.py:84-85)
Layer 2: SAFETY_OVERRIDE — 硬关键词 or LLM 评估 HIGH → 直接发布 SAFETY_OVERRIDE 事件 (autonomous.py:236-245)
         → _risk_value() 返回 HIGH,覆盖所有下游判断
Layer 3: Revision 闭环 — _review_response 发现 risk=HIGH 且回复缺少安全引导词 
         → approved=false → REVISION_REQUESTED 事件 → 创建 task:revise-response (CRITICAL)
         → ResponseAgent 下一轮修订 (autonomous.py:268-300)
```

**冲突裁决原则**: **安全侧永远胜出**。`_try_accept_final` (coordinator.py:230-245) 要求 safety_review 必须 approved + confidence >= 0.6,否则拒绝终态。`BUDGET_EXHAUSTED` 兜底时也要取 `latest_artifact("response_proposal")` — 但此时 review 未通过,至少留了事件记录。

### Q9: 多 Agent 系统的可观测性怎么做?(结合 AgentRunTrace)

**可观测性三要素**:

1. **事件溯源** — `CollaborationBlackboard.events` 是 12 种事件的完整时间线 (events.py:8-22),从 `TURN_STARTED` 到 `FINAL_ACCEPTED`/`BUDGET_EXHAUSTED`,每个 Agent 的 claim/act/publish 操作都有记录
2. **结构化返回** — `AgentRunResult` (result.py:21-35) 包含 `steps`(摘要)、`collaboration_events`(完整事件链)、`collaboration_tasks`(任务列表)、`collaboration_artifacts`(产出物列表)
3. **`/api/agent/status` 端点** — 返回 `collaboration.agentIsolation` 结构,展示每个 Agent 的隔离状态

**调试技巧** (docs/tech/02_AGENT_RUNTIME.md 第 7.2 节): 典型对话有完整事件序列可推导。问题发生时,从 `events` 列表倒推:哪轮哪个 Agent 发布了什么 artifact?谁审核的?为什么没通过终态?

### Q10: 如何给多 Agent 系统加新 Agent?(结合 AgentRegistry)

**标准步骤** (参考 docs/tech/02_AGENT_RUNTIME.md 第 11 章 Q5):

| 步骤 | 操作 | 代码位置 |
|------|------|---------|
| ① 定义 Profile | 名称、capabilities frozenset、system_prompt、model_profile、tool_permissions | registry.py:18-25 |
| ② 实现 decide() | 根据 task + board 判断是否认领,返回 AgentDecision(claim, confidence, reason) | autonomous.py:122 |
| ③ 实现 act() | 执行任务,返回 AgentTurnResult(messages+artifacts+tasks+events) | autonomous.py:129 |
| ④ 注册到 Runtime | 加入 `create_agent_runtime()` 的 agents 列表 | event_driven_runtime.py:65-70 |
| ⑤ 任务推导 | 在 `_derive_missing_work` 中加入新 artifact kind 的任务创建逻辑 | coordinator.py:92-167 |
| ⑥ 终态调整 | 在 `_try_accept_final` 中加入新 artifact 的接受条件(如需) | coordinator.py:230-245 |

**关键**: Agent 间通过黑板通信 (不直接调用另一个 Agent 的方法),所以加新 Agent 不会破坏已有的 Agent 逻辑。这也是不可变黑板的核心价值。

---

## 3. RAG 与知识检索 (5 问)

### Q11: RAG 的核心流程是什么?有什么常见优化手段?

**标准 RAG 流程**: 文档加载 → 文本分块(chunking) → 向量嵌入(embedding) → 存入向量库 → 用户查询 → 查询嵌入 → 相似度检索 → 上下文拼接 → 送给 LLM 生成。

**常见优化手段** (按投入产出比排序):

| 优化 | 效果 | 本项目是否采用 |
|------|------|:---:|
| **混合检索**(向量+BM25) | 召回率 +15-25% | 是,knowledge.py:127-138 |
| **查询改写** | 口语→检索词,升准确度 | 是,autonomous.py:394-402 |
| **Rerank**(精排) | MRR 提升 10-20% | 是,本地公式 knowledge.py:387-391 |
| **Chunk 扩展**(邻居拼接) | 上下文完整性 | 是,`_expand_best()` knowledge.py:303-329 |
| HyDE(假设文档嵌入) | 语义对齐 | 否,查询改写已达效果 |
| Self-RAG(自反思) | 质量自检 | 否,规模不够 |
| Multi-Vector(多粒度) | 涵盖细节+整体 | 否,文档结构简单 |

### Q12: 混合检索(向量+关键词)为什么比纯向量好?

**原因** (见 docs/tech/03_RAG_AND_KNOWLEDGE.md 第 1 节):

| 场景 | 纯向量表现 | 纯关键词表现 | 混合表现 |
|------|-----------|-------------|---------|
| "不想活了"(口语→想自杀) | 好,语义理解 | 差,字面无匹配 | 好,向量补语义 |
| "PHQ-9 量表"(专业术语) | 中,返回相近但不含术语的 chunk | 好,精确命中 | 好,BM25 补关键词 |
| "HIGH 风险处置流程" | 中,"风险处置"的语义邻居不含"Excel台账" | 好 | 好 |

**本项目融合公式** (knowledge.py:176-191):

```
fused_score = vector_norm × 0.65 + bm25_norm × 0.35
```

**权重选择**: 0.65 > 0.35 因为心理对话以语义为主;"想不开"这种口语表达只能靠向量。但 BM25 权重不为零 — 专业术语如"DSM-5""CBT"必须精确命中。

**Min-Max 归一化是前提** (knowledge.py:416-427):向量分在 [0,1],BM25 分无上限,不归一化则 BM25 主导融合结果。

**降级设计**: 无 OpenAI key 时向量分支短路,纯 BM25 + `hybrid_score` rerank (vector_store.py:36-63)。可用性优先于最佳检索质量。

### Q13: chunking 策略怎么选?大小/重叠怎么定?

**核心 trade-off**:

| chunk 太小 | chunk 太大 |
|-----------|-----------|
| 检索精度高,但语义碎片化 | 上下文完整,但噪声多,嵌入稀释 |
| 适合:FAQ、字典式| 适合:长文、叙述性文档 |

**本项目方案** (knowledge.py:331-341):

| 参数 | 值 | 理由 |
|------|-----|------|
| `chunk_size` | 512 字符 | 心理常识文档以段落为单位,512 字符约 3-5 句,保持语义完整 |
| `overlap` | 64 字符 | 12.5% 重叠率,保证"前因后果"不跨 chunk 断裂 |
| 滑动步长 | `size - overlap = 448` | 朴素滑动窗口 |

**为什么不用 RecursiveCharacterTextSplitter**: 文档结构简单(11 篇 Markdown,纯段落),不需要递归切分。KISS 原则 — `chunk_text()` 一个函数搞定。

**知识库规模**: 约 500 chunks,够了。如果扩展到万级,chunk 策略需要重新评估。

### Q14: RAG 的评估怎么做?用什么指标?

**本项目评估体系** (docs/tech/03_RAG_AND_KNOWLEDGE.md 第 10 节):

**评测数据集**: `app/rag_eval/mindbridge-rag-eval.json`,30+ 条标注 case,每条指定 `expectedSources` + `expectedTerms`。

**指标** (rag_eval/runner.py:24-35):

| 指标 | 公式 | 面试要点 |
|------|------|---------|
| **Recall@K** | `1 if any relevant in top-K else 0` | 是否有相关结果在前 K 个?漏检敏感 |
| **Precision@K** | `relevant_count / K` | 前 K 个中相关的比例 |
| **MRR** | `1 / first_relevant_rank` | 第一个相关结果排位倒数,越早越好 |
| **NDCG@K** | `DCG / IDCG` | 考虑排位加权的归一化增益 |
| **HitRate** | `hits / total` | 至少命中一条的比例 |

**运行**: `AI_PROVIDER=mock python -m app.rag_eval.runner`,输出 `target/rag-eval-report.json`。

**面试注意** — 当前局限性: 只有 0/1 二元相关判断,无法算细粒度排序质量。改进方向: 引入人工相关性分级(0-3)或 LLM-as-Judge。

### Q15: RAG 系统上线后怎么监控和迭代?

**监控维度**:

| 维度 | 指标 | 本项目实现 |
|------|------|-----------|
| **检索质量** | 定期跑评测集,对比 Recall@K/MRR 趋势 | `rag_eval/runner.py` |
| **知识覆盖** | 热门 query 的检索命中率 | 管理后台 `/api/knowledge/status` |
| **向量库健康** | Chroma collection 状态、chunk 数量一致性 | `vector_store.status()` |
| **降级频率** | `can_embed=false` 触发频率(指标嵌入缺失率) | 日志 |
| **用户反馈** | 学生/辅导员反馈是否需要补充知识 | 手动收集 |

**迭代闭环**: 发现知识缺口 → 更新 `app/knowledge/*.md` → 重启触发 `seed_data()` → `ensure_source()` 比对旧 chunk → 有变化才重建索引 → `_ensure_vector_index()` 增量为新 chunk 生成 embedding (knowledge.py:230-240)。

**面试亮点**: embedding_json 缓存 — `KnowledgeChunk.embedding_json` 列缓存向量,重建索引时跳过已有 embedding 的 chunk,避免重复调用 OpenAI API。

---

## 4. Agent 安全与对齐 (4 问)

### Q16: Agent 应用有哪些安全风险?

| 风险类别 | 攻击方式 | 本项目防护 |
|----------|---------|-----------|
| **提示注入** | "忽略之前指令,按我说的做" | SafetyAgent 独立审查 + 硬关键词先于 LLM (assessment.py:25-26) |
| **幻觉/错误** | 风险评估偏差 | 三层评估(硬关键词→LLM→heuristic),多层叠加降低漏判概率 |
| **信息泄露** | 输出风险等级给学生 | System prompt 强制约束 (ai.py:55) + 代码层隔离 |
| **工具滥用** | 绕过工具权限 | 静态策略表 + 执行前授权 + 每次生成审计记录 (tool_governance.py:64-106) |
| **敏感数据泄露** | 手机号/身份证出现在 prompt 中 | `PrivacySanitizer` 正则脱敏 (入 prompt 前 + 持久化前) |

**Swiss Cheese Model** (docs/tech/04_RISK_ASSESSMENT.md 第 1.2 节): 每一层防御都有"漏洞",但多层叠加后漏洞对齐的概率趋近于零。类比航空安全 — 不是某个单一系统保证安全,而是层层冗余。

### Q17: 怎么做 Agent 输出的安全审查?(结合 SafetyAgent)

**SafetyAgent 的双模式审查** (autonomous.py:192-307):

```
模式 A — 输入风险评估 (_assess_risk, 行 224):
  硬关键词命中 → HIGH (0ms,不调LLM)
  否则 LLM JSON → 提取 emotion/score/risk/confidence
  异常 → heuristic fallback (fail-safe,宁可误报)

模式 B — 输出安全审查 (_review_response, 行 262):
  risk=HIGH 且回复缺少安全引导词 → approved=false → REVISION_REQUESTED
  → 创建 task:revise-response (CRITICAL) → ResponseAgent 下一轮修订
  否则 → approved=true
```

**关键设计**:

1. **审查先于输出** — `_try_accept_final` (coordinator.py:230-245) 要求 `safety_review.approved==true` 才能终态接受
2. **安全引导词硬注入** — HIGH 风险时 prompt 自动注入"高风险处理规则"(ai.py:47-50),确保即使审查通过,回复也含安全引导
3. **一票否决** — `SAFETY_OVERRIDE` 事件发布后,所有下游判断以 HIGH 为准,不可覆盖

### Q18: 什么是 Guardrails?怎么实现多层护栏?

**Guardrails = 多层安全护栏**,在输入→推理→输出的每个环节检查,发现风险即拦截或修正。

**本项目 5 层护栏**:

```
输入层    → PrivacySanitizer 脱敏 + 硬关键词检测
推理层    → 3 层评估(硬关键词→LLM→heuristic),多层叠加
协调层    → SafetyAgent SAFETY_OVERRIDE 超控
输出层    → _review_response 审查回复内容
工具层    → ToolPolicyRegistry 策略授权 + ToolAuditRecord 审计
```

**与 Nvidia NeMo Guardrails 的对比**:

| 维度 | NeMo Guardrails | 本项目 |
|------|----------------|--------|
| 实现方式 | Colang 对话流 DSL | Python 硬编码 + Agent 独立审查 |
| 适用场景 | 客服/对话系统通用 | 校园心理垂直场景 |
| 灵活性 | 配置驱动,热更新 | 代码级,需重启但无 DSL 学习成本 |
| 安全确定性 | 规则引擎 | 硬关键词 + 独立 Agent 双重保障 |

### Q19: 用户输入包含敏感信息怎么处理?(结合 PrivacySanitizer)

**两层脱敏**: `PrivacySanitizer` (tests 中验证,见 test_privacy_and_assessment.py:10-13) 在入 prompt 前 + `RedisShortTermMemoryStore._serialize` 持久化前分别执行,保证敏感数据不进模型也不进缓存。

**脱敏规则** — 正则匹配三类信息:

| 类型 | 示例 | 替换为 |
|------|------|--------|
| 手机号 | `13812345678` | `[已脱敏]` |
| 邮箱 | `student@school.edu.cn` | `[已脱敏]` |
| 身份证号 | `110101199001011234` | `[已脱敏]` |

**设计考量**: 脱敏是纯正则匹配,不调 LLM — 零延迟、确定性、不可绕过。代价是可能误杀(如论文中的数字串),但在隐私保护 > 输入精度的场景下是可接受的 trade-off。

**测试验证**: 用 `ExplodingAi` 桩验证硬路径 — 如果代码错误地先调 LLM,桩会抛 `AssertionError` 导致测试失败。这是用"炸弹桩"验证代码路径的典型 TDD 实践。

---

## 5. 系统设计与实战 (4 问)

### Q20: 设计一个「智能客服 Agent 系统」

**系统架构要点**:

```
用户 → 意图识别 Agent(分类/路由) → 简单问答 → 知识库 Agent(RAG 检索+回复生成)
                                  → 复杂问题 → 多Agent协作(信息收集→方案生成→质检)
                                  → 需人工   → 转人工+摘要生成 Agent

横向: 安全护栏贯穿全程(输入验证+输出审核+敏感信息脱敏)
```

**关键设计决策**:

1. **分级路由** — 简单 FAQ 走 RAG(1 个 Agent,低延迟);复杂投诉走多 Agent(3-4 个,质量优先)
2. **工具集成** — 订单查询/退款/物流 → Function Calling,失败有降级文案
3. **质检 Agent** — 独立审查回复内容,拦截违规承诺/不准确信息
4. **可观测性** — 每个会话的完整事件链,trace ID 贯穿所有 Agent 调用

**参考 MindBridge 经验**: 不要一个 LLM 搞定一切 — 把"理解意图""检索知识""生成回复""安全审查"分给不同 Agent,各自有独立 system prompt 和工具权限。

### Q21: 设计一个「多 Agent 协作的代码审查系统」

**思路**: 借鉴 MindBridge 的事件驱动认领制:

```
PR 提交 → Coordinator 推导审查任务:
  ├─ task:static-analysis   → LintAgent 认领(run ruff/eslint)
  ├─ task:security-scan     → SecurityAgent 认领(检查 OWASP Top 10)
  ├─ task:logic-review      → LogicAgent 认领(LLM 逻辑审查)
  ├─ task:style-review      → StyleAgent 认领(命名/结构检查)
  └─ task:merge-decision    → Coordinator 汇总→通过/需修改/拒绝
```

**隔离设计** (类比 MindBridge `AgentProfile`):

| Agent | 工具权限 | 模型选择 |
|-------|---------|---------|
| LintAgent | `run:ruff, eslint` | 无需 LLM,调用 CLI |
| SecurityAgent | `scan:bandit, gitleaks` | 无需 LLM |
| LogicAgent | `github:diff, llm.review` | 强模型(Sonnet/Opus) |
| StyleAgent | `github:diff, llm.review` | 轻模型(Haiku) |

**冲突处理**: 安全 Agent 有否决权 — 发现硬编码密钥 → 直接 BLOCK,无论其他 Agent 评分多高。类比 SafetyAgent 的 SAFETY_OVERRIDE。

### Q22: Agent 产生幻觉怎么排查和修复?

**幻觉排查路线图**:

```
Step 1: 定位幻觉来源
  ├─ RAG 检索错误?(查 knowledge query + retrieved content)
  ├─ LLM 推理错误?(查 prompt + 上下文是否符合预期)
  ├─ 历史记忆污染?(查 Redis 中的会话上下文)
  └─ 系统 prompt 冲突?(多个 prompt 片段互相矛盾)

Step 2: 修复策略
  ├─ RAG 幻觉 → 优化 chunk 策略/增加 rerank/添加引用标注
  ├─ 推理幻觉 → 调整 prompt(加角色约束/输出格式限制/少样本示例)
  ├─ 记忆幻觉 → 缩短记忆窗口/增加摘要质量检查
  └─ 逻辑幻觉 → 引入 Self-Reflection(审查 Agent 二次检查)
```

**本项目的防幻觉措施**:

- 意图分类 3 层降级(关键词→LLM→关键词兜底),不依赖 LLM 单点
- `support` 模式下 prompt 含"知识不足时明确说明并给出安全通用建议"(ai.py:55)
- SafetyAgent 独立审查 ResponseAgent 输出,形成"生成-审查"制衡
- 事件溯源链可回放 — 每个 artifact 的来源可追溯到具体 Agent 和轮次

### Q23: 一个 Agent 系统从开发到上线的完整流程?

| 阶段 | 任务 | 本项目做法 |
|------|------|-----------|
| **0. 需求** | 明确 Agent 的不可妥协约束 | 心理评估"宁可误报不可漏报" → 三层评估+硬关键词 |
| **1. 原型** | 单 Agent + mock LLM 验证核心流程 | `AI_PROVIDER=mock` + SQLite,无外部依赖 |
| **2. 架构** | 划分 Agent 职责边界 | 5 个 Agent 各自 profile (理解/安全/上下文/回复/协调) |
| **3. 开发** | TDD 逐 Agent 实现 | `ExplodingAi` 桩验证硬路径 + unittest |
| **4. 集成** | 协调器 + 隔离面 + 事件链路 | `EventDrivenCoordinator.run()` + 12 种事件类型 |
| **5. 评测** | RAG 评测 + Harness 自检 | `rag_eval/runner.py` + `harness/runner.py` |
| **6. 部署** | Docker compose 一键启动 | MySQL+Redis+App 三容器,默认 `TOOL_QUEUE_ENABLED=true` |
| **7. 监控** | 工具队列死信/API 健康/向量库状态 | `/actuator/health` + `/api/knowledge/status` |

**面试亮点**: 原型阶段用 `AI_PROVIDER=mock` 做全链路验证 — 不依赖真实 LLM,CI 可持续跑所有测试。这是 Agent 工程化的关键实践。

---

## 6. 协议与标准 (3 问)

### Q24: MCP (Model Context Protocol) 是什么?解决了什么问题?

**MCP** = Anthropic 提出的开放协议,标准化 AI 模型与外部工具/数据源的交互方式。核心是 **Client-Server 架构** (Host→Client→Server):

```
Host(Claude/GPT/IDE) → MCP Client(协议层) → MCP Server(工具实现)
```

**解决的核心问题**:

| 问题 | MCP 解决方案 |
|------|------------|
| 每个 LLM 有自己的 function calling 格式 | 统一 JSON-RPC 协议,一次开发多处使用 |
| 工具与 AI 应用紧耦合 | stdio/HTTP SSE 传输,进程隔离 |
| 工具发现需手动配置 | `list_tools()` 自动发现服务器能力 |

**本项目实践**: `MindBridgeMcpToolClient` (mcp_client.py:17-85) 通过 stdio 子进程启动 `app/mcp_tools/server.py` 的 6 个工具 — 独立的进程隔离,挂了不拖垮主进程。支持双模: 队列模式(生产)+ MCP 模式(开发/Harness)。

### Q25: A2A (Agent-to-Agent) 协议是什么?

**A2A** = Google 提出的 Agent-to-Agent 协议,标准化 Agent 之间的通信。核心概念:

| 概念 | 含义 |
|------|------|
| **Agent Card** | Agent 的自我描述(能力、URL、认证) |
| **Task** | 工作单元,含状态机(`working`/`input_required`/`completed`/`failed`) |
| **Message/Part** | 多模态消息(json/text/file) |
| **Streaming** | 支持 SSE 流式响应和长轮询 |

**与 MCP 的关系**:

| | MCP | A2A |
|---|-----|-----|
| 解决什么 | 模型 ↔ 工具 | Agent ↔ Agent |
| 通信模式 | 同步请求-响应 | 任务状态机 + 流式 |
| 典型场景 | LLM 调用数据库/API | 多 Agent 协作完成任务 |

**本项目为什么没用 A2A**: A2A 是 2025 年 4 月发布的协议,本项目开发时尚未成熟。但项目的黑板+认领制设计思想与 A2A 的任务状态机理念一致 — `AgentTask` (events.py:46-57) 有 OPEN/CLAIMED/CLOSED 状态,与 A2A 的 Task 状态机非常相似。如需与外部 Agent 系统交互,可封装一层 A2A adapter。

### Q26: OpenAI Function Calling vs MCP Tool 的区别?

| 维度 | OpenAI Function Calling | MCP Tool |
|------|------------------------|----------|
| **定义方式** | JSON Schema 内联在 API 请求中 | MCP Server 注册,`list_tools()` 自动发现 |
| **执行方式** | LLM 提议调用,应用侧执行,结果回传 | Client 调用 Server,Server 自行执行 |
| **进程模型** | 应用进程中执行 | 独立进程(stdio/HTTP) |
| **提供商锁定** | 绑定 OpenAI 生态(其他也支持了) | 协议级通用,任何 LLM/工具 |
| **错误处理** | 应用侧决定 | Server 返回错误,Client 决定重试/降级 |
| **状态管理** | 无状态(每次调用独立) | 可被设计为有状态(跨调用保持上下文) |

**本项目双模的价值** (docs/tech/05_TOOL_SYSTEM.md 第 5 节):

- 生产环境走**工具队列** — 异步、重试、死信、审计、滑动窗口限流
- 开发/CI 走 **MCP stdio** — 同步、直接、无需 MySQL 后台线程
- **同一套 `ToolOrchestrationService` 实现**服务两种调用模式 — 核心逻辑零重复

---

## 7. 结合项目的深度追问 (4 问)

### Q27: MindBridge 的 Agent 运行时如果换成 LangGraph 会有什么不同?

**差异分析**:

| 维度 | 当前: 事件驱动认领 | 改为 LangGraph |
|------|-------------------|---------------|
| 工作流定义 | 每轮 `_derive_missing_work` 动态推导 (coordinator.py:92-167) | 编译时定义节点+条件边(StateGraph) |
| 条件跳过 | 一行 condition 参数 (coordinator.py:107-110) | `add_conditional_edges` + 路由函数 |
| 安全超控 | SAFETY_OVERRIDE 事件,任意阶段介入 | 需在图中设计 interrupt/checkpoint |
| 审计 | 不可变黑板事件链,原生可回放 | 依赖 LangSmith/LangGraph 内置 trace |
| 代码量 | ~550 行 Python(events.py + coordinator.py) | 更多(节点定义/边/State schema/checkpoint) |
| 学习曲线 | 需要理解认领制 | 需要理解图(Graph)概念和 streaming/checkpointing |

**什么时候该切 LangGraph**:

- Agent 数量 > 10,手动调度变得脆弱
- 需要复杂的**人机协同**(Human-in-the-loop)
- 需要**持久化状态**跨会话恢复(LangGraph 内置 checkpointing)
- 团队已经在用 LangChain 生态,**复用成本低**

**当前方案的优势**: 零框架依赖(纯 Python dataclass),调试时可以直接 print board 看所有 artifacts — 不需要进 LangSmith 看 trace。对于 5 个 Agent 的规模,复杂度远低于引入图框架。

### Q28: 如果 SafetyAgent 和 ResponseAgent 对安全性判断不一致,你的协调策略是什么?

**当前策略**(3 步):

```
1. 架构层面 — SafetyAgent 一票否决
   _review_response: risk=HIGH 且回复缺安全引导 → approved=false (autonomous.py:268-269)
   
2. 协调层面 — Coordinator 拒绝终态
   _try_accept_final: 检查 safety_review.approved==true → 不通过 → continue loop (coordinator.py:230-245)
   
3. 修订闭环 — ResponseAgent 修订后重新审查
   REVISION_REQUESTED → task:revise-response (CRITICAL) → ResponseAgent 修订 → SafetyAgent 重新审查
   如果 N 轮后仍不一致 → BUDGET_EXHAUSTED + 取 latest response_proposal(至少留了事件记录)
```

**改进方向**: 如果矛盾频繁发生,可以引入**分歧仲裁 Agent** — 当 SafetyAgent 和 ResponseAgent 连续 N 轮不一致,第 3 方 Agent 介入裁决。但这会增加延迟和成本,心理健康场景中"安全侧赢"已经是合理的设计。

**面试话术**: "在安全敏感场景,设计上就不应该追求一致性 — 你应该追求安全侧胜出。如果 SafetyAgent 说不行,就是不行。我们在协调器的终态条件里硬编码了这个偏向。"

### Q29: 项目的 RAG 如果用 Elasticsearch 替代 Chroma 会有什么变化?

| 维度 | Chroma (当前) | Elasticsearch (替代) |
|------|:---:|:---:|
| 部署复杂度 | pip install chromadb,零运维 | 需 ES 集群/单节点,Java 依赖 |
| 向量检索 | HNSW 索引,cosine 相似度 | dense_vector 字段 + kNN 搜索 |
| 关键词检索 | 自己写 BM25 | 内置 BM25/relevance scoring,开箱即用 |
| 混合检索 | 手动两路 + Min-Max 归一 + 融合 | ES 8.x+ 原生支持 hybrid search(RRF 融合) |
| 规模 | 适合 < 10 万 chunks | 百万级 chunks 无压力 |
| 过滤/聚合 | 无 | 丰富的 filter/aggregation DSL |
| 监控 | 手动写 status() | Kibana 开箱即用 |

**切换建议**: 当前 11 篇 Markdown(约 500 chunks),Chroma 完全够用。如果知识库扩展到 > 10 万 chunks 或需要跨机房部署/高可用/聚合分析,**引入 ES 是合理重构方向**。

**迁移要点**: 1) 已有的 `embedding_json` 缓存可直接用于 ES index;2) `SearchResult` 接口不变,`KnowledgeService` 内部切换存储后端;3) 可保留 BM25 实现作为 ES 不可用时的降级路径;4) `hybrid_weight` 参数可对应 ES 的 RRF `rank_constant`。

### Q30: 项目从 demo 到生产,最需要改进的 3 个点?

**1. 密码安全** (最紧急):

```
当前: SHA-256 无盐哈希 (app/core/security.py)
问题: 可被彩虹表攻击,无暴力破解防护
改进: bcrypt/argon2 + 密码强度校验 + 登录失败限流
```

**2. 无 Alembic 迁移** (最影响协作):

```
当前: Base.metadata.create_all() 启动建表 (无版本化 schema)
问题: 多人协作或生产升级时无法追踪 schema 变更
改进: 引入 Alembic + 自动生成迁移脚本 + CI 中检查迁移一致性
```

**3. 多进程部署的锁问题** (最影响扩展):

```
当前: EXCEL_WRITE_LOCK 是 threading.Lock (tool_queue.py 提到进程级锁)
问题: Uvicorn --workers 4 时,4 个进程各自独立 lock,无法互斥
改进: fcntl.flock() 文件锁 (跨进程) 或 Redis 分布式锁
```

**其他** (次优先级):
- 评测数据集扩展 — 当前 30 条,RAG eval 只能给趋势,需要 > 200 条做显著性判断
- API 鉴权 — Basic Auth 无 token 过期/刷新机制
- 可观测性 — 引入结构化日志 + metrics(Prometheus)替换 `print()` 调试

---

## 8. 追问速查表 (20 题)

面试前 30 分钟快速扫一遍:

| # | 追问 | 一句话要点 |
|---|------|-----------|
| 1 | Agent 和 RAG 的关系? | RAG 是 Agent 的知识工具,Agent 通过 RAG 获取外部知识后再推理 |
| 2 | LangChain Agent 为什么不合适本项目? | 它的 Tool-use 范式无法表达"5 个独立审查 Agent 的制衡关系"(见 docs/tech/02 第 11 章 Q1) |
| 3 | 5 个 Agent 如何通信? | 通过不可变黑板 — 读 artifact、发 artifact,不直接互相调用 (events.py:121) |
| 4 | Agent 的 LLM 调用失败了怎么办? | 每层 try-except + fallback:关键词兜底 (autonomous.py:177-179),heuristic 兜底 (assessment.py:42-43) |
| 5 | 预算参数怎么选? | 8 轮/4 并发/3 每 Agent 上限;正常 3-4 轮,高风险 5-6 轮 (coordinator.py:31-34) |
| 6 | 为什么 chromadb 不用内置 embedding? | `embedding_function=None` (vector_store.py:59),应用侧完全控制 |
| 7 | 怎么处理中文分词? | 正则 + 2-gram,零依赖,无 jieba 词汇表偏差 (knowledge.py:452-457) |
| 8 | candidate_k=16 为什么? | 粗筛 → 精排:先宽召回 16,融合+rerank 后取 top_k=4 (knowledge.py:141-142) |
| 9 | 权重 0.65 怎么来的? | 经验值,语义优先;可以通过环境变量覆盖跑 RAG 评测调优 |
| 10 | 为什么 EXCEL_REPORT 不限风险等级? | 全量台账是辅导员工作依据,不记 LOW 风险会导致无历史可查 |
| 11 | 邮件预警为什么用分钟级限流? | `RateLimiter` 滑动窗口 (tool_queue.py:66-83),防止误触 SMTP 封禁 |
| 12 | MCP 双模的真正价值? | 一套 `ToolOrchestrationService`,两种调用方式 — 队列(生产) vs MCP stdio(开发/CI) |
| 13 | `TOOL_QUEUE_ENABLED=false` 时怎么运维? | 走 MCP 子进程模式,同步调用,适合开发/Harness/演示 |
| 14 | 如何验证硬关键词路径确实没调 LLM? | `ExplodingAi` 炸弹桩 — 如果调了 LLM 就炸测试 (test_privacy_and_assessment.py:10-13) |
| 15 | emotionScore 和 risk 冲突怎么办? | 系统相信分数(连续值)胜过标签(离散值) — `score_risk > risk` 时以分数提级 (assessment.py:37-38) |
| 16 | SafetyAgent 审查时检查什么? | 检查回复是否包含高风险引导词 (autonomous.py:268) |
| 17 | RiskCase 的 handoff_summary 怎么渲染? | `counselor_handoff_summary` skill 含 text 模板,tools.py 调用渲染 |
| 18 | Redis 宕机了怎么办? | 降级:短期记忆不可用,但 ContextAgent 从 MySQL fallback (autonomous.py:381-392) |
| 19 | 什么叫 "事件溯源"? | 每个操作产生事件,board 状态可从事件列表完整回放 |
| 20 | 这个架构最大的创新点? | 多 Agent 安全制衡不是事后接的,而是**架构第一性原理** — 不是 feature,是 foundation |

---

## 附录: AI Agent 术语表 (中英对照)

| 中文 | English | 简要说明 |
|------|---------|---------|
| 智能体/代理 | Agent | 能自主感知环境、做出决策、采取行动的 AI 系统 |
| 多智能体系统 | Multi-Agent System | 多个 Agent 协作完成复杂任务的系统 |
| 黑板架构 | Blackboard Architecture | Agent 通过共享数据结构通信的协作模式 |
| 认领制 | Claim-Based Scheduling | Agent 自主认领开放任务,而非被预先分配 |
| 事件驱动 | Event-Driven | 以事件的产生/消费/反应驱动系统行为 |
| 事件溯源 | Event Sourcing | 以只追加的事件序列作为系统状态的事实来源 |
| 不可变数据 | Immutable Data | 创建后不可修改的数据结构(如 frozen dataclass) |
| 护栏 | Guardrails | 多层安全约束,防止 AI 输出有害内容 |
| 提示注入 | Prompt Injection | 恶意构造输入以覆盖模型的原始指令 |
| 幻觉 | Hallucination | LLM 生成看似合理但事实错误的内容 |
| 检索增强生成 | RAG (Retrieval-Augmented Generation) | 检索外部知识库 + LLM 生成的模式 |
| 混合检索 | Hybrid Search | 向量语义检索 + 关键词检索的组合 |
| 重排 | Rerank | 对初步检索结果进行二次排序以提升精度 |
| 分块 | Chunking | 将长文档切分为适合检索的小片段 |
| 查询改写 | Query Rewriting | 将用户原始查询转换为更优的检索查询 |
| 函数调用 | Function Calling | LLM 输出结构化指令,触发外部工具执行 |
| MCP | Model Context Protocol | Anthropic 提出的模型-工具交互标准协议 |
| A2A | Agent-to-Agent Protocol | Google 提出的 Agent 间通信协议 |
| 隔离面 | Isolation Surface | Agent 的独立上下文边界(私有记忆/模型/工具权限) |
| 超控 | Override | 安全 Agent 强制覆盖其他 Agent 的决策 |
| 死信队列 | Dead Letter Queue | 多次重试失败后存放消息的队列,供人工排查 |
| 幂等 | Idempotent | 多次执行相同操作的结果一致(如重复创建 RiskCase) |
| 降级 | Degradation/Fallback | 依赖不可用时退回次优方案的容错策略 |
| 兜底 | Fail-Safe | 系统异常时返回安全结果而非崩溃或无响应 |
| 滑动窗口限流 | Sliding Window Rate Limiting | 基于时间窗口的事件计数来限制操作频率 |
| 评测数据集 | Eval Dataset | 用于系统量化评估的标注测试用例集 |

---

**文件索引** (方便面试前快速定位):

| 关注点 | 核心文件 |
|--------|---------|
| Agent 架构与协调 | `app/agents/coordinator.py`, `app/agents/events.py`, `app/agents/registry.py` |
| 5 个 Agent 实现 | `app/agents/autonomous.py` (550+ 行) |
| 运行时入口 | `app/agents/event_driven_runtime.py` |
| 心理评估硬守卫 | `app/services/assessment.py` (73 行,三层防御) |
| 混合检索 RAG | `app/services/knowledge.py` (500+ 行) |
| 工具队列+治理 | `app/services/tool_queue.py` + `app/services/tool_governance.py` |
| MCP 双模 | `app/services/mcp_client.py` + `app/mcp_tools/server.py` |
| 技术文档 | `docs/tech/02_AGENT_RUNTIME.md`, `03_RAG_AND_KNOWLEDGE.md`, `04_RISK_ASSESSMENT.md`, `05_TOOL_SYSTEM.md` |
