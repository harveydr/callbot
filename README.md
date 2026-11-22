# EchoDesk

多租户 AI 外呼控制台。产品规则以 `docs/PRD.md` 为准。

## 本地运行

依赖：Python 3.11.9、Node.js 22、PostgreSQL 16.4、Redis 7.2.5。

```bash
docker compose up -d db redis
cp .env.example .env
# 填写 JWT_SECRET、FIELD_ENC_KEY、PHONE_PEPPER、SEED_ADMIN_PASSWORD，不要提交 .env
cd backend && alembic upgrade head && cd ..
SEED_ADMIN_PASSWORD='至少8位' DATABASE_URL=postgresql+asyncpg://echodesk_app@127.0.0.1:5432/echodesk python scripts/seed.py
cd backend && uvicorn app.main:app --reload
cd frontend && npm ci && npm run dev
```

平台账号是 `platform.admin`，首次登录必须改密。演示租户 `demo` 带 `is_demo=true`，报表会排除它。

`telesales` 和 `collection` 默认关闭。托管线路不能承载电销。呼前闸门、AI 身份声明、录音告知和禁呼不能通过配置关掉。

当前语音网关是本地 mock。真实 FreeSWITCH 中继不在这个仓库的默认 Compose 里，上线前要用备案号码做真实冒烟。

## 检查

```bash
cd backend && pytest -q && ruff check app tests alembic && mypy app
python scripts/eval_redline.py
python scripts/go_live_check.py
cd frontend && npm run lint && npm run build
```
