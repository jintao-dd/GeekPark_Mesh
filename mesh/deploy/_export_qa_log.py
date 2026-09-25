"""导出 prod agent_qa_log 到 JSONL，供本地评测。"""
import json
from app import db

con = db.connect()
rows = con.execute(
    "SELECT id, created_at, question, answer_text, intent, route, tools_called, "
    "answer_status, evidence_count, evidence_refs, claim_bindings, trace_json "
    "FROM agent_qa_log ORDER BY id DESC LIMIT 200"
).fetchall()

for r in rows:
    obj = {
        "id": r["id"],
        "created_at": str(r["created_at"]),
        "question": r["question"],
        "answer_text": r["answer_text"],
        "intent": r["intent"],
        "route": r["route"],
        "tools_called": r["tools_called"],
        "answer_status": r["answer_status"],
        "evidence_count": r["evidence_count"],
        "evidence_refs": r["evidence_refs"],
        "claim_bindings": r["claim_bindings"],
        "trace_json": r["trace_json"],
    }
    print(json.dumps(obj, ensure_ascii=False))
