# GPT 注册改造：接入 Cloud Mail 域名邮箱

> 目标：将 `chatgpt.py` 中的 `mail.tm` 临时邮箱替换为自建 Cloud Mail 服务，使用自有域名邮箱完成 OpenAI 注册和验证码接收。

---

## 1. 现状 vs 目标

| 项目 | 当前（mail.tm） | 目标（Cloud Mail） |
|------|----------------|-------------------|
| 邮箱来源 | 第三方公共临时邮箱 | 自有域名邮箱（如 `xxx@yourdomain.com`） |
| 邮箱创建 | `POST https://api.mail.tm/accounts` | `POST /api/account/add` |
| 验证码获取 | 轮询 `GET /messages` + 正则提取 | 轮询 `GET /api/email/latest` 取 `code` 字段 |
| 认证方式 | mail.tm Bearer Token | Cloud Mail JWT（登录换取） |
| 可控性 | 无（公共服务随时失效） | 完全自控 |

---

## 2. Cloud Mail API 接口速查

所有接口前缀为 `/api`，需在 Header 携带 `Authorization: Bearer <jwt>`。

### 2.1 登录（获取 JWT）

```
POST /api/login
Body: { "email": "admin@yourdomain.com", "password": "xxx" }
Response: { "data": { "token": "<jwt>" } }
```

### 2.2 添加邮箱地址

```
POST /api/account/add
Body: { "email": "random123@yourdomain.com" }
Response: { "data": { "accountId": 42, "email": "random123@yourdomain.com" } }
```

前提：管理员需在 Cloud Mail 设置中开启 `addEmail=OPEN` 和 `manyEmail=OPEN`。

### 2.3 轮询新邮件（增量拉取）

```
GET /api/email/latest?emailId=<lastId>&accountId=<accountId>&allReceive=0
Response: { "data": [ { "emailId": 101, "code": "847291", "subject": "...", ... } ] }
```

- `emailId`：上次已知的最大 emailId，传 `0` 表示拉取所有
- `code` 字段：Cloud Mail 已通过 Workers AI 自动提取的验证码（≤8字符），**无需再做正则**
- 若 `code` 为空，可回退读 `text` 字段自行正则

### 2.4 删除邮箱（用完即删，可选）

```
DELETE /api/account/delete?accountId=<accountId>
```

---

## 3. 改造方案

### 3.1 需要替换的模块

`chatgpt.py` 中只需替换 **Section 1（Mail.tm 模块）** 的三个函数：

| 原函数 | 替换为 |
|--------|--------|
| `mreq()` | `CloudMailClient` 类（封装 JWT 认证） |
| `setup_mail_tm()` | `setup_cloud_mail()` |
| `getotp()` | `CloudMailClient.poll_code()` |

`run()` 函数中调用点只有两处，改动最小：

```python
# 原来
mail_data = setup_mail_tm(proxies)
email, password, code_fetcher = mail_data

# 改后
mail_data = setup_cloud_mail(proxies)
email, password, code_fetcher = mail_data
```

### 3.2 新增配置项

在文件顶部增加：

```python
CLOUD_MAIL_BASE  = "https://your-cloud-mail.workers.dev"  # Cloud Mail 部署地址
CLOUD_MAIL_EMAIL = "admin@yourdomain.com"                  # 管理员账号
CLOUD_MAIL_PASS  = "your_password"                         # 管理员密码
CLOUD_MAIL_DOMAIN = "yourdomain.com"                       # 邮件域名
```

### 3.3 核心实现逻辑

#### `CloudMailClient` 类

```python
class CloudMailClient:
    def __init__(self, base, proxies=None):
        self.base = base.rstrip("/")
        self.proxies = proxies
        self.token = None

    def _req(self, method, path, **kwargs):
        hdrs = {"content-type": "application/json", "accept": "application/json"}
        if self.token:
            hdrs["authorization"] = f"Bearer {self.token}"
        try:
            with Session(proxies=self.proxies) as s:
                return s.request(method, f"{self.base}/api{path}", headers=hdrs, **kwargs)
        except:
            return None

    def login(self, email, password):
        r = self._req("POST", "/login", json={"email": email, "password": password})
        if r and r.status_code == 200:
            self.token = r.json()["data"]["token"]
            return True
        return False

    def add_account(self, email):
        r = self._req("POST", "/account/add", json={"email": email})
        if r and r.status_code == 200:
            return r.json()["data"]["accountId"]
        return None

    def delete_account(self, account_id):
        self._req("DELETE", f"/account/delete?accountId={account_id}")

    def poll_code(self, account_id, timeout=480, interval=8):
        last_id = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self._req("GET", f"/email/latest?emailId={last_id}&accountId={account_id}&allReceive=0")
            if r and r.status_code == 200:
                msgs = r.json().get("data") or []
                for msg in msgs:
                    last_id = max(last_id, msg.get("emailId", 0))
                    code = msg.get("code", "")
                    if code:
                        return code
                    # 回退：正则从 text 提取
                    m = re.search(r"\b(\d{6})\b", msg.get("text", ""))
                    if m:
                        return m.group(1)
            time.sleep(interval)
        return None
```

#### `setup_cloud_mail()` 替换 `setup_mail_tm()`

```python
def setup_cloud_mail(proxies=None):
    client = CloudMailClient(CLOUD_MAIL_BASE, proxies)
    if not client.login(CLOUD_MAIL_EMAIL, CLOUD_MAIL_PASS):
        print("  [!] Cloud Mail 登录失败")
        return None, None, None

    prefix = rstr(10)
    email = f"{prefix}@{CLOUD_MAIL_DOMAIN}"
    account_id = client.add_account(email)
    if not account_id:
        print(f"  [!] 创建邮箱失败: {email}")
        return None, None, None

    openai_password = _gen_password()

    def fetch_code():
        print("  [*] 等待验证码 (最多8分钟)...")
        code = client.poll_code(account_id)
        client.delete_account(account_id)  # 用完即删
        return code

    return email, openai_password, fetch_code
```

---

## 4. Cloud Mail 服务端前置配置

在 Cloud Mail 管理后台（Settings）需确认以下开关：

| 配置项 | 要求 |
|--------|------|
| `receive`（收件开关） | OPEN |
| `addEmail`（允许添加邮箱） | OPEN |
| `manyEmail`（多邮箱模式） | OPEN |
| `aiCode`（AI 验证码提取） | OPEN（可选，开启后 `code` 字段自动填充） |
| 管理员角色 `accountCount` | 0（不限数量） |

---

## 5. 改造后完整数据流

```
chatgpt.py run()
    │
    ├─ setup_cloud_mail()
    │       │
    │       ├─ POST /api/login          → 获取 JWT
    │       └─ POST /api/account/add   → 创建 xxx@yourdomain.com
    │
    ├─ [OpenAI 注册流程，使用该邮箱]
    │       │
    │       └─ OpenAI 发送验证码邮件
    │               │
    │               └─ Cloudflare Email Routing → Worker → D1 存储
    │                       └─ Workers AI 提取 code 字段
    │
    ├─ fetch_code()
    │       └─ 轮询 GET /api/email/latest → 读取 code 字段
    │
    └─ DELETE /api/account/delete      → 清理邮箱
```

---

## 6. 改动范围总结

- **新增**：`CLOUD_MAIL_*` 配置常量、`CloudMailClient` 类、`setup_cloud_mail()` 函数
- **删除**：`mreq()`、`getotp()`、`setup_mail_tm()` 三个函数
- **修改**：`run()` 中一行调用 `setup_mail_tm` → `setup_cloud_mail`
- **不变**：OpenAI OAuth 注册流程（Section 2、3、4）完全不动
