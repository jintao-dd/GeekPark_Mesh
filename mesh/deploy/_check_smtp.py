import os
import sqlite3

print("SMTP_HOST", os.environ.get("SMTP_HOST", ""))
print("SMTP_USER", os.environ.get("SMTP_USER", ""))
print("SMTP_PORT", os.environ.get("SMTP_PORT", ""))
print("SMTP_SSL", os.environ.get("SMTP_SSL", ""))
print("SMTP_FROM", os.environ.get("SMTP_FROM", ""))
print("has_password", bool(os.environ.get("SMTP_PASSWORD")))

con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
for r in con.execute(
    "SELECT at, subject, ok, error, to_addr FROM mail_log ORDER BY id DESC LIMIT 5"
):
    print("---")
    print(dict(r))
