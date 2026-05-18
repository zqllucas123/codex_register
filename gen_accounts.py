import argparse
import json
import random
import string
import time
from pathlib import Path

import requests as _requests_lib

CLOUD_MAIL_BASE   = "https://chatgpt-register-email.chatvista-email.workers.dev"
CLOUD_MAIL_DOMAIN = "chatvista.online"
OUT_FILE = Path(__file__).parent / "cloud_mail_accounts.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def rstr(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def gen_password():
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    base = [
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_uppercase),
        random.choice(string.digits),
        random.choice("!@#$%^&*"),
    ]
    base += [random.choice(chars) for _ in range(10)]
    random.shuffle(base)
    return "".join(base)


def register_one(session, email, password):
    try:
        return session.post(
            f"{CLOUD_MAIL_BASE}/api/register",
            json={"email": email, "password": password},
            headers={"content-type": "application/json", "accept": "application/json", "user-agent": UA},
            timeout=20,
        )
    except Exception as e:
        print(f"  [!] 请求异常: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="批量生成 Cloud Mail 账号")
    parser.add_argument("--count", type=int, default=1, help="生成账号数量")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    args = parser.parse_args()

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None

    results = []
    with _requests_lib.Session() as s:
        if proxies:
            s.proxies = proxies
        for i in range(args.count):
            email = f"{rstr(10)}@{CLOUD_MAIL_DOMAIN}"
            password = gen_password()
            r = register_one(s, email, password)
            if r and r.status_code == 200:
                results.append({"email": email, "password": password})
                print(f"[{i+1}/{args.count}] OK  {email}  {password}")
            else:
                print(f"[{i+1}/{args.count}] FAIL {email}  status={r.status_code if r else 'no response'}  body={r.text[:300] if r else ''}")
            if i < args.count - 1:
                time.sleep(1)

    # 追加写入 JSON 文件
    existing = []
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text())
        except Exception:
            pass
    OUT_FILE.write_text(json.dumps(existing + results, ensure_ascii=False, indent=2))
    print(f"\n成功 {len(results)}/{args.count}，已保存至 {OUT_FILE}")


if __name__ == "__main__":
    main()
