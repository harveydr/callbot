# AI 智能外呼平台（代号「回声 / EchoDesk」）— 多租户 AI 语音外呼 SaaS
# Claude Code 开发规范文档 v1.0

**文档版本**: v1.0
**创建日期**: 2026-11-22
**项目代号**: EchoDesk（回声）
**状态**: 需求已确认，可直接进入开发
**开发工具**: Claude Code
**PRD Generator 版本**: v4.0

> **本文档是开发的唯一真相源。** 任何与本文档冲突的口头约定，以本文档为准；需要变更时先改本文档再改代码。
>
> **产品经理的两条前置结论（请先读，它们决定其余一切）**
> 1. **合规是这个产品的第一约束，不是第 N 个功能。** 我国现行规则下，持「呼叫中心业务」许可的经营性呼叫平台，**呼出仅可用于经用户同意的即时回访和信息咨询，不允许提供商业营销类电话呼出服务**（工信部信管〔2020〕81 号）。因此本产品 V1 采用 **BYOC（租户自带线路与资质）** 为主体架构：平台只做软件与调度，通信资源、码号、用户授权由租户持有并对内容负责。这条架构决策直接写进数据模型、线路模块、计费模型和准入流程，不可后期补。
> 2. **四类场景全支持，但上线次序必须分层。** 通知提醒 / 售后回访先上（合规性质最干净），电销获客以「租户自带线路 + 自带授权 + 灰度白名单」方式受控开放，金融催收放 V1.1 且需单独合规审批。

---

## 1. 项目概述与目标

### 1.1 项目背景

企业侧存在大量「标准化、高重复、低单价」的电话触达需求（预约确认、续费提醒、面试邀约、满意度回访、贷款线索首筛、逾期提醒）。人工坐席成本高（一线城市含社保综合成本约每人每月 8k–12k 元，本 PRD 不做精确断言，仅作规模判断依据）、话术一致性差、夜间无法覆盖、质检覆盖率低（抽检通常 < 5%）。

大模型的上下文理解与多轮应答能力，使外呼从「按键式话术树」升级为「听得懂人话、能被打断、能自由问答，但在关键节点被硬约束」的混合智能体。同时录音全量转写让质检覆盖率从抽检变为 100%。

但行业同时处于**强监管周期**：2025 年 9 月 1 日起 AI 生成合成内容强制标识；2026 年工信部前三季度已处罚违规企业 184 家次、关停违规语音专线 1425 条（据工信部信管函〔2026〕375 号转述）。因此本产品把「合规引擎」作为核心竞争壁垒而非附加模块。

### 1.2 V1 核心目标

**一句话定位**：面向企业的多租户 AI 外呼 SaaS，用「大模型应答 + 话术树硬约束 + 合规红线闸门」把电话触达做成可授权、可溯源、可审计的批量作业。

**V1 量化指标（试点级）**

| 指标 | 目标值 | 说明 |
|---|---|---|
| 并发通话路数 | 10 路（峰值），单路可配 | 语音网关单机可支撑数百路，10 路为业务配额而非硬件上限 |
| 日呼叫量 | ≤ 1000 通/天（全平台合计） | 试点期，验证效果与合规闭环 |
| 租户数 | ≤ 20 个种子租户 | 人工开通 + 准入审核 |
| 端到端语音响应延迟 | P50 ≤ 1200ms，P95 ≤ 1800ms | 从用户说完到 AI 首帧音频 |
| AI 意图识别准确率 | ≥ 90%（评测集） | 六类意图：同意/拒绝/犹豫/咨询/投诉/无效 |
| 合规红线拦截召回率 | ≥ 99% | 违禁承诺、夸大表述、辱骂等，宁可误拦 |
| AI 独立完成率 | 通知/回访类 ≥ 85% 无需转人工 | 其余转人工或改期 |
| 有效意向识别 | 电销类意向 A+B 级占比可量化输出 | 以「意向率」而非「接通率」为价值口径 |
| 系统可用性 | 99.5%（月度，呼叫调度面） | 通话中掉话率 < 0.5% |

### 1.3 目标用户与角色

| 角色 | role 字段值 | 特征 | 核心需求 |
|---|---|---|---|
| 平台超管 | `platform_admin` | 我方运营/合规人员 | 租户开通与资质审核、线路池、全局合规策略、平台账单 |
| 租户管理员 | `tenant_admin` | 客户方 IT/业务负责人 | 配置线路与号码、建话术模板、授权凭证、看报表、管坐席 |
| 话术运营 | `operator` | 客户方业务运营 | 建外呼任务、上传名单、调话术、看实时通话、复盘 |
| 人工坐席 | `agent` | 客户方销售/客服 | 接收转人工来电、填写小结、处理投诉工单 |
| 合规审计员 | `auditor` | 客户方风控/合规 | 只读全量录音与转写、调证包导出、投诉台账、DND 名单 |
| 被叫用户 | 非系统用户 | 终端消费者 | 被告知是 AI、随时可拒接并要求不再被打扰 |

### 1.4 架构设计要点

- **多租户**：单库共享 + `tenant_id` 全表强制隔离 + PostgreSQL Row Level Security（RLS）作为数据库层兜底。10 路 / 20 租户规模下不按 schema 拆分，避免运维复杂度。
- **控制面 / 媒体面物理分离**：Web 控制面（FastAPI）与语音媒体面（FreeSWITCH + 语音 Agent 编排服务）独立进程、可独立重启。控制面挂了不能影响正在进行的通话。
- **BYOC 线路模型**：租户绑定自有 SIP 中继（SIP trunk）与真实主叫号码；平台可登记「托管线路」但 V1 仅对通知/回访类开放。
- **异步优先**：呼叫发起、转写、质检、计费、报表全部异步化，Web 层只做参数校验 + 入队。
- **不可变审计**：合规事件、录音、转写、授权凭证以仅追加（append-only）方式存储，应用层无删除接口。

---

## 2. 需求分析与功能范围（Scope）

### 2.1 四类场景的统一抽象

四类业务共用同一套引擎，差异全部收敛到**场景模板（Scenario Template）**这一配置对象：开场白、目标、可问字段、意向判定规则、合规策略集、通话后动作。新增场景 = 新增模板 + 新增合规策略集，不改代码。

| 场景 | scenario_code | 合规定性 | 优先级 |
|---|---|---|---|
| 通知与提醒 | `notification` | 服务/业务提醒类，合规性质最干净 | **P0** |
| 售后回访调研 | `callback_survey` | 属 81 号文允许的「经用户同意的即时回访」 | **P0** |
| 电销获客转化 | `telesales` | 商业营销类呼出：平台侧不得以呼叫中心许可对其提供呼出服务；只能由租户自带资质/线路承载 | **P1（灰度白名单）** |
| 催收与账单提醒 | `collection` | 金融强监管，另有行业规则 | **V1.1（单独合规审批 + 白名单租户）** |

**理由**：V1 先把「允许做的」跑稳、拿到可交付效果；电销是营收大头但监管红线最密，必须以「平台只提供软件、不提供通信资源与营销内容背书」的边界进入，且默认关闭、逐租户灰度。

### 2.2 V1 功能优先级

**P0 Must Have（不做不能上线）**

1. 租户开通与资质准入（营业执照、许可证、授权材料、话术备案）
2. 线路与号码池管理（SIP trunk、主叫号码、并发配额、外显校验）
3. 名单导入 + **授权凭证（Consent）强绑定**，无授权不可拨
4. DND / 黑名单 / 拒接冷却 / 时段与频次闸门（合规引擎）
5. 外呼任务编排（定时、重拨策略、并发令牌桶）
6. 实时语音对话引擎（VAD + 流式 ASR + LLM + 流式 TTS + 打断）
7. 混合话术引擎（LLM 自由应答 + 关键节点话术树 + 红线实时拦截）
8. AI 身份声明强制注入（开场必说，不可关闭）
9. 录音双轨留存 + 全量转写 + 100% 自动质检
10. 转人工（热转接）与改期
11. 意向分级（A/B/C/D）与通话小结
12. CDR 落库 + 三方对账 + 用量计费
13. 投诉工单 + DND 联动
14. 合规证据链：调证包一键导出
15. 权限体系、审计日志、监控告警

**P1 Should Have（V1 内可简化实现）**

- 电销场景（灰度、逐租户开关）
- 号码健康度与投诉率熔断
- A/B 话术实验与转化率对比
- 实时监听 / 耳语辅导（坐席侧）
- 变量个性化播报（客户姓名、账单金额）
- 开放 API（供租户 CRM 回调）

**P2 → V2 附录（V1 不碰）**

- 托管线路与码号自营、金融催收全托管、视频外呼、WhatsApp/短信多通道联动、坐席全功能工作台、方言与多语种、自建 ASR/TTS、预测式外呼算法、跨租户话术市场。

### 2.3 V1 明确不做（Negative Scope，写进代码级禁止项）

| 禁止项 | 原因 |
|---|---|
| 支持「打显」/ 伪装本地号码 / 随机换号 | 监管严禁违规更改、隐藏主叫号码 |
| 提供虚拟号、小号、未实名号呼出 | 号码必须真实可溯源 |
| 无授权凭证的陌生号码批量盲呼（冷名单） | 违反民法典 1033 条、消保条例 24 条 |
| 允许关闭或跳过 AI 身份声明 | 违反深度合成规定 17 条与标识办法 |
| 平台侧对 `telesales` 场景提供线路/呼出服务 | 81 号文明令不允许呼叫中心提供商业营销类呼出 |
| 租户间数据/号码资产共享、转租转售线路 | 码号资源不得转让出租 |
| 克隆特定真人音色 | 深度合成规定禁止仿声误导 |
| 通话录音留存期 < 法定/合同留存期 | 证据链完整性 |

---

## 3. 技术架构

### 3.1 技术栈（锁定版本）

| 层 | 选型 | 版本 | 理由 |
|---|---|---|---|
| 后端 API | Python + FastAPI | Python 3.11.9 / FastAPI 0.115.6 / Uvicorn 0.32.1 | AI 密集型项目，语音与模型 SDK 生态以 Python 为先；异步高 I/O 满足百毫秒级编排 |
| ORM | SQLAlchemy | 2.0.36（async）+ asyncpg 0.30.0 | 生产主流，async 支持成熟 |
| 迁移 | Alembic | 1.14.0 | 异步 env.py 需专门配置（见 §16） |
| 数据校验 | Pydantic | 2.10.3 | FastAPI 原生 |
| 数据库 | PostgreSQL | 16.4 | JSONB 承载话术树与合规策略、RLS 做租户兜底 |
| 缓存/队列 | Redis | 7.2.5 + Celery 5.4.0 | 队列、令牌桶、实时状态、限流 |
| 语音网关 | FreeSWITCH | 1.10.12 | 生产级 SIP/媒体，单机数百路；`mod_audio_fork` 推流 |
| 语音编排 | 自研 voice-orchestrator（Python asyncio + WebSocket） | — | 串起 VAD→ASR→LLM→TTS，是唯一有状态面 |
| VAD | silero-vad | 5.x（ONNX） | 本地推理，不依赖外网 |
| ASR | 供应商 A：流式 ASR（可切换） | 适配层 `asr/vendor.py` | 供应商中立，按并发配额切换 |
| TTS | 供应商 B：流式 TTS（可切换） | 适配层 `tts/vendor.py` | 需支持流式首包与语速控制 |
| LLM | OpenAI 兼容接口（多模型可配） | `httpx` 0.27.2 流式 | 支持主备模型自动降级 |
| 前端 | Vue 3 + Element Plus + Vite | Vue 3.5.13 / Element Plus 2.9.1 / Vite 6.0.5 | 国内后台标配，表格表单密度高 |
| 前端状态/请求 | Pinia 2.3.0 / axios 1.7.9 | — | — |
| 实时推送 | WebSocket（FastAPI）+ SSE 兜底 | — | 通话大盘 |
| 部署 | Docker Compose + Nginx | Nginx 1.27.3 | 试点期单机/双机，`restart: unless-stopped` |
| 对象存储 | 云 OSS/S3 兼容（录音） | `boto3` 1.35.69 | 多实例必须共享存储 |
| 监控 | Prometheus + Grafana + Alertmanager | — | 含业务指标（接通率、投诉率、延迟） |

### 3.2 系统架构图

```
                         ┌──────────────────────────────────────────────┐
   浏览器(运营/坐席/审计) │                 Nginx :443 (TLS终结, WAF)     │
        │ HTTPS/WSS      └───────┬───────────────────────┬──────────────┘
        ▼                        ▼                       ▼
  ┌───────────────┐      ┌───────────────┐       ┌──────────────────┐
  │ Web 前端 Vue3 │─────▶│  control-api  │◀─────▶│  websockets hub  │
  └───────────────┘      │  (FastAPI)    │       │  (实时通话大盘)   │
                         └───┬───────┬───┘       └────────▲─────────┘
                             │       │                    │ 事件
             ┌───────────────┘       └──────────┐         │
             ▼                                  ▼         │
     ┌───────────────┐                  ┌──────────────┐   │
     │ PostgreSQL 16 │                  │  Redis 7.2   │   │
     │ + RLS(tenant) │                  │ 缓存/队列/令牌│   │
     └───────────────┘                  └──────┬───────┘   │
             ▲                               │           │
             │ 任务/CDR                       ▼           │
     ┌───────────────┐                ┌──────────────────┴──┐
     │ celery worker │                │  voice-orchestrator │◀─ LLM API
     │ (导入/转写/质检│                │  (VAD→ASR→LLM→TTS)  │◀─ ASR API
     │  /计费/报表)   │                └──────────┬──────────┘◀─ TTS API
     └───────────────┘                           │ 媒体流 WS/UDP
                                         ┌───────▼────────┐   SIP/RTP
                                         │  FreeSWITCH    │◀────────────┐
                                         │  SIP trunk/号码池│             │
                                         └───────┬────────┘        运营商中继
                                                 │ 录音                │
                                                 ▼                     ▼
                                          ┌────────────┐         被叫用户手机
                                          │ OSS 对象存储│
                                          │ (双轨录音)  │
                                          └────────────┘
```

### 3.3 服务依赖与启动顺序

```
PostgreSQL → Redis → MinIO/OSS就绪检查 → control-api → celery worker
           → voice-orchestrator → FreeSWITCH → Nginx
```
- `control-api` 启动前：PostgreSQL 可执行 `SELECT 1`（重试 10 次，间隔 3s，超时退出而非无限等待）。
- `voice-orchestrator` 启动前：Redis PING + FreeSWITCH ESL 端口可达。
- 健康检查：`/healthz`（进程存活）、`/readyz`（依赖可达）。

### 3.4 有状态 / 无状态

| 组件 | 状态 | 水平扩展 | 说明 |
|---|---|---|---|
| control-api | 无状态 | 容易 | 会话用 JWT，不存本地 |
| celery worker | 无状态 | 容易 | 任务幂等即可加机 |
| voice-orchestrator | **有状态** | 难（会话粘性） | 一通通话的 WS 会话绑定单实例；扩容需一致性哈希按 call_uuid 分片 |
| FreeSWITCH | 有状态 | 需边界代理 | V1 单机；V2 加 SBC + 多 FS 池 |
| PostgreSQL / Redis / OSS | 有状态 | 主从/集群 | 见 §13 |

---

## 4. 权限体系

### 4.1 权限矩阵（角色 × 功能 × 数据范围）

数据范围：`T` = 本租户全量；`T-own` = 本人创建；`SELF` = 仅本人；`ALL` = 跨租户（仅平台侧）。

| 功能 | platform_admin | tenant_admin | operator | agent | auditor |
|---|---|---|---|---|---|
| 租户/资质管理 | ALL（可写） | T（只读） | — | — | T（只读） |
| 线路与号码池 | ALL（可写） | T（绑定/解绑自有线路） | T（只读） | — | T（只读） |
| 名单与授权凭证 | ALL | T CRUD | T CRUD | — | T 只读 |
| DND/黑名单 | ALL | T CRUD | T 新增 | — | T 只读 |
| 外呼任务 | ALL | T CRUD | T CRUD | — | T 只读 |
| 话术模板/红线库 | ALL（全局默认） | T CRUD | T 草稿+提交审核 | — | T 只读 |
| 实时通话监听 | T（应急） | T | T | — | T 只读 |
| 录音与转写 | T（需二次确认） | T | T（限本人任务） | 转接给自己的通话 | T 只读（全量） |
| 投诉工单 | ALL | T 处理 | T 新增 | T 处理 | T 只读 |
| 报表 | ALL | T | T | SELF（本人转接量） | T |
| 账单/计费 | ALL | T 只读 | — | — | T 只读 |
| 调证包导出 | ALL | T（二次确认） | — | — | T（二次确认） |
| 平台全局合规策略 | ALL（可写） | — | — | — | — |

### 4.2 数据过滤规则（伪代码）

```python
def scope_filter(user, model, stmt):
    if user.role == "platform_admin":
        return stmt                                  # 跨租户，但查询必须带 X-Tenant-Override 并记审计
    # 其余所有角色：一律强制 tenant_id
    stmt = stmt.where(model.tenant_id == user.tenant_id)
    if user.role == "operator" and model.immutable:   # 录音/转写不可变表
        stmt = stmt.where(model.created_by == user.id)
    if user.role == "agent" and model.table == "call_transfer":
        stmt = stmt.where(model.agent_id == user.id)
    if user.role == "auditor":
        stmt = stmt.where(sql.false())  # 写操作一律拒绝 → 由 RBAC 中间件在路由层先拦
    return stmt
```

**双重保障**：应用层 `scope_filter` + 数据库 RLS。

```sql
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON calls
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```
每次请求进入时：`SET LOCAL app.tenant_id = :tenant_id;`（同一事务内），**忘记设置 = 查不到数据而非越权**（安全失败默认）。

### 4.3 前端路由守卫

| 路由前缀 | 允许角色 | 不满足时 |
|---|---|---|
| `/login` | 匿名 | — |
| `/t/*` | 任意已登录租户角色 | 未登录 → `/login`；角色不匹配 → `/403` |
| `/t/audit/*` | tenant_admin, auditor | `/403` |
| `/t/billing/*` | tenant_admin | `/403` |
| `/p/*`（平台后台） | platform_admin | `/403` |
| 首次登录 `must_change_password=true` | — | 强制跳 `/force-password`，拦截其余全部路由 |

### 4.4 敏感操作二次确认清单

需二次确认（重新输入密码或 TOTP）：导出调证包 / 导出录音、批量删除名单、解绑线路、修改全局合规策略、关闭合规闸门（仅平台超管，且必须填写理由 + 双人复核）、修改 DND 全局策略、手动重开被熔断的租户。

---

## 5. 数据模型

### 5.1 ER 关系（拓扑顺序 = 建表/种子顺序）

```
tenants ─┬─< user_roles >─ users
         ├─< qualifications
         ├─< trunks ─< caller_numbers ─< number_health_stats
         ├─< consent_records ─< contact_optins
         ├─< contact_lists ─< contacts
         ├─< scenario_templates ─< script_nodes / redline_rules
         ├─< campaigns ─< tasks ─< calls ─┬─< call_turns
         │                               ├─< call_events
         │                               ├─< recordings
         │                               ├─< transcripts
         │                               ├─< qc_results
         │                               ├─< call_transfers
         │                               └─< billing_items
         ├─< dnd_entries
         ├─< complaints
         ├─< audit_logs
         └─< plans / subscriptions / invoices
```

### 5.2 核心表 DDL（节选，字段完整可用）

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";      -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;         -- 号码/姓名模糊查

-- 时间统一 TIMESTAMPTZ，应用层统一 aware UTC（见 §16）
CREATE TABLE tenants (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name            text NOT NULL,
  code            text NOT NULL UNIQUE,              -- 短码，用于日志/线路前缀
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','active','suspended','closed')),
  plan_code       text NOT NULL DEFAULT 'trial',
  allowed_scenarios text[] NOT NULL DEFAULT '{}',     -- 灰度：{notification,callback_survey}
  max_concurrency int NOT NULL DEFAULT 2 CHECK (max_concurrency BETWEEN 1 AND 1000),
  daily_call_quota int NOT NULL DEFAULT 200 CHECK (daily_call_quota > 0),
  compliance_mode text NOT NULL DEFAULT 'strict'
                  CHECK (compliance_mode IN ('strict','custom')),
  rls_bypass      boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  version         int NOT NULL DEFAULT 0              -- 乐观锁
);

CREATE TABLE qualifications (                        -- 租户资质：准入即卡点
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  doc_type  text NOT NULL CHECK (doc_type IN
             ('business_license','telco_value_added_license','callcenter_license',
              'number_certificate','consent_policy','script_filing','other')),
  doc_no    text,
  file_key  text NOT NULL,                           -- OSS key，禁止裸路径
  expires_at date,
  status    text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','expired')),
  reviewed_by uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at timestamptz,
  remark    text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, doc_type, file_key)
);

CREATE TABLE trunks (                                -- 线路：BYOC 为默认
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  name text NOT NULL,
  mode text NOT NULL DEFAULT 'byoc' CHECK (mode IN ('byoc','managed')),
  provider text NOT NULL,
  sip_host text NOT NULL, sip_port int NOT NULL DEFAULT 5060,
  sip_transport text NOT NULL DEFAULT 'udp' CHECK (sip_transport IN ('udp','tcp','tls')),
  auth_user text, auth_credential_enc bytea,         -- AES-256-GCM 密文
  max_concurrency int NOT NULL DEFAULT 5 CHECK (max_concurrency > 0),
  allowed_scenarios text[] NOT NULL DEFAULT '{}',     -- 该线路被许可的呼出用途（硬约束）
  status text NOT NULL DEFAULT 'inactive' CHECK (status IN ('inactive','active','blocked')),
  created_at timestamptz NOT NULL DEFAULT now(),
  version int NOT NULL DEFAULT 0,
  UNIQUE (tenant_id, name)
);

CREATE TABLE caller_numbers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  trunk_id  uuid NOT NULL REFERENCES trunks(id) ON DELETE CASCADE,
  number    text NOT NULL,                            -- 真实可溯源主叫号，禁止伪造/随机化
  region    text NOT NULL,
  status    text NOT NULL DEFAULT 'active' CHECK (status IN ('active','cooling','blocked')),
  complaint_count_30d int NOT NULL DEFAULT 0,
  complaint_rate_30d  numeric(6,4) NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, number)
);

CREATE TABLE consent_records (                        -- 授权凭证：无凭证不可拨
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  phone_enc bytea NOT NULL,                           -- AES-256-GCM
  phone_last4 char(4) NOT NULL,                       -- 明文后 4 位，用于查询/模糊匹配
  phone_hash char(64) NOT NULL,                       -- HMAC-SHA256(pepper, phone)，等值查/去重
  scenario_code text NOT NULL,
  source    text NOT NULL CHECK (source IN
            ('form','app','contract','sms_confirm','ivr_confirm','import_with_proof','offline_sign')),
  proof_key text,                                     -- 授权凭证文件 OSS key
  consented_at timestamptz NOT NULL,
  expires_at timestamptz,
  revoked   boolean NOT NULL DEFAULT false,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, phone_hash, scenario_code)
);
CREATE INDEX ON consent_records (tenant_id, phone_hash, scenario_code) WHERE NOT revoked;

CREATE TABLE campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  scenario_code text NOT NULL,
  template_id uuid NOT NULL REFERENCES scenario_templates(id) ON DELETE RESTRICT,
  list_id uuid NOT NULL REFERENCES contact_lists(id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','pending_review','approved','running','paused','done','aborted')),
  start_at timestamptz NOT NULL, end_at timestamptz NOT NULL,
  call_window text NOT NULL DEFAULT '09:00-19:00',    -- 本地时区 Asia/Shanghai
  timezone text NOT NULL DEFAULT 'Asia/Shanghai',
  max_concurrency int NOT NULL DEFAULT 2,
  freq_per_phone_daily int NOT NULL DEFAULT 1,        -- 同号码每日最多呼叫次数
  freq_per_phone_7d   int NOT NULL DEFAULT 3,
  retry_policy jsonb NOT NULL DEFAULT '{"max_retry":2,"interval_min":[60,1440]}'::jsonb,
  trunk_id uuid NOT NULL REFERENCES trunks(id) ON DELETE RESTRICT,
  caller_number_id uuid REFERENCES caller_numbers(id) ON DELETE SET NULL,
  created_by uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  version int NOT NULL DEFAULT 0
);

CREATE TABLE calls (                                  -- 呼叫主表（含状态机）
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  campaign_id uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
  call_uuid uuid NOT NULL UNIQUE,                     -- FS 侧 uuid
  status text NOT NULL DEFAULT 'queued' CHECK (status IN
    ('queued','dialing','ringing','answered','talking','interrupted',
     'transferring','transfer_failed','completed','no_answer','busy',
     'rejected','failed','blocked_compliance','cancelled')),
  block_reason text,                                  -- 命中哪条合规规则（如 DND/时段/无授权）
  caller_number text, trunk_id uuid,
  start_at timestamptz, answer_at timestamptz, end_at timestamptz,
  duration_sec int NOT NULL DEFAULT 0,
  ai_duration_sec int NOT NULL DEFAULT 0,
  human_duration_sec int NOT NULL DEFAULT 0,
  turn_count int NOT NULL DEFAULT 0,
  intent_grade text CHECK (intent_grade IN ('A','B','C','D','NA')),
  summary text,
  compliance_hit jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{rule_id,at,action}]
  barge_in_count int NOT NULL DEFAULT 0,
  latency_p50_ms int, latency_p95_ms int,
  cdr jsonb,                                          -- 运营商原始话单
  idempotency_key text UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  version int NOT NULL DEFAULT 0
);
CREATE INDEX ON calls (tenant_id, campaign_id, created_at DESC);
CREATE INDEX ON calls (tenant_id, status) WHERE status IN ('queued','talking');

CREATE TABLE recordings (                             -- append-only，无删除接口
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  call_id uuid NOT NULL REFERENCES calls(id) ON DELETE RESTRICT,
  file_key text NOT NULL UNIQUE,
  seconds int NOT NULL, codec text NOT NULL DEFAULT 'wav16',
  aigc_label_meta jsonb,                              -- 隐式标识写入结果（见 §12）
  retention_until timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dnd_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  phone_hash char(64) NOT NULL, phone_last4 char(4) NOT NULL,
  source text NOT NULL CHECK (source IN
         ('user_said_no','complaint','carrier_report','operator_manual','global_12321')),
  scope text NOT NULL DEFAULT 'tenant' CHECK (scope IN ('tenant','platform')),
  permanent boolean NOT NULL DEFAULT true,
  created_by uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, phone_hash)
);

CREATE TABLE audit_logs (                             -- 仅追加
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id uuid, user_id uuid, action text NOT NULL,
  target_type text, target_id text,
  detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip inet, trace_id text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_logs (tenant_id, created_at DESC);
CREATE INDEX ON audit_logs (action, created_at DESC);
```

### 5.3 JSONB 结构示例

**话术树节点（`scenario_template.nodes`，关键节点硬约束）**

```json
{
  "nodes": [
    {"id":"n_open","type":"mandatory","text_key":"open.ai_notice_and_purpose",
     "must_say":["ai_identity","company","purpose"],"barge_in":true,"next":["n_gate"]},
    {"id":"n_gate","type":"gate","desc":"确认是否方便接听",
     "intent_map":{"agree":"n_ask","refuse":"n_bye","invalid":"n_retry"},
     "fallback_after_ms":6000,"next":["n_ask","n_bye","n_retry"]},
    {"id":"n_ask","type":"llm_free","desc":"自由问答（受红线与知识域约束）",
     "knowledge_scope":"product_faq","guard":"redline_set_default",
     "must_handoff_on":["ask_human","complaint","legal_threat"],"next":["n_close","n_handoff"]},
    {"id":"n_handoff","type":"transfer","skill":"sales","no_agent_action":"callback_30min"},
    {"id":"n_bye","type":"mandatory","text_key":"bye.polite_and_dnd_offer",
     "must_say":["dnd_offer"],"action_on_say":"mark_cooldown_30d","next":[]},
    {"id":"n_close","type":"mandatory","text_key":"close.summarize_confirm","next":[]}
  ],
  "variables":["customer_name","product_name","appoint_time","amount"],
  "intent_taxonomy":["agree","refuse","hesitate","inquiry","complaint","invalid"]
}
```

**合规策略集（`tenants.compliance_config`）**

```json
{
  "call_window_default":"09:00-19:00","forbid_window":["12:00-14:00"],
  "freq":{"per_phone_daily":1,"per_phone_7d":3,"per_phone_30d":6},
  "dnd":{"enforce_platform_global":true,"cooldown_after_refuse_days":30,
         "cooldown_after_no_answer_days":7},
  "ai_notice":{"mode":"mandatory","placement":"first_3_seconds",
               "text":"您好，我是XX公司的AI智能助理，本次来电是……"},
  "recording_notice":"mandatory_in_opening",
  "redline_set":"default","auto_hangup_on":["abuse","complaint_x2","demand_stop_x2"],
  "circuit":{"complaint_rate_warn":0.003,"complaint_rate_stop":0.010,
             "reject_rate_stop":0.85,"answer_rate_floor":0.15}
}
```

### 5.4 索引清单（补充）

```sql
CREATE INDEX ON calls (tenant_id, contact_id, created_at DESC);
CREATE INDEX ON calls (campaign_id, status, created_at DESC);
CREATE INDEX ON tasks (status, next_run_at) WHERE status='pending';
CREATE INDEX ON transcripts USING gin (to_tsvector('simple', content));
CREATE INDEX ON billing_items (tenant_id, bill_period);
CREATE INDEX ON complaints (tenant_id, status, created_at DESC);
```

### 5.5 迁移与大表策略

- 工具 Alembic（异步 env.py 配置见 §16）。所有 DDL 走 migration，禁止手工改表。
- `calls` / `call_turns` / `audit_logs` 为快增表。按月做**分区**（`PARTITION BY RANGE (created_at)`），V1 建 3 个月分区 + 默认分区，Celery 每日任务预建下月分区。
- 1000 通/天规模下：`calls` 约 3 万行/月，`call_turns` 约 18 万行/月（按 6 轮/通），`audit_logs` 约 10 万行/月。单表 < 50 万/月，暂不需归档；**`call_turns` 与 `transcripts` 超过 12 个月后按月归档到冷存**（OSS + Parquet）。

---

## 6. 功能模块详细设计

### 6.1 租户开通与资质准入

**流程**：平台超管创建租户 → 上传资质（营业执照、增值电信许可/呼叫中心许可、码号证书、授权政策、话术备案） → 审核（通过/驳回） → 配置 `allowed_scenarios` 与配额 → 绑定线路 → 冒烟测试（测试号自呼 1 通） → `active`。

**业务规则**
- `telesales` 场景要求：资质含租户自有电信/呼叫中心许可 **且** 线路 `mode='byoc'` 且 `trunk.allowed_scenarios` 含 `telesales`，三者缺一 → 任务审核不通过，错误码 `COMPLIANCE_QUALIFICATION_MISSING`。
- 资质临期 30/7/1 天三级告警，过期自动 `suspended`（只允许已完成通话收尾，不再发起新呼叫）。

**异常处理**：审核中租户登录 → 看到「资质审核中」页 + 材料清单，不报错；`file_key` 拉取失败 → 前端占位图 + 「文件加载失败，可重试」。

**数据流向**：`POST /api/p/tenants` → `TenantService.create` → 写 `tenants` + `qualifications` → 写 `audit_logs` → 返回 `tenant_id`。

**幂等**：`POST /api/p/tenants` 要求 `Idempotency-Key`，命中唯一约束 `code` 冲突返回已有租户（409 + 原 id）。

### 6.2 线路与号码池

- 支持录入 SIP trunk 参数，凭据用字段级 AES-256-GCM 加密，展示恒为 `****`。
- **线路可用场景硬绑**：`trunk.allowed_scenarios` 决定该线路能承载哪些 `scenario_code`；任务绑定线路时校验，不匹配即拒（这是 81 号文约束在代码里的落点）。
- 号码健康度：每日任务统计每个主叫号的接通率/拒接率/投诉数；`complaint_rate_30d ≥ stop` → 自动 `blocked` + 通知租户；线路整体接通率 < `answer_rate_floor` → 提示更换号码（号码被标记征兆）。
- **并发令牌**：`Redis` 双层令牌桶（`trunk:{id}:conc`、`tenant:{id}:conc`），语音编排服务呼叫前 `Lua` 原子取令牌，通话结束释放。

**异常**：SIP 注册失败 → 线路 `inactive` + 展示失败原因（`403 Forbidden`/`DNS 失败`区分）；中继不通 → 外呼任务自动 `paused` 并告警，不静默失败。

### 6.3 名单导入与授权校验

**导入**：Excel/CSV（≤ 50MB，≤ 20 万行）→ 落临时文件 → Celery 异步解析 → 逐行校验：手机号格式、去重（`phone_hash`）、**授权匹配**（`consent_records` 中存在未过期未撤回且 `scenario_code` 匹配的记录）、DND 命中、平台全局 DND。

**结果**：三态分类 `可呼 / 无授权 / DND命中`，并给出每类**具体原因**（如「授权已过期 2026-10-01」「场景不匹配：授权=notification，任务=telesales」）。**无授权号码不是报错，是不进外呼队列**，且在报表中单列 —— 让「打了多少不能打的」变成可见数字，是产品合规设计的一部分。

**并发控制**：同一名单同时只允许一个导入任务（`contact_lists.import_status` 乐观锁）。
**幂等**：导入任务以 `(list_id, file_md5)` 唯一，重复导入同一文件直接返回上次结果。
**异常**：格式错误 → 返回前 50 条错误行与行号；文件超阈值 → 转后台并推送站内信；解析超时 → 任务标 `failed`，可重跑（幂等）。

### 6.4 外呼任务与调度

**状态机**：`draft → pending_review → approved → running ⇄ paused → done | aborted`

**规则**
- 任务启动前二次校验：租户 `active`、场景在 `allowed_scenarios` 内、资质未过期、线路 `active` 且场景匹配、当前时间在 `call_window` 内、未在 `forbid_window`。
- 令牌桶控制并发；重拨按 `retry_policy` 生成 `tasks(next_run_at)`；命中 `rejected` 立即写冷却，**不做当日二次骚扰**。
- 熔断：单任务 5 分钟内拒接率 > 85% 自动 `paused`（提示「号码质量差或时段不合适」）；租户级投诉率超阈 → 全租户 `aborted`。

**数据流向**：`POST /api/t/campaigns/{id}/start` → `CampaignService.start`（校验 + 状态流转 + 乐观锁） → Celery `dispatch_campaign` → 逐条 `build_task` → `call_service.dial` → FS originate → 事件回灌 → `calls` 更新 + WS 推送。

**幂等**：start/stop 均带 `Idempotency-Key`；`build_task` 以 `(campaign_id, contact_id, attempt_no)` 唯一约束保证不重复外呼。

### 6.5 实时语音对话引擎（核心）

**一轮对话链路**

```
RTP → FreeSWITCH(mod_audio_fork) → WS → voice-orchestrator
   → VAD(silero) 判停 → 流式ASR(增量结果) → 意图+状态判定
   → 合规闸门(前置红线校验) → 话术树(节点约束) / LLM(自由应答, 流式)
   → 分句 → 流式TTS → 注入回 FS 播放
```

**延迟预算（P50 目标 1200ms）**

| 环节 | 预算 | 优化手段 |
|---|---|---|
| VAD 判停 | 350–500ms | 自适应静音阈值；句尾语气词提前判停 |
| ASR 终稿 | 100–200ms | 流式增量，终稿只做校正 |
| LLM 首 token | 300–500ms | 短 prompt、上下文压缩、模型预热、多模型主备 |
| TTS 首包 | 150–300ms | 首句分句即合成；高频短语预合成缓存 |
| 编解码/网络 | ≤ 100ms | 本地部署 FS，就近接入 |

**关键能力**
- **打断（barge-in）**：AI 播报中检测到人声 → 立即停止 TTS 播放并清空待播队列，`barge_in_count++`。
- **填充语**：LLM 首 token > 400ms 时，插入预合成「嗯」「我帮您看下哈」，把等待藏在语音里。
- **关键节点不可跳过**：`type=mandatory` 节点由引擎强制播报，LLM 不得改写 `must_say` 内容。
- **知识域受限**：`llm_free` 节点只注入 `knowledge_scope` 对应片段，模型不可答域外内容（防幻觉承诺价格/条款）。
- **超时无输入**：`fallback_after_ms` 触发一次澄清，二次无输入 → `n_bye` 礼貌收尾。

**异常与降级**：见 §13 降级层级（ASR 不可用 → 转话术树 IVR + 提示按键；TTS 不可用 → 转人工或改期；LLM 不可用 → 走纯话术树；全链路不可用 → 礼貌挂断 + 改期，且**绝不静默断线**）。

### 6.6 合规引擎（Compliance Gate）

三道闸门，全部在服务端，前端隐藏按钮不算合规。

1. **呼前闸门**（`pre_dial_gate`）：授权有效性、DND（租户级 + 平台全局）、时段、频次、场景-资质-线路匹配、租户配额。任一不过 → `calls.status='blocked_compliance'` + `block_reason`，**不产生呼叫、不计费**。
2. **呼中闸门**（`in_call_guard`）：
   - 开场 3 秒内必含 AI 身份声明，未播报则引擎强制插入并记录 `compliance_hit`；
   - 租户/模型侧输出文本先过**红线规则集**（正则 + 关键词 + 轻量分类模型双层）：绝对化承诺（「保证收益」「百分百」「稳赚」）、虚构身份（「我是银行/公安」）、诱导借贷、辱骂、泄露他人信息等 → 拦截该句、改说合规模板句，累计 2 次 → 强制转人工或挂断；
   - 被叫明确表达「不要再打」→ 立即写 `dnd_entries`（`source=user_said_no`）+ 播报确认 + 挂断，**同事务**；
   - 要求转人工/投诉 → 立即转接。
3. **呼后闸门**：录音 + 转写落库、隐式标识写入、100% 自动质检（红线复扫 + 服务规范）、投诉识别、合规台账生成。

**数据流向**：闸门为独立 `ComplianceService`，被 `call_service`（呼前）、`voice-orchestrator`（呼中，同步阻塞 ≤ 30ms，本地规则引擎为主）、`celery qc`（呼后）调用；命中一律写 `call_events` + `audit_logs`。

### 6.7 转人工

热转接（AI 桥接 → 转坐席队列 → 坐席弹屏带 AI 小结与转写）。无坐席可用 → `n_handoff.no_agent_action`：登记回拨承诺（30 分钟内/次日）并写 `tasks`；转接失败 → AI 告知并承诺回拨，`call_transfers.status='failed'` 计入坐席容量告警。

### 6.8 质检与意向分级

呼后 Celery：录音 → 转写（分轨）→ ①红线复扫 ②服务规范（是否亮明 AI、是否告知用途、是否给出拒绝通道、是否辱骂）③意向分级 A/B/C/D ④通话小结与结构化字段抽取 ⑤情绪与投诉识别。

质检结论等级：`pass` / `warn`（有瑕疵，进复核队列）/ `fail`（触碰红线，任务级告警 + 阻断同话术后续任务）。

### 6.9 CDR、对账与计费

三方数据源：本地 `calls.duration`、FS/租户中继原始 CDR（`calls.cdr`）、计费明细 `billing_items`。

**三层保障**：①FS 事件回调落 CDR（幂等，`call_uuid` 去重）②`answered/completed` 但 60s 无 CDR → 主动查询 FS `uuid_setvar`/CDR API，按 30s/1min/3min 三次 ③每日 02:00 与租户中继账单对账，差异（时长差 > 5% 或本地有/账单无）进 `billing_dispute` 人工队列并告警，**不自动扣费**。

计费模型（V1）：`套餐费（按档位含并发与分钟额度）+ 超量语音分钟费 + AI 用量（ASR/TTS 分钟 + LLM token）+ 号码月租`。账单口径以 CDR 为准，向租户展示逐项明细。⚠️ 所有第三方单价为可配置项（`billing_rates` 表），上线前必须用合同价填充，代码不得硬编码。

### 6.10 投诉与 DND 联动

投诉来源：通话中识别、坐席登记、租户录入、运营商/12321 通报录入。规则：投诉即写 DND + 该号码 30 天全租户冷却（平台级可选）+ 关联主叫号 `complaint_count_30d++` + 超阈熔断线路/租户。工单 SLA：24h 内响应、5 工作日内反馈处置结果（对齐 12321 转办时限口径）。

### 6.11 报表

租户侧：接通率、平均时长、意向分布（A/B/C/D）、转人工率、拒接率、AI 独立完成率、合规命中 Top 规则、无授权/DND 拦截量、成本明细。平台侧：跨租户并发水位、各供应商可用性、延迟分位、全局投诉率。全部提供「时间范围 + 任务/话术版本」双维度对比（P1）。

---

## 7. AI Prompt 设计

> 统一约定：所有 prompt 走 `llm/prompts/*.j2`；**Temperature 0.3、Top_p 0.8、max_tokens 150（口播场景短）**；系统提示末尾固定注入合规契约（不得承诺未授权内容、不得隐瞒 AI 身份、不得索取身份证号/银行卡完整号）。上下文长度上限 6000 token，超出按「保留最近 8 轮 + 摘要前文」压缩。

### 7.1 P-OPEN 开场与身份声明（模板化，非 LLM 生成）

**不使用 LLM**，由模板 + 变量拼接后直接 TTS，保证「AI 身份 + 主体 + 用途 + 可拒绝」四要素 100% 出现。这是刻意的产品决策：合规文本不能被模型改写。

### 7.2 P-INTENT 意图与状态判定

```
System: 你是外呼通话中的意图判定器。只输出 JSON，不解释。
六类意图定义：agree(同意/配合) refuse(明确拒绝) hesitate(犹豫/需考虑)
inquiry(提问咨询) complaint(不满/投诉倾向) invalid(无意义/语音信箱/静音)。
额外判定：ask_human(要求人工)、demand_stop(要求不再来电)。
禁止输出定义外的值。
Input: {last_user_text, node_id, asr_confidence}
Output JSON Schema:
{"type":"object","required":["intent","confidence"],
 "properties":{"intent":{"enum":["agree","refuse","hesitate","inquiry","complaint","invalid"]},
   "ask_human":{"type":"boolean"},"demand_stop":{"type":"boolean"},
   "confidence":{"type":"number","minimum":0,"maximum":1}},
 "additionalProperties":false}
```
**降级**：LLM 不可用 → 关键词 + ASR 置信度规则引擎（拒绝/转人工关键词表命中即判），准确率降至约 80%，报表标注「规则模式」。
**格式异常容错**：JSON 解析失败 → 重试 1 次（temperature=0）→ 仍失败按 `inquiry` 处理并强制走澄清话术，记 `llm_format_error`；缺字段按默认值（confidence=0.5）。
**Fallback 值**：`intent="inquiry"`, `confidence=0.5`。

### 7.3 P-RESPONSE 应答生成（主对话）

```
System: 你是{company}的 AI 智能助理，正在通过电话与{customer_name}沟通。
【硬性规则，违反即为事故】
1 你必须在被问及时明确说明自己是人工智能助理，绝不冒充真人、银行或政府机构人员。
2 只能依据【知识域】回答；知识域外一律回答"这个问题我记下来，让同事回电给您"。
3 禁止任何承诺性表述（收益、价格、疗效、通过率、时间保证），禁止绝对化用词。
4 对方明确拒绝或要求不再来电时，礼貌确认并结束，不得挽留超过一次。
5 一次只说一到两句，口语化，不用书面语、不用星号括号等特殊符号。
【当前节点】{node_desc}  【必须传达】{must_say}  【知识域】{knowledge}
【对话历史】{history}
Output: 纯文本口播内容（≤ 80 字），不要引号与前后缀。
```
**Token 控制**：知识域用检索后 Top-3 片段注入；历史用最近 8 轮 + 滚动摘要。
**降级**：见 §13 L1；连续 2 次失败 → 切纯话术树模式（`calls` 标记 `mode=script_tree`）。

### 7.4 P-GUARD 红线校验（低延迟，本地优先）

先跑本地规则（Aho-Corasick 关键词 + 正则 + 否定词处理，< 5ms），命中即拦不查模型；未命中且句子含承诺/金额/身份类词时才调用轻量模型二次判定（timeout 30ms，超时放行但记 `guard_timeout` 供事后复核）。

### 7.5 P-QC 质检与小结

```
Input: 双轨转写全文 + 话术模板要求
Output JSON: {"intent_grade":"A|B|C|D|NA","summary":"≤60字",
 "structured":{"appoint_time":"","amount":null,"objection":""},
 "qc":{"redline_hits":[{"rule_id":"","turn_idx":0}],
       "ai_notice_present":true,"purpose_stated":true,"opt_out_offered":true,
       "result":"pass|warn|fail"},"next_action":"callback|sms|close|complaint_ticket"}
```
**降级**：转写缺失或过长 → 只跑规则质检（AI 身份、时段、时长），`summary` 置 `null`，前端显示「AI 小结生成中」，任务重跑；**不得用假小结填充**。

---

## 8. 页面清单与路由

| 角色 | 页面 | 路由 | 说明 |
|---|---|---|---|
| 全部 | 登录 / 强制改密 | `/login` `/force-password` | 登录失败 5 次锁 15 分钟 |
| operator | 控制台首页 | `/t/dashboard` | 今日呼叫、并发水位、合规命中、异常任务 |
| operator | 外呼任务 | `/t/campaigns` `/:id` | 列表 + 详情（名单进度、实时队列） |
| operator | 名单与授权 | `/t/lists` `/t/consents` | 导入向导（三态结果）、授权台账 |
| operator | 话术工作室 | `/t/templates` `/:id/nodes` | 节点编辑器、变量、试打断模拟（沙箱自测） |
| operator | 实时通话 | `/t/live` | WS 实时转写、监听、强转人工 |
| agent | 我的来电/回拨 | `/agent/queue` | 弹屏含 AI 小结与转写 |
| agent | 通话小结 | `/agent/calls/:id` | 填写结构化小结 |
| tenant_admin | 线路与号码 | `/t/trunk` | SIP 配置、健康度、号码标记预警 |
| tenant_admin | 成员与权限 | `/t/members` | 邀请、角色、禁用 |
| tenant_admin | 报表 / 账单 | `/t/reports` `/t/billing` | 明细导出（异步任务） |
| auditor | 合规中心 | `/t/audit/recordings` `/compliance` `/dnd` `/complaints` | 全量录音、红线台账、DND、投诉、调证包 |
| platform_admin | 平台后台 | `/p/tenants` `/p/trunks` `/p/policies` `/p/billing` | 租户审核、全局策略、熔断、账单 |

**列表页规范**：默认 `page_size=20`（可选 20/50/100），`sort=-created_at`，搜索防抖 400ms，筛选写入 URL query（可分享）。

**异常态定义（每个页面必须实现）**：加载中（骨架屏）/ 空数据（插画 + 引导按钮）/ 失败（原因 + 重试）/ 无权限（403 页）/ 超时（「请求超时，已重试 1 次仍失败」）/ 服务降级（顶部黄色横幅显示 L1/L2 提示与影响范围）。

**弱网兜底**：话术编辑器本地 `localStorage` 草稿（每 30s），提交失败保留草稿并提示；列表请求失败展示上次缓存数据 + 「数据更新于 X」。

---

## 9. API 接口清单

统一前缀 `/api`；鉴权 `Authorization: Bearer <JWT>`；租户上下文由 token 中 `tenant_id` 决定，**请求体/查询参数不接受 tenant_id**（防越权）。幂等写接口需 `Idempotency-Key`（UUID，服务端保留 24h）。统一响应：`{code, message, data, trace_id}`。

### 9.1 认证与用户（6 个）

| 方法 | 路径 | 说明 | 角色 |
|---|---|---|---|
| POST | `/api/auth/login` | 登录 | 匿名 |
| POST | `/api/auth/refresh` | 刷新 token | 已登录 |
| POST | `/api/auth/logout` | 登出（黑名单） | 已登录 |
| POST | `/api/auth/change-password` | 改密 | 已登录 |
| GET | `/api/users/me` | 当前用户与权限 | 已登录 |
| POST | `/api/t/members` | 邀请成员 | tenant_admin |

`POST /api/auth/login` Schema：`{username: string(3-64, required), password: string(8-64, required), captcha_token?: string}`
响应：`{access_token:string, expires_in:int, must_change_password:bool, role:string, tenant_id:uuid}`
失败：401 `BAD_CREDENTIALS`；423 `ACCOUNT_LOCKED`（含 `unlock_at`）。

### 9.2 租户与准入（平台侧，7 个）

`POST /api/p/tenants`、`PATCH /api/p/tenants/{id}`、`GET /api/p/tenants`（分页）、`GET /api/p/tenants/{id}`、`POST /api/p/tenants/{id}/qualifications`、`POST /api/p/qualifications/{id}/review`（body `{status:'approved'|'rejected', remark:string}`）、`POST /api/p/tenants/{id}/suspend|resume`。

### 9.3 线路与号码（6 个）

`POST/GET/PATCH /api/t/trunks`、`POST /api/t/trunks/{id}/test-call`（自呼冒烟）、`POST/GET /api/t/caller-numbers`。
`POST /api/t/trunks` 关键校验：`sip_host` 非内网保留地址（防 SSRF 指向内网）、`max_concurrency ≤ 租户 max_concurrency`、`allowed_scenarios ⊆ 租户 allowed_scenarios`。

### 9.4 名单与授权（8 个）

`POST /api/t/lists`、`POST /api/t/lists/{id}/import`（multipart，返回 `task_id`）、`GET /api/t/imports/{task_id}`（进度 + 三态统计 + 错误行）、`GET /api/t/contacts`、`DELETE /api/t/contacts`（软删，需二次确认）、`POST /api/t/consents`、`GET /api/t/consents`、`POST /api/t/consents/{id}/revoke`。

`GET /api/t/imports/{task_id}` 响应示例：
```json
{"code":0,"data":{"task_id":"...","status":"done","total":12000,
 "dialable":8320,"no_consent":2100,"dnd_hit":1580,"invalid_format":0,
 "errors":[{"row":37,"reason":"phone_invalid"}]}}
```

### 9.5 任务与呼叫（9 个）

`POST /api/t/campaigns`、`GET /api/t/campaigns`、`GET /api/t/campaigns/{id}`、`PATCH /api/t/campaigns/{id}`（乐观锁，409 冲突）、`POST /api/t/campaigns/{id}/submit-review`、`POST /api/t/campaigns/{id}/start|pause|abort`、`GET /api/t/campaigns/{id}/stats`、`GET /api/t/calls`、`GET /api/t/calls/{id}`（含 turn 级转写）。

`POST /api/t/campaigns` 请求 Schema（节选）
```json
{"name":"string(1-64,required)","scenario_code":"enum(notification|callback_survey|telesales|collection),required",
 "template_id":"uuid,required","list_id":"uuid,required","trunk_id":"uuid,required",
 "start_at":"ISO8601,required","end_at":"ISO8601,required(>start_at)",
 "call_window":"regex ^([01]\\d|2[0-3]):[0-5]\\d-([01]\\d|2[0-3]):[0-5]\\d$, default 09:00-19:00",
 "max_concurrency":"int 1..tenant.max_concurrency, default 2",
 "freq_per_phone_daily":"int 1..3, default 1",
 "retry_policy":"{max_retry:0..3, interval_min:int[]} optional"}
```
超时：普通接口 3s；`/import` 与 `/start` 为异步，返回 202 + `task_id`（10s 内必须响应，否则前端可安全重试，幂等保证不重复外呼）。

### 9.6 质检与合规（8 个）

`GET /api/t/calls/{id}/transcript`、`GET /api/t/qc-results`、`POST /api/t/qc-results/{id}/review`、`GET /api/t/compliance/events`、`GET/POST/DELETE /api/t/dnd`、`POST /api/t/complaints`、`PATCH /api/t/complaints/{id}`、`POST /api/t/audit/evidence-pack`（二次确认，异步产出下载链接，链接 30 分钟有效且一次性）。

### 9.7 计费与报表（5 个）

`GET /api/t/billing/summary`、`GET /api/t/billing/items`、`GET /api/t/reports/overview`、`GET /api/t/reports/export`（异步）、`GET /api/t/ws/tickets`（WS 鉴权票据）。

### 9.8 平台运维（4 个）

`GET /api/p/metrics`、`GET /api/p/circuit-status`、`POST /api/p/circuit/{tenant_id}/reset`（双人复核）、`GET /api/p/alerts`。

**API 总数：53 个**（V1 不含开放 API；开放 API 属 P1，另出接口文档）。

---

## 10. 高并发与性能方案

### 10.1 峰值估算（公式，不拍脑袋）

**业务面**：1000 通/天，集中在 09:00–19:00（10h）→ 均值 100 通/h；外呼有「整点起批」集中效应，峰值系数取 3 → 峰值 300 通/h ≈ **5 通/分钟** ≈ **0.083 CPS**（呼叫发起速率）。
并发路数 = 到达率 × 平均通话时长 = `0.083 × 90s ≈ 7.5`，与配额 10 路一致，符合。
**AI 调用量**：约 6 轮/通 → 峰值 `5×6 = 30 轮/分钟 = 0.5 LLM QPS`、ASR 常驻 10 路流、TTS ≈ 10×6/90 = 0.67 rps。
**Web 面**：20 租户 × ≤ 5 名活跃用户 = 100 人，人均 30 请求/日 → `100×30/86400×峰值系数5 ≈ 0.17 QPS`；实时大盘 100 个 WS 连接，每条消息 5 条/秒 ≈ 500 msg/s 推送（I/O 型，可扛）。
**结论**：瓶颈**不在 HTTP/Web/QPS**，而在①端到端语音延迟 ②ASR/TTS 供应商并发配额 ③运营商线路并发数 ④录音存储增长 ⑤voice-orchestrator 的有状态分片。

### 10.2 并发承载分析

| 操作 | 峰值同时 | 处理方式 | 响应目标 | 结论 |
|---|---|---|---|---|
| 打开控制台/列表查询 | 100 | 直接查询 + 索引 + 分页 | P95 < 300ms | 轻松 |
| 名单导入 20 万行 | 5 并发任务 | Celery + 批量 `COPY`（1000/批） | 完成 < 15 分钟 | 需异步，禁止同步导入 |
| 任务启动 | 20 | 校验 + 入队（202） | < 500ms | 令牌桶保护下游 |
| 呼叫发起 | 5/min（可配至 10 路） | 双层令牌桶 + FS originate | 应答 < 2s | 上限由线路并发决定 |
| 语音一轮对话 | 10 路 × ~4 轮/分 | voice-orchestrator asyncio | 首帧 < 1200ms | **核心瓶颈，见 §6.5 预算表** |
| 录音上传 OSS | 10 并发 | 边说边推（分片） | 不阻塞通话 | 带宽预算：10×2×16k≈0.6Mbps |
| 转写批处理 | 1000 通/日 | Celery 队列，`worker_prefetch=1` | 日终完成（P95 < 30min 延迟） | 供应商限流是唯一风险 |
| 调证包导出 | 2 | 异步 + 一次性链接 | 数百通 < 10 分钟 | 必须异步，避免同步打满 |
| 报表导出（月） | 3 | 异步 + 分页聚合 | < 5 分钟 | 大区间加 `from_db_replica` |

### 10.3 缓存策略

| 数据 | TTL | 失效 |
|---|---|---|
| 租户配置/合规策略 | 300s + 版本号 | 修改即删（write-through） |
| 话术模板（生效版） | 600s | 发布时删 |
| DND 布隆 + 精确集合 | 常驻，增量更新 | 新增即写（Redis Set + Bloom） |
| 授权校验结果 `phone_hash:scenario` | 120s | 撤回授权时按 hash 删 |
| 并发水位/实时统计 | 10s | 自然过期 |
| 红线规则集 | 300s | 规则发布即删 |

**防护**：DND/授权查询未命中 → 缓存空值 30s（防穿透）；模板加载 → 互斥锁防击穿（`SETNX` + 双检）；所有 TTL **加 ±10% 随机偏移**（防雪崩）。

### 10.4 队列与连接池

Celery：队列 `dispatch`（并发 4）、`io_heavy`（转写质检，并发 8）、`billing`（并发 2）、`report`（并发 2）；`acks_late=True` + 任务幂等键 + 最大重试 3 次（指数退避）；`task_time_limit` 按队列 60s/600s/120s/300s。
SQLAlchemy：`pool_size=10, max_overflow=10, pool_pre_ping=True, pool_recycle=1800`。
限流：全局 200 rps（Nginx `limit_req`）；单用户 30 rps；单 IP 登录 5 rps；写接口按租户 QPS 配额；429 返回 `Retry-After`。

### 10.5 幂等与锁

幂等接口：任务 start/pause/abort、呼叫发起（`Idempotency-Key` + `(campaign,contact,attempt)` 唯一）、计费入账（`call_uuid` 唯一）、投诉登记、CDR 落库。
乐观锁（`version`）：`campaigns`（并发启停）、`trunks`、`tenants`、`scenario_templates` → 409 `VERSION_CONFLICT`，前端提示「已被他人修改，请刷新」。
悲观锁：令牌桶扣减（Redis Lua 原子）、账单结转（`SELECT ... FOR UPDATE`）。

---

## 11. 安全设计

### 11.1 五层防护

**网络层**：全站 HTTPS（HTTP→301）；Nginx `server_tokens off`；安全头 `Strict-Transport-Security: max-age=31536000; includeSubDomains`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: strict-origin-when-cross-origin`、`Content-Security-Policy` 白名单；防火墙仅开 80/443/SSH（非默认端口）+ 运维 IP 白名单，**PostgreSQL/Redis/FS 内网端口一律不对外**；FS 的 SIP 端口仅允许已登记的中继源 IP（`acl`）。

**应用层**：JWT（HS256 → 生产建议 RS256，`access` 30min + `refresh` 7d，refresh 复用检测）；RBAC 每接口校验角色 + 数据范围；**租户隔离双保险**（应用层 `scope_filter` + RLS）；ORM 参数化（禁裸 SQL 拼接，排序字段白名单，防 `ORDER BY` 注入）；XSS 由 Vue 转义 + 富文本后端净化；JWT+Header 天然免疫 CSRF；CORS 白名单精确到协议与域名；上传校验扩展名 + MIME + 魔数 + 大小（Excel ≤ 50MB、录音 ≤ 200MB），存 OSS 且 `Content-Disposition: attachment`；生产关闭 `/docs`；登录 5 次失败锁 15 分钟；高危接口 `timestamp+nonce+sign` 防重放（5 分钟窗口 + Redis 去重）；转写/录音 URL 一律预签名（15 分钟）。

**数据层**：密码 `argon2id`（time=3, memory=64MB, parallelism=4）；字段级加密 `AES-256-GCM`（手机号、被叫号码凭据、SIP 凭据），密钥来自 `FIELD_ENC_KEY` 环境变量（KMS 可替换），**密文不可索引 → 用 `phone_hash`(HMAC-SHA256) 做等值查、`phone_last4` 做模糊查**；每日 02:30 `pg_dump` + WAL 归档 → OSS 加密异地存储，保留 30 天 + 每月 1 份保留 12 个月；备份文件本身加密；审计日志仅追加。

**运维层**：SSH 密钥登录 + 禁密码 + fail2ban；容器 `USER app` 非 root；基础镜像固定版本（`python:3.11.9-slim-bookworm`），生产固定 SHA256；`pip-audit`/`npm audit` 每月 + CI 阻断高危；生产错误不返堆栈（返回 `trace_id`）。

**密钥与证书生命周期**：SSL 到期前 30/7/1 天三级告警 + 自动续期；JWT 密钥支持新旧双验 24h 平滑轮换；ASR/TTS/LLM Key 支持双 Key 并存热切（改 `.env` + 重启）；数据库密码轮换走「建新用户 → 切 `.env` → 重启 → 删旧用户」；高危漏洞 24h 评估、72h 修复。

### 11.2 API 错误码

`200` 成功 / `400` 参数错误 / `401` 未认证或过期 / `403` 无权限 / `404` 不存在 / `409` 冲突（乐观锁、幂等重复、唯一约束）/ `422` 校验失败 / `423` 账号锁定 / `429` 限流 / `500` 服务器错误 / `503` 依赖熔断中。
业务码：`COMPLIANCE_NO_CONSENT`、`COMPLIANCE_DND_HIT`、`COMPLIANCE_WINDOW_CLOSED`、`COMPLIANCE_QUALIFICATION_MISSING`、`COMPLIANCE_SCENARIO_NOT_ALLOWED`、`COMPLIANCE_CIRCUIT_OPEN`、`TRUNK_UNREACHABLE`、`QUOTA_EXCEEDED`、`VERSION_CONFLICT`、`LLM_UNAVAILABLE`。

### 11.3 日志规范

JSON 结构化：`{timestamp,level,trace_id,user_id,tenant_id,action,duration_ms,status,ip,error_detail,extra}`；入口生成 `trace_id` 并全链路透传（含 FS 通道与外部 API），响应头 `X-Request-ID`。
脱敏（强制）：手机号 `138****5678`、身份证/银行卡不明文、录音 URL 不落日志、密码/Token/API Key 绝对禁止。
保留：INFO 30 天、ERROR 90 天、CRITICAL 180 天；单文件 100MB 轮转。
必记操作：登录/失败、权限拒绝、名单导入、授权写入与撤回、任务启停、**每一次呼前闸门拦截（含 block_reason）**、每一次呼中红线命中、AI 身份声明播报、DND 写入、外部 API 调用与失败、熔断状态变更、计费入账与对账差异、录音导出与调证包生成（最高敏感级）。

---

## 12. 合规与法务设计（专章）

> **本章不是附录，是产品需求。** 每条约束都必须在代码或数据模型里有落点，并标注「系统落点」。

### 12.1 适用规则清单（含时效，务必由法务复核后再定稿）

| 规则 | 关键要求（与本产品直接相关） | 系统落点 |
|---|---|---|
| 《民法典》第 1032、1033 条 | 除权利人明确同意外，不得以电话等方式侵扰他人私人生活安宁 | `consent_records` 强校验；无授权不进队列 |
| 《消费者权益保护法》第 29 条 / 《实施条例》第 24 条 | 未经消费者同意不得拨打商业性电话；收集使用个人信息须明示并经同意 | 授权来源枚举 + 凭证文件留存 + 撤回即失效 |
| 工信部信管〔2020〕81 号《关于加强呼叫中心业务管理的通知》 | 呼叫中心以呼入为主；**经用户同意方可提供即时回访和信息咨询等呼出，不允许提供商业营销类呼出**；接入号码原则上只开呼入；须提交禁呼/不违规呼出承诺 | BYOC 架构；`trunk.allowed_scenarios` 硬绑；`telesales` 不由平台供线路；平台侧全局禁营销呼出 |
| 《电信条例》《电信业务经营许可管理办法》 | 经营（增值）电信业务须持许可；码号不得转让出租 | 资质表校验；禁止线路/号码转租转售；平台不自营呼出资源（V1） |
| 《个人信息保护法》第 5、13、21、23 条 | 最小必要、须取得同意；受托处理不得超范围，委托事项完成后应及时删除，不得留存或转售 | 平台定位「受托处理者」；租户数据与录音按合同期删除 + 删除留痕 |
| 《互联网信息服务深度合成管理规定》第 17 条 | 合成人声等可能致混淆误认的服务须显著标识 | 开场 3s 内 AI 身份声明（模板强制、不可关） |
| 《人工智能生成合成内容标识办法》（国信办通字〔2025〕2 号，2025-03-07 发布，**2025-09-01 施行**）+ 强制性国标 GB 45438-2025（同日实施） | 显式标识：音频起始/末尾/中间加语音提示或「短长 短短」节奏（摩斯码 AI），语音用正常语速（约 120–160 字/min）；隐式标识：文件元数据加含 `AIGC` 字段（Label/ContentProducer/ProduceID 等） | 录音导出前写入显式语音标识 + 元数据隐式标识（§12.3） |
| 监管执法态势 | 工信部信管函〔2026〕375 号：2026 年前三季度处罚违规企业 184 家次、关停违规语音专线 1425 条、短信端口 525 个 | 投诉熔断、号码健康度、通话录音与授权证据链 |
| ⚠️ 待核实项 | ①《通信短信息和语音呼叫服务管理规定》仍为 2020-08-31 征求意见稿（正式文本未落地，需持续跟踪）；②行业媒体报道《人工智能拟人化互动服务管理暂行办法》已于 2026-07-15 施行（**自媒体来源，须以官方文本核实**，若成立则需追加「全程显著标注 AI 身份」要求）；③「呼出录音/同意凭证留存不少于 30 日」「8:00–21:00 呼叫时段」为行业通行做法与服务商要求，**非法律统一强制条文**，本产品取严执行 | 上线前由法务确认，并把结论写进 `compliance_config` 默认值与本文档变更记录 |

### 12.2 系统级硬约束（不可被配置关闭）

1. AI 身份声明：开场 3 秒内、必含「AI/人工智能」+「助理/语音助手」要素；模板渲染，LLM 无权改写；未播报视为异常并记录。
2. 录音告知：开场内声明「本次通话将被录音」。
3. 拒绝即刻生效：识别到「别再打/不需要/投诉」→ 同事务写 DND + 挂断，坐席端不可撤销（仅平台超管双人复核可解除并留痕）。
4. 时段与频次：默认 09:00–19:00、避开 12:00–14:00；同号码日 ≤ 1 次、7 日 ≤ 3 次、30 日 ≤ 6 次（租户可**加严不可放松**）。
5. 主叫真实可溯源：禁止改显/随机换号/伪装本地号；`caller_numbers` 必须实名登记且与线路匹配。
6. 全局 DND 优先级最高：平台级 12321/运营商通报名单，任何租户不可覆盖。
7. 音色合规：仅使用平台自有/已授权音色，禁止克隆特定真人音色。
8. 证据留存：录音、转写、授权凭证、闸门拦截记录、话术版本快照保留 ≥ 30 日（默认 180 日，可按合同配置）；期间不可删除。
9. 话术版本快照：每次呼叫锁定 `template_version`，事后可复现「当时说的每句话依据哪版话术」。

### 12.3 AI 内容标识实现

- **显式标识**：录音文件起始位置注入语音提示「以下为人工智能生成合成语音」（正常语速），或采用国标允许的「短长 短短」节奏标识；**通话中**通过开场 AI 身份声明满足交互场景的显式提示。
- **隐式标识**：录音导出时在文件元数据写入含 `AIGC` 的字段，取值遵循 `{"AIGC":{"Label":"1","ContentProducer":"<平台编码>","ProduceID":"<call_uuid>",...}}`（Label=1 表示确定为 AI 生成合成内容；平台编码按《实践指南——服务提供者编码规则》申请）。
- 落库：`recordings.aigc_label_meta` 记录写入结果；导出功能必须复用同一份带标识文件，不得输出未标识原件。
- 若合同客户要求「无显式标识的原始录音」：仅在协议明确其标识义务与使用责任后提供，并留存提供对象信息日志 ≥ 6 个月。

### 12.4 数据角色与删除义务

平台为**受托处理者**。要求：与租户签署数据处理协议（DPA）；按目的与范围处理；租户终止服务后按合同期删除名单与录音（保留法定/合同留存义务内的除外），删除操作产生**删除报告**（不可变、可作证据）；禁止任何跨租户数据复用、号码资产池共享、外呼记录用于训练模型（除非租户单独书面授权，且需在数据模型 `tenants.data_use_consent` 显式为 true，默认 false）。

### 12.5 未成年人特殊保护

若租户业务触达未成年人（教育类场景典型），系统要求：租户上传监护人同意凭证类型；名单命中未成年标记（如学校场景监护人号码）时强制走「监护人核验」话术分支，否则拦截。⚠️ 教育类外呼建议上线前单独法务评估。

---

## 13. 架构韧性与降级

### 13.1 单点故障

| 组件 | 是否单点 | V1 接受 | V2 消除 |
|---|---|---|---|
| Nginx | 是 | ✅ | 双实例 + Keepalived VIP |
| control-api | 否（可多副本） | ✅ | 已支持多副本 |
| voice-orchestrator | **是（会话粘性）** | ✅（标注！） | 按 `call_uuid` 一致性哈希分片 + 多实例 |
| FreeSWITCH | 是 | ✅ | SBC + FS 池 + 故障转移 |
| PostgreSQL | 是 | ✅（每日备份 + WAL） | 主从流复制 + 自动故障转移 |
| Redis | 是 | ✅ | 哨兵/集群；队列换独立实例 |
| ASR/TTS/LLM | 外部依赖 | ✅（多供应商） | 多活 + 自建兜底 |
| OSS | 外部依赖 | ✅ | 双写或本地临时缓存重传 |

### 13.2 故障影响矩阵

| 故障 | 完全不可用 | 降级可用 | 不受影响 |
|---|---|---|---|
| PostgreSQL | 建任务、写 CDR/账单 | 已入队呼叫（走 Redis 队列缓存）、报表缓存页 | 进行中的通话 |
| Redis | 新呼叫发起（令牌桶）、缓存 | 只读查询（穿透 DB，变慢） | 进行中通话、录音上传 |
| control-api | 控制台操作 | 通话继续、事件暂存 FS | 进行中的通话与语音链路 |
| voice-orchestrator | 新通话的 AI 应答 | 已建会话短暂重连（3 秒内恢复） | 纯按键 IVR 类任务 |
| FreeSWITCH | 全部新呼叫 + 媒体 | 无（进行中的通话全部中断）→ **L3** | 控制台只读 |
| ASR | 语音识别 | 切话术树 + 按键交互；或转人工 | 计费、报表 |
| TTS | 语音合成 | 转人工 / 承诺回拨 + 挂断 | ASR 转写可继续 |
| LLM | 自由应答 | 纯话术树模式（P-RESPONSE 不可用） | 通知/按键类全部正常 |
| OSS | 录音落盘 | 本地缓冲重传（缓冲 4GB / 约 2 小时余量） | 通话本身 |
| 单一外部供应商配额耗尽 | 该供应商路径 | 自动切换备用供应商 | 用户无感（延迟略升） |

### 13.3 RTO / RPO

**RTO 1 小时**（对外服务，通话不可长时间中断；`restart: unless-stopped` + healthcheck + 告警 15 分钟响应）；**RPO 24 小时**（日备份 + WAL 归档；V2 主从后降到 5 分钟内）。录音是**不可再生数据**，故额外要求：边通话边分片上传 + 本地 2 小时缓冲，OSS 故障时不丢录音。

### 13.4 熔断参数（所有外部 API 统一）

连续 5 次失败 **或** 30s 内失败率 > 50% → OPEN；OPEN 持续 30s → HALF-OPEN 放 1 个试探请求；成功 → CLOSED，失败 → 再 OPEN。ASR/TTS/LLM 各自独立熔断器 + 租户级熔断（投诉率超阈停呼）。状态变更写 WARN 日志 + 告警。
重试：LLM/ASR/TTS 3 次指数退避（1s/2s/4s）后降级；DB 写 2 次固定 500ms；短信/通知 3 次固定 2s；CDR 对账 5 次退避后进人工队列。**只重试幂等操作**。

### 13.5 优雅降级层级

| 级别 | 触发 | 用户（被叫）感知 | 租户侧 | 运维动作 |
|---|---|---|---|---|
| L0 | 正常 | 正常 AI 对话 | 正常 | — |
| L1 | LLM 不可用 | 「我用标准流程为您解答」，走话术树 | 顶部黄条 + 任务标注「话术树模式」 | 检查供应商/切主模型 |
| L2 | ASR 或 TTS 不可用 | 转人工或「稍后回拨」后礼貌挂断 | 任务自动 `paused`，红色横幅 | 切备用供应商 |
| L3 | FS/媒体面不可用 | 无（呼叫无法建立） | 全站红条「外呼服务维护中」，新任务禁止启动 | 立即拉起，P1 事故 |
| L4 | 整站不可用 | 无 | 域名不可达 → 群/短信通知 | P0 事故流程 |

切换方式：L1/L2 自动（熔断驱动），L3/L4 手动（特性开关 `platform.calling.enabled=false`）。

### 13.6 超时分级

普通 API 3s；启动/导入类 10s（202 异步）；质检转写单通 300s；调证包/报表 5 分钟转后台；AI 首帧 1500ms（超时插填充语，> 3000ms 走降级）；SIP 无应答 45s 判 `no_answer`。

### 13.7 Fallback 清单

| 依赖 | 正常来源 | Fallback | 用户感知 |
|---|---|---|---|
| 意向分级/小结 | LLM | 规则 + `null` | 前端显示「AI 小结生成中」，**不编造** |
| 意图判定 | LLM | 关键词规则 | 报表标注「规则模式」 |
| ASR 文本 | 流式 ASR | 上一版增量结果 | 轻微滞后感 |
| 并发水位 | Redis | DB 聚合（慢路径） | 数字延迟 ≤ 60s |
| 单价/配额 | 配置表 | 上次快照 + 告警 | 账单标注「按快照试算」 |
| 号码健康度 | 日统计任务 | 昨日数据 + 数据时间戳 | 无感 |

---

## 14. 跨系统状态一致性

**呼叫状态机**（禁止非法跃迁，`call_service` 内白名单校验）

```
queued → dialing → ringing → answered → talking → completed
  │         │          │          │         ├→ transferring → (completed | transfer_failed→talking)
  │         │          │          │         └→ interrupted → talking | completed
  ├→ blocked_compliance      ├→ no_answer / busy / rejected → (retry task 生成) 
  └→ cancelled               └→ failed
```
中间态 `transferring`、`transfer_failed` 必须存在：转接失败要能回到 `talking` 并承诺回拨，不能直接 `completed`（否则丢数据、丢计费）。

**计费一致性三层**：①FS 事件回调（幂等，`call_uuid`）②超时主动查询 30s/1min/3min ③每日 02:00 与中继账单对账 → 差异进 `billing_dispute`（人工队列）+ 告警，**差异不自动扣费**。录音未落库但 CDR 存在 → 计费照常、质检标记 `recording_missing`（可拨证据链缺口，须人工跟进）。

---

## 15. 第三方服务风险管理

| 服务 | 用途 | 关键风险 | 应对 |
|---|---|---|---|
| 运营商 SIP 中继（租户自带/托管） | 呼叫落地 | **并发数、时段、行业白名单、码号备案**；被投诉即封线 | 准入审核 + 并发双层限流 + 投诉熔断 + 中继多备路 |
| ASR 供应商 | 流式识别 | 并发配额（新账号默认很低）、计费分钟、限流 429 | 申请配额提前 2 周 + 多供应商适配层 + 熔断降级 |
| TTS 供应商 | 流式合成 | 首包延迟、音色授权、并发 | 预合成高频短语缓存 + 备用供应商 |
| LLM API | 应答/意图/质检 | 限流 TPM/RPM、价格波动、内容安全、模型下线 | 主备模型 + 上下文压缩 + 本地红线兜底 + 模型版本固定并留回归集 |
| OSS | 录音/资质文件 | 配额、下行流量费、误删 | 版本化 + 生命周期策略（先转低频/归档）+ 关闭覆盖写 |
| 短信 | 回拨确认、坐席通知 | 签名/模板报备周期、频次限制 | 提前报备 + 失败补发 |
| 时间/风控 | 时段判定 | 时区错误导致深夜外呼（高危合规事故） | 全链路 `TIMESTAMPTZ` + 存 `timezone` + 呼前再判一次 + 单测覆盖跨时区与夏令时 |

**冷启动预热**：新供应商账号前 2 周按 20% 流量灰度；LLM/ASR 配额提工单提升预留 2 周；中继并发先按 5 路跑通再提量。
**费用预警**：`billing_budget` 设月度上限，达 80% 告警、100% 自动暂停新任务（不中断进行中通话）；供应商账单每日抓取比对。
**备选与切换成本**：ASR/TTS/LLM 全部走适配层，切换 = 配置 + 重启（≤ 30 分钟）；⚠️ TTS 音色切换会改变用户体验，必须经租户确认（音色属品牌资产）。

---

## 16. 开发规约与已知陷阱

### 16.1 类型统一规约

- 时间：数据库统一 `TIMESTAMPTZ`；代码统一 **aware UTC**（`datetime.now(timezone.utc)`）；展示层按租户 `timezone` 转换。**禁止** `datetime.now()` 裸用（CI 加 lint 规则拦截）。
- 主键：一律 `uuid`（`audit_logs` 用 `bigint identity`，理由：高写入自增更省）。
- 枚举值：一律小写字符串 + DB `CHECK` 约束，前后端同一份常量文件（`shared/enums.py` 生成 `shared/enums.ts`）。
- 空值：DB `NULL` ↔ Python `None` ↔ JSON `null`；前端 `null` 渲染为「—」，**输入框空值提交为 `null` 而非 `""`**。
- 手机号：入库即加密 + hash，业务层只流转 `phone_hash`，展示 `138****5678`。

### 16.2 框架组合兼容性（开发第一天必须验证）

- `pytest-asyncio` ≥ 1.0：需显式 `asyncio_mode="auto"`，注意 event loop scope 默认 `function`。
- SQLAlchemy 2.0 async：**禁止 lazy loading**，关联一律 `selectinload()/joinedload()`，否则 `MissingGreenlet`。
- Pydantic v2：`from_attributes=True`（不是 `orm_mode`）、`field_validator`（不是 `validator`）。
- Alembic 异步：`env.py` 需 `async_engine_from_config` + `run_sync`，否则迁移卡住。
- Celery + asyncio：worker 内不直接 `asyncio.run` 复用外层 loop，用 `asyncio.new_event_loop()` 隔离；LLM/ASR 长连接不要在 Celery 里复用（跨进程失效）。
- FreeSWITCH `mod_audio_fork`：WS 断连不会自动重连，voice-orchestrator 必须实现心跳 + 会话重建，且重建后**不能重复播放已播内容**。
- Docker 内容器互联用服务名（`db`/`redis`/`fs`），**禁止 localhost**；FS 的 RTP 端口范围需在 compose 与宿主防火墙同时放开，`network_mode` 选择要写清（RTP 媒体建议 `host`）。
- 前端 API 地址从环境变量 `VITE_BASE_URL` 读取，不硬编码协议。

### 16.3 外键与删除顺序

拓扑（建表/种子正序）：`tenants → users → user_roles → qualifications → trunks → caller_numbers → consent_records → contact_lists → contacts → scenario_templates → script_nodes/redline_rules → campaigns → tasks → calls → call_turns/call_events/recordings/transcripts/qc_results/call_transfers/billing_items → dnd_entries/complaints → audit_logs`。
**测试数据清理按逆序**，`conftest.py` 的 autouse 清理 fixture **在写第一个测试前设计好**。
级联策略：`qualifications` CASCADE（随租户删）；`trunks` RESTRICT（有任务则禁删）；`calls.trunk_id` SET NULL（保留历史话单）；`calls.contact_id` RESTRICT（有呼叫记录禁止删联系人，改软删）；`recordings` RESTRICT（录音不可随呼叫删除）；进行中的 `campaigns` RESTRICT。

### 16.4 全局副作用与测试隔离

Redis：`dev db=0 / test db=15`，autouse `FLUSHDB`；令牌桶键前缀 `test:`。文件：上传目录 `tmp/test-files`，teardown 清空。环境变量：`monkeypatch` 用完自动恢复。外部供应商：全部 `vcr`/fake adapter，**测试不得打真实供应商**。特性开关：测试内 `reset feature flags` fixture。

### 16.5 本地 vs 生产差异

| 维度 | 本地 | 生产 | 常见坑 |
|---|---|---|---|
| OS | macOS | Linux | 路径大小写、`fork` 行为 |
| Python | 3.11.x | 3.11.9（容器锁定） | 语法/依赖 ABI |
| 时区 | 本机 | UTC + 租户 tz | 时段闸门差 8 小时（**合规级事故**） |
| 语音 | 无 FS（mock provider） | FreeSWITCH + 真实中继 | mock 通过但生产单通不通 |
| 音频格式 | wav 44.1k | 8k/16k PCM | 采样率不匹配导致机器人「听不懂」 |
| 网络 | 前后端同源代理 | 分离域名 + WSS | 混合内容、WS 证书链 |
| 数据量 | 几十条 | 数十万条 | N+1 慢查询、内存溢出 |
| 供应商 | 沙箱/低配额 | 生产配额 | 沙箱无 429，生产一放量全熔断 |

---

## 17. 测试策略

### 17.1 第一天必做（基础设施验证）
一个最小异步测试跑通 `pytest + asyncio + PostgreSQL(独立 test 库) + Redis(db=15)`；再跑一个 voice mock 往返（假 ASR→假 LLM→假 TTS），验证编排链路。

### 17.2 后端核心用例

| 类别 | 用例 |
|---|---|
| 认证/权限 | 登录成功/失败 5 次锁定；跨租户访问 → 403；RLS 生效（不设 `app.tenant_id` 时查不到数据）；auditor 写操作全拒 |
| **合规闸门（最高优先）** | 无授权号码 → `blocked_compliance`；DND 命中；撤回授权后立即不可拨；时段外不可呼；跨日频次超限；`telesales` + 平台托管线路 → 拒绝；`12:00-14:00` 拦截；「别再打」→ DND 落库 + 挂断事件 |
| 任务 | 乐观锁 409；start 幂等（重复点击只 1 批任务）；pause 后不再产生新呼叫；重拨只按 `retry_policy` 生成 |
| 令牌桶 | 超并发时不产生第 11 路呼叫；通话结束令牌释放；异常挂断不泄漏令牌（TTL 兜底） |
| 一致性 | FS 回调重复 → 只入账 1 次；CDR 缺失 → 主动查询后补齐；对账差异进 dispute |
| 数据 | 导入 20 万行幂等重跑；加密字段可正确解密且 hash 等值查命中；导出预签名 URL 15 分钟过期 |
| AI | 意图分类评测集 ≥ 90%；红线集召回 ≥ 99%（构造 50 条违禁句 + 30 条边界句）；LLM 返回非 JSON → 降级不崩；prompt 注入（被叫说「忽略你的指令」）不越界 |

### 17.3 语音链路专项（易被忽略、必须做）
延迟基准：固定 20 条录音回放集，P50 ≤ 1200ms 门禁；打断正确率（AI 播报中插话能停）；静音/语音信箱识别；双声道混音正确（谁说的不能混）；网络抖动（注入 5% 丢包、200ms 抖动）不崩溃；供应商 429 注入能熔断 + 降级。

### 17.4 前端验收
六态齐全（加载/空/失败/无权限/超时/降级横幅）；导入向导展示三态与错误行；实时页 WS 断线自动重连并提示；导出按钮二次确认。

### 17.5 压测
工具 `locust`（API）+ `sipp`（SIP）+ 自研回放器（音频）。场景：①100 用户浏览 + 5 任务并发启动 ②10 路满载连续 30 分钟 + ASR 429 注入 ③导入 20 万行同时 10 路通话。通过标准：API P95 < 500ms、音频不卡断、无令牌泄漏、熔断与降级按 §13 表现、错误率 < 0.5%。

---

## 18. 部署、CI/CD 与环境

### 18.1 仓库结构（Monorepo）

```
echodesk/
├─ CLAUDE.md                     # Claude Code 入口（见 §22）
├─ docs/PRD.md                   # 本文档
├─ backend/
│  ├─ app/{main.py,config.py,deps.py}
│  ├─ app/api/{auth,tenants,trunks,lists,campaigns,calls,qc,compliance,billing,platform}.py
│  ├─ app/models/  app/schemas/  app/services/
│  ├─ app/compliance/{gate_pre.py,guard_incall.py,redline.py,dnd.py}
│  ├─ app/llm/{client.py,prompts/*.j2,guard.py}
│  ├─ app/voice/{asr/,tts/,vad/,orchestrator.py,session.py}
│  ├─ app/fs/{esl_client.py,events.py,audio_fork.py}
│  ├─ app/tasks/{dispatch.py,importer.py,qc.py,billing.py,report.py,maintenance.py}
│  ├─ alembic/{env.py,versions/}
│  └─ tests/{conftest.py,test_auth.py,test_compliance_gate.py,test_campaign.py,...}
├─ frontend/{src/{api,router,stores,views/{t,p,agent,audit},components},vite.config.ts}
├─ voice/                       # voice-orchestrator 独立服务 + Dockerfile
├─ infra/{docker-compose.yml,nginx/,freeswitch/{sip_profiles,acl.conf.xml},prometheus/,alerts/}
├─ .github/workflows/{ci.yml,deploy.yml}
└─ scripts/{seed.py,loadtest/,eval_intent.py,eval_redline.py}
```

### 18.2 Docker Compose（关键片段）

```yaml
services:
  db:
    image: postgres:16.4
    environment: {POSTGRES_DB: echodesk, POSTGRES_USER: echodesk, POSTGRES_PASSWORD_FILE: /run/secrets/db_pw}
    volumes: ["pg:/var/lib/postgresql/data"]
    healthcheck: {test: ["CMD-SHELL","pg_isready -U echodesk"], interval: 10s, retries: 5}
    restart: unless-stopped
  redis: {image: redis:7.2.5, command: ["redis-server","--appendonly","yes"], restart: unless-stopped,
          healthcheck: {test: ["CMD","redis-cli","ping"], interval: 10s, retries: 5}}
  api:
    image: echodesk-api:1.0.0
    env_file: .env
    depends_on: {db: {condition: service_healthy}, redis: {condition: service_healthy}}
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
    restart: unless-stopped
  worker:
    image: echodesk-api:1.0.0
    command: celery -A app.tasks worker -Q dispatch,io_heavy,billing,report -l info --prefetch-multiplier=1
    restart: unless-stopped
  voice:
    build: ./voice
    environment: {FS_WS_URL: "ws://fs:8081/audio", REDIS_URL: "redis://redis:6379/0"}
    restart: unless-stopped
  fs:
    image: echodesk-freeswitch:1.10.12
    network_mode: host
    volumes: ["./infra/freeswitch:/usr/local/freeswitch/conf", "rec:/var/rec"]
    restart: always
  nginx:
    image: nginx:1.27.3
    ports: ["80:80","443:443"]
    depends_on: [api]
    restart: unless-stopped
volumes: {pg: {}, rec: {}}
```

### 18.3 CI/CD
`ci.yml`：lint（ruff、eslint）→ `pip-audit`/`npm audit --audit-level=high` → typecheck → 单测（含合规用例，**用例不过不许合**）→ 构建镜像（带 git sha 标签）→ 意图/红线评测集（低于门禁阻断合并）。
`deploy.yml`：先跑 `alembic upgrade head` → 滚动更新 api/worker → `voice` 单独发布（**先置 `platform.calling.enabled=false` 摘流量，等在途通话归零，再发布**）→ healthcheck 通过再放开 → 失败自动回滚镜像标签 + `alembic downgrade -1`（迁移必须可逆，破坏性变更走两阶段 expand/backfill/contract）。
本地开发：`docker compose up db redis` → `alembic upgrade head` → `python scripts/seed.py` → `uvicorn app.main:app --reload` → `cd frontend && npm i && npm run dev`。
多环境差异：`dev`（mock 语音供应商、低配额）/ `staging`（真实中继 + 测试号码，可打真实通话但号码受限）/ `prod`。`staging` 必须能拨打到白名单测试号，否则语音类缺陷无法在上线前暴露。

### 18.4 环境变量（`.env.example` 节选，真实 `.env` 不入 Git，`.gitignore` 必含 `.env*`）

```
DATABASE_URL=postgresql+asyncpg://echodesk:***@db:5432/echodesk   # 不入代码库
REDIS_URL=redis://redis:6379/0
JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_key                          # 不入代码库
FIELD_ENC_KEY_FILE=/run/secrets/field_key                          # 不入代码库；AES-256-GCM
PEPPER_FILE=/run/secrets/phone_pepper                              # 不入代码库；HMAC 手机号
OSS_ENDPOINT= OSS_BUCKET= OSS_ACCESS_KEY= OSS_SECRET_KEY=          # 不入代码库
ASR_PROVIDER= ASR_KEY= ASR_BACKUP_PROVIDER= ASR_BACKUP_KEY=        # 不入代码库
TTS_PROVIDER= TTS_KEY= TTS_VOICE_ID=
LLM_BASE_URL= LLM_KEY= LLM_MODEL_MAIN= LLM_MODEL_BACKUP=           # 模型版本必须固定
FS_ESL_HOST=fs FS_ESL_PORT=8021 FS_ESL_PASSWORD=                   # 不入代码库
AIGC_PRODUCER_CODE=                                                 # 平台编码（隐式标识用）
CALL_WINDOW_DEFAULT=09:00-19:00  DEFAULT_TZ=Asia/Shanghai
COMPLIANCE_MAX_RETRY_DAILY=1  GLOBAL_DND_ENABLED=true
CALLING_ENABLED=true                                               # 特性开关：紧急停呼
RUNTIME_TUNABLES={...}    # 见下：可热改，不需重启
```
**运行时可调（Redis 配置中心，无需重启）**：限流阈值、LLM temperature/模型、缓存 TTL、熔断阈值、投诉熔断阈值、时段窗口（仅可加严）、并发配额、采样率。
**特性开关**：`feature.telesales`（默认 false，逐租户灰度）、`feature.collection`（false）、`feature.llm_free_response`、`feature.asr_backup`、`platform.calling.enabled`（全局急停）。

---

## 19. 事故响应与运维

| 级别 | 定义 | 响应 | 恢复 |
|---|---|---|---|
| P0 | 录音/授权数据丢失或泄露、深夜违规外呼、跨租户越权、被监管关停 | 15 分钟 | 1 小时 |
| P1 | 外呼整体不可用、计费严重错误、AI 全线不可用 | 30 分钟 | 4 小时 |
| P2 | 单线路/单供应商异常、报表或质检延迟 | 2 小时 | 24 小时 |
| P3 | UI 错位、文案问题 | 下工作日 | 下个版本 |

**升级路径**：值班运维 →（30 分钟未解）技术负责人 →（影响合规/营收）业务负责人 + 法务。
**人工应急（最后的兜底是人）**：外呼不可用 → 通知租户改用人工坐席手拨（平台提供名单，仍走 DND 校验导出）；租户投诉激增 → 一键 `platform.calling.enabled=false` 全局停呼 + 逐个租户排查；计费争议 → 人工以 CDR 逐条核算，先不扣费。
**用户沟通模板**：
```
【故障公告】EchoDesk 外呼服务出现异常，正在紧急处理。影响：X 时段起新外呼无法发起；
进行中通话不受影响 / 已中断。预计恢复：HH:MM。期间如需紧急外呼请改用人工线路。
【恢复公告】已于 HH:MM 恢复。故障期间的呼叫清单已生成，可在「任务详情-异常呼叫」下载，
未送达号码可在确认后排重呼。
```
**P0/P1 复盘**：48 小时内出文档（时间线、5 Whys、改进项带责任人与截止日、不追责个人）。

**日常运维**：查日志 `docker compose logs -f api --since 30m`；备份 `pg_dump` 每日 02:30 → OSS 加密；磁盘告警 80% → 清轮转日志 + `tmp/`；PostgreSQL 每周 `VACUUM (AUTOVVACUUM 保障) / REINDEX` 慢表；性能基线记录：`GET /api/t/calls` P50/P95/P99（当前基线目标 120/300/600ms）、语音首帧 P50/P95（1200/1800ms），每周对比发现退化；SSL 自动续期验证每周一跑；证书/密钥轮换记录台账。

---

## 20. 成本估算（试点期，月度）

> ⚠️ **单价必须以实际询价/合同价填充**，下表服务器价为公开区间参考（tech-stack 参考表），语音与 AI 单价未做实时询价，**不得当报价使用**。

| 项 | 规格 | 数量 | 月费估算 | 依据 |
|---|---|---|---|---|
| 应用服务器 | 4C8G | 1 | ¥200–300 | 参考区间（100–1000 用户档） |
| 语音服务器（FS + voice） | 8C16G | 1 | ¥700–1200 | 同上档上调，媒体面需富余 |
| PostgreSQL | 2C4G 高可用 | 1 | ¥500–800 | 云 RDS 常见区间 |
| Redis | 2G | 1 | ¥100–200 | 云托管常见区间 |
| 对象存储（录音） | ~540MB/天 ≈ 16GB/月，含 180 天留存 ~100GB | — | ¥50–150 | 存储 + 请求 + 下行流量 |
| 带宽/公网 | 10 路通话约 0.6Mbps + 控制台 | — | ¥100–300 | 语音带宽小，主要是控制台与录音下载 |
| **基础设施小计** | | | **¥1650–3000** | |
| SIP 中继线路费 | 按分钟 + 号码月租 | ⚠️ 待询价 | ⚠️ 缺失，需以合同价填充 | BYOC 模式下通常由租户承担 |
| ASR（流式） | ≈ 1500 分钟/天（1000 通 × 90s × 双轨近似） ≈ 45,000 分钟/月 | ⚠️ 待询价 | ⚠️ 缺失 | 用量口径已给，单价待填 |
| TTS（流式） | ≈ AI 播报占比约 40% ≈ 18,000 分钟/月 | ⚠️ 待询价 | ⚠️ 缺失 | 同上 |
| LLM | 1000 通 × 6 轮 × ~1500 token ≈ **9,000 万 token/月**（含 prompt 开销） | ⚠️ 待询价 | ⚠️ 缺失 | 优化项：上下文压缩、缓存、小模型分流可降 40–60% |
| 质检/转写 | 与 ASR 同量级 | ⚠️ 待询价 | ⚠️ 缺失 | — |
| 监控/日志 | Prometheus 自托管 | — | ¥0–200 | 自托管近零成本 |

**单通成本模型（上线前必须算实）**
```
单通成本 = 线路分钟价×平均时长 + ASR分钟价×分钟数 + TTS分钟价×合成分钟
         + LLM token价×单通token + (基础设施月费 ÷ 月通话数) + 号码月租÷月通话数
```
产品要求：该公式在系统内以 `billing_rates` 配置项实现，成本页实时展示，**每通成本、每意向成本（成本 ÷ A+B 级数）必须可查** —— 这是客户续费的核心决策数字，也是我方毛利口径。

---

## 21. 开发里程碑（12 周，试点级规模）

| 周 | 交付物 | 关键验收 |
|---|---|---|
| W1 | 基础设施验证（§17.1）、数据模型 + 迁移、鉴权与 RBAC + RLS | 异步测试链路跑通；跨租户访问 403 |
| W2 | 租户/资质/线路/号码 CRUD + 加密 + 令牌桶 | 线路测试呼叫可达 |
| W3 | 名单导入 + 授权模型 + DND + 呼前闸门 | **合规用例全绿（此周为硬门槛）** |
| W4 | 任务编排、重拨、状态机、启停 | 幂等与乐观锁用例通过 |
| W5 | FS + voice-orchestrator 打通（mock 供应商） | 一通完整录音落库 |
| W6 | ASR/VAD/TTS 接入，打断、填充语 | 首帧 P50 ≤ 1500ms（真实供应商） |
| W7 | 话术引擎（mandatory/llm_free/gate）+ P-RESPONSE/P-INTENT | 真实号码打 50 通，可对话可被打断 |
| W8 | 呼中红线闸门、AI 身份声明、DND 即刻生效、转人工 | 红线召回 ≥ 99%；「别再打」100% 落库 |
| W9 | 录音双轨 + 隐式/显式标识 + 转写 + 质检 + 意向分级 | 调证包可导出且含标识 |
| W10 | CDR + 三方对账 + 计费 + 报表 | 对账差异进 dispute；账单明细正确 |
| W11 | 合规中心、投诉工单、熔断、监控告警、压测 | 10 路满载 30 分钟 + 429 注入通过 |
| W12 | 灰度上线（2–3 个内部/种子租户，通知+回访场景）、文档与交接 | Smoke test 清单全过，回滚演练 1 次 |

**推荐开发模式**：Agent Teams —— 合规/闸门（最高风险，单独负责且用例先行）、语音链路、业务 CRUD、前端可并行；但 W8 合规硬门槛前不得并行铺开电销与催收。

---

## 22. CLAUDE.md（同时输出为项目根文件）

```markdown
# EchoDesk — 多租户 AI 语音外呼 SaaS

技术栈：Python 3.11.9 + FastAPI 0.115.6 + SQLAlchemy 2.0.36(async) + PostgreSQL 16.4(RLS)
+ Redis 7.2.5 + Celery 5.4.0 + FreeSWITCH 1.10.12 + Vue 3.5.13 + Vite 6.0.5，Docker Compose 部署。

所有开发规范见 docs/PRD.md（唯一真相源，含数据模型/API/合规章节）。

## 关键命令
docker compose up -d db redis            # 起依赖
alembic upgrade head                     # 迁移
python scripts/seed.py                   # 种子数据
uvicorn app.main:app --reload            # 后端
cd frontend && npm i && npm run dev      # 前端
pytest -q                                # 测试
ruff check . && mypy app                 # 静态检查

## 红线（违反即视为事故）
- 时间一律 aware UTC + TIMESTAMPTZ；**任何时段/频次闸门必须在呼前再判一次**，时区错误 = 合规事故。
- 所有查询必须带 tenant_id 过滤；不得因为已有 RLS 就省略应用层 scope_filter。
- 手机号等 PII 一律「加密存储 + hash 等值查 + last4 展示」，日志与导出不得出现明文全号。
- AI 身份声明、DND 拦截、录音告知为不可关闭逻辑；禁止新增任何"可跳过合规"的配置项。
- 关键写入必须幂等（Idempotency-Key 或唯一约束）；重试只允许用于幂等操作。
- 禁止修改 .env 变量名；密钥不硬编码、不入 Git；日志不输出密码/Token/完整手机号。
- 只引入 PRD 已列依赖；表结构变更必须走 Alembic 迁移且可回滚。
- SQLAlchemy async 禁止 lazy loading（用 selectinload/joinedload）；Pydantic v2 用 from_attributes。
- 测试用独立库与 Redis db=15，清理按 FK 逆序；供应商一律 mock，禁止测试打真实 API。
- 容器间用服务名连接（db/redis/fs），禁止 localhost；前端地址从 VITE_BASE_URL 读取。
```

---

## 23. 上线检查清单（Go-Live）

**基础设施**：备份已成功且**验证过可恢复**（演练 1 次）；磁盘/慢查询/证书告警已接；时钟同步（NTP）确认；PostgreSQL 连接池与 `pool_pre_ping` 生效；RLS 策略已在生产库启用并用越权用例验证；`/docs` 已关闭；生产镜像固定 SHA。
**语音链路**：中继并发与号码已备案；真实号码外呼 20 通冒烟（含语音信箱、秒挂、打断、转人工）；录音双轨可播、双声道不错位；供应商并发配额已提升（**不是沙箱配额**）；熔断与降级演练各 1 次（关 LLM / 关 TTS / 注入 429）。
**合规项（全部通过才可上线）**：AI 身份声明在 3 秒内必现且不可关闭；开场录音告知必现；「别再打」→ DND + 挂断 100% 生效；全局 DND 与租户 DND 均生效；时段窗口按租户时区正确；红线召回 ≥ 99%；录音/转写/授权证据链留存期正确；显式+隐式标识写入并可验；话术版本快照与呼叫记录可关联；调证包一键导出可用；租户准入材料与 DPA 齐备；`telesales`/`collection` 开关默认关闭；法务已复核 §12 清单（含待核实项）。
**灰度**：第 1 周仅通知/回访场景、仅白名单号码、单租 1–2 路、日量 ≤ 50 通；第 2 周放开到 3 租户 10 路；每步观察拒接率、投诉数、通话质量。
**上线后 Smoke Test**：登录、导入名单（含无授权样本，验证被正确拦截）、启动任务、真实 1 通 AI 通话、录音播放、AI 小结、报表数字对得上、账单明细生成、投诉登记后 DND 生效。
**快速回滚触发条件**：录音缺失率 > 1%、非授权呼出 ≥ 1 起、AI 未声明身份 ≥ 1 起、单通成本超预算 200%、投诉率 > 0.3% → 立即 `CALLING_ENABLED=false` 并回滚。
**人工应急准备**：值班表与响应群、租户紧急联系人、供应商工单入口、备用中继与备用供应商 Key 已就位、停呼开关已实操演练。

---

## 24. 附录

### 24.1 种子数据（顺序即插入顺序）
平台角色与全局红线规则集 → 默认合规策略集 → 场景模板（`notification`、`callback_survey`）→ 平台超管账号（首登强制改密）→ 演示租户 + 线路 + 授权样例 + DND 样例。演示数据必须打 `is_demo=true` 并在报表中排除。

### 24.2 告警规则（节选）
`api_p95>1s` 5 分钟；`call_answer_rate<0.15`（租户，30 分钟）→ 提示号码被标记；`complaint_rate_30d>0.003` warn、`>0.010` 熔断；`asr_error_rate>10%` 2 分钟；`llm_first_token_p95>1500ms` 10 分钟；`recording_missing>0` 立即（合规级）；`billing_dispute>0` 每日汇总；`disk>80%`；`cert_expire_days<30/7/1`；**`offwindow_call_count>0` 立即 CRITICAL**。

### 24.3 V2 规划（P2，V1 不碰）
托管线路与码号经营（仅限允许呼出用途）、金融催收全托管、预测式/预览式外呼算法、坐席工作台与耳语辅导、多通道联动（短信/企微/WhatsApp）、方言与多语种、自研或私有化 ASR/TTS、跨租户话术模板市场、A/B 与自动话术优化、多地域部署与就近接入。

### 24.4 数据合规说明
平台定位为受托处理者（§12.4）；数据保留政策：录音与转写默认 180 天（可按合同 30 天～2 年配置，不得低于法定/合同义务），名单与授权按服务合同期，终止后 30 天内删除并出删除报告；日志 INFO 30 天 / ERROR 90 天 / CRITICAL 180 天；未成年人触达须监护人核验（§12.5）；隐私政策与用户协议须包含 AI 身份、录音与算法处理说明、拒收与撤回渠道；不使用租户数据训练模型，除非单独书面授权。

### 24.5 关键规则来源（供法务复核）
工信部信管〔2020〕81 号《关于加强呼叫中心业务管理的通知》；《民法典》第 1032、1033 条；《消费者权益保护法》第 29 条及《实施条例》第 24 条；《个人信息保护法》第 5、13、21、23 条；《互联网信息服务深度合成管理规定》第 17 条；《人工智能生成合成内容标识办法》（国信办通字〔2025〕2 号，2025-09-01 施行）及配套强制性国标 GB 45438-2025；工信部信管函〔2026〕375 号《关于 2026 年第三季度电信服务质量的通告》；湖南省通信管理局行政执法典型案例释义（呼叫中心商业营销呼出被约谈案例）。

### 24.6 假设与待确认清单（本文档采用的关键假设，如有偏差请指出即改）
1. 语音/线路/AI 供应商未指定品牌，采用供应商中立适配层；**所有单价待询价填充**。
2. 部署在国内、需 ICP 备案与相应增值电信资质（我方主体是否已持有需确认 —— 这是能否合法经营 SaaS 的前提）。
3. V1 不做「平台自营线路」，`telesales` 场景要求租户自带线路与资质。
4. 电销与催收默认关闭，按灰度白名单开放。
5. 试点规模取 1000 通/天、10 路并发（用户选项为试点级），架构按 200 路预留。
6. 行业媒体报道的《人工智能拟人化互动服务管理暂行办法》与「留存 ≥ 30 日」「8:00–21:00 时段」等条目属**待核实/行业惯例**，已在 §12 标注，须以官方文本替换。

### 24.7 变更记录
| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-22 | 首版，需求四项已确认（四场景全覆盖 / 多租户 SaaS / 混合智能 / 试点级规模） |

---

内容由 AI 生成
