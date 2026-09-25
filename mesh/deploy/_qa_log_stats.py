from app import db

con = db.connect()
total = con.execute("SELECT COUNT(*) c FROM agent_qa_log").fetchone()["c"]
recent = con.execute(
    "SELECT COUNT(*) c FROM agent_qa_log WHERE created_at::timestamp >= NOW() - INTERVAL '7 days'"
).fetchone()["c"]
recent_24 = con.execute(
    "SELECT COUNT(*) c FROM agent_qa_log WHERE created_at::timestamp >= NOW() - INTERVAL '1 days'"
).fetchone()["c"]
print(f"total={total} recent_7d={recent} recent_24h={recent_24}")
