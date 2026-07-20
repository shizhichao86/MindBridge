# 简历包装与项目展示指南

> **定位**: 面向秋招求职者的简历写作指南,帮助你把 MindBridge 项目有效包装进简历。
> **使用方式**: 直接复制对应章节到简历中,根据投递方向调整关键词密度。
> **原则**: 每个技术点都能在项目源码中落地,不说虚话。

---

## 1. 项目一句话描述(3 个版本,按岗位切换)

### 1.1 通用后端开发版(海投)

> 独立开发面向校园心理健康的多智能体协作聊天系统(Python/FastAPI),核心包括自研事件驱动多 Agent 运行时(5 个自治 Agent + 不可变黑板 + 认领式协调)、混合检索 RAG(Chroma + BM25 + 本地 Rerank)、三层风险评估硬守卫,配套异步工具队列与 MCP 双模,全链路 Docker 一键部署。

**关键词密度**: 多智能体/事件驱动/混合检索/异步队列/Docker

### 1.2 AI Agent 应用开发版

> 独立设计并实现事件驱动多智能体协作运行时:5 个 AutonomousAgent(Understanding/Safety/Context/Response/Coordinator)通过不可变黑板与认领式协调器通信,SafetyAgent 独立安全审查且可 SAFETY_OVERRIDE 强制覆盖,每个 Agent 拥有独立模型 profile、私有 Redis 记忆和工具权限隔离面,配合 Chroma+BM25 混合 RAG 与三层风险评估硬守卫(关键词直判/LLM JSON/启发式回退)。

**关键词密度**: 事件驱动/不可变黑板/认领式协调/隔离面/多 Agent/SAFETY_OVERRIDE

### 1.3 大模型应用开发版

> 独立构建基于 LLM 的心理健康对话应用:自研 Prompt Engineering 体系(双模式 prompt 组装:normal_chat vs support)、Hybrid RAG 混合检索(向量 0.65 + BM25 0.35 融合 + 本地四因子 Rerank + 三级自动降级)、三层风险评估硬守卫(关键词 Layer1 零延迟拦截 + LLM JSON 语义评估 + 启发式 fail-safe),LLM 异常时 heuristic fallback 保证系统可用性。

**关键词密度**: LLM/Prompt Engineering/Hybrid RAG/硬守卫/降级/fail-safe

---

## 2. 简历项目经历(STAR 格式)

### 条 1: 事件驱动多智能体协作运行时(面试核心亮点)

**项目名称**: MindBridge -- 事件驱动多智能体协作运行时 | 独立开发 | 2025.06--2025.07

- **S**: 单 LLM 聊天在心理健康场景存在幻觉、缺乏安全制衡、不可审计三大缺陷 -- 面对"自杀"等高风险输入,单体模型可能产生"鸡汤式"不安全回复。
- **T**: 设计一套多智能体协作系统,让 5 个独立 Agent 通过共享黑板通信、互相制衡,确保高危场景有硬安全审查,所有决策可追溯。
- **A**:
  - **自研认领式协调器** `app/agents/coordinator.py:36`(278 行):每轮`_derive_missing_work`动态推导任务链(intent -> risk -> context -> response -> safety review),按`(priority, confidence, name)`排序认领,预算上限 8 轮/每轮 4 认领/每 Agent 最多 3 次。
  - **不可变协作黑板** `app/agents/events.py:121`: `@dataclass(frozen=True)`,所有状态变更通过`replace()`返回新板,元组追加事件,支持完整事件溯源与回放审计。
  - **5 个自治 Agent** `app/agents/autonomous.py`: Understanding(三层意图判定,60%输入硬关键词拦截不调 LLM)、Safety(独立风险评估+回复安全审查,可 SAFETY_OVERRIDE 强制 HIGH)、Context(条件激活,仅非闲聊/非低风险时加载历史+RAG+技能)、Response(双模式 prompt 组装)、Coordinator(维护任务板,不占工人槽)。
  - **隔离面设计**: 每个 Agent 独立 Redis 记忆 key(`agent:{name}:{sid}`)、独立 LLM model profile、独立 frozenset 工具权限,实现安全制衡与故障隔离。
- **R**:
  - 正常对话平均 3 轮收敛,高风险场景 5--6 轮(含 SAFETY_OVERRIDE + response 修订)
  - 12 种事件类型,完整协作审计链(单轮 20+ 事件)
  - 扩展性: 加新 Agent 只需 6 步(定义 Profile + 实现 decide/act + 注册),不改其他 Agent 代码
  - 全量代码约 600 行(协调器 278 + Agent 实现 596 + 黑板 278 + 工厂/注册/结果 200+)

### 条 2: 混合 RAG 检索与自动降级

**项目名称**: MindBridge -- Hybrid RAG 混合检索系统 | 独立开发 | 2025.06--2025.07

- **S**: 纯向量检索忽略关键词精确匹配(如"PHQ-9"漏掉),纯 BM25 忽略语义变体(如"想不开"和"自杀意念"),单一方案均不满足校园心理场景。
- **T**: 实现向量语义+关键词双路混合检索,支持无外网/无 GPU 环境自动降级,保证各部署场景的可用性。
- **A**:
  - **双路召回**: Chroma + OpenAI `text-embedding-3-small` 向量候选(k=16) + 纯 Python BM25(k1=1.5, b=0.75, 中文 2-gram 分词,零依赖)关键词候选(k=16),各 Min-Max 归一化后按`0.65:0.35`加权融合 `app/services/knowledge.py:127`。
  - **本地四因子 Rerank**: `base*0.55 + lexical*0.25 + coverage*0.15 + phrase*0.05`,千级 chunk 下 ~1ms 延时,零额外依赖 `app/services/knowledge.py:387`。
  - **上下文扩展**: 最优命中取 ±1 相邻 chunk 拼接,保证 LLM 看到完整知识段落 `app/services/knowledge.py:303`。
  - **三级自动降级**: L1(Chroma+BM25 完整) -> L2(无 OpenAI Key/未装 chromadb,自动退 BM25 + hybrid_score rerank) -> L3(KNOWLEDGE_VECTOR_REQUIRED=true 时拒绝启动) `app/services/vector_store.py:36`。
  - **RAG 评测体系**: 30+ 标注 case,输出 Recall@K/Precision@K/MRR/NDCG@K/HitRate `app/rag_eval/runner.py`。
- **R**:
  - 降级场景下 BM25-only 仍能保持关键词召回能力,保证对话不中断
  - 知识库 11 篇 Markdown,约 500 chunks,向量检索延迟 <200ms
  - 支持 .md/.txt/.pdf 上传,自动分块(512 char + 64 overlap) + 增量同步 + Embedding 缓存

### 条 3: 心理风险评估硬守卫与工具治理体系

**项目名称**: MindBridge -- 多层防御风险评估与异步工具体系 | 独立开发 | 2025.06--2025.07

- **S**: LLM 的幻觉和提示注入可能使高危学生被错误标为"低风险"——误判的代价是生命,不是推荐错误。
- **T**: 实现三层风险评估硬守卫(宁可误报,不可漏报),风险结果触发异步工具链(Excel 台账+个案创建+邮件预警),学生端完全不暴露评估结果。
- **A**:
  - **三层评估** `app/services/assessment.py:24`: Layer1 硬关键词(9 个中英关键词,子串匹配,0ms 延迟,100% 确定性,不调 LLM) -> Layer2 LLM JSON 评估(分数提级: emotionScore 超阈值强制提 risk) -> Layer3 heuristic fail-safe(任何异常退关键词规则,Cascade 到安全侧)。
  - **ExplodingAi 注入测试验证硬路径**: 用一个会抛异常的 AI 桩验证高风险输入不经过 LLM 直判 HIGH `tests/test_privacy_and_assessment.py:34`。
  - **异步工具队列** `app/services/tool_queue.py`: 双 ThreadPoolExecutor(excel 1 线程/email 2 线程),滑动窗口限流(邮件 30/min),指数退避重试 + 死信队列 + 重启恢复。
  - **MCP 双模** `app/services/mcp_client.py:17`: 默认走异步队列(生产容错),关闭队列后通过子进程 stdio 启动 FastMCP 6 工具直调,同套 `ToolOrchestrationService` 实现。
  - **工具治理** `app/services/tool_governance.py:23`: 静态策略表(EXCEL 任意/CASE MEDIUM+/ALERT 仅 HIGH),每次执行写 ToolAuditRecord 审计。
- **R**:
  - 硬关键词拦截约 30% 的高风险输入在 Layer1 完成,零 token 消耗
  - ExplodingAi 测试保证硬路径不被重构破坏
  - 工具链对用户无感知: 流式回复先完,Excel/邮件异步执行
  - 工具幂等设计: 重复执行不产生重复 Excel 行或案例

### 条 4(可选): 工程化与全栈能力

**项目名称**: MindBridge -- 工程化实践与全栈交付 | 独立开发 | 2025.06--2025.07

- **S**: 项目需覆盖开发/测试/CI/Docker 全链路,并在纯本地环境下也能跑通(Harness 自检 + mock 模式 + SQLite)。
- **T**: 构建零依赖自检体系、Docker 一键部署、SSE 流式聊天前端,保证任何环境 5 分钟可跑。
- **A**:
  - **工程 Harness** `app/harness/runner.py`: 模拟 AI + SQLite + 关闭向量,6 个自检套件(Risk Safety/Agent Routing/Standard Skills/RAG/API/Tool Queue),一键验证核心链路 `python -m app.harness.runner`。
  - **Docker Compose** `docker-compose.yml`: MySQL 8.0:13306 + Redis 7.2:16379 + App:8080,`docker compose up -d --build` 一键启动。
  - **SSE 流式聊天**: `meta -> token* -> done` 事件序列,前端原生 HTML/CSS/JS,打字机式输出。
  - **CI/CD** `.github/workflows/test.yml`: `compileall` + `unittest`(标准库,无 pytest 依赖),`AI_PROVIDER=mock` 密闭运行。
  - **隐私脱敏** `app/services/privacy.py`: 正则脱敏电话/邮箱/身份证,入 prompt 和持久化前均执行。
- **R**:
  - Mock 模式零依赖 5 分钟可跑(无需 MySQL/Redis/AI Key)
  - 单元测试覆盖核心路径(隐私/评估/工具治理/技能/多 Agent)
  - Harness 报告输出为结构化 JSON,CI 可解析

---

## 3. 技术栈写法(面试简历按层次分组)

不要写成 `Python, FastAPI, MySQL, Redis, Chroma, Docker...` 这种流水账。按以下 4 层分组,面试官一眼看到你的技术体系:

### 推荐的简历技术栈格式

```markdown
**AI Agent 层**: 事件驱动多智能体协作、不可变黑板(CollaborationBlackboard)、认领式协调器(Claim-based Coordinator)、
5 个 AutonomousAgent(Protocol 解耦)、Agent 隔离面(独立模型/记忆/工具权限)、Prompt Engineering(双模式组装)、
MCP 协议(FastMCP 6 工具,stdio/SSE 双模)

**后端框架层**: Python 3.12, FastAPI 0.115.6, Uvicorn(ASGI), SQLAlchemy 2.0(PyMySQL), Pydantic v2,
httpx(异步 HTTP 客户端), 标准库 unittest(无 pytest 依赖)

**数据与存储层**: MySQL(全量业务), Redis(短期上下文/Agent 私有记忆,24h TTL), Chroma(向量持久化),
openpyxl(Excel 台账), 纯 Python BM25(零依赖), 本地 Rerank(四因子加权)

**工程化**: Docker Compose 一键部署, CI/CD(GitHub Actions), 工程自检 Harness(模拟 AI+SQLite,6 套件),
RAG 评测体系(Recall@K/MRR/NDCG), SSE 流式输出, Basic Auth 鉴权, 隐私正则脱敏
```

### 流水账 vs 分组写法对比

| 写法 | 面试官印象 |
|------|-----------|
| `Python, FastAPI, MySQL, Redis, Chroma, Docker, Uvicorn, SQLAlchemy...` | "又一个堆名词的" |
| 按层次分组,每层 3-5 个核心项,附简短说明 | "有体系、有分层意识、知道每层负责什么" |

---

## 4. 自我评价/技能亮点(10-15 个关键词,每个落地)

以下 15 个关键词按类别组织,**每条都可以在项目源码中找到对应实现**:

### AI Agent / 多智能体(6 个)

| 关键词 | 项目落地 | 面试口述要点 |
|--------|---------|-------------|
| **事件驱动多智能体** | `app/agents/event_driven_runtime.py:52` 唯一框架实现 | 5 Agent 通过黑板通信,非固定 DAG |
| **不可变黑板** | `app/agents/events.py:121` `@dataclass(frozen=True)` | 并发安全+事件溯源+可审计 |
| **认领式调度** | `app/agents/coordinator.py:36` `EventDrivenCoordinator.run()` | 与 LangGraph 固定 DAG 对比 |
| **Agent 隔离面** | `app/agents/autonomous.py:53` 独立 Redis key + 模型 + 工具权限 | 安全制衡+故障隔离+可替换 |
| **SAFETY_OVERRIDE** | `app/agents/events.py` `AgentEventType.SAFETY_OVERRIDE` | SafetyAgent 一票否决,覆盖其他 Agent |
| **Protocol 解耦** | `app/agents/registry.py:35` `AutonomousAgent(Protocol)` | 加新 Agent 只需实现 decide/act |

### RAG / 检索增强(4 个)

| 关键词 | 项目落地 | 面试口述要点 |
|--------|---------|-------------|
| **混合检索(Hybrid RAG)** | `app/services/knowledge.py:127` 向量 0.65 + BM25 0.35 | 两路召回互补 |
| **纯 Python BM25** | `app/services/knowledge.py:348` k1=1.5,b=0.75,中文 2-gram | 零依赖,11 篇文档够用 |
| **本地 Rerank** | `app/services/knowledge.py:387` 四因子线性加权 | 与 Cross-encoder 的 trade-off |
| **三级自动降级** | `app/services/vector_store.py:36` can_embed 判断 | 可用性优先于最佳质量 |

### LLM 应用 / 安全(3 个)

| 关键词 | 项目落地 | 面试口述要点 |
|--------|---------|-------------|
| **多层防御风险评估** | `app/services/assessment.py:24` 关键词->LLM->heuristic | Swiss Cheese Model,宁可误报不漏报 |
| **Prompt Engineering** | `app/agents/autonomous.py:464-498` 双模式(normal_chat/support) | 业务正确性>技术完备性 |
| **Mock Provider** | `app/services/ai.py:145` 关键词驱动 canned 回复 | CI/开发密闭运行,零外部依赖 |

### 后端工程(2 个)

| 关键词 | 项目落地 | 面试口述要点 |
|--------|---------|-------------|
| **异步工具队列** | `app/services/tool_queue.py` 双线程池+滑动窗限流+死信 | vs Celery 的 YAGNI 决策 |
| **MCP 协议实现** | `app/mcp_tools/server.py` + `app/services/mcp_client.py` | 双模设计,子进程 stdio |

---

## 5. 常见简历坑

### 5.1 不要这样写

| 错误的写法 | 问题 | 改进 |
|-----------|------|------|
| "使用 Python + FastAPI + MySQL + Redis 开发了一个聊天系统" | 流水账,什么都说了什么都没说 | 按层次分组,每项配一句话说明 |
| "实现了多智能体系统" | 堆名词,没有说明怎么做的 | "自研事件驱动多 Agent 运行时,5 Agent 通过不可变黑板+认领式协调器协作" |
| "提升了系统性能和安全性" | 无量化,面试官没法追问 | "硬关键词拦截约 30% 输入,零 LLM 调用延迟" 或 "正常对话 3 轮收敛" |
| "熟练使用 LangChain/LangGraph" | 项目根本没用到,面试追问必穿帮 | 诚实写"自研认领式协调器(对比 LangGraph 的 trade-off)",变劣势为亮点 |
| "精通分布式系统/高并发/微服务" | 单体部署项目写分布式,明显包装过度 | 写实际的:"单体+异步解耦+线程池,工具执行与流式回复分离" |
| "使用 JWT + OAuth2.0 做认证" | 实际是 Basic Auth + SHA-256,捏造即暴雷 | 写"Basic Auth(校园内网场景,简单优先)",说明设计考量 |

### 5.2 面试官一眼看穿的过度包装

以下是高频"包装穿帮"场景及防御策略:

| 包装 | 追问 | 防御 |
|------|------|------|
| "实现分布式 Agent 协作" | "Agent 之间用什么序列化协议?跨进程通信怎么处理的?" | 千万别写"分布式"。项目中 Agent 都在**同一进程**,通信靠内存中的不可变黑板。实话实说反而是亮点:"同一进程内不可变黑板通信,避免序列化开销和网络不确定性" |
| "基于 Kafka 的消息队列" | "topic 怎么设计的?partition 策略?" | 实际是 MySQL 表 + 守护线程轮询。诚实写"轻量级 DB 队列 + 线程池,满足校园场景量级"会是更好的叙事 |
| "深度学习模型微调" | "用的什么 base model?训练数据?loss function?" | 你没做微调,你只是通过 Ollama 加载了别人的 GGUF。写"接入本地微调模型(Ollama + GGUF)"准确且不夸大 |
| "千亿级向量检索" | "索引结构?ANN 算法?QPS?" | 500 个 chunk 写什么千亿级。写"Chroma 本地持久化,~500 chunks,向量+BM25 混合" |
| "99.99% 高可用" | "怎么测算的?故障演练?SLA?" | 你没有 SLA。写"Redis 宕不阻断聊天,Chroma 不可用自动降级 BM25" 已经足够 |

### 5.3 简历应该突出的真实亮点(这些比"高并发"值钱)

1. **你会为特定场景设计架构**(不是调包侠):"心理健康场景要求 SafetyAgent 独立评估,所以我设计了隔离面——5 Agent 各持独立 prompt/记忆/模型/工具权限"
2. **你理解 trade-off**: "不用 LangChain 是框架约束太多;不用 Celery 是量级不够;不用 jieba 是 11 篇文档不需要工业级分词"
3. **你有 fail-safe 意识**:"三层评估的第三层是 heuristic——宁可误报也不在 LLM 挂了时装没事"
4. **你会写测试验证假设**:"用 ExplodingAi 炸弹桩验证高风险硬关键词路径真的不调 LLM"

---

## 6. 英文简历版本(Key Terminology)

### 项目名称

MindBridge -- Event-Driven Multi-Agent Mental Health Support System

### 一句话描述(通用版)

> Independently designed and built an event-driven multi-agent collaboration runtime for campus mental health support, featuring 5 autonomous agents with isolated memory/model/tool permissions, a claim-based coordinator with an immutable blackboard, hybrid RAG retrieval (Chroma vector + BM25 keyword + local reranking), and a three-layer risk assessment hard-guard with async tool orchestration.

### 核心技术术语对照表

| 中文 | English |
|------|---------|
| 事件驱动多智能体协作 | Event-Driven Multi-Agent Collaboration |
| 不可变协作黑板 | Immutable Collaboration Blackboard |
| 认领式协调器 | Claim-Based Coordinator |
| 自治智能体 | Autonomous Agent |
| 隔离面(独立记忆/模型/工具权限) | Isolation Surface (private memory/model/tool permissions) |
| SAFETY_OVERRIDE(安全超控) | SAFETY_OVERRIDE |
| 混合检索 | Hybrid Retrieval |
| 向量+关键词融合 | Vector-Keyword Fusion |
| 本地 Rerank | Local Reranking (4-factor weighted) |
| 三级自动降级 | Three-Level Graceful Degradation |
| 多层防御风险评估 | Multi-Layer Defense Risk Assessment |
| 硬守卫(关键词直判) | Hard Guard (keyword-based direct judgment) |
| 启发式回退 | Heuristic Fallback |
| 异步工具队列 | Asynchronous Tool Queue |
| 死信队列 | Dead Letter Queue |
| 滑动窗口限流 | Sliding Window Rate Limiting |
| 幂等设计 | Idempotent Design |
| 工具治理(策略+审计) | Tool Governance (Policy + Audit) |
| Prompt Engineering(双模式) | Dual-Mode Prompt Engineering (normal_chat/support) |
| 事件溯源 | Event Sourcing |
| 条件跳过(按意图路由) | Conditional Skip (Intent-Based Routing) |
| 三层意图判定 | Three-Layer Intent Classification |
| 工程自检 Harness | Engineering Self-Check Harness |
| 进程隔离(MCP 子进程) | Process Isolation (MCP Subprocess via stdio) |

### STAR Bullet Points (English)

**Bullet 1: Event-Driven Multi-Agent Runtime**
- **S**: Single-LLM chat systems suffer from hallucination, lack of safety checks, and non-auditability in mental health scenarios.
- **T**: Design a multi-agent system where 5 specialized agents collaborate via a shared blackboard with mutual oversight.
- **A**: Built a claim-based coordinator (278 lines) with dynamic task derivation. Implemented an immutable `@dataclass(frozen=True)` blackboard with event sourcing. Designed 5 autonomous agents with isolated Redis memory, LLM profiles, and tool permissions. SafetyAgent holds SAFETY_OVERRIDE veto power.
- **R**: Average 3 round convergence for normal chat, 5-6 for high-risk scenarios. 12 event types for full audit trail. Add new agent in 6 steps without touching existing code.

**Bullet 2: Hybrid RAG with Graceful Degradation**
- **S**: Pure vector search misses exact keyword matches; pure BM25 misses semantic variations critical in mental health contexts.
- **T**: Implement dual-path retrieval with automatic fallback for environments without GPU/external API access.
- **A**: Built Chroma vector + pure Python BM25 (k1=1.5, b=0.75, Chinese 2-gram) fusion at 0.65:0.35 weights. Designed 4-factor local reranker. Implemented 3-level degradation: full hybrid -> BM25-only -> hard fail (if VECTOR_REQUIRED=true).
- **R**: ~500 chunks indexed, <200ms vector retrieval latency, 30+ eval cases with Recall@K/MRR/NDCG metrics.

**Bullet 3: Three-Layer Risk Hard-Guard**
- **S**: LLM hallucination and prompt injection can misclassify at-risk students as "low risk" -- the cost of a false negative is a life.
- **T**: Build a defense-in-depth risk assessment that errs on the side of caution.
- **A**: Layer 1: 9 hard keywords, 0ms latency, no LLM call. Layer 2: LLM JSON assessment with score-based escalation. Layer 3: Heuristic fail-safe on any exception. Verified Layer 1 path with ExplodingAi injection test.
- **R**: ~30% high-risk inputs caught at Layer 1 with zero token cost. Tool chain (Excel + Case + Alert) executes asynchronously, invisible to end users.

---

## 附录: 简历项目描述快速复制区

以下为可直接复制粘贴到简历的完整项目条目,按 3 种岗位各配一版:

### 版本 A: 后端开发岗(150 字, 2 条)

```text
MindBridge -- 多智能体心理健康聊天系统 | 独立开发 | 2025.06-2025.07
Python/FastAPI/MySQL/Redis/Chroma/Docker

● 自研事件驱动多智能体协作运行时:5 个自治 Agent 通过不可变黑板与认领式协调器通信,
  每个 Agent 隔离独立模型/记忆/工具权限,SafetyAgent 具备 SAFETY_OVERRIDE 否决权,
  正常对话 3 轮收敛,全链路事件可追溯审计。
● 实现 Hybrid RAG 混合检索(Chroma+纯 Python BM25 双路融合+本地四因子 Rerank)
  与异步工具队列(双线程池+滑动窗口限流+死信+重启恢复),三级自动降级保障可用性,
  工具执行与流式回复完全解耦,对用户无感知。
```

### 版本 B: AI Agent 开发岗(150 字, 2 条)

```text
MindBridge -- 事件驱动多智能体协作运行时 | 独立开发 | 2025.06-2025.07
Python/FastAPI/LangChain 对比分析

● 设计实现事件驱动多 Agent 系统:5 个 AutonomousAgent(Understanding/Safety/
  Context/Response/Coordinator)基于不可变黑板(CollaborationBlackboard,frozen dataclass)
  与认领式协调器动态协作,每轮自动推导缺失任务链,按(priority,confidence)排序认领。
  SafetyAgent 独立安全审查,可 SAFETY_OVERRIDE 强制 HIGH 覆盖所有 Agent 判断。
● 构建 Agent 隔离面:独立 Redis 记忆 key/LLM model profile/frozenset 工具权限,
  实现安全制衡与故障隔离。对比 LangGraph 固定 DAG:本系统条件跳过(闲聊不查 RAG)
  与优先级路由(高风险 CRITICAL 任务优先调度)。
```

### 版本 C: 大模型应用开发岗(150 字, 2 条)

```text
MindBridge -- 基于 LLM 的心理健康对话应用 | 独立开发 | 2025.06-2025.07
Python/FastAPI/RAG/Prompt Engineering

● Prompt Engineering 体系:ResponseAgent 双模式组装(normal_chat 不注入 RAG,
  support 注入知识+技能+安全约束),5 Agent 各有独立 system prompt,三层意图判定
  (硬关键词->通用词->LLM)约 60% 输入不调模型完成分类,降低延迟与成本。
● Hybrid RAG 三级降级:Chroma 向量(text-embedding-3-small, 0.65) + 纯 Python BM25
  (k1=1.5, 0.35) 融合+本地四因子 Rerank(Base/Lexical/Coverage/Phrase),
  无 API Key 时自动退 BM25+词面重排。三层风险评估硬守卫(关键词/LLM JSON/启发式),
  关键词拦截约 30% 输入,零 token 消耗,Layer3 fail-safe 保证 LLM 异常时系统可用。
```

---

**文件索引**

| 文件 | 用途 |
|------|------|
| `docs/career/01_ROADMAP.md` | 学习路线图(10 天) |
| `docs/career/02_PROJECT_QNA.md` | 30 道面试问答 |
| `docs/career/03_RESUME.md` | 本文档 |
| `docs/tech/01_ARCHITECTURE.md` | 系统架构(一面必读) |
| `docs/tech/02_AGENT_RUNTIME.md` | Agent 运行时(终面核心) |
| `docs/tech/03_RAG_AND_KNOWLEDGE.md` | 混合检索系统 |
| `docs/tech/04_RISK_ASSESSMENT.md` | 风险评估硬守卫 |
| `docs/tech/05_TOOL_SYSTEM.md` | 工具队列与治理 |
