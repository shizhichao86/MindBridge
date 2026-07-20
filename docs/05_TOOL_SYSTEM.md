<!--
  文档定位: 工具队列、治理与 MCP 双模 —— 从评估结果到辅导员通知的完整执行链路
  面试权重: ★★★☆☆ (工程实践亮点,双模设计体现架构灵活性)
  前置阅读: docs/01_ARCHITECTURE_OVERVIEW.md, docs/04_RISK_ASSESSMENT.md
-->

# 工具队列、治理与 MCP 双模

---

## 1. 工具系统概览

### 1.1 三类工具的角色卡

```
┌──────────────────────────────────────────────────────────────────────┐
│                        MindBridge 工具系统                            │
├───────────────┬──────────────────┬───────────────────────────────────┤
│  工具          │  触发条件          │  做什么                            │
├───────────────┼──────────────────┼───────────────────────────────────┤
│ EXCEL_REPORT  │  任意风险等级       │  写入辅导员 Excel 台账               │
│ CASE_CREATE   │  MEDIUM / HIGH    │  创建或复用风险个案                   │
│ ALERT_SEND    │  HIGH             │  发送/记录辅导员预警邮件               │
└───────────────┴──────────────────┴───────────────────────────────────┘
```

**触发时机:** 智能体运行时结束 → 生成 `PsychologicalReport` → `ToolQueueService.enqueue_report()` (`app/services/tool_queue.py:29`)

### 1.2 核心设计原则:工具执行与流式回复完全解耦

```
  时间轴 ──────────────────────────────────────────────────────▶

  用户视角:  [输入] ──→ [token token token...] ──→ [完整回复]
                                                         │
  后台异步:                                               ├──▶ Excel 写入
                                                         ├──▶ 个案创建
                                                         └──▶ 邮件预警
```

**为什么解耦?**

| 不解耦的方案 | 解耦的方案 (实际) |
|-------------|-----------------|
| 学生等 Excel 写完才看到回复 → 延迟 +3s | 学生立即看到完整回复 |
| 邮件发送失败 → 学生看到错误或空回复 | 邮件失败静默记录,学生无感知 |
| 同步阻塞 FastAPI worker 线程 | worker 线程立即释放处理下一个请求 |

代码实现 (`app/agents/harness.py`):`harness.run()` 同步返回结果后,HTTP 层先 `yield` 完流式 token,**再 `await dispatch_tools(tool_plan)`**。

---

## 2. 工具队列架构

### 2.1 守护线程模型

```
  FastAPI Main Thread          mindbridge-tool-dispatcher (Daemon Thread)
  ┌─────────────────┐          ┌─────────────────────────────────────┐
  │ .enqueue_report()│          │  while not stop_event:              │
  │   → INSERT ToolJob│         │    _dispatch_once()                 │
  │   → return       │          │      → SELECT PENDING WHERE run_after <= now
  └─────────────────┘          │      → 标记 RUNNING                  │
                               │      → executor.submit(_run_job)     │
                               │    sleep(poll_interval)              │
                               └─────────────────────────────────────┘
```

**启动/停止:** `app/services/tool_queue.py:101-113`

- `start()`:FastAPI `@app.on_event("startup")` 调用,`daemon=True` 确保主进程退出时自动回收
- `stop()`:FastAPI `@app.on_event("shutdown")` 调用,设置 `stop_event`,最多等 5 秒后强制 shutdown 线程池
- `_recover_running_jobs()`:启动时把所有 `RUNNING` 状态作业重置为 `PENDING`(`app/services/tool_queue.py:264-275`),处理非优雅关闭遗留

### 2.2 双线程池隔离

`app/services/tool_queue.py:91-99`:

```python
self.excel_executor = ThreadPoolExecutor(max_workers=max(1, settings.tool_queue_excel_workers),
                                          thread_name_prefix="mindbridge-excel")
self.email_executor = ThreadPoolExecutor(max_workers=max(1, settings.tool_queue_email_workers),
                                          thread_name_prefix="mindbridge-email")
```

| 线程池 | 处理作业 | 默认 workers | 原因 |
|--------|---------|-------------|------|
| `excel_executor` | EXCEL_REPORT + CASE_CREATE | 1 | Excel 写文件有进程级锁,多线程无益 |
| `email_executor` | ALERT_SEND + RISK_ALERT | 2 | SMTP 是 IO 密集型,2 并发合理 |

**路由逻辑:** `app/services/tool_queue.py:144-147` — `_executor_for()` 按 `ToolJobKind` 分配。

### 2.3 作业状态机

```
                    ┌──────────┐
                    │ PENDING  │ ◀────────────────────────────┐
                    └────┬─────┘                              │
                         │                                    │
                    _dispatch_once()                           │
                    picks up job                               │
                         │                                    │
                    ┌────▼─────┐     dependency not ready     │
                    │ RUNNING  │ ───────  or rate limited ────┤
                    └────┬─────┘                              │
                         │                                    │
                    _run_job()                                │
                    _execute()                                │
                         │                                    │
              ┌──────────┼──────────┐                         │
              │          │          │                         │
         ┌────▼───┐ ┌────▼───┐ ┌───▼────┐                     │
         │SUCCESS │ │PENDING │ │ DEAD   │                     │
         │        │ │(retry) │ │        │                     │
         └────────┘ └───┬────┘ └────────┘                     │
                        │                                     │
                        │ attempts < max_attempts             │
                        └─────────────────────────────────────┘
```

**状态流转代码:** `app/services/tool_queue.py:149-178`

### 2.4 依赖管理

`app/services/tool_queue.py:207-222` `_dependency_ready()`:

```
  ALERT_SEND 依赖 ──▶ CASE_CREATE SUCCESS (有 depends_on_job_id 时)
                 ──▶ RiskCase 已存在 (无 depends_on_job_id 时)
  RISK_ALERT 依赖 ──▶ ExcelRecord SUCCESS
```

依赖不满足 → `_requeue()` 回到 PENDING,带 2s 延迟 (`app/services/tool_queue.py:156-157`):

```python
if not self._dependency_ready(db, job):
    self._requeue(db, job, self._dependency_wait_reason(job), 2.0)
    return
```

**为什么用轮询重试而不是事件通知?** 实现简单,依赖作业通常在同一轮询周期内完成 (CASE_CREATE 是同步的数据库写入),2s 延迟已经足够覆盖。事件驱动会增加 Redis pub/sub 依赖,违背"Redis 挂了不影响核心功能"的设计约束。

### 2.5 滑动窗口限流

`app/services/tool_queue.py:66-83` `RateLimiter`:

```
  [event1, event2, event3]  ← deque, 记录最近 60s 内的事件时间戳
  新事件到达 → 弹出 60s 外的 → 计数 < limit → 允许
                                  计数 >= limit → 拒绝,返回 retry_after
```

只对**邮件预警**限流 (`app/services/tool_queue.py:158-162`),防止 SMTP 服务器被高频调用封禁。

### 2.6 延迟重试 + 死信队列 + 重启恢复

`app/services/tool_queue.py:237-262` `_fail_or_dead_letter()`:

| 条件 | 动作 |
|------|------|
| `attempts < max_attempts` | 回 PENDING,`run_after = now + retry_delay * attempts` (指数退避) |
| `attempts >= max_attempts` | 进 DEAD + 写 `DeadLetterRecord` |

**死信记录** 保存 `job_id/report_id/kind/reason/payload`,供管理员手动排查。

**重启恢复** (`app/services/tool_queue.py:264-275`):启动时批量将 RUNNING → PENDING,带 `"服务重启后恢复未完成任务"` 标记。

---

## 3. 工具治理策略

### 3.1 静态策略表

`app/services/tool_governance.py:23-45` `ToolPolicyRegistry.POLICIES`:

```
┌─────────────────┬──────────────────────────────────────┐
│ 工具              │  允许的风险等级                          │
├─────────────────┼──────────────────────────────────────┤
│ EXCEL_REPORT    │  LOW, MEDIUM, HIGH (全量记录)          │
│ CASE_CREATE     │  MEDIUM, HIGH                         │
│ ALERT_SEND      │  HIGH (仅高风险触发预警)                  │
│ RISK_ALERT      │  HIGH (遗留兼容)                        │
└─────────────────┴──────────────────────────────────────┘
```

**为什么用静态表而不是配置文件?** 策略是安全约束,不是业务配置。改配置的人可能不理解"LOW 风险发预警=狼来了效应"。代码级常量 + code review 门槛是最小权限的实践。

### 3.2 授权 + 审计

`app/services/tool_governance.py:64-106`:

每次工具执行生成 `ToolAuditRecord`:
- `policy` — 命中的策略名
- `status` — `AUTHORIZED` 或 `BLOCKED`
- `reason` — 拒绝原因 (如"工具 ALERT_SEND 不允许处理风险等级 LOW")
- `payload` — JSON 快照 (jobId, kind, attempts, riskLevel, policy)

**设计意图:** 事后可追溯到"谁在什么时候因为什么策略允许/拒绝了什么操作"。

---

## 4. 工具实现细节

### 4.1 Excel 台账

`app/services/tools.py:27-51`:

```
write_excel(report)
  │
  ├── 幂等检查: ExcelRecord EXISTS + SUCCESS → 直接返回
  ├── 进程级锁: threading.Lock() (EXCEL_WRITE_LOCK)
  │     └── 为什么不是文件锁? openpyxl 不是进程安全的,
  │         但系统单进程部署,进程级锁已足够
  ├── 文件不存在 → 创建 + 写入表头
  └── append 一行 [reportId, riskLevel, emotion, confidence, summary, createdAt]
```

**Trade-off: 为什么不用数据库存 Excel?** Excel 是辅导员的工作界面,他们需要离线打开、筛选、打印。数据库 UI 对他们不友好。

### 4.2 风险个案

`app/services/tools.py:53-68`:

```
create_case(report)
  │
  ├── 幂等: RiskCase EXISTS → 直接返回
  ├── owner = alert_email_to 的第一个收件人 (或 "unassigned")
  └── handoff_summary = MindBridgeSkillLibrary.counselor_handoff_summary(report, user)
```

**handoff_summary 为什么用技能模板?** (`app/services/skills.py`)
- 模板包含 `## Workflow` + ` ```text ` 模板,用学生显示名/风险等级/摘要/情绪标签渲染
- 分离关注点:工具只负责创建实体,模板负责内容渲染
- 修改移交摘要格式只需改 `skills/counselor_handoff_summary/SKILL.md`,不动代码

### 4.3 邮件预警

`app/services/tools.py:106-147` `notify()`:

```
notify(report, case?)
  │
  ├── 幂等: AlertRecord EXISTS + SUCCESS → 直接返回
  │
  ├── mode == "log" → SUCCESS (仅日志,永远成功)
  │     为什么? 开发环境没配 SMTP,不能因此阻断流程
  │
  ├── mode == "smtp" → 检查配置完整性
  │     missing: SMTP_HOST / ALERT_EMAIL_FROM / ALERT_EMAIL_TO
  │     → FAILED + 详细缺失信息
  │
  └── 发送: SSL 或 STARTTLS,支持 SMTP 认证
```

**三种投递模式对比:**

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `log` (默认) | 写 AlertRecord SUCCESS,不发邮件 | 开发/演示 |
| `smtp` | 真发邮件,失败写 FAILED | 生产 |
| 未配置 | 立即 FAILED | 防止静默失败 |

---

## 5. MCP 双模 (架构亮点!)

### 5.1 为什么需要双模?

```
  模式一: 异步工具队列 (TOOL_QUEUE_ENABLED=true, 默认)
  ┌─────────┐    ┌──────────────┐    ┌───────────┐
  │ harness │───▶│ToolQueueService│───▶│ Worker    │───▶│ToolOrchestrationService
  └─────────┘    └──────────────┘    └───────────┘
  特点: 解耦、容错、重试、死信,但需要 MySQL + 后台线程

  模式二: MCP stdio 子进程 (TOOL_QUEUE_ENABLED=false)
  ┌─────────┐    ┌────────────────────┐    ┌────────────────┐
  │ harness │───▶│MindBridgeMcpToolClient│───▶│ mcp_tools/server│───▶│ToolOrchestrationService
  └─────────┘    └────────────────────┘    └────────────────┘
                          │                        │
                     stdio 子进程           FastMCP 6 个工具
  特点: 同步、直接、无需 MySQL 后台线程,适合开发/演示
```

### 5.2 同一套实现,两种调用层

关键设计:**两种模式共用 `ToolOrchestrationService`**,只是调用方式不同:

| 维度 | 模式一 (队列) | 模式二 (MCP) |
|------|-------------|-------------|
| 调用入口 | `ToolQueueWorker._execute()` | `MindBridgeMcpToolClient.handle_report()` |
| 传输层 | 数据库作业表 | stdio JSON-RPC |
| 容错 | 重试 + 死信队列 | 异常直接抛出 |
| 适用 | 生产 | 开发/演示/Harness |

**切换逻辑在 `app/agents/harness.py` 的 `dispatch_tools()`:**
```
if TOOL_QUEUE_ENABLED → enqueue_report() → return (异步)
else → MindBridgeMcpToolClient.handle_report() → await (同步调用)
```

### 5.3 MCP 子进程客户端

`app/services/mcp_client.py:17-85` `MindBridgeMcpToolClient`:

```python
async def handle_report(self, report_id, risk_level):
    async with self._session() as session:
        # 1. Excel 写入 (始终执行)
        results = [await self._call_tool(session, "mindbridge_excel_report", {"report_id": report_id})]

        # 2. 个案创建 (MEDIUM+)
        if risk_level in {MEDIUM, HIGH}:
            case_result = await self._call_tool(session, "mindbridge_case_create", ...)
            case_id = self._extract_case_id(case_result)  # 正则解析 "caseId=123"

        # 3. 预警发送 (HIGH)
        if risk_level == HIGH:
            await self._call_tool(session, "mindbridge_alert_send", {"case_id": case_id})
```

**子进程启动** (`app/services/mcp_client.py:50-61`):
```python
server = StdioServerParameters(
    command=sys.executable,
    args=["-m", "app.mcp_tools.server"],
    env=env,  # PYTHONPATH 注入项目根
    cwd=str(project_root),
)
```

**为什么用子进程而不是直接 import?** 进程隔离——MCP 工具服务如果挂了,不会拖垮主进程。

### 5.4 6 个 MCP 工具一览

`app/mcp_tools/server.py:16-103`:

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `mindbridge_excel_report` | 写 Excel 台账 | `report_id` |
| `mindbridge_case_create` | 创建风险个案 | `report_id` |
| `mindbridge_alert_send` | 发送预警邮件 | `case_id` |
| `mindbridge_alert_ack` | 确认接手个案 | `case_id, actor, note?` |
| `mindbridge_case_note_add` | 添加跟进备注 | `case_id, actor, note` |
| `mindbridge_alert_notify` | 直接通知 (遗留) | `report_id` |

每个工具独立 `SessionLocal()` + `create_schema()`,确保在独立进程中能自举数据库。

---

## 6. 数据闭环:四存储分工

```
                         PsychologicalReport
                                │
               ┌────────────────┼────────────────┐
               │                │                │
               ▼                ▼                ▼
          ┌─────────┐    ┌──────────┐    ┌───────────┐
          │  MySQL  │    │  Excel   │    │   Email   │
          │ (全量)   │    │ (台账)    │    │  (预警)    │
          ├─────────┤    ├──────────┤    ├───────────┤
          │ ChatSession│   │ reportId │    │ Subject   │
          │ ChatMessage │   │ riskLevel│    │ Body      │
          │ PsychReport │   │ emotion  │    │ handoff   │
          │ RiskCase   │    │ summary  │    │ summary   │
          │ CaseNote   │    │ createdAt│    │           │
          │ ToolJob    │    └──────────┘    └───────────┘
          │ DeadLetter │
          │ ToolAudit  │         ┌──────────┐
          │ AlertRecord│         │  Redis   │
          │ ExcelRecord│         │ (短期)    │
          └─────────┘         ├──────────┤
                              │ 40条/24h │
                              │ 上下文缓存 │
                              └──────────┘
```

| 存储 | 存什么 | 多久 | 谁看 |
|------|--------|------|------|
| MySQL | 全量业务数据 | 永久 | 系统/管理后台 |
| Redis | 短期对话上下文 | 24h TTL | 智能体运行时 |
| Excel | 风险台账 | 永久 (文件) | 辅导员离线查看 |
| Email | 高风险预警 | SMTP 服务器保留 | 辅导员/管理员 |

**设计哲学:** MySQL 是 single source of truth;Redis 是加速层 (挂了不阻断);Excel 是辅导员工作界面;Email 是推通知。

---

## 7. 面试追问 (4 问)

### Q1: 工具队列为什么不用 Celery/RabbitMQ?

A: MindBridge 是校园场景的单体部署,作业量级是"每天几个到几十个评估报告",不是"每秒几千个任务"。引入 Celery 需要维护 broker (Redis/RabbitMQ) + worker 进程管理 + 序列化协议,运维成本远超自带线程池。**YAGNI 原则:不够痛之前不加依赖。**

当前方案只需 MySQL + Python 标准库 `threading`,零外部依赖即可工作。

### Q2: 进程级锁 (`EXCEL_WRITE_LOCK`) 在多进程部署下会有什么问题?怎么解决?

A: 进程级锁只保护单进程内的并发。如果 Uvicorn 配 `--workers 4`,四个 worker 进程各有一个独立的 `threading.Lock`,无法互斥。以下是解决方案分级:

| 方案 | 复杂度 | 可靠性 |
|------|--------|--------|
| 文件锁 `fcntl.flock()` (推荐) | 低 | 跨进程可靠 |
| Redis 分布式锁 | 中 | 需 Redis 可用 |
| 队列串行化 (当前) | 低 | 靠 `excel_executor` max_workers=1 串行 |

项目中实际通过两个机制兜底: (1) `excel_executor` 只有一个 worker 线程,(2) `write_excel()` 先查幂等再写。所以即使锁失效,也只是同一行可能写两次,不会数据损坏。

### Q3: `ALERT_SEND` 依赖 `CASE_CREATE` 先成功,如果 CASE_CREATE 一直失败,ALERT_SEND 会怎样?

A: `ALERT_SEND` 在 `_dependency_ready()` 里检查依赖:

```
depends_on_job_id → 等待该 job SUCCESS
无 depends_on_job_id → 回退查 RiskCase 表是否已有记录
```

如果 CASE_CREATE 进 DEAD → ALERT_SEND 永远等不到依赖 → 也会在 `max_attempts` 次重试后进 DEAD → 写 `DeadLetterRecord`。**管理员需要监控死信队列并手动处理。** 这是有意的 trade-off:不自动降级发预警 (缺失 case 上下文),因为高风险预警邮件必须包含 `handoff_summary`。

### Q4: MCP 双模的实际价值是什么?为什么不直接统一成一种?

A:

| 场景 | 模式一 (队列) | 模式二 (MCP) |
|------|-------------|-------------|
| 生产环境 | 启动 worker 线程 | TOOL_QUEUE_ENABLED=true (默认) |
| CI/Harness | 不想启动后台线程 | TOOL_QUEUE_ENABLED=false |
| 开发调试 | 队列异步难追踪 | 同步调用,异常直接可见 |
| 外部集成 | N/A | MCP 是开放协议,第三方 AI 可调用 |

实际价值:**一套实现服务于两个完全不同的调用场景。** Harness 自检 (`python -m app.harness.runner`) 跑 MCP 模式,无需启动 worker 线程,测试隔离性更好。生产跑队列模式,享受重试和死信队列的容错能力。
