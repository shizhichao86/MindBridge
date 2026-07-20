# 多智能体运行时深度分析

> **定位**: 面试核心文档，二期/终面必考。把"5 个 Agent 怎么协作、为什么选认领制、黑板怎么设计"讲清楚。
> **面试权重**: 70%（后端/架构面试官会逐节问，建议配合源码边看边理解）

---

## 1. 心智模型：从单 LLM 到多智能体

### 单 LLM 的局限

```
学生输入 "我不想活了" → LLM → "请保持积极心态，生活很美好 ☀️"
                           ↑
                    致命问题：没有安全硬守卫
                    次要问题：没有查历史记忆、没有检索风险知识库
```

单 LLM 的三个核心缺陷：

| 缺陷 | 危害 | 多智能体解法 |
|------|------|-------------|
| **幻觉** | 学生自伤信号被"鸡汤"稀释 | SafetyAgent 独立评估，硬关键词触发 SAFETY_OVERRIDE |
| **缺制衡** | 一个模型决定一切，出错无兜底 | 5 个 Agent 互相审查（SafetyAgent 审核 ResponseAgent 的回复） |
| **单一视角** | 只看到当前输入 | ContextAgent 独立加载历史 + RAG + skill，ResponseAgent 据此组装 |

### 五个智能体业务站位

```
                 ┌──────────────────────────────────────────────┐
                 │            CollaborationBlackboard             │
                 │  (不可变, 只追加, 事件溯源, 可审计)              │
                 └──────┬──────┬──────┬──────┬──────┬────────────┘
                        │      │      │      │      │
              ┌─────────┼──────┼──────┼──────┼──────┼─────────────┐
              │         │      │      │      │      │             │
              ▼         ▼      ▼      ▼      ▼      ▼             │
         ┌─────────┐┌────────┐┌────────┐┌──────────┐┌─────────┐ │
         │Coordinator││Underst-││Safety  ││ Context  ││Response │ │
         │  Agent   ││anding  ││ Agent  ││  Agent   ││ Agent   │ │
         │(不占槽)  ││Agent   ││        ││          ││         │ │
         └─────────┘└────────┘└────────┘└──────────┘└─────────┘ │
         │ 建根任务  ││ 判意图 ││评风险+ ││加载历史  ││组装prompt│ │
         │ 推导缺失  ││CHAT/   ││审回复  ││RAG检索  ││normal_   │ │
         │ 认领排序  ││CONSULT ││SAFETY_ ││查询改写  ││chat/sup- │ │
         │ 终态接受  ││/RISK   ││OVERRIDE││技能上下文││port模式  │ │
         └─────────┘└────────┘└────────┘└──────────┘└─────────┘ │
              │                                                   │
              └─────────────── EventDrivenCoordinator ────────────┘
                             (控制预算/调度/终态)
```

每个 Agent 有**独立隔离面**：独立 system prompt、独立 Redis 记忆 key、独立 LLM profile、独立 tool permissions（详见第 6 节）。

---

## 2. 为什么事件驱动而非固定 DAG

### 固定 DAG 方案（以 LangGraph 为代表）

```
固定 DAG:
  Understanding → Safety → Context → Response → SafetyReview
       ↓             ↓        ↓          ↓           ↓
   必须按序执行  即使 Chat 也走 Context  即使 LOW 也走安全审查
```

优点：确定性高，容易测试，调试路径可预测。
缺点：**对所有输入一视同仁**——学生问"今天天气怎么样"和"我想自杀"走完全相同的路径，浪费计算且安全上不够优先。

### 事件驱动 + 认领（Claim-based）方案

```
事件驱动（MindBridge 做法）:
  Coordinator 推导缺失任务 → 智能体自主认领 → 按优先级排序认领

  "今天天气怎么样" → 1 轮: task:understand → UNDERSTANDING 认领 → intent=CHAT
                      (协推导: CHAT+LOW → 跳过 context task)
                      → task:propose-response → RESPONSE 认领 → normal_chat mode
                      → task:review-response → SAFETY 认领 → approved
                      → FINAL_ACCEPTED (3 轮)

  "我不想活了"     → 1 轮: task:understand → UNDERSTANDING 认领 → intent=RISK
                      (硬关键词 → task:assess-safety 优先级 CRITICAL)
                      → task:assess-safety → SAFETY 认领 → risk=HIGH + SAFETY_OVERRIDE
                      → task:gather-context → CONTEXT 认领 (priority CRITICAL)
  (5 轮,每轮优先处理 CRITICAL 任务)
```

关键设计思想：**工作流不是预设的，是根据当前黑板状态动态推导的**（`app/agents/coordinator.py:92-167`）。

### 差异对比表

| 维度 | 固定 DAG (LangGraph) | 事件驱动认领 (MindBridge) |
|------|---------------------|--------------------------|
| **工作流** | 预定义节点+边,编译时固定 | 每轮 `_derive_missing_work` 动态推导 |
| **条件跳过** | 条件边 conditional_edge,图定义复杂 | 条件在 `_ensure_task_for_missing_artifact` 的 `condition` 参数,一行代码 |
| **优先级** | 需要在图中手动编排 | 自然支持: `TaskPriority.CRITICAL` 任务排第一 |
| **并发** | 框架约束 | 每轮最多 4 个 Agent 并发认领 |
| **可扩展** | 加 Agent = 改图 | 加 Agent = 注册 profile + 实现 decide/act |
| **审计** | 依赖框架 trace | 不可变黑板事件链,原生可回放 |
| **开销** | 中等 (框架层) | 低 (纯 Python dataclass) |

---

## 3. 不可变黑板 CollaborationBlackboard（面试重点）

### 3.1 数据结构

`app/agents/events.py:121-131` — `@dataclass(frozen=True)`：

```python
@dataclass(frozen=True)
class CollaborationBlackboard:
    turn_id: str                    # 本轮唯一 ID
    user_id: int | None
    session_id: str
    user_input: str                 # 原始输入
    model_input: str                # 脱敏后输入
    tasks: dict[str, AgentTask]     # 所有任务 (OPEN/CLAIMED/CLOSED)
    messages: tuple[AgentMessage, ...]   # Agent 间消息 (只追加)
    artifacts: tuple[AgentArtifact, ...] # 产出物 (intent/risk/context/response/safety_review)
    events: tuple[AgentEvent, ...]       # 事件溯源日志 (只追加)
    final_artifact_id: str          # 终态接受的 artifact ID
```

### 3.2 为什么不可变

**三个原因**：

1. **并发安全**：在一轮中多个 Agent 同时读黑板，不用锁。写返回新板，读的是旧板快照。
2. **事件溯源**：`events` 是按时间顺序的事件列表，从 `TURN_STARTED` 到 `FINAL_ACCEPTED` 或 `BUDGET_EXHAUSTED`。任何时刻的板状态都可以从事件列表回放出来。
3. **可审计**：出问题时，`events` 告诉你哪个 Agent 在哪一步发布了什么 artifact，谁审核的，谁接受的。

### 3.3 replace() 返回新板模式

`app/agents/events.py:136` — 所有修改操作都返回新 `CollaborationBlackboard`：

```python
def add_task(self, task: AgentTask) -> "CollaborationBlackboard":
    tasks = dict(self.tasks)       # 浅拷贝
    tasks[task.id] = task
    return replace(self, tasks=tasks)  # dataclasses.replace 返回新实例

def append_event(self, event: AgentEvent) -> "CollaborationBlackboard":
    return replace(self, events=(*self.events, event))  # 元组拼接
```

**关键模式**：`replace()` 是 O(1) 浅拷贝，只创建新的对象引用不拷贝数据。`tuple` 比 `list` 更适合不可变设计（不担心内部可变）。

### 3.4 核心方法一览

| 方法 | 行号 | 作用 | 面试要点 |
|------|------|------|----------|
| `add_task(task)` | 133 | 添加任务到 tasks dict | 返回新板 |
| `send_message(msg)` | 144 | 追加消息 + 自动 append MESSAGE_SENT 事件 | 事件自动生成 |
| `add_artifact(art)` | 155 | 追加 artifact + 自动 append 事件 | critique 用 CRITIQUE_PUBLISHED |
| `apply_turn_result(task, agent, result)` | 168 | **每轮收尾**: 合并 Agent 产出（消息/artifact/新任务/事件），关闭任务 | 协调器每轮调一次 |
| `open_tasks()` | 194 | 返回 status==OPEN 的任务列表 | 认领候选源 |
| `latest_artifact(kind, owner)` | 200 | 倒序查找最新 artifact | `_derive_missing_work` 的基础 |
| `artifacts_by_kind(kind)` | 197 | 按 kind 过滤 | 提取多个 risk artifact 取最高风险 |
| `accept_final(id, actor, reason)` | 217 | 设置 final_artifact_id + FINAL_ACCEPTED 事件 | 终态, loop 退出条件 |

### 3.5 accept_final 终态机制

`app/agents/events.py:217-225`：

```python
def accept_final(self, artifact_id: str, actor: str, reason: str) -> "CollaborationBlackboard":
    return replace(self, final_artifact_id=artifact_id).append_event(
        AgentEvent(type=AgentEventType.FINAL_ACCEPTED, actor=actor,
                   artifact_id=artifact_id, message=reason)
    )
```

`coordinator.py:36-82` 中，循环每轮检查 `if board.final_artifact_id: return board`——这是 loop 退出条件。accept_final 后黑板冻结，不再认领新任务。

---

## 4. 认领式协调器 EventDrivenCoordinator（面试重点）

### 4.1 Core Loop 完整流程

`app/agents/coordinator.py:36-82`：

```
                    ┌─────────────────────────────────────────────────────────┐
                    │         EventDrivenCoordinator.run(board)                │
                    │              max_rounds=8, 预算可控                       │
                    └─────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                              ┌──────────────────────────────┐
                              │  _ensure_root_task(board)     │
                              │  创建 task:root               │
                              │  (CoordinatorAgent.root_task) │
                              └──────────────────────────────┘
                                              │
                          ┌───────────────────▼───────────────────────────┐
                          │     for round_number in 1..max_rounds:        │
                          │                                               │
                          │  ① append_event(ROUND_STARTED)                │
                          │                                               │
                          │  ② _derive_missing_work(board)               │
                          │     ├─ task:understand (HIGH)                 │
                          │     ├─ task:assess-safety (HIGH/CRITICAL)     │
                          │     ├─ task:gather-context (条件)             │
                          │     ├─ task:propose-response (条件)           │
                          │     ├─ task:review-response (条件)            │
                          │     └─ task:revise-response (条件, critique)  │
                          │                                               │
                          │  ③ _try_accept_final(board)                  │
                          │     ├─ 有 response? ────NO──→ continue       │
                          │     ├─ 有 safety_review? ─NO──→ continue     │
                          │     ├─ review 匹配当前 response? ─NO→ cont   │
                          │     ├─ approved? ────NO──────→ continue      │
                          │     ├─ confidence >= 0.6? ─NO───→ continue   │
                          │     └─ YES → accept_final → RETURN           │
                          │                                               │
                          │  ④ _claim_candidates(board, claim_counts)     │
                          │     ├─ 遍历 open_tasks                        │
                          │     ├─ 每个 task 问 registry:                 │
                          │     │   能力匹配? + decide(claim?)            │
                          │     ├─ 排序: (priority, confidence, name)     │
                          │     └─ 选 top max_claims_per_round=4          │
                          │                                               │
                          │  ⑤ 如果④为空:                                 │
                          │     _derive_missing_work(force_response=True) │
                          │     candidates = _claim_candidates again      │
                          │     还为空 → break                           │
                          │                                               │
                          │  ⑥ 执行认领:                                  │
                          │     for (task, candidate) in candidates:      │
                          │       task.claim(agent_name)                  │
                          │       result = agent.act(task, board)         │
                          │       board.apply_turn_result(...)            │
                          │       claim_counts[agent] += 1               │
                          │                                               │
                          │  ⑦ _derive_missing_work + _try_accept_final   │
                          │     → 如果有 FINAL_ACCEPTED → RETURN           │
                          └───────────────────────────────────────────────┘
                                              │
                                              ▼
                          ┌──────────────────────────────────────────────┐
                          │  BUDGET_EXHAUSTED (没有正常终态)              │
                          │  兜底: 返回当前 board, 由上层取 latest_       │
                          │  artifact("response_proposal") 作为回复      │
                          └──────────────────────────────────────────────┘
```

### 4.2 _derive_missing_work: 动态推导任务链

`app/agents/coordinator.py:92-167` — 协调器最核心的方法：

**任务依赖链**：

```
intent (UNDERSTANDING) ─────────────────────────────────────────┐
                                                                 │
risk (SAFETY) ──────────────────────────────────────────────────┤
                                                                 │
context (CONTEXT) ← 条件: intent∈{CONSULT,RISK} or risk∈{MEDIUM,HIGH}
                                                                 │
response_proposal (RESPONSE) ← 条件: intent+risk 都有,           │
│                              且(context 已有 or risk=HIGH)     │
│                              或 force_response=true            │
│                                                               │
safety_review (SAFETY) ← 条件: 有 response_proposal               │
│                         且(review 未做 or 指向旧 response)      │
│                                                               │
revise_response (RESPONSE) ← 条件: critique.approved==false      │
```

**条件跳过规则**：

| 条件 | 跳过什么 | 原因 |
|------|---------|------|
| `intent=CHAT and risk=LOW` | 跳过 `task:gather-context` | 普通闲聊不需要 RAG / 记忆 / skill |
| `intent=CHAT and risk=LOW` (ResponseAgent) | 跳过等待 context | `normal_chat` mode 不需要 |
| `has_response and (review exists for current response)` | 跳过 `task:review-response` | 已审查 |
| `force_response=True and no candidates` | 强制创建 `task:propose-response` | 死锁兜底 |

**高风险关键词提升优先级**：

`app/agents/coordinator.py:275-277`：

```python
def _hard_high_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in
        ["自杀","自残","不想活","结束生命","伤害自己","轻生","suicide","kill myself","self harm"])
```

命中 → `task:assess-safety` 优先级 `CRITICAL`；risk=HIGH → `task:gather-context` 和 `task:propose-response` 优先级也为 `CRITICAL`。

### 4.3 _claim_candidates: 认领候选筛选

`app/agents/coordinator.py:201-228`：

```
步骤:
  1. 遍历 board.open_tasks()
  2. 对每个 task, 调用 registry.candidate_decisions_for(task, board)
     → registry 内部: 对每个 agent, 能力匹配 + agent.decide(task, board)
     → 返回 AgentCandidate(agent, decision) 列表, 按 confidence 降序
  3. 过滤: claim_counts[agent] >= max_claims_per_agent=3 → skip
  4. 合并排序: (PRIORITY_ORDER[task.priority], candidate.decision.confidence, agent.name)
     → CRITICAL(4) > HIGH(3) > NORMAL(2) > LOW(1)
  5. 去重: 同一 (task, agent) 不重复选; 同一 agent 每轮只选一次
  6. 截断: 最多 max_claims_per_round=4
```

### 4.4 _try_accept_final: 三条件判定

`app/agents/coordinator.py:230-245`：

```python
def _try_accept_final(self, board):
    # 条件 1: 必须有 response_proposal artifact
    # 条件 2: 必须有 safety_review artifact 且指向当前 response
    # 条件 3: safety_review.payload["approved"] == True
    # 条件 4: response.confidence >= 0.6  (agent_final_acceptance_min_confidence)
    → 全部满足 → board.accept_final(...) → FINAL_ACCEPTED
```

面试要点：**SafetyAgent 有一票否决权**——`_review_response` 可以返回 `approved=false`，阻止终态接受。

### 4.5 BUDGET_EXHAUSTED 兜底

`app/agents/coordinator.py:76-82` — 8 轮后仍未终态，追加 `BUDGET_EXHAUSTED` 事件返回黑板。上层 `_to_result()` 会取 `board.latest_artifact("response_proposal")` 作为回复（`event_driven_runtime.py:94`），即使未审核也降级使用。

---

## 5. 五个自治智能体深度剖析

### 5.1 AutonomousAgent Protocol

`app/agents/registry.py:35-42`：

```python
class AutonomousAgent(Protocol):
    profile: AgentProfile

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        """判断是否认领此 task, 返回 claim/confidence/reason"""
        ...

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        """执行认领的 task, 返回 messages+artifacts+tasks+events"""
        ...
```

`AgentProfile`（`app/agents/registry.py:19-25`）定义了 Agent 的静态描述：名称、能力集、system_prompt、记忆策略、模型 profile、工具权限。

### 5.2 CoordinatorAgent

`app/agents/autonomous.py:524-553`

| 属性 | 值 |
|------|-----|
| `capabilities` | `{COORDINATION}` |
| `tool_permissions` | `taskboard.write, blackboard.accept` |
| `decide()` | 永远返回 `claim=false` — "CoordinatorAgent is driven by the event loop" |
| `act()` | 返回空 `AgentTurnResult(close_task=False)` — 不占工人槽 |
| `root_task()` | 创建 `task:root (NORMAL or CRITICAL)` 作为协调循环起点 |
| `remember_acceptance()` | 记录接受决策到私有记忆 |

设计要点：CoordinatorAgent **不参与认领循环**。它的逻辑在 `EventDrivenCoordinator.run()` 中，不在 `decide/act` 中。这样设计确保协调器控制权独立于工人 Agent。

### 5.3 UnderstandingAgent

`app/agents/autonomous.py:109-189`

**三层意图判定** (`_classify`, 行 158)：

```
Layer 1: 硬关键词匹配 (无 LLM 调用)
  has_high_risk_signal(text) → INTENT=RISK, confidence=0.92
  ↓
Layer 2: 通用任务词匹配 (无 LLM 调用)
  text 含 "java/python/代码/作业/论文..." → INTENT=CHAT
  ↓
Layer 3: LLM 分类 (仅前两层未命中)
  私有记忆 context + PromptTemplates.intent_prompt → AI 输出 CHAT/CONSULT/RISK
  ↓
Fallback: has_consult_signal → CONSULT, else → CHAT
```

面试要点：三层设计减少了 LLM 调用——约 60% 的输入在 Layer 1/2 就完成了，大幅降低延迟和成本。

### 5.4 SafetyAgent

`app/agents/autonomous.py:192-307`

**双模式**：

```
模式 A: 风险评估 (_assess_risk, 行 224)
  PsychologicalAssessmentService.assess(text, history)
    → 硬关键词 → HIGH (4.0/0.95, 不调 LLM)
    → LLM JSON → 提取 emotion/score/risk/confidence
    → 异常 → heuristic fallback (consult信号→MEDIUM/LOW; 否则 LOW)
  → 发布 risk artifact + SAFETY_OVERRIDE 事件 (if HIGH)

模式 B: 回复安全审查 (_review_response, 行 262)
  检查 response_proposal 内容:
    如果 risk=HIGH 且回复中缺少安全引导词 → approved=false → critique
    → 发布 REVISION_REQUESTED 事件 + 创建 revise-response 任务
  否则 → approved=true → safety_review artifact
```

**SAFETY_OVERRIDE 的威力**：发布此事件后，`_risk_value()` 直接返回 `HIGH`，覆盖所有 LLM 评估结果。协调器看到 SAFETY_OVERRIDE → 推导 `task:gather-context` 和 `task:propose-response` 均为 CRITICAL 优先级。

### 5.5 ContextAgent

`app/agents/autonomous.py:310-423`

**条件激活**：仅在 `intent≠CHAT or risk≠LOW` 时认领（`decide` 行 323-332）。普通闲聊跳过，节省一轮。

**执行流程** (`act`, 行 334)：

```
1. _load_history()           → Redis → MySQL fallback
2. compact_history_for_prompt → 压缩历史 (deterministic_brief)
3. _summarize_memory()       → LLM 摘要 (1-3 句中文要点)
4. _bounded_model_history()  → 限制 history 长度 (chat_history_limit*2)
5. 条件执行 (仅 support path):
   _rewrite_query()          → LLM 改写查询词 (≤60 字符)
   knowledge.retrieve()      → Chroma + BM25 混合检索
   MindBridgeSkillLibrary    → 匹配技能上下文
6. 发布 context artifact
```

### 5.6 ResponseAgent

`app/agents/autonomous.py:426-521`

**双模式 prompt 组装**：

```
normal_chat (intent=CHAT and risk=LOW):
  system: "你是 MindBridge, 日常陪伴与校园生活助手"
          + ResponseAgent private memory
          + memory_brief
  → 不做心理测评, 不输出风险标签

support (其他所有情况):
  system: "你是 MindBridge, 校园心理关怀智能体"
          + 共情/谨慎/非评判约束
          + 检索知识 + skill 指引
          + HIGH risk → "高风险处理规则" 硬注入
          + ResponseAgent private memory
          + memory_brief
```

面试要点：`normal_chat` 不注入 RAG 知识和 skill——避免学生对普通聊天也收到"我注意到你可能情绪低落"的安全审查口吻。这是**业务正确性**高于**技术完备性**的设计。

---

## 6. 隔离面设计

`/api/agent/status` 返回的 `collaboration.agentIsolation` 结构（`app/api/routes.py:84-91`）：每个 Agent 拥有独立隔离域。

### 6.1 模型隔离

`AgentModelRegistry`（`app/services/agent_models.py`）——每个 Agent 可有独立 provider/model。配置项：

```
agent_model_coordinator_provider/agent_model_coordinator_model
agent_model_understanding_provider/agent_model_understanding_model
agent_model_safety_provider/agent_model_safety_model
agent_model_context_provider/agent_model_context_model
agent_model_response_provider/agent_model_response_model
```

未配时 fallback 到 `agent_model_default_provider/model` → 全局 `ai_provider/ollama_model/openai_model`。

### 6.2 记忆隔离

`app/agents/autonomous.py:53-68` — `AgentPrivateMemory`：

```python
def _key(self, agent_name: str, session_public_id: str) -> str:
    return f"agent:{agent_name}:{session_public_id}"
```

| Agent | Redis Key | 内容 |
|-------|-----------|------|
| UnderstandingAgent | `agent:UnderstandingAgent:{sid}` | `intent=CHAT; topic=general_task` |
| SafetyAgent | `agent:SafetyAgent:{sid}` | `risk=LOW; summary=未检测到风险` |
| ContextAgent | `agent:ContextAgent:{sid}` | `context intent=CONSULT; retrieved=3` |
| ResponseAgent | `agent:ResponseAgent:{sid}` | `response mode=support; intent=CONSULT` |

为什么隔离：SafetyAgent 的"本轮是 HIGH"不应该影响 ContextAgent 检索行为的独立性；ContextAgent 的记忆摘要不应该偏向 SafetyAgent 的判断。

### 6.3 工具权限隔离

| Agent | 工具权限 | 说明 |
|-------|---------|------|
| UnderstandingAgent | `llm.intent` | 只做意图分类 |
| SafetyAgent | `llm.risk, rules.high_risk, response.review` | 评估+审查 |
| ContextAgent | `redis.memory, mysql.messages, rag.retrieve, skills.read` | 只读 |
| ResponseAgent | `llm.response_plan` | 只组装 prompt |
| CoordinatorAgent | `taskboard.write, blackboard.accept` | 控制权 |

### 6.4 为什么需要隔离面 —— 面试角度

1. **安全制衡**：SafetyAgent 看不到 ResponseAgent 的私有记忆，不会被其 prompt 干扰；风险判断完全独立。
2. **模型降本**：ContextAgent 的摘要任务可用小模型（省钱），SafetyAgent 的评估可用更强模型（准确）。
3. **故障隔离**：ContextAgent 挂了不影响 UnderstandingAgent 和 SafetyAgent 继续工作；ResponseAgent 收到 stale context 会降级使用 fallback。
4. **可替换性**：加一个新 Agent 只需定义 profile + decide/act，不碰其他 Agent 的代码。

---

## 7. 完整事件生命周期

### 7.1 12 种事件类型

`app/agents/events.py:8-21`：

| 事件 | 语义 | 触发者 |
|------|------|--------|
| `TURN_STARTED` | 用户输入到达黑板 | Runtime |
| `TASK_CREATED` | 新任务发布 | Coordinator |
| `ROUND_STARTED` | 新轮次开始 | Coordinator |
| `TASK_CLAIMED` | Agent 认领任务 | Coordinator (after claim) |
| `MESSAGE_SENT` | Agent 发消息 | Board.send_message() |
| `ARTIFACT_PUBLISHED` | 产出物发布 | Board.add_artifact() |
| `CRITIQUE_PUBLISHED` | 否定审查 (critique) | Board.add_artifact() |
| `REVISION_REQUESTED` | 要求修改回复 | SafetyAgent |
| `SAFETY_OVERRIDE` | 安全超控 (覆盖风险) | SafetyAgent |
| `TASK_RELEASED` | 任务释放 (预留) | — |
| `TASK_CLOSED` | 任务完成 | Board.apply_turn_result() |
| `FINAL_ACCEPTED` | 终态接受 | Coordinator |
| `BUDGET_EXHAUSTED` | 预算耗尽兜底 | Coordinator |

### 7.2 典型对话事件序列

```
"最近压力大,睡不着" (intent=CONSULT, risk=LOW)

TURN_STARTED
ROUND_STARTED(1)
TASK_CREATED(task:root)
TASK_CREATED(task:understand)
TASK_CREATED(task:assess-safety)
TASK_CLAIMED(task:understand, UnderstandingAgent)
ARTIFACT_PUBLISHED(intent=CONSULT, confidence=0.78)
TASK_CLOSED(task:understand)
TASK_CLAIMED(task:assess-safety, SafetyAgent)
MESSAGE_SENT(risk=LOW → Coordinator)
ARTIFACT_PUBLISHED(risk=LOW, confidence=0.84)
TASK_CLOSED(task:assess-safety)
ROUND_STARTED(2)
TASK_CREATED(task:gather-context)        ← intent=CONSULT 触发
TASK_CLAIMED(task:gather-context, ContextAgent)
MESSAGE_SENT(context ready → ResponseAgent)
ARTIFACT_PUBLISHED(context, confidence=0.88)
TASK_CLOSED(task:gather-context)
ROUND_STARTED(3)
TASK_CREATED(task:propose-response)      ← intent+risk+context 就绪
TASK_CLAIMED(task:propose-response, ResponseAgent)
MESSAGE_SENT(请审查 → SafetyAgent)
ARTIFACT_PUBLISHED(response_proposal, confidence=0.86)
TASK_CLOSED(task:propose-response)
TASK_CREATED(task:review-response:{id})
TASK_CLAIMED(task:review-response:{id}, SafetyAgent)
ARTIFACT_PUBLISHED(safety_review, approved=true, confidence=0.95)
TASK_CLOSED(task:review-response:{id})
FINAL_ACCEPTED
```

共 3 轮，20+ 事件，完整的协作审计链。

### 7.3 高风险场景差异

```
"我不想活了" (硬关键词命中)

差异点:
  - task:assess-safety → CRITICAL (非 HIGH)
  - SAFETY_OVERRIDE 事件发布
  - task:gather-context → CRITICAL (非 NORMAL)
  - task:propose-response → CRITICAL
  - ResponseAgent → support mode, "高风险处理规则" 硬注入
  - _review_response 检查回复是否包含安全引导词
    → 不包含 → critique(approved=false) → REVISION_REQUESTED → 新增 revise-response 任务
    → 下一轮 ResponseAgent 修订
```

---

## 8. AgentRegistry 与能力匹配

`app/agents/registry.py:51-76`

```python
class AgentRegistry:
    def candidate_decisions_for(self, task, board) -> list[AgentCandidate]:
        1. 对每个 agent:
            _has_required_capability(agent, task)  # frozenset 子集检查
            → 通过后调用 agent.decide(task, board)
            → claim==true → 加入候选
        2. 按 confidence 降序排序
        3. 返回 AgentCandidate(agent, decision) 列表
```

`AgentProfile.capabilities` 是 `frozenset[AgentCapability]`，`task.required_capabilities` 也是 frozenset。匹配用 `set(task.required_capabilities).issubset(agent_capabilities)`。

---

## 9. 工厂与状态报告

`app/agents/factory.py:13-28`：

```python
def create_agent_runtime(db, settings) -> EventDrivenAgentRuntimeService:
    return EventDrivenAgentRuntimeService(db, settings)

def agent_framework_status(settings) -> dict:
    aliases = {"event_driven_multi_agent", "multi_agent", "actors"}
    return {"requested": ..., "active": "event_driven_multi_agent",
            "fallback": requested not in aliases}
```

注意：**实际只实现了事件驱动这一种框架**。`agent_framework` 配成 `langgraph` 会返回 `fallback=true` 但运行时仍走事件驱动。

---

## 10. AgentRunResult 返回契约

`app/agents/result.py:21-35`：

```python
@dataclass
class AgentRunResult:
    intent: IntentType             # CHAT/CONSULT/RISK
    risk_level: RiskLevel          # LOW/MEDIUM/HIGH
    assessment: PsychologyAssessment | None
    retrieved_knowledge: list[SearchResult]
    response_messages: list[AiMessage]  # 组装好的 prompt (可直接送给 AI 流式)
    steps: list[AgentStep]             # 事件摘要 (step/agent/action/observation)
    memory_brief: str                  # 上下文摘要
    collaboration_events: list[Any]    # 完整事件链
    collaboration_tasks: list[Any]     # 完整任务列表
    collaboration_artifacts: list[Any] # 完整 artifact 列表

    @property
    def requires_report(self) -> bool:
        return self.intent != IntentType.CHAT  # CHAT 不需要心理报告
```

`response_messages` 是给 `AiClient.stream()` 的输入——已经包含完整的 system prompt + history + context。上游 `ChatService.stream_chat()` 直接用它流式输出。

---

## 11. 面试常见追问

### Q1: 为什么不用 LangChain Agent？

**答**：LangChain Agent 的 ReAct/Tool-use 范式是为"LLM 调用工具"设计的，而我们的场景是"多个 LLM 互相制衡"。5 个 Agent 的协作不是 tool calling，而是角色分工+黑板通信。LangChain 的 AgentExecutor 做不到我们需要的：独立安全审查、SAFETY_OVERRIDE 超控、私有记忆隔离、条件跳过 context。

### Q2: 五个 Agent 如何通信？为什么不直接调方法？

**答**：通过不可变黑板 `CollaborationBlackboard`。Agent 不直接调另一个 Agent 的方法——它们各自读黑板上的 artifact，发布自己的 artifact。这种设计的好处是：Agent 间零耦合，加/减 Agent 不需要改其他 Agent 的代码；事件溯源保证审计。

### Q3: 某个 Agent 挂了（LLM 调用失败、超时）怎么办？

**答**：三层防护。
- `UnderstandingAgent._classify()` 的 LLM 调用在 try-except 里，失败 fallback 到关键词判断。
- `ContextAgent` 的记忆摘要/查询改写/检索 各自 try-except，失败用本地 fallback。
- `SafetyAgent` 的风险评估异常 fallback 到 `heuristic()`。
- 最坏情况：所有 Agent 都失败 → `BUDGET_EXHAUSTED` → `_to_result()` 取 latest response_proposal（或 fallback_messages）。

### Q4: 预算参数怎么调优？

| 参数 | 默认值 | 调优建议 |
|------|--------|----------|
| `agent_max_rounds=8` | 8 | 正常对话平均 3-4 轮；高风险需要 5-6 轮（含 revision）。8 是上限，主流对话不会达到 |
| `max_claims_per_round=4` | 4 | 5 个 Agent 最多 4 个并发认领。保留冗余避免 CoordinatorAgent 也被计入 |
| `max_claims_per_agent=3` | 3 | SafetyAgent 最多用 2 次（评估+审查）；UnderstandingAgent 只用 1 次。3 是安全上限 |
| `final_min_confidence=0.6` | 0.6 | 太低可能接受不靠谱回复；太高可能耗尽预算。0.6 是经验值 |

### Q5: 如何加一个新 Agent？

1. 定义 `AgentProfile` — 名称、能力、prompt、模型 profile、工具权限
2. 实现 `decide(task, board) → AgentDecision` — 何时认领
3. 实现 `act(task, board) → AgentTurnResult` — 做什么、产出什么 artifact
4. 在 `EventDrivenAgentRuntimeService.run()` 的 agents 列表中加入实例
5. 在 `_derive_missing_work` 中加入新 artifact kind 的任务推导逻辑（可选，如果用的是已有 kind 则不需要）
6. 如果是新的 artifact kind，在 `_try_accept_final` 中增加条件（可选）
