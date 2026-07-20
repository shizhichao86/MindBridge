# 面试导向学习路线图

> 文档定位：面向面试准备的 5-7 天学习计划,每个阶段标注面试权重和验证方式。
> 面试权重：本文档在面试准备场景中权重 **中**（20%），核心知识沉淀在 [02_PROJECT_QNA.md](./02_PROJECT_QNA.md) 问答集中。

---

## 0. 学习前准备

### 0.1 Python 基础要求

| 知识点 | 项目中对应位置 | 准备时长 |
|--------|--------------|---------|
| `@dataclass(frozen=True)` | `app/agents/events.py:46` — AgentTask、CollaborationBlackboard 全为 frozen | 30 min |
| `typing.Protocol` | `app/agents/registry.py:35` — AutonomousAgent Protocol | 15 min |
| type hints（`dict[str, Any]`、`frozenset[str]`） | 全局使用 | 已掌握即可 |
| async generator | `app/api/routes.py` — SSE 流式端点 | 30 min |

### 0.2 环境搭建

```bash
docker compose up -d --build  # MySQL:13306 + Redis:16379 + App:8080
curl -u student:student123 -N -H 'Content-Type: application/json' \
  -d '{"message":"我最近很焦虑"}' http://127.0.0.1:8080/api/chat/stream
```

若无法启动 Docker,使用 Python 本地启动（需先配 MySQL/Redis）：

```bash
AI_PROVIDER=mock uvicorn app.main:app --host 127.0.0.1 --port 8080
```

---

## 1. 阶段一：项目全景理解（1-2 天）【面试权重 10%】

### 目标

能画出一张图："学生发一句话 → 系统内部发生了什么 → 返回什么"。

### 核心文件

```
README.md               → 整体架构、技术栈、调用示例
.env.example            → 所有可配置开关的含义
docker-compose.yml      → 基础架构组件（MySQL / Redis / App）
```

### 关键理解点

1. **POST /api/chat/stream 完整链路**

```
Browser → Basic Auth → routes.py → ChatService.stream_chat()
  → MindBridgeAgentHarness.run()  [同步阻塞,完成 Agent 协作 + 报告落库]
    → EventDrivenAgentRuntimeService.run()
      → EventDrivenCoordinator.run(board)  [多轮认领循环]
    → 心理报告落库 + 工具队列入队
  → SSE 流式返回 token 分片 → event:done
  → 异步 dispatch_tools() [解耦,不阻塞学生]
```

2. **五个 Agent 分工速记**

| Agent | 产出 artifact | 时机 |
|-------|-------------|------|
| CoordinatorAgent | 任务板、最终采纳 | 全程 |
| UnderstandingAgent | `intent` (CHAT/CONSULT/RISK) | 第一轮 |
| SafetyAgent | `risk` + `safety_review` | 并行 |
| ContextAgent | `context` (记忆 + RAG + Skill) | 仅 intent!=CHAT 或 risk!=LOW |
| ResponseAgent | `response_proposal` | 依赖 intent + risk 就绪 |

### 验证方式

向自己口头解释："我在终端输入 curl -N 发一条'我不想活了'，系统每一步做了什么？"能讲满 3 分钟即可。

---

## 2. 阶段二：智能体运行时核心（2-3 天）【面试权重 40% -- 重中之重】

### 阅读顺序（严格按此顺序）

```
app/agents/events.py          → 所有不可变数据结构 (黑板/任务/消息/artifact/事件)
app/agents/registry.py        → AutonomousAgent Protocol + AgentRegistry (能力匹配+认领排序)
app/agents/autonomous.py      → 五个 Agent 的 decide/act 实现
app/agents/coordinator.py     → EventDrivenCoordinator (认领循环 + 预算 + 终态接受)
app/agents/event_driven_runtime.py → 外层 Runtime: 生命周期、审计/乐观锁/响应截断
app/agents/harness.py         → MindBridgeAgentHarness (HTTP 层与 Runtime 之间的胶水)
app/agents/result.py          → AgentRunResult 共享返回类型
app/agents/factory.py         → 工厂函数,入口创建
```

### 核心产出

#### 产出 A：CollaborationBlackboard 生命周期图

```
TURN_STARTED
  │
  ├─► Coordinator: 添加 root task (task:root)
  │
  ├─► [多轮认领循环]
  │   ├─ Round N:
  │   │   ├─ _derive_missing_work() → 确保 intent/risk/context/response 任务存在
  │   │   ├─ _try_accept_final()     → 检查是否可终止
  │   │   ├─ _claim_candidates()     → Agent 认领任务 (优先级→置信度→名称排序)
  │   │   └─ agent.act() → apply_turn_result() → 黑板追加 artifact/event/message
  │   └─ 预算耗尽 → BUDGET_EXHAUSTED
  │
  └─► FINAL_ACCEPTED (response + safety_review 通过 + confidence>=0.6)
```

#### 产出 B：事件序列示例（高风险消息）

```
TURN_STARTED → TASK_CREATED(task:root)
  → TASK_CREATED(task:understand) → TASK_CLAIMED → ARTIFACT_PUBLISHED(intent=RISK)
  → TASK_CREATED(task:assess-safety) → SAFETY_OVERRIDE → ARTIFACT_PUBLISHED(risk=HIGH)
  → TASK_CREATED(task:gather-context) → ARTIFACT_PUBLISHED(context)
  → TASK_CREATED(task:propose-response) → ARTIFACT_PUBLISHED(response_proposal)
  → TASK_CREATED(task:review-response) → ARTIFACT_PUBLISHED(safety_review,approved=True)
  → FINAL_ACCEPTED
```

#### 产出 C：五个 Agent 的 decide/act 行为表

| Agent | decide 何时 claim=True | act 产出 |
|-------|----------------------|---------|
| **UnderstandingAgent** | 尚无 intent artifact 且任务需要理解 | `intent` artifact (CHAT/CONSULT/RISK + 置信度) |
| **SafetyAgent** | (1) 已有 response 但无对应 review; (2) 尚无 risk artifact; (3) 任务要求 SAFETY | `risk` artifact (+ SAFETY_OVERRIDE 事件) / `safety_review` 或 `critique` artifact |
| **ContextAgent** | (1) 无 context artifact 且任务要求; (2) intent/risk 触发支持路径 | `context` artifact (记忆摘要 + RAG 结果 + Skill 约束) |
| **ResponseAgent** | (1) intent+risk 就绪; (2) 有 context 或 risk=HIGH; (3) 有 revisionOf | `response_proposal` artifact (normal_chat 或 support 模式) |
| **CoordinatorAgent** | 不参与认领 (decide 永远 False) | 由 EventDrivenCoordinator 外部驱动 |

### 面试加分项

准备一段 **3 分钟口述**："我们的多智能体协作运行时"，结构：

1. 30s: 为什么事件驱动而非 LangChain 固定链
2. 60s: 不可变黑板 + 认领式协调的核心机制
3. 60s: 五个 Agent 各司其职 + SAFETY_OVERRIDE 硬安全门槛
4. 30s: 预算管理 (8 轮 / 4 认领 / 3 次 / 置信度 0.6)

---

## 3. 阶段三：RAG 与知识检索（1-2 天）【面试权重 20%】

### 核心文件

```
app/services/knowledge.py     → KnowledgeService (检索入口 + 融合 + 重排)
app/services/vector_store.py  → ChromaKnowledgeStore (向量存储抽象)
app/rag_eval/runner.py        → RAG 评测脚本
```

### 核心产出

#### 融合公式（`knowledge.py:151-192`）

```
候选 = Chroma 向量检索(top_k=16) + BM25 关键词检索(top_k=16)
  → 各自 min-max 归一化
  → 加权融合: score = (vec_score * 0.65 + bm25_score * 0.35) / 1.0
  → 本地重排: final = base*0.55 + hybrid*0.25 + coverage*0.15 + phrase*0.05
  → 上下文扩展: 最佳命中取相邻 chunk 拼接
```

#### 降级流程图

```
检索请求
  ├─ can_embed=True → Chroma 向量 + BM25 混合检索
  │   ├─ 成功 → 融合重排 → 返回 top_k
  │   └─ 失败 → KNOWLEDGE_VECTOR_REQUIRED?
  │       ├─ true  → 抛出异常
  │       └─ false → 降级到纯 BM25 + hybrid_score 重排
  └─ can_embed=False → 纯 BM25 + hybrid_score 重排
```

### 验证方式

```bash
AI_PROVIDER=mock python -m app.rag_eval.runner  # 输出 target/rag-eval-report.json
```

检查 Recall@K、MRR、NDCG@K 指标。准备 2 分钟口述回答"我们的混合 RAG 怎么做的"。

---

## 4. 阶段四：安全与工程系统（1-2 天）【面试权重 20%】

### 核心文件

```
app/services/assessment.py    → PsychologicalAssessmentService (三层评估)
app/services/tool_queue.py    → ToolQueueService + ToolQueueWorker (异步工具队列)
app/services/tool_governance.py → 工具治理策略 + 审计
app/services/tools.py         → ToolOrchestrationService (Excel/案例/告警)
```

### 核心产出：高风险消息完整工具链

```
高风险消息
  → PsychologicalAssessmentService.assess() → risk=HIGH
  → 工具计划生成
    ├─ EXCEL_REPORT (总是,任意风险) → openpyxl 写入表格
    ├─ CASE_CREATE (MEDIUM+) → 幂等创建风险个案
    └─ ALERT_SEND (仅 HIGH,依赖 CASE_CREATE SUCCESS) → 邮件/log
  → 工具队列异步执行 (Excel 1 线程 + Email 2 线程 + 滑动窗口限流)
  → 失败重试 → 超 max_attempts → dead_letter_records
```

---

## 5. 阶段五：面试模拟与代码深挖（1-2 天）【面试权重 10%】

### 行动清单

1. **通读 [02_PROJECT_QNA.md](./02_PROJECT_QNA.md)**，逐题在心里过一遍回答要点
2. **选 10 个高频问题练习口述**（推荐 Q1/Q2/Q5/Q9/Q10/Q11/Q15/Q20/Q22/Q28）
3. **读 tests/*.py**：理解 `AI_PROVIDER=mock` 下的测试隔离策略
4. **读 harness/runner.py**：理解工程自检 6 套件
5. **运行关键测试**：

```bash
python -m unittest discover -s tests
python -m app.harness.runner
```

### 面试前自检清单

- [ ] 能否 30 秒说出 POST /api/chat/stream 完整链路？
- [ ] 能否 3 分钟讲清事件驱动的多智能体协作机制？
- [ ] 能否 30 秒画出 CollaborationBlackboard 的不可变更新模式？
- [ ] 能否解释 decide/act 分离的设计理由？
- [ ] 能否说出融合公式的三个权重 (0.65/0.35 和重排四因子)？
- [ ] 能否讲清高风险消息从输入到 EXCEL/CASE/ALERT 的全流程？

---

## 6. 面试前 1 小时快速回顾

### 6.1 关键词速查表

| 术语 | 一句话解释 |
|------|----------|
| **不可变黑板** | `@dataclass(frozen=True)`,每次变更 `replace(...)` 返回新板 |
| **认领式协调** | Agent 主动 `decide()` 认领 open task,非固定编排链 |
| **SAFETY_OVERRIDE** | SafetyAgent 发布的事件,强制 risk=HIGH,不可降级 |
| **fusion + rerank** | 向量 0.65 + BM25 0.35 min-max 融合,再本地四因子重排 |
| **三层评估** | 硬关键词 → LLM JSON 评估 → 启发式回退 |
| **Stream-Tool 解耦** | 先 SSE 流完学生消息,再异步 dispatch_tools() |

### 6.2 五张 Agent 角色卡

```
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│ UnderstandingAgent  │  │ SafetyAgent          │  │ ContextAgent        │
├─────────────────────┤  ├─────────────────────┤  ├─────────────────────┤
│ 能力: UNDERSTANDING │  │ 能力: SAFETY         │  │ 能力: CONTEXT        │
│ 产出: intent        │  │ 产出: risk + review  │  │ 产出: context        │
│ 模型: understanding │  │ 模型: safety         │  │ 模型: context        │
│ 工具: llm.intent    │  │ 工具: llm.risk +     │  │ 工具: redis + rag    │
│ 记忆: 私有意图历史  │  │   rules.high_risk    │  │   + skills.read      │
│ 触发: 总是(第一轮)  │  │ 记忆: 私有安全账本   │  │ 记忆: 私有上下文     │
│                     │  │ 触发: 并行+审查      │  │ 触发: 仅支持路径     │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
┌─────────────────────┐  ┌─────────────────────┐
│ ResponseAgent       │  │ CoordinatorAgent    │
├─────────────────────┤  ├─────────────────────┤
│ 能力: RESPONSE      │  │ 能力: COORDINATION   │
│ 产出: proposal      │  │ 产出: 任务板+最终采纳│
│ 模型: response      │  │ 模型: coordinator    │
│ 工具: llm.response  │  │ 工具: taskboard +    │
│ 记忆: 私有回复策略  │  │   blackboard.accept  │
│ 触发: intent+risk就绪│  │ 记忆: 协调追踪       │
│                     │  │ 触发: 外部循环驱动   │
└─────────────────────┘  └─────────────────────┘
```

### 6.3 三个核心亮点（面试开场的"电梯演讲"）

1. **事件驱动认领式多智能体**：不是 if-else 链,五个 Agent 按优先级和置信度自主认领任务,SAFETY_OVERRIDE 机制保证安全不容协商。
2. **混合检索 RAG 自带降级**：Chroma 向量 + 纯 Python BM25 双路召回,向量不可用时自动降级,不阻断服务。
3. **Stream-Tool 异步解耦**：学生永远第一时间看到回复,后台工具队列(Excel/案例/告警)静默执行,失败进死信队列。
