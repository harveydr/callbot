# CLAUDE.md — EchoDesk（多租户 AI 语音外呼 SaaS）

> 本文件是 Claude Code / 开发 Agent 读取项目上下文的入口，实际使用时放到仓库根目录 `CLAUDE.md`。
> 内容刻意保持精简，规范细节一律指向 PRD，不在此重复。

## 项目简介
面向企业的多租户 AI 语音外呼 SaaS：大模型负责理解与应答，关键节点由话术树与合规红线硬约束，V1 为试点级规模（10 路并发、1000 通/天）。

## 技术栈摘要
Python 3.11.9 · FastAPI 0.115.6 · SQLAlchemy 2.0.36(async) · Alembic 1.14.0 · Pydantic 2.10.3 · PostgreSQL 16.4（RLS）· Redis 7.2.5 + Celery 5.4.0 · FreeSWITCH 1.10.12 + silero-vad · Vue 3.5.13 + Element Plus 2.9.1 + Vite 6.0.5 · Docker Compose + Nginx 1.27.3

## 文档位置
**所有开发规范见 `docs/PRD.md`**（即 `prd/AI智能外呼系统PRD-v1.md`）。该文档是唯一真相源：数据模型 DDL、53 个 API 的 Schema、权限矩阵、合规引擎规则、降级层级、成本模型、测试门禁全部在其中。冲突时以 PRD 为准，需要变更先改 PRD 再改代码。

## 开发规范
- 代码风格：`ruff` + `mypy`（严格）；前端 `eslint` + `prettier`；CI 不通过不得合并。
- 命名：API 资源用复数、路径 `/api/{模块}/{资源}`；枚举值一律小写，前后端共用同一份枚举定义。
- Git：`feat|fix|refactor|test|docs|chore(scope): 摘要`；一个 PR 一个可验证行为变更；含迁移的 PR 必须说明回滚方式。
- 提交前自检：合规相关改动必须附对应测试用例，合规用例红着不许提 PR。

## 关键命令
```bash
docker compose up -d db redis          # 启动依赖
alembic upgrade head                   # 执行迁移
python scripts/seed.py                 # 种子数据
uvicorn app.main:app --reload          # 后端开发服务
cd frontend && npm install && npm run dev   # 前端开发服务
pytest -q                              # 全部测试
pytest tests/test_compliance_gate.py -q     # 合规门禁用例（最高优先级）
ruff check . && mypy app               # 静态检查
docker compose up --build -d           # 全栈启动（含 FreeSWITCH）
```

## 禁止事项
- 不要引入 PRD 未列出的第三方依赖。
- 不要直接修改数据库表结构，必须通过 Alembic 迁移，且迁移必须可回滚。
- 不要修改 `.env` 中的变量名；不要在代码中硬编码密码、密钥、API Key。
- 不要跳过权限校验与 `tenant_id` 过滤——即使数据库已启用 RLS，应用层 `scope_filter` 仍必须保留。
- 关键写入接口必须实现幂等（`Idempotency-Key` 或唯一约束）；重试只允许用于幂等操作。
- 日志中不得输出密码、Token、完整手机号、身份证号等敏感信息原文。
- 每个功能开发完成后必须能通过 PRD 中对应的测试用例。
- 禁止新增任何「可关闭/可跳过合规」的配置项（AI 身份声明、DND、录音告知、时段与频次闸门）。
- 禁止实现改显、随机换号、伪装本地号码、虚拟号呼出等能力，即使有人提需求。

## 开发红线
- 写第一个功能代码之前，先跑通最小异步测试用例，验证 pytest + asyncio + PostgreSQL + Redis 框架组合可用（PRD §17.1）。
- 时间类型统一 **aware UTC + TIMESTAMPTZ**，主键统一 `uuid`（`audit_logs` 用 bigint identity），枚举统一小写；禁止混用。**任何时段/频次判定必须在呼前再判一次并带租户时区**，时区错误等同合规事故。
- SQLAlchemy async 禁止 lazy loading，所有关联查询显式 `selectinload()/joinedload()`。
- 测试数据清理按 FK 逆序执行，`conftest.py` 的清理 fixture 必须在写第一个测试之前设计好。
- 测试使用独立数据库与独立 Redis db（db=15），禁止复用开发环境；供应商一律 mock，禁止测试打真实 ASR/TTS/LLM 接口。
- 有全局副作用的操作（写 Redis 缓存、改环境变量、创建临时文件）测试结束后必须清理。
- Docker 容器间连接用服务名（`db`/`redis`/`fs`），禁止 localhost；前端 API 地址从 `VITE_BASE_URL` 读取，不硬编码协议。
- `voice-orchestrator` 发布前必须先把 `platform.calling.enabled` 置 false 摘流量、等在途通话归零，再滚动发布。

---

内容由 AI 生成
