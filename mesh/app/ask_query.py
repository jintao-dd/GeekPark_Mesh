"""多轮检索问句合并（无循环依赖）。"""


def retrieval_query(q: str, history: list[dict]) -> str:
    """多轮追问：把指代/短问与上一轮用户问题合并，供检索层使用。"""
    q = (q or "").strip()
    if not q or not history:
        return q
    cues = (
        "他", "她", "它", "他们", "她们", "那家", "这家", "还有", "呢", "吗",
        "展开", "详细", "第一家", "第二个", "上面", "刚才", "之前", "对比", "哪些",
    )
    if len(q) > 28 and not any(c in q for c in cues):
        return q
    last_user = ""
    for m in reversed(history):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            last_user = m["content"].strip()
            break
    if not last_user or last_user == q:
        return q
    return f"{last_user} {q}"
