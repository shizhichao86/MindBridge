# 简历包装与项目展示指南

> **定位**: 面向 AI Agent 应用开发 / 大模型应用开发岗位的简历写作指南。
> **使用方式**: 「推荐版本」直接复制到简历;备选版本按投递方向微调关键词侧重。
> **原则**: 每个技术点都能在项目源码中落地,不写虚的。

---

# ★ 推荐版本(融合优化,直接复制)

```
MindBridge — 事件驱动多智能体协作平台               2026.01 - 2026.04

项目简介: 自研事件驱动多智能体协作运行时,5 个自治 Agent(Understanding/Safety/
Context/Response/Coordinator)通过不可变黑板 CollaborationBlackboard 与认领式协调器
EventDrivenCoordinator 实现协作决策。集成 Hybrid RAG 混合检索(Chroma+BM25+本地四因子
Reranker+三级降级)、心理风险评估硬守卫(关键词/LLM JSON/启发式三层防线)和 MCP 异步工具
队列(Excel 台账/案例/预警/死信),覆盖从意图识别到高风险闭环的完整链路。

技术栈:
  AI Agent 层: 事件驱动多智能体、不可变黑板、认领式协调、Agent 隔离面(独立记忆/模型/工具)、MCP 协议(SDK 子进程 stdio)
  检索与 LLM: Hybrid RAG、Chroma 向量库、纯 Python BM25、本地 Reranker、Prompt Engineering(Skill 系统)
  后端与工程: Python/FastAPI、SQLAlchemy(PyMySQL)、Redis(短期记忆 24h TTL)、MySQL(全量持久化)、Docker Compose

核心职责:

1. Agent Runtime Harness 与多智能体协作
   - 设计 MindBridgeAgentHarness 编排层,将 Agent Runtime 调用、数据库落库和工具后处理解耦,
     统一管理 输入脱敏→Agent 协作→报告生成→Trace 保存→消息持久化→工具计划生成 全链路。
   - 实现不可变黑板 CollaborationBlackboard(@dataclass frozen=True,事件溯源 append-only),
     认领式协调器 EventDrivenCoordinator 按 capacity 自动派生任务链(intent→risk→context→
     response→safety_review),Agent 自主认领并发布 artifact,SafetyAgent 独立审查并通过
     SAFETY_OVERRIDE 强制高风险判定,协调器最终采纳。
   - 设计决策: 选认领制而非固定 DAG,普通对话 ContextAgent 自动跳过(无需 RAG),高风险场景
     充分协作,天然实现"双速执行";每个 Agent 独立隔离面(模型 profile + 私有 Redis key +
     frozenset 工具权限)确保安全制衡。

2. Hybrid RAG 混合检索与自动降级
   - 实现 Chroma 向量(OpenAI text-embedding-3-small,候选 k=16) + 纯 Python BM25(k1=1.5,
     b=0.75,中文 2-gram 分词,零依赖)双路召回,Min-Max 归一化后按 vector×0.65+BM25×0.35
     加权融合,再经本地四因子 Reranker(base/lexical/coverage/phrase)精排,最优 chunk 取
     ±1 相邻块拼接扩展上下文。
   - 设计三级自动降级: Chroma+BM25 完整混合→纯 BM25+hybrid_score(无 API Key/Chroma 缺
     失时自动切换)→拒绝启动(KNOWLEDGE_VECTOR_REQUIRED=true 时),实现零配置 fallback。
   - 构建 RAG 评测体系(Recall@K / Precision@K / MRR / NDCG / HitRate),评测数据集 63 条
     case,当前 HitRate 0.9667,MRR 0.9083。

3. 分层记忆管理与 Prompt 压缩
   - 两级记忆架构: 短期上下文优先 Redis(24h TTL,key mindbridge:short-term-memory:{id}),
     Redis 缺失时 MySQL 回填;全量历史持久化 MySQL。
   - 实现历史窗口裁剪 + 摘要压缩 + 最近对话保留策略,生成 memoryBrief(≤500 字符)和受限
     modelHistory(最近 N 条),防止长对话下 Prompt 膨胀。
   - 独立 Redis 命名空间(agent:{name}:{session_id})实现 Agent 记忆隔离,Redis 宕机不阻断
     聊天(代码容错)。

4. MCP 工具双模 + 异步队列 + 治理
   - 封装 6 个 MCP 工具(Excel 台账写入/个案创建/预警发送/案例确认/备注追加/通知发送),实
     现双模调用: 生产环境走异步工具队列,开发/演示走 MCP SDK 子进程 stdio 直调,两条路径
     复用同一套 ToolOrchestrationService。
   - 异步工具队列: 守护线程 ToolQueueWorker + 双 ThreadPoolExecutor(excel 1 线程 / email
     2 线程)+ 作业依赖管理(ALERT_SEND 等待 CASE_CREATE SUCCESS)+ 滑动窗口限流(邮件
     30/min)+ 指数退避重试(最大 3 次)+ 死信队列(失败作业可追溯重放)。
   - 静态治理策略: EXCEL_REPORT 任意风险等级 / CASE_CREATE 仅 MEDIUM+ / ALERT_SEND 仅
     HIGH,每次工具执行写 ToolAuditRecord(含 strategy/reason/allowed/status 字段),实现
     后台可审计。

5. Engineering Harness 与质量保障
   - 建设一键工程自检 Harness(临时 SQLite + 内存短期记忆 + mock AI + 关闭向量),覆盖 Risk
     Safety / Agent Routing / Standard Skills / RAG / API / Tool Queue 六类核心链路。
   - Skill 系统: MindBridgeSkillRegistry 运行时动态加载 skills/*/SKILL.md(YAML front-
     matter + Workflow 定义),MindBridgeSkillLibrary 按 intent/risk/关键词 选配技能,
     高风险场景强制叠加 high_risk_safety_plan,生成 counselor_handoff_summary 交接摘要,
     提升回复策略的可审计性与可测试性。
   - Docker Compose 一键编排(MySQL 8.0:13306 + Redis 7.2:16379 + App:8080),支持 Mock
     模式(AI_PROVIDER=mock + SQLite + 关闭向量,5 分钟密闭启动,零外部依赖)。
```

---

## 备选版本(按岗位微调,在上方基础上删减)

### AI Agent 应用开发岗 — 侧重 Agent 架构

把"核心职责"第 1 条放第一位,第 2/3 条合为一条"Agent 记忆与 RAG",第 4/5 条合为"工具与工程化":

```
1. Agent Runtime Harness 与多智能体协作(同上)
2. Agent 记忆管理与 RAG 检索
   - 分层记忆: Redis 短期(24h TTL)→MySQL 回填;memoryBrief+受限 modelHistory 防 Prompt 膨胀;
     独立命名空间 agent:{name}:{sid} 实现 Agent 记忆隔离。
   - Hybrid RAG: Chroma 向量(0.65)+纯 Python BM25(0.35)融合+四因子 Reranker+三级降级;
     HitRate 0.9667, MRR 0.9083。
3. MCP 工具体系与工程保障: 6 个 MCP 工具(Excel/案例/预警),双模调用(队列/SDK 子进程 stdio)
   + 工具治理策略+ToolAuditRecord 审计;Engineering Harness 6 套件覆盖核心链路。
```

### 后端开发岗 — 侧重系统设计

把"核心职责"第 4 条(工具队列)提前,第 2 条(RAG)合并到第 3 条(记忆),第 1 条(Agent)精简:

```
1. 异步工具队列与治理: 双线程池+作业 DAG 依赖+滑动窗口限流+死信队列+MCP 双模,工具执行与
   流式回复解耦,对用户无感知。
2. Hybrid RAG 与分层记忆: Chroma+BM25 混合检索(向量 0.65+关键词 0.35)+三级自动降级;
   两级记忆(Redis→MySQL 回填)+Prompt 压缩,HitRate 0.97。
3. 多 Agent 协作: 5 Agent 通过不可变黑板+认领式协调器协作,SafetyAgent 独立安全审查。
```

### 大模型应用开发岗 — 侧重 LLM/RAG/Prompt

```
1. Hybrid RAG 与 Prompt Engineering: Chroma+BM25 双路融合+四因子 Reranker+三级降级;
   Skill 系统动态加载,ResponseAgent 双模式 prompt 组装(normal_chat/support),Prompt 压缩。
2. 多层安全护栏: 三层风险评估硬守卫(关键词→LLM JSON→heuristic),硬关键词零 token 拦截;
   SafetyAgent 独立安全审查+SAFETY_OVERRIDE 强制覆盖。
3. MCP 工具与工程化: 6 MCP 工具(FastMCP SDK 子进程 stdio),双模异步队列,工具审计可追溯。
```

---

## 项目一句话描述(简历顶部用)

| 岗位 | 描述 |
|------|------|
| 通用 | 独立设计并开发事件驱动多智能体协作平台,5 Agent 经不可变黑板+认领式协调器通信,集成 Hybrid RAG(Chroma+BM25 融合)、三层风险评估硬守卫及 MCP 异步工具体系,覆盖意图路由到高风险闭环完整链路 |
| AI Agent 岗 | 自研事件驱动多 Agent 运行时:不可变黑板(事件溯源)+认领式协调器(市场式调度)+5 个 AutonomousAgent(独立记忆/模型/工具隔离面),SafetyAgent 具备 SAFETY_OVERRIDE 一票否决,与 LangGraph 固定 DAG 形成架构对比 |
| 大模型岗 | 自建 LLM 应用全栈:Hybrid RAG(Chroma+BM25+四因子 Rerank+三级降级,HitRate 0.97)+双模式 Prompt Engineering(normal_chat/support)+Skill 系统(Markdown 驱动)+三层安全护栏(关键词/LLM/规则) |

---

## 技术栈写法(简历用,按层次分组)

不要写 `Python, FastAPI, MySQL, Redis, Chroma, Docker...` 流水账。用以下分组:

```text
AI Agent 层: 事件驱动多智能体、不可变黑板、认领式协调、Agent 隔离面、MCP 协议(SDK stdio)

LLM 与检索: Hybrid RAG、Chroma 向量库、纯 Python BM25、本地 Reranker、Prompt Engineering、
Skill 系统、分层记忆(Redis short-term + MySQL 全量)

后端框架: Python 3.12 / FastAPI 0.115 / Uvicorn / SQLAlchemy(PyMySQL) / Pydantic v2

数据与工程: MySQL / Redis / Docker Compose / Engineering Harness(6 套件) / unittest
```

---

## 面试防御清单(写了就要能答)

简历上每句话都会在面试中被追问。以下是一一对应的防御准备:

| 你写的 | 面试官可能问 | 一句话回答要点 |
|--------|------------|--------------|
| "事件驱动多智能体" | 事件驱动具体指什么? | 12 种事件类型,每轮 append-only,可回溯 |
| "不可变黑板" | 为什么不可变? | 并发安全+审计能力,replace() 返回新板 |
| "认领式协调器" | 和 LangGraph DAG 比? | DAG 缺乏条件跳过能力,认领制天然"双速执行" |
| "Agent 隔离面" | 为什么要隔离? | 安全制衡——Safety 挂不影响 Response |
| "SAFETY_OVERRIDE" | 什么场景触发? | 高风险消息+审查发现回复不安全 |
| "Hybrid RAG" | 为什么不用纯向量? | 心理术语精确匹配(BM25)+语义变体(向量) |
| "三级降级" | 什么时候降级? | 无 API Key/无 GPU/chromadb 未装→自动回退 |
| "HitRate 0.9667" | 怎么测的? | 63 条标注 case + rag_eval/runner.py |
| "Prompt 压缩" | 怎么压缩? | 摘要+窗口裁剪,memoryBrief ≤500 字符 |
| "MCP 双模" | 为什么两种模式? | 生产容错(队列) vs 开发便利(stdio) |
| "工具治理" | 策略怎么定? | 静态策略表:EXCEL 任意/CASE MEDIUM+/ALERT HIGH |
| "Engineering Harness" | Harness 测什么? | 6 套件一键验证全链路,SQLite+Mock AI |
| "Skill 系统" | 为什么 Markdown? | 非开发人员可直接改,不用动 Python |

---

## 英文简历版本

### 项目名称

**MindBridge — Event-Driven Multi-Agent Collaboration Platform**

### 一句话描述

> Independently designed and built an event-driven multi-agent runtime featuring 5 autonomous agents with isolated memory/model/tool permissions, a claim-based coordinator with an immutable event-sourced blackboard, hybrid RAG (Chroma vector + pure Python BM25), and a three-layer risk assessment hard-guard.

### 核心术语对照

| 中文 | English |
|------|---------|
| 事件驱动多智能体协作 | Event-Driven Multi-Agent Collaboration |
| 不可变黑板(事件溯源) | Immutable Blackboard (Event Sourcing) |
| 认领式协调器 | Claim-Based Coordinator |
| Agent 隔离面 | Agent Isolation Surface |
| 安全超控 | SAFETY_OVERRIDE |
| 混合检索 | Hybrid Retrieval |
| 三级自动降级 | Three-Level Graceful Degradation |
| 硬守卫(关键词直判) | Hard Guard (Keyword Direct Judgment) |
| 启发式回退 | Heuristic Fallback |
| 异步工具队列 | Asynchronous Tool Queue |
| 死信队列 | Dead Letter Queue |
| 滑动窗口限流 | Sliding Window Rate Limiting |
| 工具治理(策略+审计) | Tool Governance (Policy + Audit) |
| 双模式 Prompt 组装 | Dual-Mode Prompt Engineering |
| 分层记忆管理 | Tiered Memory Management |
| 工程自检 Harness | Engineering Self-Check Harness |
| 进程隔离(MCP 子进程) | Process Isolation (MCP Subprocess) |

---

## 不要这样写(反面案例)

| 错误写法 | 问题 | 正确写法 |
|---------|------|---------|
| "使用 Python + FastAPI + MySQL + Redis 开发了一个聊天系统" | 流水账 | 按层次分组 + 每层核心词 |
| "实现了多智能体系统" | 堆名词,没说怎么做的 | "5 Agent 通过不可变黑板+认领式协调器协作" |
| "提升了系统性能和安全性" | 零量化 | "HitRate 0.97" 或 "约 30% 输入零 LLM 调用" |
| "精通分布式/高并发" | 明显包装过度,一问就穿 | 写真实的:"单体+异步解耦,工具与流式分离" |
| "使用 Kafka/RabbitMQ" | 实际是 DB 队列,捏造即暴雷 | "MySQL 队列表+守护线程,校园量级 YAGNI" |
| "深度学习模型微调" | 你只是 Ollama 加载 GGUF | "接入本地微调模型(Ollama + GGUF)" |

---

**文件索引**

| 文件 | 用途 |
|------|------|
| [01_ROADMAP.md](./01_ROADMAP.md) | 学习路线图 |
| [02_PROJECT_QNA.md](./02_PROJECT_QNA.md) | 30 道项目面试问答 |
| [03_RESUME.md](./03_RESUME.md) | 本文档(简历包装) |
| [04_AGENT_INTERVIEW.md](./04_AGENT_INTERVIEW.md) | AI Agent 岗位面试题 |
| [../tech/01_ARCHITECTURE.md](../tech/01_ARCHITECTURE.md) | 系统架构 |
| [../tech/02_AGENT_RUNTIME.md](../tech/02_AGENT_RUNTIME.md) | Agent 运行时(面试核心) |
| [../tech/03_RAG_AND_KNOWLEDGE.md](../tech/03_RAG_AND_KNOWLEDGE.md) | RAG 混合检索 |
