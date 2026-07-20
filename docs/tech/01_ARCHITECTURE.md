# 系统架构文档

> **定位**: 面试开场文档，面试官从"这个项目做了什么、怎么做"开始问。阐述系统全景与分层设计，不深入智能体运行时（详见 02）。
> **面试权重**: 30%（投简历/一面必读，二期/终面侧重 02）

---

## 1. 项目定位与业务场景

### 价值主张

MindBridge Python 是一个面向**校园心理健康支持**的 FastAPI 服务。目标用户是高校学生（前端聊天）和辅导员/心理咨询师（后台看板）。核心功能：

| 角色 | 职责 | 能力 |
|------|------|------|
| **学生** | 文字聊天、倾诉情绪、求助 | SSE 流式对话，心理知识 RAG，危机识别（不暴露后台评分） |
| **管理员** | 查看报告、案例、台账、告警 | Excel 台账导出，邮件预警，工具队列看板，会话追溯 |

### 为什么需要多智能体而非单体 LLM

单 LLM 聊天的三个致命缺陷：

```
单 LLM:  学生输入 → LLM 直接回答
          ↓ 问题 ↓
  1. 幻觉: 面对 "我想自杀" 可能给出鸡汤式鼓励，缺少安全硬守卫
  2. 视角单一: 不会同时查历史记忆 + RAG 知识 + 风险评估，回答片面
  3. 不可审计: 出事了不知道谁决定回复了什么
```

多智能体拆分 5 个独立角色（Understanding / Safety / Context / Response / Coordinator），每个有独立 prompt、独立记忆、独立模型，互相制衡，事故事件可追溯。

---

## 2. 系统全景图

```
                          ┌─────────────────────────────────────────────────────────────┐
                          │                      数据闭环                                  │
                          │  MySQL ── 全量聊天 + 报告 + 案例 + 台账 + trace               │
                          │  Redis ── 短期记忆 (24h TTL, 宕不阻断聊天)                    │
                          │  Chroma ── 向量索引, BM25 本地降级                           │
                          │  Excel ── data/mindbridge-risk-ledger.xlsx (openpyxl)        │
                          └─────────────────────────────────────────────────────────────┘
                                                   ↕
  ┌──────┐   HTTP    ┌──────────┐   call   ┌────────────────────┐   run()   ┌──────────────────────────┐
  │ 浏览器 │ ──────→  │ FastAPI   │ ──────→  │ MindBridgeAgentHarness │ ────────→ │ EventDrivenAgentRuntime  │
  │(学生/ │ ←─ SSE ─ │ (routes.py│ ← sync  │ (harness.py:63)       │ ← sync   │ (event_driven_runtime.   │
  │ 管理员)│          │  单一路由) │          │  编排: 脱敏/报存/工具计划 │          │  py:52) → Coordinator   │
  └──────┘           └──────────┘          └────────────────────┘           │   + 5 个 AutonomousAgent │
                            │                                               └──────────────────────────┘
                            │ 旁路启动
              ┌─ FastAPI startup ─────────────────────┐
              │  create_schema() → seed_data()        │
              │  ToolQueueWorker.start() 守护线程      │  ← 工具执行 (Excel/邮件)
              │  MCP 子进程 stdio (tool_queue 关闭时)  │
              └───────────────────────────────────────┘
```

**SSE 事件序列**：`meta(sessionId)` → `token*`(逐 token) → `done`。工具派发在流式**之后**，不阻塞学生看到回复。这是 README 明确记载的有意设计（`app/services/chat.py:26-44`）。

---

## 3. 分层架构详解（6 层）

### 3.1 接入层：单一 APIRouter + Basic Auth

`app/api/routes.py` — 全部路由在一个 `APIRouter` 上，**不走 `/api` 前缀**（路由路径自带 `/api/...`、`/actuator/health`）。静态文件 `StaticFiles(html=True)` 挂在 `/`，注册在 router **之后**，因此 API 优先匹配（`app/main.py:42-44`）。

**鉴权**：HTTP Basic Auth（`app/core/security.py:33-38`），从 header 解码 `base64(username:password)`。中间件只有一个 `no_cache_frontend_assets`（`app/main.py:16-22`），对 `/`、`*.html`、`*.js`、`*.css` 设 `Cache-Control: no-store`。

路由清单（共 19 个）：

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| GET | `/actuator/health` | 无 | 健康检查 |
| GET | `/api/profile` | user | 当前用户信息 |
| POST | `/api/chat/stream` | user(非admin) | 流式聊天 |
| GET | `/api/agent/status` | user | 智能体状态 |
| GET | `/api/reports/me` | user | 我的报告 |
| GET | `/api/admin/*` (12 个) | admin | 后台看板 |

**设计考量**：为什么不用 JWT/Cookie？
- 校园内网场景，demo 定位，简单优先
- Basic Auth 无状态，无需 token 刷新逻辑
- 前端原生 HTML/JS，`Authorization` 头直传

### 3.2 编排层：MindBridgeAgentHarness

`app/agents/harness.py` — 定位是 HTTP 层与 Agent Runtime 之间的**薄编排层**：

```
harness.run(user, request) 完成（同步阻塞）：
  1. PrivacySanitizer 脱敏 → model_input
  2. 解析/创建 ChatSession (public_id 幂等)
  3. create_agent_runtime().run() → AgentRunResult
  4. 保存用户消息入 MySQL + Redis
  5. 创建 PsychologicalReport (非 CHAT 时)
  6. 保存 AgentRunTrace (完整事件链)
  7. 生成 AgentToolPlan (报告/案例/告警)
  8. 返回 AgentHarnessOutcome
```

**为什么 `harness.run()` 同步阻塞？**

| 考量 | 说明 |
|------|------|
| **业务正确性** | Agent 协作轮次本身是同步推进的，每一轮依赖上一轮的黑板状态，异步化无收益 |
| **设计简约** | 阻塞在 `stream_chat` 开头完成，后续 SSE 流式不受影响 |
| **性能** | 单次 Agent run 通常在几百毫秒内完成（关键词命中硬路径时极快），不构成瓶颈 |

### 3.3 智能体运行时（点到即止）

`app/agents/event_driven_runtime.py` — 唯一实现的多智能体框架。5 个自治智能体 + 1 个认领式协调器，通过不可变黑板 CollaborationBlackboard 通信。

- **框架名**: `event_driven_multi_agent`（别名 `multi_agent` / `actors`）
- **调度**: 认领制 claim-based，非固定 DAG
- **预算**: 最多 8 轮 / 每轮 4 认领 / 每个 Agent 最多 3 次认领

**注意**：`.env.example` 默认 `AGENT_FRAMEWORK=langgraph` 是**历史值**，`agent_framework_status()` 会返回 `fallback=true`，但运行时始终走事件驱动（`app/agents/factory.py:19-28`）。

详见 `docs/tech/02_AGENT_RUNTIME.md`。

### 3.4 服务层（7 个核心服务）

| 服务 | 文件 | 职责 |
|------|------|------|
| `AiClient` | `app/services/ai.py` | 统一 LLM 客户端，支持 3 种 provider：mock（关键词驱动的 canned 回复）/ ollama（本地 GGUF）/ openai（兼容 API） |
| `KnowledgeService` | `app/services/knowledge.py` | 混合检索 RAG：Chroma 向量候选 + 本地 BM25 + 加权融合 + local rerank |
| `PsychologicalAssessmentService` | `app/services/assessment.py` | 三层风险评估：硬关键词 → LLM JSON → 启发式回退 |
| `ToolOrchestrationService` | `app/services/tools.py` | Excel 台账写入（openpyxl + 进程级锁）+ RiskCase 创建（幂等）+ 邮件预警 |
| `ToolQueueService` + `ToolQueueWorker` | `app/services/tool_queue.py` | 异步工具队列，守护线程 poll PENDING，依赖就绪后执行，超限入死信 |
| `RedisShortTermMemoryStore` | `app/services/memory.py` | 短期上下文（40 条 / 24h TTL），Redis 宕不阻断聊天 |
| `PrivacySanitizer` | `app/services/privacy.py` | 入 prompt / 持久化前正则脱敏（电话/邮箱/身份证） |

**工具双模**：
- 默认：异步队列（`TOOL_QUEUE_ENABLED=true`，守护线程轮询）
- 备选：`MindBridgeMcpToolClient` 子进程 stdio 启动 `app/mcp_tools/server.py`（FastMCP 6 个工具），直调同套 `ToolOrchestrationService`

### 3.5 数据层：12 张表 + 无仓储模式

`app/models/entities.py` — 12 个 SQLAlchemy ORM 实体，`Base = DeclarativeBase`（`app/core/database.py:7`）。

```
UserAccount  ──→ ChatSession  ──→ ChatMessage
                    │
                    ├──→ PsychologicalReport ──→ RiskCase ──→ CaseNote
                    │         │
                    │         ├──→ AlertRecord
                    │         ├──→ ExcelRecord
                    │         └──→ ToolJob ──→ DeadLetterRecord
                    │
                    └──→ AgentRunTrace
                              └──→ ToolAuditRecord

KnowledgeChunk (独立, 无外键)
```

**无仓储模式**：服务直接调 `self.db.query(Model)`，无 Repository 抽象层。理由：项目规模小（单数据源），中间抽象层增加理解成本，利不大于弊。

**MySQL + Redis 分工**：

| 存储 | 内容 | TTL |
|------|------|-----|
| MySQL | 全量聊天、报告、案例、台账、trace、审计 | 永久 |
| Redis | 短期上下文 (key=`mindbridge:short-term-memory:{session_public_id}`) | 24h |
| Redis | Agent 私有记忆 (key=`agent:{name}:{session_id}`) | 24h |

### 3.6 横切关注点

**配置**：`app/core/config.py` — `pydantic-settings`，读 `.env`，`@lru_cache` 缓存（`app/core/config.py:92`）。harness 改 env 后需 `get_settings.cache_clear()`。

**密码**：SHA-256 无盐（`app/core/security.py:13-14`），demo 级。种子账号 `student/student123`，`admin/admin123`。

**Bootstrap**：`@app.on_event("startup")` 调用 `create_schema()`（`Base.metadata.create_all()`，**无 Alembic**）+ `seed_data()`（创建种子用户 + 11 篇心理常识同步入库）。

---

## 4. 核心数据流：一个完整请求的生命周期

```
POST /api/chat/stream {"message":"我最近压力很大,睡不着","sessionId":"abc123"}

┌──── Step 1: HTTP 鉴权 ───────────────────────────────────────────────────────┐
│ routes.py:39-42                                                              │
│ 1. Depends(current_user) → Basic Auth 解 base64 → 查 DB → 验证 SHA-256       │
│ 2. 拒绝 ROLE_ADMIN 会话 (只有学生能聊天)                                       │
│ 3. Depends(get_db) → 注入 Session                                             │
└──────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌──── Step 2: 编排执行 ─────────────────────────────────────────────────────────┐
│ chat.py:26 — outcome = self.agent_harness.run(user, request)                  │
│                                                                               │
│ harness.py:63 — 同步执行以下:                                                  │
│   1. PrivacySanitizer.sanitize() — 脱敏电话/邮箱/身份证                        │
│   2. _resolve_session() — 按 public_id 查找或新建 ChatSession                  │
│   3. factory.create_agent_runtime().run() → AgentRunResult (↓ Step 3/4)       │
│   4. save_message(USER) — 写 MySQL + Redis                                    │
│   5. _create_report() — 非 CHAT 时写 PsychologicalReport                       │
│   6. AgentTraceService.save_run() — 完整事件链 + steps + knowledge             │
│   7. 返回 AgentHarnessOutcome                                                 │
└──────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌──── Step 3: 智能体协作 ──────────────────────────────────────────────────────┐
│ EventDrivenAgentRuntimeService.run()                                         │
│                                                                              │
│ 创建 CollaborationBlackboard(turn_id, user_input, model_input)                │
│ 创建 CoordinatorAgent + 4 个 Worker Agent                                    │
│                                                                              │
│ Coordinator.run(board) - 核心循环:                                            │
│   Round 1: _ensure_root_task                                                │
│            _derive_missing_work → 创建 task:understand + task:assess-safety   │
│            UnderstandingAgent.claim → publish intent=CONSULT                 │
│            SafetyAgent.claim → publish risk=LOW                              │
│            _derive_missing_work → 创建 task:gather-context                   │
│   Round 2: ContextAgent.claim → publish context (memory_brief, RAG, skills)  │
│            _derive_missing_work → 创建 task:propose-response                 │
│   Round 3: ResponseAgent.claim → publish response_proposal                   │
│            _derive_missing_work → 创建 task:review-response                  │
│            SafetyAgent.claim → publish safety_review (approved=true)         │
│            _try_accept_final → FINAL_ACCEPTED                                │
│                                                                              │
│   → AgentRunResult(intent, risk_level, response_messages, steps, events...)   │
└──────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌──── Step 4: SSE 流式输出 ─────────────────────────────────────────────────────┐
│ chat.py:27-31                                                                 │
│ 1. yield sse("meta", sessionId)                                              │
│ 2. for token in ai.stream(outcome.response_messages):                        │
│      yield sse("token", content=token)                                       │
│    save_assistant_message() — 写 MySQL + Redis                                │
└──────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌──── Step 5: 工具派发 (流式后,不阻塞学生) ──────────────────────────────────────┐
│ chat.py:34-44                                                                 │
│ await harness.dispatch_tools(outcome.tool_plan)                               │
│   失败仅记日志,不抛给学生                                                      │
└──────────────────────────────────────────────────────────────────────────────┘
                                     ↓
┌──── Step 6: 后处理 ───────────────────────────────────────────────────────────┐
│ yield sse("done", sessionId)                                                  │
│                                                                               │
│ 工具队列守护线程 (异步):                                                       │
│   ToolQueueWorker poll PENDING → EXCEL_REPORT → CASE_CREATE → ALERT_SEND     │
│   (依赖串联: 告警等待案例创建完毕)                                              │
│   超 max_attempts 进 dead_letter_records                                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 设计决策与权衡

### 5.1 为什么不用 LangChain / LangGraph

| 维度 | LangChain/LangGraph | MindBridge 自研 |
|------|---------------------|-----------------|
| **控制粒度** | 框架约束多，定制需要 hack | 完全自主：5 个 Agent 协议 + 认领式协调器 |
| **调试** | trace 难读，中间状态不透明 | 不可变黑板 + 事件溯源，每步可回放 |
| **隔离** | Agent 间共享 LLM/memory | 每个 Agent 独立模型 profile、私有 Redis key、独立 tools 列表 |
| **学习成本** | 高（框架 API 频繁变动） | 3 个 dataclass + 1 个 Protocol = 全部接口 |

Trade-off：自研框架的开发成本 vs 灵活性。选灵活性，因为心理健康场景对安全隔离有强要求。

### 5.2 为什么 Agent Runtime 同步阻塞

这是明确定义的设计决策（README 记载）。Agent 协作本质是状态机：每轮依赖上轮黑板状态。在单次 HTTP 请求的上下文中，同步顺序执行是最直接的建模。

**代价**：单个请求阻塞期间 FastAPI worker 不就绪其他请求。缓解方式：Uvicorn 多 worker，单次 Agent run <500ms。

### 5.3 为什么无 Alembic 迁移

`Base.metadata.create_all()` 启动即建表。理由：
- 项目处于早期迭代，schema 频繁变更
- 单实例部署，无多环境迁移同步需求
- CI 用 SQLite（自动创建），零配置
- 如果生产化需要迁移，可随时引入 Alembic

### 5.4 为什么 unittest 而非 pytest

CI 只装 `requirements.txt`，其中无 pytest。`python -m unittest discover -s tests` 是标准库，零依赖。CI 只有 `compileall` + `unittest` 两步，偏好简单。

### 5.5 为什么 Redis 宕机不阻断聊天

`RedisShortTermMemoryStore` 所有操作 try-except 包裹，失败返回空列表。短期记忆丢失退化为"无历史上下文"，AI 仍能回答但不带历史。MySQL 存全量消息保证数据持久性。

### 5.6 为什么 SHA-256 无盐

`app/core/security.py:13` — `hashlib.sha256(password).hexdigest()` 无盐、无迭代。这是**显式的 demo 级设计**，不可生产使用。如果需要生产化，需引入 bcrypt/scrypt + salt。

---

## 6. 技术栈全景表

| 组件 | 用途 | 版本/说明 |
|------|------|-----------|
| Python | 语言 | 3.12 |
| FastAPI | Web 框架 | 0.115.6 (`app/main.py`) |
| Uvicorn | ASGI 服务器 | 单 worker 多进程 |
| SQLAlchemy | ORM | 2.0+, PyMySQL 驱动 |
| Redis | 短期记忆 | 7.2, `redis` 库 |
| Chroma | 向量检索 | 嵌入式, `chromadb` |
| openpyxl | Excel 台账 | 进程级锁 |
| pypdf | PDF 解析 | 知识摄入 |
| httpx | HTTP 客户端 | AI 请求 + MCP 通信 |
| mcp | MCP 工具服务 | FastMCP stdio/SSE |
| Pydantic | 配置 + DTO | pydantic-settings `.env` |

AI Provider 三模式：
| 模式 | 说明 | 场景 |
|------|------|------|
| `mock` | 关键词驱动 canned 回复, 不调 LLM | CI / harness / 无外网 |
| `ollama` | 本地 GGUF `mindbridge-qwen2.5-7b-ft` | 本地开发 |
| `openai` | 兼容 API (`gpt-4o-mini` + `text-embedding-3-small`) | 生产 |

---

## 7. 项目结构树

```
mindbridge-py/
├── app/
│   ├── main.py                          # FastAPI create_app, startup/shutdown, StaticFiles
│   ├── __init__.py
│   ├── api/
│   │   ├── routes.py                    # 单一 APIRouter, 19 个路由, Basic Auth
│   │   └── __init__.py
│   ├── agents/
│   │   ├── autonomous.py                # 5 个 AutonomousAgent 实现 (Und/Safe/Ctx/Resp/Coord)
│   │   ├── coordinator.py               # EventDrivenCoordinator: 认领 + 预算 + 终态判定
│   │   ├── events.py                    # 不可变黑板 + AgentTask/Message/Artifact/Event
│   │   ├── event_driven_runtime.py      # EventDrivenAgentRuntimeService: run + to_result
│   │   ├── factory.py                   # create_agent_runtime + agent_framework_status
│   │   ├── harness.py                   # MindBridgeAgentHarness: 编排 + 报告 + trace + tool plan
│   │   ├── registry.py                  # AgentRegistry + AgentProfile + AutonomousAgent Protocol
│   │   ├── result.py                    # AgentRunResult + AgentStep
│   │   └── __init__.py
│   ├── core/
│   │   ├── config.py                    # Settings (pydantic-settings, 50+ 配置项, @lru_cache)
│   │   ├── database.py                  # SQLAlchemy engine + SessionLocal + get_db
│   │   ├── security.py                  # SHA-256 无盐密码 + Basic Auth + current_user/require_admin
│   │   ├── bootstrap.py                 # create_schema(Base.create_all) + seed_data
│   │   └── enums.py                     # IntentType/RiskLevel/EmotionLabel/ToolJobStatus 等
│   ├── models/
│   │   ├── entities.py                  # 12 ORM 实体 (User → Session → Message → Report → ...)
│   │   └── __init__.py
│   ├── schemas/
│   │   ├── dtos.py                      # Pydantic DTO: ChatRequest, ChatStreamEvent, KnowledgeIngest
│   │   └── __init__.py
│   ├── services/
│   │   ├── ai.py                        # AiClient + PromptTemplates + 关键词常量
│   │   ├── agent_models.py              # AgentModelRegistry: 每个 Agent 独立模型 profile
│   │   ├── assessment.py                # PsychologicalAssessmentService + heuristic fallback
│   │   ├── chat.py                      # ChatService: stream_chat (SSE yield)
│   │   ├── knowledge.py                 # KnowledgeService: 混合检索 RAG (Chroma + BM25)
│   │   ├── mcp_client.py                # MindBridgeMcpToolClient: 子进程 stdio MCP
│   │   ├── memory.py                    # RedisShortTermMemoryStore (24h TTL, 降级容忍)
│   │   ├── model_assets.py              # finetuned_model_status
│   │   ├── privacy.py                   # PrivacySanitizer: 电话/邮箱/身份证脱敏
│   │   ├── report.py                    # ReportService: 后台看板查询 (报告/案例/台账/告警/trace)
│   │   ├── skills.py                    # MindBridgeSkillRegistry + MindBridgeSkillLibrary
│   │   ├── tool_governance.py           # ToolPolicyRegistry + ToolGovernanceService
│   │   ├── tool_queue.py                # ToolQueueService + ToolQueueWorker 守护线程
│   │   ├── tools.py                     # ToolOrchestrationService: Excel + Case + Alert
│   │   ├── trace.py                     # AgentTraceService: AgentRunTrace 持久化
│   │   └── vector_store.py              # ChromaKnowledgeStore + BM25Scorer
│   ├── mcp_tools/
│   │   ├── server.py                    # FastMCP 6 工具服务 (stdio)
│   │   └── __init__.py
│   ├── harness/
│   │   ├── runner.py                    # 工程自检 Harness (模拟 AI + SQLite + 无向量)
│   │   └── __init__.py
│   ├── rag_eval/
│   │   ├── runner.py                    # RAG 评测 runner
│   │   └── __init__.py
│   ├── knowledge/                       # 11 篇内置心理常识 .md 文件
│   └── static/                          # 原生 HTML/CSS/JS 前端
├── skills/                              # 7 个 YAML frontmatter skill 定义
├── tests/
│   ├── test_event_driven_multi_agent.py # 多智能体运行时测试
│   ├── test_memory_compaction.py        # 记忆压缩测试
│   ├── test_privacy_and_assessment.py   # 隐私 + 评估测试 (含硬路径验证)
│   ├── test_skills.py                   # 技能系统测试
│   └── test_tool_governance.py          # 工具治理测试
├── scripts/
├── models/mindbridge-qwen2.5-7b-ft/    # Modelfile (GGUF 不入库)
├── docker-compose.yml                   # MySQL 8.0:13306 + Redis 7.2:16379 + app:8080
├── Dockerfile
├── requirements.txt
├── .env.example
└── CLAUDE.md
```

---

## 附录：快速参考

- **启动**: `uvicorn app.main:app --host 127.0.0.1 --port 8080`
- **测试**: `python -m unittest discover -s tests`
- **Harness**: `python -m app.harness.runner` (模拟 AI + SQLite)
- **Docker**: `docker compose up -d --build`
- **种子账号**: `student/student123`, `admin/admin123`
- **降级**: Redis 宕 → 无历史；Chroma 不可用/无 OPENAI_API_KEY → BM25 本地；LLM 不可用 → heuristic fallback
