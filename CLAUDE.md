# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

MindBridge Python 是面向校园心理健康支持的 FastAPI 服务,核心是一个**事件驱动多智能体协作运行时**(Coordinator / Understanding / Safety / Context / Response 五个自治智能体通过共享黑板认领任务),配合混合检索 RAG、心理风险评估、Excel 台账与邮件预警、MCP 工具服务。学生侧通过 SSE 流式聊天,后台有管理员视角的报告/案例/工具队列看板。

技术栈:Python 3.12 + FastAPI 0.115.6 + Uvicorn + SQLAlchemy/PyMySQL + Redis + Chroma + openpyxl + pypdf + httpx + mcp。前端为原生 HTML/CSS/JS(挂载在 `/`)。

## 常用命令

依赖安装:
```bash
pip install -r requirements.txt
```

本地开发启动(默认 127.0.0.1:8080):
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8080
# 或通过脚本(默认 AI_PROVIDER=ollama):
AI_PROVIDER=ollama ./scripts/run-dev.sh
```

测试(使用标准库 `unittest`,**不要用 pytest** —— requirements.txt 中无 pytest):
```bash
python -m unittest discover -s tests                              # 全量
python -m unittest tests.test_skills                             # 单个文件
python -m unittest tests.test_skills.SkillRegistryTests          # 单个类
python -m unittest tests.test_skills.SkillRegistryTests.test_skill_registry_loads_valid_skill  # 单个方法
```

工程自检 Harness(模拟 AI + SQLite + 关闭向量,6 个套件):
```bash
python -m app.harness.runner    # 输出 target/harness/harness-report.json
```

RAG 评测:
```bash
AI_PROVIDER=mock python -m app.rag_eval.runner    # 输出 target/rag-eval-report.json
```

MCP 工具服务(stdio):
```bash
python -m app.mcp_tools.server
```

Docker 一键启动(mysql:8.0 → 主机 13306、redis:7.2 → 16379、app → 8080):
```bash
docker compose up -d --build
```

Ollama 本地微调模型(需先放置 GGUF):
```bash
./scripts/start-ollama.sh
UPSTREAM_GGUF=/path/to/mindbridge-qwen2.5-7b-ft-q4_k_m.gguf ./scripts/create-finetuned-model.sh
```

默认种子账号(仅 demo 级,密码为 SHA-256 无盐):`student/student123`、`admin/admin123`。

## 高层架构

### 分层与请求流

代码采用**分层 + 模块化**结构,无仓储模式(服务直接查 SQLAlchemy Session):

1. **HTTP 层** — `app/api/routes.py` 单一 `APIRouter`,路由只做 Basic Auth 鉴权 + 委派,无 `/api` 前缀(路由自带完整路径如 `/api/...`、`/actuator/health`)。静态文件 `app/static/` 用 `StaticFiles(html=True)` 挂在 `/`,注册在 router 之后,因此 API 路由优先。
2. **业务编排层** — `app/agents/harness.py` 的 `MindBridgeAgentHarness`,把会话/报告/trace/工具计划的生成包在运行时之外,让 HTTP 层保持薄。`/api/chat/stream` 是 `async def`,`ChatService.stream_chat` 是 async 生成器,但 `harness.run()` 是**同步阻塞**(DB/Redis/智能体工作在流式开始前完成)——这是 README 明确记载的有意设计。
3. **智能体运行时** — `app/agents/`(见下)。
4. **服务层** — `app/services/`(AI、知识/RAG、记忆、评估、工具、队列、技能、trace、报告等)。
5. **数据层** — `app/models/entities.py`(12 个 SQLAlchemy ORM 实体)、`app/schemas/dtos.py`(Pydantic DTO)、`app/core/database.py`(会话)。
6. **横切** — `app/core/`(config、security、bootstrap、enums)。

SSE 事件序列:`meta`(sessionId)→ `token`*(内容分片)→ `done`。工具派发与流式**解耦**:先流式回完学生消息,再 `await dispatch_tools(tool_plan)`,失败仅记日志、不暴露给学生。

### 事件驱动多智能体运行时(架构核心)

入口 `app/agents/event_driven_runtime.py::EventDrivenAgentRuntimeService` → `create_agent_runtime()`(`app/agents/factory.py`)。

- **不可变黑板** `CollaborationBlackboard`(`app/agents/events.py`,`@dataclass(frozen=True)`,每次变更通过 `replace(...)` 返回新板)——事件溯源的只追加协作日志,承载 tasks/messages/artifacts/events/final_artifact_id。
- **认领式协调器** `EventDrivenCoordinator.run(board)`(`app/agents/coordinator.py`):每轮 `_derive_missing_work`(确保 intent→risk→(context if consult/risk)→response_proposal→safety_review 链路任务存在)→ `_try_accept_final`(响应+审核通过+置信度≥`agent_final_acceptance_min_confidence=0.6` 时终态接受)→ 按 `(priority, confidence, name)` 选候选认领。预算:`agent_max_rounds=8`、`agent_max_claims_per_round=4`、`agent_max_claims_per_agent=3`。高风险硬关键词把任务优先级升为 CRITICAL。
- **五个智能体**(`app/agents/autonomous.py`,均实现 `AutonomousAgent` Protocol 的 `decide/act`):
  - `CoordinatorAgent`(COORDINATION):建根任务、不占工人槽、记忆接受态。
  - `UnderstandingAgent`(UNDERSTANDING):判意图 `CHAT/CONSULT/RISK`(关键词 + LLM),发布 `intent` artifact。
  - `SafetyAgent`(SAFETY):独立风险评估(`PsychologicalAssessmentService`),审核 `response_proposal`,可发 `SAFETY_OVERRIDE` 强制 `RISK/HIGH`。
  - `ContextAgent`(CONTEXT):仅在 `intent != CHAT or risk != LOW` 时加载 Redis/MySQL 历史、压缩、重写查询、RAG 检索、组装技能上下文。
  - `ResponseAgent`(RESPONSE):据黑板 artifact 组装候选 prompt(`normal_chat` vs `support` 模式)。
- 每个智能体有**独立隔离面**:私有 Redis 记忆(`agent:{name}:{session_id}`)、独立模型 profile(`AgentModelRegistry`)、独立工具权限 frozenset、独立 system prompt。

共享返回类型 `AgentRunResult`(`app/agents/result.py`),`max_steps=8`。

`agent_framework_status()` 报告 `active=event_driven_multi_agent`,非别名(`event_driven_multi_agent/multi_agent/actors`)则 `fallback=True`——**实际只实现了事件驱动这一种框架**(`.env.example` 默认 `AGENT_FRAMEWORK=langgraph` 是历史值,运行时仍走事件驱动)。

### RAG 混合检索与降级

`app/services/knowledge.py::KnowledgeService` + `app/services/vector_store.py::ChromaKnowledgeStore`:

1. 向量候选:Chroma + OpenAI `text-embedding-3-small` 嵌入(`candidate_k=16`)。
2. BM25 候选:纯 Python BM25(k1=1.5, b=0.75,中文分词 + 2-gram)。
3. 融合:各自 min-max 归一后 `vector*0.65 + bm25*0.35`,再用本地 `_rerank`(`hybrid_score*0.25 + coverage*0.15 + phrase*0.05 + base*0.55`)。
4. 上下文扩展:最佳命中取相邻 chunk 拼接。

**降级是自动的**:`can_embed=False`(未配 `OPENAI_API_KEY`/`chromadb` 未装/`KNOWLEDGE_VECTOR_ENABLED=false`)→ 退回本地 BM25 + `hybrid_score` 重排;`KNOWLEDGE_VECTOR_REQUIRED=true` 时则直接报错。

启动时 `app/core/bootstrap.py::seed_data()` 会把 `app/knowledge/*.md`(11 篇内置心理常识)同步入库,内容变更时按当前分块规则刷新。

### 心理风险评估的硬守卫

`app/services/assessment.py::PsychologicalAssessmentService.assess()`:

1. 命中高风险关键词 → 立即 `HIGH/4.0/HIGH/0.95`,**不调用模型**(`test_privacy_and_assessment.py` 用注入会抛 `AssertionError` 的桩验证此硬路径)。
2. 否则 LLM 严格 JSON 评估,分数超阈值则提升风险等级。
3. 任意异常 → 启发式回退(consult 信号 + 抑郁词 → MEDIUM;consult 信号 → LOW;否则 LOW)。

`HIGH_RISK_WORDS`/`CONSULT_WORDS` 关键词表在 `ai.py` 中,被意图分类、mock、评估、协调器、安全审核多处复用。**后台评估结果(风险等级/评分/诊断)绝不展示给学生**——这是跨 prompt、技能、报告服务的强约束。

### 工具队列、治理与 MCP 双模

- `ToolOrchestrationService`(`app/services/tools.py`):Excel 台账(openpyxl + 进程级 `EXCEL_WRITE_LOCK`,幂等)、RiskCase 创建(幂等,`handoff_summary` 由 `counselor_handoff_summary` 技能渲染)、告警发送(`alert_email_delivery_mode`:`log` 永远 SUCCESS / `smtp` 真发 / 缺配置 FAILED,按分钟限流)。
- `ToolPolicyRegistry`/`ToolGovernanceService`(`app/services/tool_governance.py`):静态策略(`EXCEL_REPORT` 任意风险、`CASE_CREATE` MEDIUM+、`ALERT_SEND` 仅 HIGH),每次执行写 `ToolAuditRecord`。
- `ToolQueueService` + `ToolQueueWorker`(`app/services/tool_queue.py`):FastAPI startup 启动守护线程,轮询 PENDING、依赖就绪(`ALERT_SEND` 等 `CASE_CREATE` SUCCESS)、两个 `ThreadPoolExecutor`(excel 1 / email 2)、滑动窗口限流、超 `max_attempts` 进 `dead_letter_records`。`enqueue_report`:总是 EXCEL_REPORT;MEDIUM/HIGH 加 CASE_CREATE;HIGH 再加依赖 case 的 ALERT_SEND。
- **双模**:默认走异步工具队列;`TOOL_QUEUE_ENABLED=false` 时,`MindBridgeMcpToolClient` 以子进程 stdio 启动 `app/mcp_tools/server.py`(FastMCP 6 个工具)直接调用——两条路径复用同一套 `ToolOrchestrationService` 实现。

### 数据闭环

MySQL 存全量业务与聊天(`chat_sessions`/`chat_messages`),Redis 仅存短期上下文(默认 40 条 / 24h TTL,key=`mindbridge:short-term-memory:{session_public_id}`,Redis 宕不阻断聊天),高风险写 Excel 台账(`data/mindbridge-risk-ledger.xlsx`)+ 邮件预警。所有 schema 由 `Base.metadata.create_all()` 启动建表(**无 Alembic 迁移**),也支持 SQLite(harness 与 CI 用)。

### 技能系统

`app/services/skills.py::MindBridgeSkillRegistry` 在运行时加载 `skills/*/SKILL.md`(YAML frontmatter `name`/`description` + `## Workflow`)。`MindBridgeSkillLibrary.response_skill_names(intent, risk, text)` 按意图/风险/关键词选技能,串接进 ResponseAgent 的 system prompt;`counselor_handoff_summary` 技能含 `text` 模板,渲染案例移交摘要。7 个内置技能多为学生侧表达规范,`counselor_handoff_summary` 为员工侧。

## 关键约定与陷阱

- **测试用 `unittest`,不是 pytest**:CI(`.github/workflows/test.yml`)跑 `python -m unittest discover -s tests`,环境变量 `AI_PROVIDER=mock`、`DATABASE_URL=sqlite:///./target/ci-test.sqlite3`、`KNOWLEDGE_VECTOR_ENABLED=false`(无 Redis service,Redis 相关测试容错)。
- **无 lint/格式化步骤**:CI 只 `compileall` + `unittest`。不要假设有 ruff/black/mypy。
- **配置缓存**:`get_settings()` 用 `@lru_cache`;harness 改 env 后调 `get_settings.cache_clear()` 重建。改设置后跑 harness 需注意此点。
- **AI_PROVIDER**:`mock`(关键词驱动的 canned 回复,CI/harness 用)、`ollama`(本地微调 GGUF)、`openai`(兼容 API)。无外网/无 key 时务必用 `mock` 做密闭测试。
- **密码是 SHA-256 无盐**(demo 级,见 `app/core/security.py`),勿作生产;改鉴权需同步更新 seed 与前端。
- **隐私脱敏**:`PrivacySanitizer` 在入 prompt 与持久化前正则脱敏电话/邮箱/身份证(`[已脱敏]`);`RedisShortTermMemoryStore._serialize` 会先脱敏再存。
- **`@app.on_event` 启停**:startup `create_schema()` + `seed_data()` + 启动工具队列 worker;shutdown 停 worker。用的是已废弃的事件装饰器,非 lifespan 上下文管理器——改结构时注意。
- **文档漂移**:README 称 compose 用 MySQL 8.4,实为 `mysql:8.0`;`.env.example` 默认 `AGENT_FRAMEWORK=langgraph` 但只支持 `event_driven_multi_agent`;README 称 `models/mindbridge-qwen2.5-7b-ft/README.md` 存在,磁盘上只有 `Modelfile`。
- **GGUF 不入库**:`.dockerignore`/`.gitignore` 排除 `*.gguf`,Dockerfile 只 COPY `Modelfile`;GGUF 须放主机 Ollama。
- **改 `app/knowledge/*.md`** 会触发重启时按分块规则刷新对应源;新增源同样在 `seed_data` 时入库 + Chroma 索引(首次向量检索时按需建)。
- **不可变黑板**是协作运行时的不变量:`CollaborationBlackboard` 变更必须走 `replace(...)` 返回新板,勿原地改。

## 配置入口

`app/core/config.py::Settings`(`pydantic-settings`,读 `.env`,`extra="ignore"`)。关键开关:AI 提供商与模型、DB/Redis、知识检索权重与开关(`KNOWLEDGE_VECTOR_ENABLED/REQUIRED`、`KNOWLEDGE_HYBRID_VECTOR_WEIGHT=0.65`/`BM25_WEIGHT=0.35`、`KNOWLEDGE_RERANK_ENABLED`)、Chroma 目录、智能体预算(`agent_max_rounds` 等)、工具队列与邮件预警。`.env.example` 是完整模板。
