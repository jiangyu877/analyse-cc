# 客户零售交易、分析与预测平台

本项目是标准 B/S（Browser/Server）系统。用户只需浏览器，不安装客户端，也不直接连接数据库；Flask + Waitress Web 服务统一处理登录、权限、业务事务、分析任务和页面响应，PostgreSQL 18 仅允许服务端访问。

```text
浏览器（客户360 / 商品 / 订单 / 支付退款 / 算法任务）
        │ HTTP / HTTPS
        ▼
Flask Web 服务（路由 → 服务层 → 仓储层，RBAC + CSRF + 审计）
        │ SQLAlchemy / 事务
        ▼
PostgreSQL（auth / biz / ods / dwd / ads / ml / audit）
```

## 业务能力

- 客户360：客户资料、订单、净消费、RFM 分层与流失概率。
- 商品库存：SKU、分类、价格、库存和低库存预警。
- 交易闭环：订单锁行扣库存，支付生成正向流水，退款生成负向流水。
- 算法任务：RFM、KMeans、流失分类的参数、指标和结果按 `task_id` 留存。
- 安全控制：RBAC、CSRF、登录限流、审计日志、安全响应头；SQL 实验页仅管理员可用并强制只读。
- 服务运维：Waitress 生产入口、数据库连接池、`/healthz` 健康检查和 Docker Compose。

## Windows 本机运行

环境要求：Python 3.12、PostgreSQL 18。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

把 `.env` 中的数据库信息、`SECRET_KEY` 和三个初始账号密码改为实际值，然后初始化：

```powershell
python scripts/init_db.py
```

应用启动时会自动加载 `.env`。开发调试使用：

```powershell
python run.py
```

本机或局域网正式演示使用 Waitress：

```powershell
$env:FLASK_ENV="production"
$env:HOST="0.0.0.0"
python serve.py
```

服务器本机访问 `http://127.0.0.1:5000`；同一局域网其他设备访问 `http://服务器IP:5000`。生产公网部署应由 Nginx/IIS 终止 HTTPS，并设置 `COOKIE_SECURE=true` 与 `TRUST_PROXY=true`。

## Docker 部署

安装 Docker 后，在项目目录设置随机密钥并启动：

```powershell
$env:SECRET_KEY="替换为至少32位随机字符串"
$env:POSTGRES_PASSWORD="替换为高强度数据库密码"
$env:ADMIN_PASSWORD="替换为管理员强密码"
$env:OPERATOR_PASSWORD="替换为业务员强密码"
$env:ANALYST_PASSWORD="替换为分析员强密码"
docker compose up --build -d
docker compose ps
```

访问 `http://127.0.0.1:5000`。首次创建数据卷时，PostgreSQL 会自动执行 V2 Schema 和种子数据；后续重启不会重复初始化或删除数据。

健康检查：`GET /healthz`，数据库正常时返回：

```json
{"status":"ok","database":"up"}
```

## 系统账号

| 角色 | 用户名 | 密码来源 |
|---|---|---|
| 管理员 | `admin` | `ADMIN_PASSWORD` 环境变量 |
| 业务员 | `operator` | `OPERATOR_PASSWORD` 环境变量 |
| 分析员 | `analyst` | `ANALYST_PASSWORD` 环境变量 |

种子 SQL 不再包含公开默认密码。空库初始化时未配置的账号保持随机不可登录状态，设置相应环境变量并重新运行 `scripts/init_db.py` 即可启用。

## GitHub 与公网部署

GitHub Pages 只能运行静态 HTML，无法承载本项目的 Flask 登录、PostgreSQL、事务和算法任务。仓库使用 Render Blueprint 部署主站和托管 PostgreSQL：

1. 将代码推送到 `https://github.com/jiangyu877/analyse-cc` 的 `main` 分支。
2. 登录 Render，选择 **New > Blueprint**，连接该仓库。
3. Render 读取根目录的 `render.yaml`，创建 `analyse-cc` Web 服务和 `analyse-cc-db` PostgreSQL。
4. Blueprint 创建时按提示输入三个至少 12 位的账号密码；`SECRET_KEY` 由平台自动生成。
5. 首次启动会自动创建 V2 Schema、注入账号密码，并导入 5,000 个客户和 50,000 笔消费。
6. 部署完成后访问 Render 分配的 `https://...onrender.com` 地址。

`SECRET_KEY`、数据库连接和三个账号密码都由平台环境变量提供，不能写入仓库。GitHub Actions 会在每次推送后运行测试。若 Render 当前账户不提供免费 PostgreSQL，将 `render.yaml` 中的 `plan` 改为控制台可选方案后重新应用 Blueprint。

## 数据与迁移

`database/schema.sql` 是空库初始化入口，仅创建 V2 对象，不删除 `public` 遗留表。已有 V1 数据时执行：

```powershell
python scripts/init_db.py --migrate-legacy
```

旧用户映射到 `biz.customer`，旧消费原始记录进入 `ods.legacy_spending`，原表保持不变。

生成并导入 5,000 个客户和 50,000 笔完整历史消费：

```powershell
python scripts/import_demo_data.py --customers 5000 --transactions 50000
```

脚本使用固定业务编号且带唯一约束，可重复执行而不会重复生成订单或流水。管理员可在 `/imports` 查看导入批次和数据总量。

## 测试

```powershell
pytest -q
```

重点验收：库存不足整单回滚；支付生成消费流水；退款降低客户净消费；算法任务与指标可追溯；普通用户不能执行管理操作或写 SQL。

## Gradio 图形分析

Gradio 工作台默认仅监听本机 `127.0.0.1:7860`。启动命令：

```powershell
.\.venv\Scripts\python.exe gradio_app.py
```

登录 Flask 后，在“算法任务”页面点击“打开 Gradio 图形分析”。核心图形包括：

该链接来自 `GRADIO_PUBLIC_URL`；未配置时公网主站会隐藏按钮，避免把访问者错误地引导到其本机 `127.0.0.1`。Gradio 当前作为本机受控分析工具，不随 Render 主站公开部署。

- RFM 客户价值气泡图与客户分层结构图。
- KMeans 客户群散点图与群体特征热力图。
- 流失风险 Top20、风险结构环图、概率直方图及 AUC/F1 解读。

RFM、KMeans 与流失分类在执行分析时自动创建 `ml.model_task`，图表、指标和数据库结果使用同一个 `task_id`，不会清空历史结果。
