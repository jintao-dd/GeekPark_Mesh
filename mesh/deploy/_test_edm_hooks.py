#!/usr/bin/env python3
import sys
sys.path.insert(0, ".")
from app.edm import render_edm, _build_hooks

data = {
    "question": "这两周，谁和谁产生了值得对齐的关系？",
    "lead": "编辑部、BD、商业化各自推进，Mesh 把跨团队触点整理成可检索的关系图谱。",
    "kpis": [{"n": "14", "label": "组可同步关系"}, {"n": "23", "label": "人/公司接触"}],
    "relations": [
        {"title": "Global Partnership × 编辑部", "label": "同一件事，两个部门各知一半", "body": "海外合作方已接触，内容侧待安排深度报道", "teams": ["Global Partnership", "编辑部"], "weak": False},
        {"title": "某 AI 公司", "label": "采访对象也是客户", "body": "BD 在谈合作，编辑部上周已采访 CEO", "teams": ["硅谷 BD", "编辑部"], "weak": False},
        {"title": "出海政策", "label": "外部在热聊，我们还没碰", "body": "商业化团队在关注", "teams": ["商业化团队"], "weak": True},
        {"title": "第四组", "label": "已联动", "body": "x", "teams": ["内容侧"], "weak": False},
        {"title": "第五组", "label": "已联动", "body": "y", "teams": ["调峰部"], "weak": False},
        {"title": "第六组", "label": "已联动", "body": "z", "teams": ["A"], "weak": False},
    ],
    "contacts": [{"groups": [{"items": [{"name": "张三"}, {"name": "OpenAI"}]}]}],
    "keywords": {"groups": [{"items": [{"name": "AI 芯片"}, {"name": "出海"}]}]},
    "plans": {"groups": [{"items": [{"name": "9 月发布会"}]}]},
    "views": [{"topic": "市场", "text": "To B 比 To C 更容易产生现金流"}],
}
issue = {"slug": "2026-8-17", "period_label": "2026.8.17 - 8.27", "version": "v1.4", "updated_at": "2026-08-27"}
hooks = _build_hooks(data)
print("featured", len(hooks["featured_rels"]), "extra", len(hooks["extra_rels"]))
h, t = render_edm(issue, data, "https://mesh.geekpark.ai")
assert "display:grid" not in h
assert "hooks.featured" not in h
assert "num_2.png" not in h
assert hooks["featured_rels"][0]["title"] in h
print("OK", len(h))
