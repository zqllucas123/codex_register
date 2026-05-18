import json
import re
import sys
import time
from pathlib import Path
import requests as _requests_lib

CLOUD_MAIL_BASE = "https://chatgpt-register-email.chatvista-email.workers.dev"
ACCOUNTS_FILE = Path(__file__).parent / "cloud_mail_accounts.json"

def check(email, password):
    s = _requests_lib.Session()
    hdrs = {"content-type": "application/json", "accept": "application/json"}

    r = s.post(f"{CLOUD_MAIL_BASE}/api/login", json={"email": email, "password": password}, headers=hdrs)
    if not r or r.status_code != 200:
        print(f"[{email}] 登录失败: {r.status_code if r else 'no response'} {r.text[:200] if r else ''}")
        return
    token = r.json()["data"]["token"]
    hdrs["Authorization"] = token
    time.sleep(2)

    # 获取邮箱列表
    r = s.get(f"{CLOUD_MAIL_BASE}/api/account/list?size=10", headers=hdrs)
    accounts = r.json().get("data", [])
    if not accounts:
        print(f"[{email}] 无邮箱账号")
        return

    account_id = accounts[0]["accountId"]

    # 获取邮件
    r = s.get(f"{CLOUD_MAIL_BASE}/api/email/list?accountId={account_id}&type=0&size=10", headers=hdrs)
    emails = r.json().get("data", {}).get("list", [])

    if not emails:
        print(f"[{email}] 暂无邮件")
        return

    for m in emails:
        code = m.get("code", "")
        text_body = (m.get("text") or "").strip()
        html_body = m.get("content") or ""

        # text 为空时从 HTML 中提取纯文本和验证码
        if not text_body and html_body:
            clean = re.sub(r"<style[^>]*>.*?</style>", " ", html_body, flags=re.DOTALL)
            clean = re.sub(r"<[^>]+>", " ", clean)
            text_body = re.sub(r"\s+", " ", clean).strip()
        if not code and text_body:
            m_code = re.search(r"\b(\d{6,8})\b", text_body)
            if m_code:
                code = m_code.group(1)

        print(f"  [{m['createTime']}] {m['sendEmail']} -> {m['subject']}")
        if code:
            print(f"    验证码: {code}")
        if text_body:
            print(f"    正文: {text_body[:300]}")

accounts = json.loads(ACCOUNTS_FILE.read_text())

# 支持指定邮箱：python check_mail.py xxx@chatvista.online
if len(sys.argv) > 1:
    target = sys.argv[1]
    accounts = [a for a in accounts if a["email"] == target]

for a in accounts:
    print(f"\n=== {a['email']} ===")
    check(a["email"], a["password"])
