import os, time, urllib.request
from itsdangerous import URLSafeSerializer

secret = os.environ.get("MESH_SECRET", "change-me-please")
tok = URLSafeSerializer(secret, salt="mesh-session").dumps({
    "u": "admin", "r": "owner", "t": None, "d": "admin", "a": "", "ts": int(time.time())
})
req = urllib.request.Request(
    "http://127.0.0.1:8080/admin/issue/2026-08-14",
    headers={"Cookie": f"mesh_session={tok}"},
)
try:
    with urllib.request.urlopen(req) as r:
        print("STATUS", r.status)
        print(r.read(220).decode("utf-8", "replace"))
except Exception as e:
    print("STATUS", getattr(e, "code", None))
    body = e.read(400).decode("utf-8", "replace") if hasattr(e, "read") else str(e)
    print(body)
