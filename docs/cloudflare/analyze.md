# Cloud Mail 项目深度分析

> 项目路径：`cloud-mail-main/`  
> 分析日期：2026-05-17

---

## 目录

1. [项目概述与技术栈](#1-项目概述与技术栈)
2. [整体架构与目录结构](#2-整体架构与目录结构)
3. [域名配置：是否需要自备域名](#3-域名配置是否需要自备域名)
4. [多邮箱创建机制](#4-多邮箱创建机制)
5. [邮件接收完整链路](#5-邮件接收完整链路)
6. [验证码自动提取](#6-验证码自动提取)
7. [角色与权限控制](#7-角色与权限控制)
8. [关键配置项速查](#8-关键配置项速查)

---

## 1. 项目概述与技术栈

Cloud Mail 是一个完全运行在 **Cloudflare 全栈平台**上的邮箱服务，核心目标是：用一个域名创建多个邮箱地址，支持邮件收发、验证码识别，所有基础设施均来自 Cloudflare 免费套餐。

| 层次 | 技术 |
|------|------|
| 运行时 | Cloudflare Workers |
| Web 框架 | Hono |
| ORM | Drizzle ORM |
| 数据库 | Cloudflare D1（SQLite） |
| 缓存 / Session | Cloudflare KV |
| 文件存储 | Cloudflare R2 |
| 邮件发送 | Resend |
| AI 验证码识别 | Cloudflare Workers AI（llama-3.1-8b-instruct） |
| 前端 | Vue 3 + Element Plus + Vite |
| 人机验证 | Cloudflare Turnstile |

---

## 2. 整体架构与目录结构

```
cloud-mail-main/
├── mail-worker/              # Cloudflare Worker 后端
│   ├── src/
│   │   ├── api/              # HTTP API 路由层
│   │   ├── email/
│   │   │   └── email.js      # Worker email handler 入口
│   │   ├── entity/           # D1 数据库 Schema（Drizzle）
│   │   ├── service/          # 业务逻辑层（20+ 服务）
│   │   ├── security/         # JWT + KV Session 认证中间件
│   │   └── index.js          # Worker 入口（fetch + email + cron）
│   └── wrangler.toml         # Cloudflare 部署配置
│
└── mail-vue/                 # Vue 3 前端 SPA
    └── src/
        ├── views/            # 页面（登录、邮件、设置、用户等）
        ├── request/          # Axios API 封装
        └── store/            # Pinia 状态管理
```

**请求数据流：**

```
浏览器 SPA → /api/* → Worker fetch → Hono 路由 → Service 层 → D1 / KV
```

**邮件接收流：**

```
外部邮件服务器 → Cloudflare Email Routing（MX） → Worker email handler → D1 存储
```

---

## 3. 域名配置：是否需要自备域名

**结论：必须自备域名，没有任何内置域名。**

域名在 `wrangler.toml` 的 `[vars]` 中配置，**支持同时配置多个域名**：

```toml
# mail-worker/wrangler.toml
[vars]
domain = []   # 示例: ["example1.com","example2.com"]
admin  = ""   # 管理员邮箱，示例: admin@example.com
```

**Cloudflare 侧的配套配置（需手动完成）：**

1. 将域名的 **MX 记录**指向 Cloudflare Email Routing
2. 在 Cloudflare Dashboard 开启 **Email Routing**，配置 **Catch-all 规则** → 转发到 Worker
3. 创建 D1 数据库、KV 命名空间、R2 存储桶，填入 `wrangler.toml`

---

## 4. 多邮箱创建机制

系统将"邮箱地址"与"用户账号"分离：一个用户（`user` 表）可拥有多个邮件地址（`account` 表）。

### 4.1 数据库 Schema

```js
// mail-worker/src/entity/account.js
sqliteTable('account', {
    accountId:  integer PK autoIncrement,
    email:      text NOT NULL,      // 完整邮箱地址，如 alice@example.com
    name:       text NOT NULL,      // 前缀部分，如 alice
    userId:     integer NOT NULL,   // 关联 user 表
    allReceive: integer default 0,  // 是否接收所有邮件
    sort:       integer default 0,  // 排序（置顶功能）
    isDel:      integer default 0,  // 软删除标记
});
```

### 4.2 注册时创建主邮箱

用户注册时邮箱地址即成为其"主邮箱"，同时写入 `account` 表：

```js
// mail-worker/src/service/login-service.js  第 64、133 行
if (!c.env.domain.includes(emailUtils.getDomain(email))) {
    throw new BizError(t('notEmailDomain'));   // 域名不在配置列表中则拒绝
}
await accountService.insert(c, { userId, email, name: emailUtils.getName(email) });
```

### 4.3 登录后添加额外邮箱

登录用户可在前端"添加邮箱"，校验条件（`account-service.js` `add()` 方法）：

| 校验项 | 说明 |
|--------|------|
| `addEmail` + `manyEmail` 开关 | 管理员必须同时开启这两个功能 |
| 域名合法 | 邮箱域名必须在 `c.env.domain` 列表中 |
| 前缀长度 / 过滤词 | 不低于 `minEmailPrefix`，不含 `emailPrefixFilter` |
| 角色域名权限 | 角色的 `availDomain` 为空（不限）或包含该域名 |
| 账号数量上限 | 角色的 `accountCount`，0=不限，>0=上限 |
| 邮箱未注册 | 该地址未被其他用户占用 |

```js
// mail-worker/src/service/account-service.js  第 38、65-70 行
if (!c.env.domain.includes(emailUtils.getDomain(email))) {
    throw new BizError(t('notExistDomain'));
}
if (roleRow.accountCount > 0 && userAccountCount >= roleRow.accountCount) {
    throw new BizError(t('accountLimit'), 403);
}
if (!roleService.hasAvailDomainPerm(roleRow.availDomain, email)) {
    throw new BizError(t('noDomainPermAdd'), 403);
}
```

---

## 5. 邮件接收完整链路

整个接收流程由 `mail-worker/src/email/email.js` 的 `email()` 函数驱动：

```
[外部发件方]
    │  SMTP
    ▼
[Cloudflare Email Routing]  ← 根据 MX 记录接收
    │  触发 Worker email 事件
    ▼
[email() 函数]
    │
    ├─ 1. 读取系统设置（receive 开关 / 黑名单等）
    │      若 receive=CLOSE → setReject('Service suspended')
    │
    ├─ 2. PostalMime 解析原始邮件字节流
    │      → subject / html / text / attachments / from / to
    │
    ├─ 3. 黑名单过滤（checkBlock）
    │      检查 blackSubject / blackContent / blackFrom
    │      命中任一 → setReject('Message rejected')
    │
    ├─ 4. 查找收件人 account 记录
    │      未找到 && noRecipient=CLOSE → setReject('Recipient not found')
    │      未找到 && noRecipient=OPEN  → 以匿名方式继续存储
    │
    ├─ 5. 权限校验（非管理员）
    │      检查角色 availDomain 是否允许该收件域名
    │      检查角色 banEmail 是否禁止该发件人
    │
    ├─ 6. AI 验证码提取（aiService.extractCode）
    │      Workers AI 从 subject + body 提取 ≤8 字符验证码
    │
    ├─ 7. 写入 D1 数据库（status: SAVING → RECEIVE / NOONE）
    │
    ├─ 8. 附件存入 R2 / KV 对象存储
    │
    ├─ 9. 可选：转发到 Telegram Bot
    │
    └─ 10. 可选：转发到其他邮箱（message.forward）
```

前端通过**轮询** `/email/latest?emailId={lastId}` 增量拉取新邮件，轮询间隔由管理员配置的 `autoRefresh` 字段控制（最低 3 秒）。

---

## 6. 验证码自动提取

`ai-service.js` 通过 **Workers AI** 实现，默认模型 `@cf/meta/llama-3.1-8b-instruct`：

```js
// mail-worker/src/service/ai-service.js  第 22-35 行
const result = await ai.run(c.env.ai_model || '@cf/meta/llama-3.1-8b-instruct', {
    messages: [
        {
            role: 'system',
            content: 'You extract verification codes from emails. Return only JSON like {"code":"12345678"} ...'
        },
        { role: 'user', content: `Subject: ${subject}\n\n${body}` }
    ],
    temperature: 0,
    max_tokens: 32
});
```

**触发条件：**

- 管理员需在设置中开启 `aiCode` 开关
- 可配置 `aiCodeFilter`（逗号分隔的邮箱/域名白名单），仅对特定发件方提取

**提取结果：** 写入 `email.code` 字段（≤8 字符，不含空格），前端可直接显示并一键复制。

---

## 7. 角色与权限控制

系统采用 RBAC 模型，角色关键字段：

| 字段 | 说明 |
|------|------|
| `accountCount` | 最多可拥有的邮箱数量（0=不限） |
| `availDomain` | 允许使用的域名（空=不限，逗号分隔） |
| `banEmail` | 禁止接收的发件人/域名（`*` 表示禁止所有） |
| `sendCount` | 每日发件数量上限 |

`hasAvailDomainPerm()` 核心逻辑（`role-service.js` 第 157 行）：

```js
hasAvailDomainPerm(availDomain, email) {
    availDomain = availDomain.split(',').filter(item => item !== '');
    if (availDomain.length === 0) return true;  // 空列表 = 不限制
    return availDomain.findIndex(item =>
        emailUtils.getDomain(email.toLowerCase()) === item.toLowerCase()
    ) > -1;
}
```

管理员可为不同角色分配不同域名权限，实现**多域名多用户组隔离**。

---

## 8. 关键配置项速查

### wrangler.toml 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `domain` | 邮件域名列表（JSON 数组） | `["mail.com","work.com"]` |
| `admin` | 管理员邮箱（不受角色限制） | `admin@mail.com` |
| `jwt_secret` | JWT 签名密钥 | 任意字符串 |
| `ai_model` | Workers AI 模型名 | 默认 `@cf/meta/llama-3.1-8b-instruct` |

### wrangler.toml 绑定资源

| 绑定名 | 类型 | 说明 |
|--------|------|------|
| `db` | D1 | 主数据库（固定名，不可改） |
| `kv` | KV | Session / 缓存（固定名，不可改） |
| `r2` | R2 | 附件存储（固定名，不可改） |
| `email` | send_email | Cloudflare 发件绑定（可选） |
| `ai` | AI | Workers AI 绑定 |
| `assets` | Assets | 前端静态资源 |

### Cron 任务

```toml
crons = ["*/30 * * * *", "0 16 * * *"]
# 每 30 分钟：刷新分析数据缓存
# 每天 0 点（UTC+8）：清理过期记录、重置发件计数、补全接收状态
```

---

## 总结

| 问题 | 结论 |
|------|------|
| 需要自备域名吗？ | **是，必须自备域名并托管到 Cloudflare** |
| 如何支持多域名？ | `domain` 变量填写 JSON 数组，如 `["a.com","b.com"]` |
| 如何创建多个邮箱？ | 注册时创建主邮箱；登录后在前端"添加邮箱"追加同域名下任意前缀 |
| 邮件如何到达系统？ | Cloudflare Email Routing（MX 记录）→ Catch-all → Worker email 事件 |
| 验证码如何识别？ | Workers AI（LLM）从邮件主题+正文提取，存入 `email.code` 字段 |
| 多用户隔离？ | RBAC：角色控制可用域名、邮箱数量上限、发件权限 |
