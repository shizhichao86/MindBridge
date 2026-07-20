# 面试问答集（30 问）

> 文档定位：面试高频问题的逐题深度解析,每问 100-200 字,引用实际代码位置和设计决策。
> 面试权重：本文档在面试准备场景中权重 **高**（60%），建议逐题口述练习。

---

## 一、架构与设计决策（8 问）

### Q1：为什么选事件驱动多智能体而非 LangGraph/LangChain？

**核心原因：安全不容协商。** LangChain/LangGraph 的链式/图式编排本质是预设路径 —— "先 A 后 B 再 C"。心理健康场景中,高风险消息不能等"走到安全检查那一步"才拦截。

我们选择 **认领式 (claim-based) 协调** (`app/agents/coordinator.py:36`)：Coordinator 维护任务板,五个 Agent 按 `(priority, confidence, name)` 排序自主认领开放任务,而非预设固定顺序。SafetyAgent 可在任意时刻发布 `SAFETY_OVERRIDE` 事件 (`app/agents/events.py:18`),强制风险等级为 HIGH。

**Trade-off**：确定性低于固定 DAG,调试难度更高。但心理健康场景安全性优先于可预测性,这个取舍是刻意的。

代码佐证：`app/agents/factory.py:19-28` 中 `agent_framework_status()` 返回 `available: ["event_driven_multi_agent"]`,只有一个框架,不是别名适配,是唯一实现。

---

### Q2：为什么 CollaborationBlackboard 不可变 frozen dataclass？

**核心原因：事件溯源 (Event Sourcing) 需要只追加日志。** 黑板是不可变数据类 (`app/agents/events.py:121`, `@dataclass(frozen=True)`),每次变更通过 `replace(...)` 返回新副本 (如 `add_task` 用 `replace(self, tasks=tasks)`,见 `events.py:136`)。

好处：
- **可审计** —— 每轮状态都是快照,`AgentRunTrace` 可直接序列化回放 (`events.py:130` 的 `events` 是 `tuple`,只追加)。
- **无副作用** —— 并发场景下 Agent 的 `decide()` 读的是同一快照,不会互相污染。
- **调试友好** —— 任何时刻的黑板状态都是确定的,递归追溯时不需要"反向撤销"。

**Trade-off**：每次变更创建新对象有内存开销,但 Agent 协作轮次上限 8 轮,每轮 artifact 数量可控,可接受。

---

### Q3：五个 Agent 如何通信？为什么黑板而非直接消息？

**Agent 通过共享黑板通信,不直接点对点发消息。** 机制分两层：

1. **数据层**：所有 Agent 向黑板发布 `AgentArtifact`（不可变,`events.py:91`）,其他 Agent 通过 `board.latest_artifact(kind)` 读取。
2. **协调层**：Coordinator 在每轮 `_derive_missing_work()` 检查 artifact 就绪状态,动态创建后续任务 (`coordinator.py:92-167`)。

**为什么不是直接消息**：
- 解耦生产者和消费者 —— ResponseAgent 不知道 SafetyAgent 何时审查,只发布 proposal 后等 Coordinator 创建 review 任务。
- 可扩展 —— 加新 Agent（如未来加一个 EmpathyAgent 检查共情质量）只需注册新 `AgentCapability` 和对应 `decide/act`,不改变现有 Agent。

`AgentMessage` (`events.py:80`) 仍存在,但仅用于协议语义（`kind=REVIEW_REQUEST`/`CONTEXT_READY`）,不承载业务决策载荷。

---

### Q4：为什么 Agent Runtime 的 run() 同步阻塞？

**这是 README 明确记载的有意设计。** `EventDrivenCoordinator.run()` (`coordinator.py:36`) 是同步方法 —— 不是技术约束,是架构选择。

原因：
1. **Agent 间有强数据依赖** —— intent 未发布前不能做 response,顺序执行更简单可靠。
2. **LLM 调用是瓶颈** —— 即使并行,真正的时延在模型推理,Python 的 asyncio 在此无显著收益。
3. **各 Agent 有独立 Redis 私有记忆** —— 并行写入同一 session 的不同 Redis key 可能引发竞态,同步执行消除此风险。

**Trade-off**：单次对话延迟无法通过 Agent 并行降低。但 `harness.run()` 在流式开始前完成,学生感知的延迟只有 LLM 推理时间,影响可控。

---

### Q5：为什么不用 Celery/RabbitMQ？

**问题规模不需要。** 我们只有三类后处理任务（EXCEL_REPORT / CASE_CREATE / ALERT_SEND）,写在 MySQL `tool_jobs` 表中,后台守护线程轮询 (`app/services/tool_queue.py`)。

理由：
1. **部署复杂度** —— Celery 需要 broker(Redis/RabbitMQ) + worker 进程,凭空增加运维负担。Docker Compose 一键启动即用。
2. **依赖链简单** —— ALERT_SEND 依赖 CASE_CREATE SUCCESS,用数据库状态机 (`PENDING→RUNNING→SUCCESS→触发下游`) 比消息队列的 ack 机制更直观。
3. **审计天然** —— 所有工具执行记录在 `tool_jobs` + `tool_audit_records` 两张表中,不额外接日志系统。

**Trade-off**：高并发场景下数据库轮询不如消息队列高效,但校园心理场景 QPS 极低,够用。

---

### Q6：为什么 MySQL 全量 + Redis 24h？

**MySQL 是数据闭环,Redis 是性能优化层。** (`README.md:125-123`)

- **MySQL 存全量**：`chat_sessions`、`chat_messages`、`reports`、`risk_cases`、`alert_records` 等 12 张表 —— 所有业务数据持久化。
- **Redis 存短期上下文**：每个 session 最近 40 条消息,TTL 24h (`RedisShortTermMemoryStore`),key 为 `mindbridge:short-term-memory:{session_public_id}`。

**为什么不全放 Redis**：
1. Redis 宕机不阻断聊天 —— 历史消息落 MySQL,Redis 仅加速上下文加载。
2. Redis 数据可丢 —— 24h TTL 意味着重启后从 MySQL 回填 (`ContextAgent._load_history()`, `autonomous.py:376-392`)。
3. 管理员报告依赖 MySQL 的全量历史,不能依赖易失存储。

---

### Q7：代码分层？Harness 层为什么存在？

分层结构（自顶向下）：

```
HTTP 层 (routes.py)             → 认证 + 委派,薄层
Harness 层 (harness.py)          → 编排:脱敏→Runtime→报告→工具计划→trace
Runtime 层 (event_driven_runtime.py) → 多 Agent 协作循环
Service 层 (services/*.py)       → AI/知识/评估/工具/记忆/技能
Data 层 (models/ + schemas/)     → ORM 实体 + Pydantic DTO
```

**Harness 为什么存在**：`MindBridgeAgentHarness` (`app/agents/harness.py`) 是 HTTP 层和 Runtime 的胶水。它做的事都不是 HTTP 层的职责,也不是 Runtime 的职责 —— 输入脱敏、session 解析、报告落库、工具计划生成、trace 组装。把这些放在 HTTP 层会污染路由,放在 Runtime 里会破坏 Agent 协作的纯粹性。

**Trade-off**：多了一层抽象,理解链路需要多跳一次。但换来的是每个模块职责清晰、可独立测试。

---

### Q8：可扩展性？加新 Agent 怎么办？

**三步即可** (`app/agents/registry.py` + `app/agents/autonomous.py`)：

1. 在 `AgentCapability` 枚举 (`registry.py:10-15`) 中加新值,如 `EMPATHY = "EMPATHY"`。
2. 新建类继承 `BaseAutonomousAgent`,定义 `profile` + `decide()/act()` (`autonomous.py:71-106`)。
3. 在 `EventDrivenAgentRuntimeService` 的 Agent 列表中注册新实例。

**不需要改**：Coordinator、Blackboard 数据结构、其他 Agent 的 `decide()`。

这就是 Protocol (`registry.py:35`) 的价值 —— Coordinator 只依赖 `AutonomousAgent` Protocol,不依赖具体类。`_derive_missing_work()` 中按需为新 capability 创建任务 (`coordinator.py:93-101`),即可参与认领。

---

## 二、多智能体协作（6 问）

### Q9：decide/act 设计怎么工作？

**decide() 表态,act() 办事。** (`registry.py:35-42`)

- **decide(task, board) → AgentDecision**：Agent 读黑板"我能/该不该做这个任务"。返回 `(claim: bool, confidence: float, reason: str)`。只读不写,纯函数。
- **act(task, board) → AgentTurnResult**：执行任务,产出 `artifacts/messages/events/tasks`。

Coordinator 在 `_claim_candidates()` (`coordinator.py:201-228`) 中遍历所有 OPEN task,调每个 Agent 的 `decide()`,按 `(优先级, 置信度, 名称)` 排序,每轮最多选 4 个、每 Agent 最多认领 3 次、每 (task, agent) 组合只执行一次。

**为什么分离**：`decide()` 是轻量判断（通常只查黑板 artifact 存在性）,`act()` 是重量操作（调 LLM/查 DB/做 RAG）。分离后 Coordinator 可以先全局规划再执行,避免"边想边做"的混乱。

---

### Q10：如何处理 Agent 决策冲突？

**设计就是避免冲突而非仲裁冲突。** 架构上做了三层约束：

1. **能力隔离**：每个任务标注 `required_capabilities` (`events.py:53`),只有具备该能力的 Agent 才能认领。UnderstandingAgent 不会去抢 SafetyAgent 的 risk 评估。
2. **artifact 互斥**：`decide()` 中检查 artifact 是否已存在 —— "intent artifact already exists" → `claim=False` (`autonomous.py:124`)。同一类 artifact 只有一个生产者。
3. **唯一认领**：`_claim_candidates()` 中 `seen` 集合保证同一个 (task, agent) 组合只执行一次 (`coordinator.py:220-221`)。

**唯一真正冲突场景**：`risk` artifact 可能由独立评估和硬关键词同时触发。解决方式：`_risk_value()` (`autonomous.py:570-582`) 取所有 risk artifact 的最高等级 + `SAFETY_OVERRIDE` 事件强制 HIGH —— 最坏的胜出,保守策略。

---

### Q11：SAFETY_OVERRIDE 机制是什么？

**SAFETY_OVERRIDE 是安全 Agent 的"否决权"** (`events.py:18`, `autonomous.py:236-245`)。

当 `PsychologicalAssessmentService.assess()` 返回 `risk=HIGH` 时,SafetyAgent 在 `AgentTurnResult` 中附加 `SAFETY_OVERRIDE` 事件。

效力：
- `_risk_value()` (`coordinator.py:273`): 一旦黑板中出现任何 `SAFETY_OVERRIDE` 事件,返回 `RiskLevel.HIGH`,**无视其他 artifact 的 risk 值**。
- `_derive_missing_work()` 中,task:assess-safety 优先级从 HIGH 升为 CRITICAL (`coordinator.py:108`)。
- ResponseAgent 的 `system_prompt` 中注入高风险处理规则,普通回复模板不可用。

**设计哲学**：安全决策不容协商。这是整个系统最不可退让的约束 —— 宁可误报不可漏报。

---

### Q12：预算管理（8 轮 / 4 认领 / 3 次）？

三个可配置参数共同限流 (`coordinator.py:31-34`)：

| 参数 | 默认值 | 含义 | 配置 key |
|------|--------|------|---------|
| `max_rounds` | 8 | 最多循环轮次 | `agent_max_rounds` |
| `max_claims_per_round` | 4 | 每轮最多认领数 | `agent_max_claims_per_round` |
| `max_claims_per_agent` | 3 | 每 Agent 最多认领次数 | `agent_max_claims_per_agent` |
| `final_min_confidence` | 0.6 | 终态接受最低置信度 | `agent_final_acceptance_min_confidence` |

**为什么需要预算**：
1. **防止无限循环** —— 如果 SafetyAgent 不断 critique、ResponseAgent 不断 revision,没有预算就会死循环。
2. **防止 Agent 垄断** —— `max_claims_per_agent=3` 防止单一 Agent 反复认领挤出其他 Agent。
3. **质量门槛** —— `final_min_confidence=0.6` 过滤低质量提案,不满足则继续循环或预算耗尽。

预算耗尽后的行为：`BUDGET_EXHAUSTED` 事件写入黑板,无 final_artifact_id,上层 Harness 做降级处理。

---

### Q13：每个 Agent 独立隔离面为什么？

**隐私隔离 + 各司其职。** 每个 Agent 有四个维度的独立隔离 (`autonomous.py:71-106`)：

1. **独立 Redis key**：`agent:{name}:{session_id}`,同一 session 下五个 Agent 的记忆互不可见 (`autonomous.py:67-68`)。
2. **独立模型 profile**：`AgentModelRegistry.client_for(name)`,可用不同模型（如 Safety 用更大模型提高评估准确度）。
3. **独立 system prompt**：每个 Agent 的 `profile.system_prompt` 限定角色边界 —— "你不生成最终回复""你只负责理解",防止越界。
4. **独立工具权限 frozenset**：`tool_permissions` 白名单 —— ContextAgent 能调 RAG,但不能发布 safety_review。

**为什么**：架构保证单一职责。ResponseAgent 看不到 SafetyAgent 的评估细节,避免"为了通过审查而修饰回复"的投机行为。

---

### Q14：SafetyAgent 和 ResponseAgent 安全意见不一致怎么办？

**SAFETY_OVERRIDE 始终赢。** 具体流程 (`autonomous.py:262-307`)：

1. ResponseAgent 发布 `response_proposal` artifact。
2. SafetyAgent 的 `decide()` 检测到"有未审查的 response" → `claim=True`。
3. SafetyAgent 的 `_review_response()` 检查 response 内容,对 HIGH 风险场景做关键词验证。
4. 若 `approved=False`,发布 `critique` artifact + `REVISION_REQUESTED` 事件 + 创建 `task:revise-response` 跟进任务。
5. Coordinator 的 `_derive_missing_work()` 检测到 critique 且 `payload.approved==False`,创建 revision 任务 (`coordinator.py:154-166`)。
6. ResponseAgent 的 `decide()` 检测到 `revisionOf` 标记 → `claim=True` → 重新生成 response。

这个 revision 循环受预算约束（最多 8 轮）,不会无限迭代。

---

## 三、RAG 与知识库（5 问）

### Q15：为什么 Chroma + BM25？

**互补召回策略。** (`app/services/knowledge.py:127-138`)

| 维度 | Chroma 向量 | BM25 关键词 |
|------|------------|------------|
| 擅长 | 语义相近（"睡不着"匹配"失眠"） | 精确词匹配（"焦虑"命中含"焦虑"的文档） |
| 弱点 | 对专有名词/数字不敏感 | 无法理解同义词替换 |
| 实现 | OpenAI `text-embedding-3-small` | 纯 Python,无外部依赖 (`knowledge.py:348-384`) |

**为什么不是只用向量**：校园心理知识库有结构化术语（"考试焦虑""新生适应""睡眠节律紊乱"）,BM25 对此类精确词匹配更优。实际测试中,单独用向量或 BM25 的 HitRate 都不如融合。

**为什么 Chroma 而不是 Milvus/Pinecone**：Chroma 本地持久化、零运维、pip install 即用,Docker Compose 一键启动。校园场景数据量不大（11 篇 MD 文档）,不需要分布式向量库。

---

### Q16：融合公式？权重如何确定？

**两层融合** (`knowledge.py:151-192`)：

**第一层：双路融合**
```
candidates = Chroma(top_k=16, k=16) + BM25(k=16, k=16)
→ 各自 min-max 归一化: norm(x) = (x-min)/(max-min)
→ fused = (vec_norm * 0.65 + bm25_norm * 0.35) / 1.0
```

**第二层：本地重排**
```
final = base*0.55 + hybrid_cosine*0.25 + query_coverage*0.15 + phrase*0.05
```

**权重来源**：启发式经验值,通过 RAG 评测脚本 (`app/rag_eval/runner.py`) 在 4 份评测集上回测,选择 Recall@K 和 MRR 最优组合。不是机器学习导出的精确权重,但评测验证了有效性。

**可调性**：`KNOWLEDGE_HYBRID_VECTOR_WEIGHT=0.65`、`KNOWLEDGE_HYBRID_BM25_WEIGHT=0.35`、`KNOWLEDGE_RERANK_ENABLED=true` 都是 `.env` 可配,无需改代码。

---

### Q17：降级策略？触发条件？

**三层降级** (`knowledge.py:207-216` 及相关逻辑)：

| 条件 | 行为 |
|------|------|
| `can_embed=False`（无 OPENAI_API_KEY / chromadb 未装 / 向量禁用） | 纯 BM25 + `hybrid_score` 重排 |
| 向量检索异常（API 错误/超时） + `VECTOR_REQUIRED=false` | 纯 BM25 兜底,记 warning 日志 |
| 向量检索异常 + `VECTOR_REQUIRED=true` | 直接抛异常,阻断请求 |

**设计哲学**：演示/教学环境不应因缺 API key 而无法运行,但生产验收可以要求向量服务必须可用。`KNOWLEDGE_VECTOR_REQUIRED` 这个开关区分了两种场景。

---

### Q18：中文分词挑战和方案？

**无 jieba,纯规则分词** (`knowledge.py:452-457`)：

```python
def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_]+|[一-鿿]", text.lower())
    grams = words[:]
    compact = "".join(ch for ch in text.lower() if "一" <= ch <= "鿿")
    grams.extend(compact[i:i + 2] for i in range(max(0, len(compact) - 1)))
    return [item for item in grams if item.strip()]
```

**策略**：
1. 提取英文/数字 token + 单个中文字符（连续汉字会拆成字粒度的匹配单元）。
2. 补充 2-gram（如"焦虑" → "焦" + "虑" + "焦虑" bigram）,覆盖常见心理学术语。
3. 纯 Python 无外部依赖,适合 Docker 镜像最小化。

**Trade-off**：不如 jieba 精确,但避免了额外依赖和分词词典维护。校园心理领域的核心关键词（焦虑/抑郁/失眠/自杀）都是 2-3 字,2-gram 足够覆盖。

---

### Q19：如何评估 RAG 质量？

**标准信息检索指标** (`app/rag_eval/runner.py`,输出到 `target/rag-eval-report.json`)：

| 指标 | 含义 | 计算方式 |
|------|------|---------|
| Recall@K | K 个结果中命中了多少相关文档 | 命中数 / 总相关数 |
| Precision@K | K 个结果中有多少是相关的 | 命中数 / K |
| MRR | 第一个相关结果的排名的倒数均值 | mean(1/rank_of_first_hit) |
| NDCG@K | 归一化折损累积增益,考虑排序位置 | DCG / IDCG |
| HitRate | 至少命中一条相关文档的查询比例 | 命中查询数 / 总查询数 |

评测集位于 `app/rag_eval/` 下,含 query + 预期相关文档标注。评测时使用 `AI_PROVIDER=mock` 以不依赖外部模型。

---

## 四、安全与风险（4 问）

### Q20：三层评估架构？为什么这样设计？

**硬关键词 → LLM JSON 评估 → 启发式回退** (`app/services/assessment.py:24-44`)

```
assess(text)
  ├─ Layer 1: has_high_risk_signal(text) → 立即返回 HIGH, 不调 LLM
  │    关键词: "自杀""自残""不想活""结束生命""伤害自己""轻生"
  │    (app/services/ai.py HIGH_RISK_WORDS)
  ├─ Layer 2: LLM JSON 评估 (emotion/score/risk/confidence/summary)
  │    若 emotionScore 阈值反超 risk 则提升等级
  └─ Layer 3: heuristic() 回退 (LLM 异常时的关键词兜底)
```

**为什么三层**：
1. **Layer 1 是硬守卫** —— 高风险关键词零延迟、零误判空间,不依赖模型可用性。
2. **Layer 2 是精细评估** —— LLM 理解"我最近压力很大"和"我想死"的区别。
3. **Layer 3 是最后兜底** —— 即使 LLM 完全不可用,仍有基本安全判断。

测试验证见 `tests/test_privacy_and_assessment.py`,注入会抛错误的桩来验证硬路径不被跳过。

---

### Q21：如何保证评估结果不暴露给学生？

**强约束,跨层不跨边界。** 具体措施：

1. **Skill 约束**：`supportive_response_baseline` skill 的 system prompt 中明确"不输出风险等级、评分和诊断"。
2. **ResponseAgent prompt 模板**：`PromptTemplates.answer_system_prompt()` 中风险信息以"hidden metadata"形式注入 system prompt,不在 user-visible 内容中 (`autonomous.py:466-497`)。
3. **Harness 层物理隔离**：`MindBridgeAgentHarness.run()` 中,评估结果只写进 `tool_plan` 和后台报告,绝不进入 SSE 流的 student-visible payload。
4. **测试验证**：Harness 的 Risk Safety 套件验证"后台元数据不外显" (`README.md:370`)。

**设计哲学**："后台评估结果(风险等级/评分/诊断)绝不展示给学生"是架构级强约束,不是代码建议。

---

### Q22：工具治理策略表设计考量？

**静态策略表** (`app/services/tool_governance.py`)：

| 工具 | 触发条件 | 理由 |
|------|---------|------|
| `EXCEL_REPORT` | 任意风险 | 台账记录是基础能力,所有对话都应留痕 |
| `CASE_CREATE` | MEDIUM+ | 避免噪音案例,但中风险就该关注 |
| `ALERT_SEND` | 仅 HIGH | 邮件是侵入性通知,仅紧迫场景触发 |

**设计考量**：
- **EXCEL 无门槛** —— 台账是"记录",不是"通知",无伤害性。
- **CASE_CREATE 有门槛** —— 每个 LOW 风险都建案例会淹没真正需关注的个案。
- **ALERT_SEND 最高门槛** —— 辅导员收到邮件预警后应立即行动,不可滥用。
- **每次执行写 `ToolAuditRecord`** —— 谁触发、何时触发、结果如何,全链路可审计。

---

### Q23：如果 LLM 产生有害回复怎么办？

**四层防线**：

1. **SafetyAgent 的 response review** (`autonomous.py:262-307`)：检查高风险场景下 response 是否包含安全引导关键词（如"紧急""可信任的人""高风险处理规则"）。缺失则 `approved=False`,触发 revision。
2. **REVISION_REQUESTED → task:revise-response**：Coordinator 在检测到 critique 后创建 revision 任务,ResponseAgent 重新生成。
3. **Coordinator 的终态接受门槛**：`final_min_confidence=0.6`,低置信度提案不通过。Safety review 的 `approved=False` 会让 `_try_accept_final()` 直接返回 (`coordinator.py:239`)。
4. **预算耗尽降级**：8 轮内无法产生安全回复 → `BUDGET_EXHAUSTED`,上层 Harness 返回预定义的 fallback 安全消息,绝不输出未审查的回复。

**未覆盖的边界**：如果 LLM 在语义上做了精巧的有害回复,绕过了关键词检查,当前纯规则 review 可能无法拦截。这就是生产环境需要人工审核队列的原因 —— 见 Q29。

---

## 五、工程实践（4 问）

### Q24：SSE 流式与工具队列解耦？

**设计** (`README.md:60` 尾注)：

```
1. harness.run() [同步阻塞] → 完成 Agent 协作 → 生成 tool_plan
2. SSE 流式返回 [async generator] → 学生看到 token 分片
3. dispatch_tools(tool_plan) [异步,流式结束后] → 不阻塞学生
```

**为什么解耦**：
- 学生最关心回复速度,不应该等 Excel 写入和邮件发送。
- 工具执行（尤其是 SMTP 发邮件）可能数秒,等完才回复体验极差。
- 工具失败不影响学生 —— "失败了记日志,不让学生知道" (`README.md:60`)。

**Trade-off**：学生看到"回复已发出"但后台工具可能失败。对于非关键工具（台账写入失败不影响安全）,这个 trade-off 可接受。

---

### Q25：死信队列和重试策略？

**实现** (`app/services/tool_queue.py`)：

```
tool_jobs 表
  status: PENDING → RUNNING → SUCCESS / FAILED
  ├─ FAILED → 递增 attempts → 等待 retry_delay → 重回 PENDING
  └─ attempts >= TOOL_QUEUE_MAX_ATTEMPTS → 写入 dead_letter_records
```

**重试策略**：指数退避 (`retry_delay = base_delay * 2^(attempts-1)`),避免在瞬时故障（如 SMTP 暂时不可用）时立即放弃。

**死信队列**：超最大重试次数的任务进入 `dead_letter_records` 表,保留完整 payload + 失败原因。管理员可通过后台面板查看和处理。

**限流**：Email 线程池限制 `TOOL_QUEUE_EMAIL_WORKERS=2`,每分钟最多 `ALERT_EMAIL_RATE_LIMIT_PER_MINUTE=30` 封,防止 SMTP 服务器拒绝连接。

---

### Q26：PrivacySanitizer 脱敏？

**正则脱敏,入 prompt 前 + 持久化前各执行一次** (README 注)。

脱敏规则（`app/services/ai.py` 中 `PrivacySanitizer`）：
- 手机号：`1[3-9]\d{9}` → `[已脱敏]`
- 邮箱：`\S+@\S+\.\S+` → `[已脱敏]`
- 身份证：`\d{17}[\dXx]` → `[已脱敏]`

**执行时机**：
1. **入 prompt 前** —— 学生原始输入中的 PII 不进入 LLM 上下文。
2. **持久化前** —— `RedisShortTermMemoryStore._serialize()` 先脱敏再存,MySQL `chat_messages` 同理。

**Trade-off**：纯正则无法识别上下文中的隐含身份信息（如"我是 3 班班长张三"中的姓名）。这是当前方案的限制,生产需考虑 NER。

---

### Q27：MCP 双模应用场景？

**双模 = 异步工具队列 + MCP client stdio** (README 工具队列章节)。

```
默认: ToolOrchestrationService → ToolQueueService → 守护线程池执行
降级: TOOL_QUEUE_ENABLED=false
      → MindBridgeMcpToolClient → 子进程 stdio 启动 app/mcp_tools/server.py
```

**两种模式复用同一套 `ToolOrchestrationService` 实现**,只是执行路径不同：

| 维度 | 工具队列模式 | MCP 子进程模式 |
|------|------------|-------------|
| 执行方式 | 异步,守护线程轮询 DB | 同步,RPC 调用子进程 |
| 适用场景 | 生产,需要重试+死信+审计 | 开发/演示,简化部署 |
| 可靠性 | 高 (持久化队列) | 中 (进程存活期内) |

**为什么两种**：工具队列需要 MySQL + 守护线程,对演示环境太重。MCP stdio 子进程模式零依赖额外基础设施。

---

## 六、项目反思（3 问）

### Q28：最大不足是什么？

**安全审查的 review 逻辑太简单。** `SafetyAgent._review_response()` (`autonomous.py:262-307`) 目前只用关键词匹配判断 response 是否安全：

```python
if risk == RiskLevel.HIGH and not any(
    word in combined for word in ["高风险处理规则", "当前安全", "可信任的人", "紧急"]
):
    approved = False
```

这无法拦截精巧的有害回复 —— 比如 LLM 用温柔语气给出危险建议。理想方案是再加一个 LLM-as-judge 的安全评估调用,但成本翻倍、时延翻倍。

**为什么没做**：当前是 demo/教学级项目,关键词匹配在 11 篇内置知识的约束下误判率较低。见 Q29 的生产改进方案。

---

### Q29：上生产需改进哪些？

**6 项必须改进**：

1. **密码**：SHA-256 无盐 → bcrypt/argon2 (`app/core/security.py`)。
2. **安全审查**：关键词匹配 → LLM-as-judge safety evaluation（独立模型,独立 prompt）。
3. **人工审核队列**：高风险对话不直接返回,先进入管理员审核面板,审核通过后才对学生可见（当前是实时返回后补审核）。
4. **API 限流**：当前无限流,需按 user/ip 做 token bucket。
5. **日志脱敏**：当前 `PrivacySanitizer` 覆盖文本,但日志中可能含 IP/User-Agent 等元信息,需在 FastAPI 中间件层统一处理。
6. **Alembic 迁移**：当前 `Base.metadata.create_all()` 启建表,生产需要版本化的数据库迁移。

---

### Q30：重新设计会改变什么？

**三个会改变的决定**：

1. **decide() 的决策逻辑目前基本是规则驱动（查 artifact 是否存在）**—— 会考虑让 decide() 也能调 LLM 做更智能的任务优先级排序,但这会增加延迟和成本。
2. **Agent 间通信目前全经黑板**—— 会考虑引入 direct-message 通道处理紧急信号（如 SafetyAgent 发现高危时直接告警 Coordinator,不等下一轮）。
3. **评估服务与 SafetyAgent 耦合**—— `PsychologicalAssessmentService` 是独立服务,但被 SafetyAgent 独占调用 (`autonomous.py:225`)。会考虑让 Coordinator 也能调评估,做双路交叉验证,但当前单路已经够用。

**不会改变的**：不可变黑板、认领式协调、SAFETY_OVERRIDE 的否决权 —— 这是架构基石,重新设计 100 次也会保留。

---

## 附录 A：面试回答模板

**STAR 框架适配**：每个技术决策回答用 4 层结构：

| 层 | 含义 | 示例（Q1） |
|----|------|----------|
| Situation | 项目面临什么场景 | 校园心理健康对话,安全不能等 |
| Task | 我们的目标是什么 | 需要一个"安全优先"的多 Agent 架构 |
| Action | 具体做了什么 | 选择认领式协调而非固定链,SAFETY_OVERRIDE 机制 |
| Result & Trade-off | 效果和取舍 | 安全得到保障,但确定性低于 DAG |

**时长建议**：每问 60-90 秒,信息密度高于时长。

---

## 附录 B：关键词速查表

| 术语 | 文件位置 | 一句话 |
|------|---------|--------|
| **CollaborationBlackboard** | `app/agents/events.py:121` | frozen dataclass,不可变黑板,每次 replace() 返回新板 |
| **AgentTask** | `app/agents/events.py:46` | 不可变任务,含 required_capabilities 和优先级 |
| **AgentArtifact** | `app/agents/events.py:91` | 不可变产出,含 kind/payload/confidence |
| **AgentDecision** | `app/agents/registry.py:28` | decide() 返回值,claim + confidence + reason |
| **AgentTurnResult** | `app/agents/events.py:111` | act() 返回值,含 artifacts/messages/events/tasks |
| **SAFETY_OVERRIDE** | `app/agents/events.py:18` | SafetyAgent 的否决事件,强制 risk=HIGH |
| **EventDrivenCoordinator** | `app/agents/coordinator.py:20` | 认领循环 + 预算 + 终态接受,不编码固定 Agent 链 |
| **PsychologicalAssessmentService** | `app/services/assessment.py:20` | 三层评估：硬关键词→LLM JSON→启发式回退 |
| **KnowledgeService** | `app/services/knowledge.py:36` | 混合检索入口：向量+BM25 融合→重排→扩展 |
| **PrivacySanitizer** | `app/services/ai.py` | 正则脱敏,入 prompt 前和持久化前各执行一次 |
| **AgentPrivateMemory** | `app/agents/autonomous.py:53` | Agent 隔离的 Redis 记忆 facade,key=`agent:{name}:{session_id}` |
| **ToolQueueService** | `app/services/tool_queue.py` | 异步工具执行守护线程,重试+限流+死信 |
