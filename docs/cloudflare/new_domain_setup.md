# Cloud Mail 新域名部署配置指南

本文档说明如何将 Cloud Mail 配置到一个全新的域名，从 Cloudflare 资源创建到 Worker 部署的完整流程。

---

## 前置条件

- Cloudflare 账户（免费版即可）
- 域名已托管在 Cloudflare（NS 已指向 Cloudflare）
- Node.js ≥ 18，pnpm，wrangler CLI（`npm i -g wrangler`）
- 已登录 wrangler：`wrangler login`

---

## 第一步：创建 Cloudflare 资源

### 1.1 创建 D1 数据库

```bash
wrangler d1 create cloud-mail-db
```

输出示例：

```
✅ Successfully created DB 'cloud-mail-db'
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

记录 `database_id`，后面填入 `wrangler.toml`。

### 1.2 创建 KV 命名空间

```bash
wrangler kv namespace create kv
```

输出示例：

```
✅ Success!
id = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

记录 `id`，后面填入 `wrangler.toml`。

---

## 第二步：配置 wrangler.toml

编辑 `cloud-mail-main/mail-worker/wrangler.toml`，将所有占位符替换为你的实际值：

```toml
name = "your-worker-name"           # Worker 名称，决定默认域名 xxx.workers.dev
main = "src/index.js"
compatibility_date = "2025-06-04"

[observability]
enabled = true

[[d1_databases]]
binding = "db"                       # 不可修改
database_name = "cloud-mail-db"
database_id = "第一步中记录的 database_id"

[[kv_namespaces]]
binding = "kv"                       # 不可修改
id = "第一步中记录的 kv id"

[ai]
binding = "ai"

[triggers]
crons = ["*/30 * * * *", "0 16 * * *"]

[vars]
domain = ["yourdomain.com"]          # 你的新域名，支持多个：["a.com","b.com"]
admin = "admin@yourdomain.com"       # 管理员邮箱（首次使用该邮箱注册即成为管理员）
jwt_secret = "替换为随机字符串"       # 建议 32 位以上随机字符串
```

> ⚠️ **注意事项**：
>
> - `binding = "db"` 和 `binding = "kv"` 必须保持小写，不能改为 `DB`/`KV`
> - `domain` 是数组格式，即使只有一个域名也要用方括号
> - `admin` 邮箱必须与 `domain` 中的域名一致（如 `@yourdomain.com`）
> - 不要使用 `keep_vars = true`，避免与旧 Dashboard 变量冲突

---

## 第三步：安装依赖并部署 Worker

```bash
cd cloud-mail-main/mail-worker

# 安装依赖（首次需要审批构建脚本）
pnpm install

# 如果报 [ERR_PNPM_IGNORED_BUILDS] 错误
pnpm approve-builds   # 输入 y 批准 esbuild/sharp/workerd

# 部署到 Cloudflare
npx wrangler deploy
```

部署成功后输出：

```
✅ Deployed to: https://your-worker-name.xxx.workers.dev
```

---

## 第四步：初始化数据库

部署完成后，访问以下 URL 初始化 D1 数据库表结构（一次性操作）：

```
https://your-worker-name.xxx.workers.dev/api/init/{jwt_secret}
```

将 `{jwt_secret}` 替换为 `wrangler.toml` 中配置的 `jwt_secret` 值。

**验证**：返回 `success` 表示初始化成功。

> 初始化会创建所有数据库表（`user`、`account`、`email`、`setting`、`role`、`perm` 等）并写入默认权限数据。

---

## 第五步：配置域名邮件接收（Email Routing）

这是让域名能够收取邮件的核心配置。

### 5.1 在 Cloudflare 控制台开启 Email Routing

1. 进入 Cloudflare Dashboard → 选择你的域名
2. 左侧菜单 → **Email** → **Email Routing**
3. 点击 **Enable Email Routing**
4. 按提示添加 Cloudflare 的 MX 记录（会自动添加）

### 5.2 添加 Catch-all 路由规则

1. 在 Email Routing 页面 → **Routing rules** 标签
2. 滚动到 **Catch-all address** 区域
3. 点击 Edit，设置：
   - **Action**: Send to a Worker
   - **Destination**: 选择你部署的 Worker（`your-worker-name`）
4. 保存

配置完成后，所有发往 `*@yourdomain.com` 的邮件都会转发到 Worker 处理。

---

## 第六步：注册管理员账号

1. 访问 `https://your-worker-name.xxx.workers.dev`（或绑定的自定义域名）
2. 点击注册，使用 `wrangler.toml` 中 `admin` 配置的邮箱注册
3. 该账号自动获得管理员权限，无需额外配置

---

## 第七步（可选）：绑定自定义域名

如果不想使用 `workers.dev` 默认域名：

1. Cloudflare Dashboard → Workers & Pages → 选择你的 Worker
2. **Settings** → **Domains & Routes** → **Add Custom Domain**
3. 输入 `mail.yourdomain.com`（或任意子域名）
4. Cloudflare 自动添加 DNS 记录并配置 HTTPS

---

## 第八步（可选）：配置 Python 脚本使用新域名

如果使用本项目的 `gen_accounts.py` / `check_mail.py` / `chatgpt.py`，修改以下常量：

**`chatgpt.py`**：

```python
CLOUD_MAIL_BASE   = "https://your-worker-name.xxx.workers.dev"
CLOUD_MAIL_EMAIL  = "admin@yourdomain.com"
CLOUD_MAIL_PASS   = "你的管理员密码"
CLOUD_MAIL_DOMAIN = "yourdomain.com"
```

**`gen_accounts.py`** / **`check_mail.py`**：

```python
CLOUD_MAIL_BASE = "https://your-worker-name.xxx.workers.dev"
```

> ⚠️ **Auth Header 注意**：Cloud Mail API 的 `Authorization` header 直接传裸 token，**不需要** `Bearer ` 前缀：
>
> ```python
> headers["Authorization"] = token   # ✅ 正确
> headers["Authorization"] = f"Bearer {token}"  # ❌ 会导致 401
> ```

---

## 配置检查清单

| 步骤          | 检查项                                         |
| ------------- | ---------------------------------------------- |
| D1            | `database_id` 填写正确                       |
| KV            | `id` 填写正确，`binding = "kv"`（小写）    |
| wrangler.toml | `domain` 为数组格式，`admin` 邮箱域名一致  |
| wrangler.toml | 无 `keep_vars = true`                        |
| Worker        | 已执行 `npx wrangler deploy`                 |
| DB 初始化     | 已访问 `/api/init/{jwt_secret}` 返回 success |
| Email Routing | MX 记录已生效，Catch-all 指向 Worker           |

---

## 常见问题

### Worker 返回 "Handler does not export a fetch() function"

未部署代码，执行 `npx wrangler deploy`。

### `/api/init` 返回 "JWT secret mismatch"

URL 中的 secret 与 `wrangler.toml` 的 `jwt_secret` 不一致。

### API 返回 401 "Authentication has expired"

检查请求的 `Authorization` header 是否带了 `Bearer ` 前缀（需去掉）。

### 邮件收不到

- 确认 Email Routing 已开启，MX 记录已生效（可能需要几分钟传播）
- 确认 Catch-all 规则的 Action 是 "Send to Worker" 而非 "Drop"
- 确认收件邮箱已在 Cloud Mail 中添加（`/api/account/add`）

### pnpm install 报 `[ERR_PNPM_IGNORED_BUILDS]`

执行 `pnpm approve-builds` 并对 esbuild/sharp/workerd 输入 `y`。

---

## 项目使用说明

完成以上部署后，本节说明如何使用配套 Python 脚本完成 GPT 账号注册的完整流程。

### 整体流程概览

```
gen_accounts.py          chatgpt.py               check_mail.py
─────────────           ─────────────            ─────────────
批量在 Cloud Mail  →    使用 Cloud Mail 邮箱  →   查看已注册邮箱
注册收件邮箱            自动注册 GPT 账号           的收件信息
保存至                  Token 保存至
cloud_mail_accounts.json  tokens/
```

---

### 步骤一：批量生成 Cloud Mail 收件账号

`gen_accounts.py` 在 Cloud Mail 上批量注册邮箱账号，每个账号会收到一个 `@yourdomain.com` 的随机邮箱。

**1. 修改脚本配置**（`gen_accounts.py` 顶部）：

```python
CLOUD_MAIL_BASE   = "https://your-worker-name.xxx.workers.dev"   # 改为你的 Worker 地址
CLOUD_MAIL_DOMAIN = "yourdomain.com"                              # 改为你的新域名
```

**2. 运行脚本**：

```bash
# 生成 5 个账号
uv run gen_accounts.py --count 5

# 使用代理
uv run gen_accounts.py --count 5 --proxy http://127.0.0.1:7890
```

**3. 查看结果**：

账号信息自动追加写入 `cloud_mail_accounts.json`：

```json
[
  { "email": "abc1234567@yourdomain.com", "password": "Xk3!mP9@qZ" },
  { "email": "xyz9876543@yourdomain.com", "password": "Rt7#nQ2$wL" }
]
```

---

### 步骤二：配置并运行 GPT 注册脚本

`chatgpt.py` 自动完成 OpenAI OAuth2 注册流程，每次注册时在 Cloud Mail 中临时创建一个收件地址，注册完成后自动删除。

**1. 修改脚本配置**（`chatgpt.py` 顶部 `Section 1`）：

```python
CLOUD_MAIL_BASE   = "https://your-worker-name.xxx.workers.dev"   # 改为你的 Worker 地址
CLOUD_MAIL_EMAIL  = "admin@yourdomain.com"                        # Cloud Mail 的管理员账号邮箱
CLOUD_MAIL_PASS   = "你的管理员账号密码"                           # 管理员账号密码
CLOUD_MAIL_DOMAIN = "yourdomain.com"                              # 改为你的新域名
```

> `CLOUD_MAIL_EMAIL` / `CLOUD_MAIL_PASS` 是你在**第六步**注册的管理员账号，脚本用此账号登录后临时创建/删除收件地址。

**2. 运行注册脚本**：

```bash
# 持续循环注册（每次注册间隔 5-15 秒）
uv run chatgpt.py

# 只注册一次
uv run chatgpt.py --once

# 使用代理
uv run chatgpt.py --once --proxy http://127.0.0.1:7890
```

**3. 查看注册结果**：

每次成功注册后，Token 文件保存在 `tokens/` 目录：

```
tokens/
├── token_abc1234567_yourdomain.com_1748000000.json   # 单个 Token 文件
└── accounts.txt                                       # 汇总：email----password 格式
```

`accounts.txt` 内容示例：

```
abc1234567@yourdomain.com----OpenAIPass123!
xyz9876543@yourdomain.com----SecurePass456@
```

---

### 步骤三：查看域名邮箱的收件信息

`check_mail.py` 读取 `cloud_mail_accounts.json` 中的账号，登录 Cloud Mail 查看每个邮箱的收件列表及验证码。

**1. 修改脚本配置**（`check_mail.py` 顶部）：

```python
CLOUD_MAIL_BASE = "https://your-worker-name.xxx.workers.dev"   # 改为你的 Worker 地址
```

**2. 查看所有账号的收件**：

```bash
uv run check_mail.py
```

输出示例：

```
=== abc1234567@yourdomain.com ===
  [2026-05-18 10:23:45] noreply@tm.openai.com → 主题: 你的 ChatGPT 临时验证码
    验证码: 847291

=== xyz9876543@yourdomain.com ===
  暂无邮件
```

**3. 查看指定邮箱的收件**：

```bash
uv run check_mail.py abc1234567@yourdomain.com
```

---

### 脚本配置速查

| 脚本                | 需修改的配置项                                                                        |
| ------------------- | ------------------------------------------------------------------------------------- |
| `gen_accounts.py` | `CLOUD_MAIL_BASE`、`CLOUD_MAIL_DOMAIN`                                            |
| `chatgpt.py`      | `CLOUD_MAIL_BASE`、`CLOUD_MAIL_EMAIL`、`CLOUD_MAIL_PASS`、`CLOUD_MAIL_DOMAIN` |
| `check_mail.py`   | `CLOUD_MAIL_BASE`                                                                   |

### 注意事项

- **依赖安装**：所有脚本通过 `uv run` 执行，需安装 `uv`（`pip install uv`）；依赖包含 `curl_cffi`
- **注册限制**：Cloud Mail 默认开放注册，如账号过多建议在管理后台开启注册邀请码功能
- **验证码识别**：Cloud Mail 启用了 Workers AI 自动识别 OTP 验证码，`check_mail.py` 输出的 `验证码` 字段即为识别结果
- **邮件路由延迟**：Email Routing 转发通常在 1-5 秒内完成；`chatgpt.py` 轮询超时为 8 分钟
