import os
import smtplib

user = os.environ.get("SMTP_USER", "")
pw = os.environ.get("SMTP_PASSWORD", "")
host = os.environ.get("SMTP_HOST", "smtp.feishu.cn")

def try_ssl():
    s = smtplib.SMTP_SSL(host, 465, timeout=30)
    s.login(user, pw)
    s.quit()
    print("ok_ssl_465")

def try_starttls():
    s = smtplib.SMTP(host, 587, timeout=30)
    s.starttls()
    s.login(user, pw)
    s.quit()
    print("ok_starttls_587")

for fn in (try_ssl, try_starttls):
    try:
        fn()
    except Exception as e:
        print(fn.__name__, repr(e))
