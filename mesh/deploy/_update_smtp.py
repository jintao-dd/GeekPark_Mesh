#!/usr/bin/env python3
import sys
from pathlib import Path
import smtplib


def _ssl(env):
    s = smtplib.SMTP_SSL(env.get("SMTP_HOST", "smtp.feishu.cn"), 465, timeout=30)
    s.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
    s.quit()


def _starttls(env):
    s = smtplib.SMTP(env.get("SMTP_HOST", "smtp.feishu.cn"), 587, timeout=30)
    s.starttls()
    s.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
    s.quit()


updates = {
    "SMTP_HOST": "smtp.feishu.cn",
    "SMTP_PORT": "465",
    "SMTP_SSL": "1",
    "SMTP_USER": "mesh@geekpark.net",
    "SMTP_FROM": "GeekPark Mesh <mesh@geekpark.net>",
}
if len(sys.argv) > 1:
    updates["SMTP_PASSWORD"] = sys.argv[1]

env_path = Path("/opt/geekpark-mesh/.env")
lines = env_path.read_text(encoding="utf-8").splitlines()
out, keys = [], set()
for line in lines:
    hit = False
    for k, v in updates.items():
        if line.startswith(k + "="):
            out.append(f"{k}={v}")
            keys.add(k)
            hit = True
            break
    if not hit:
        out.append(line)
for k, v in updates.items():
    if k not in keys:
        out.append(f"{k}={v}")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("env_updated")

env = {}
for line in out:
    if "=" in line and not line.strip().startswith("#"):
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip()

for mode, fn in (("ssl_465", _ssl), ("starttls_587", _starttls)):
    try:
        fn(env)
        print("smtp_login_ok", mode)
        break
    except Exception as e:
        print("smtp_fail", mode, repr(e))
