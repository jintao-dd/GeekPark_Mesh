#!/usr/bin/env python3
"""Multi-identity colleague matrix — capability dimensions, not utterance routing.

Eval utterances are samples only; production must not regex-route on them.
Run in tmesh:
  PYTHONPATH=/srv/mesh python /tmp/colleague_matrix_probe.py
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field

from app import db
from app.agent import context as ctxmod
from app.agent import identity as idmod
from app.agent import permission as permmod
from app.agent import session_state as sstore
from app.agent import tools as toolsmod
from app.agent.models import AgentEnvelope
from app.agent.runtime import _render
from app.agent.supervisor import handle_turn, supervisor_enabled

# Diverse bound people (open_id from roster). Cover不同团队.
ACTORS = [
    {"name": "杜锦涛", "open_id": "ou_fd65363b8ed1e5ddb93dd56e86a35b9b"},  # 品牌创意
    {"name": "赵思琪", "open_id": "ou_98bd3b0520cfb6a112fce9f55d948606"},  # 海外拓展
    {"name": "周永亮", "open_id": "ou_3730ed4aca435e76628757a4e12d6b58"},  # 媒体/编辑
    {"name": "张鹏", "open_id": "ou_f50030f7ce0b67599a942d63ed824a7d"},  # CEO
    {"name": "彭康林", "open_id": "ou_df89e60f03c67efff4a6b3998294f070"},  # 品牌设计
]

# Capability dimensions — one sample utterance each for probing.
DIMENSIONS = [
    {
        "dim": "org_our_team_published",
        "q": "最近周报有和我们团队相关的？",
        "need": ["used_ask", "not_false_empty"],
    },
    {
        "dim": "self_grounded",
        "q": "和我相关的呢？",
        "need": ["mentions_self", "not_calendar_only"],
    },
    {
        "dim": "leader_judgment_grounded",
        "q": "你觉得我们老板应该关注的内容是啥？",
        "need": ["used_work_tools", "not_pure_speak"],
    },
    {
        "dim": "named_colleague_progress",
        "q": "思琪最近在忙什么？",
        "need": ["used_ask_or_crm"],
    },
    {
        "dim": "org_structure",
        "q": "我们部门下面有哪些组？",
        "need": ["used_feishu"],
    },
]


@dataclass
class CaseResult:
    actor: str
    team: str
    dim: str
    q: str
    tools: list[str] = field(default_factory=list)
    intent: str = ""
    chars: int = 0
    ms: int = 0
    checks: dict[str, bool] = field(default_factory=dict)
    pass_ok: bool = False
    excerpt: str = ""


def _checks(actor_name: str, text: str, tools: list[str]) -> dict[str, bool]:
    t = text or ""
    tools = [str(x) for x in tools]
    used_ask = any(x.startswith("ask.") for x in tools)
    used_crm = any("crm" in x for x in tools)
    used_feishu = any(x.startswith("feishu.") for x in tools)
    false_empty = bool(
        re.search(r"(没有找到|未找到|没有命中).{0,20}(团队|相关|周报)", t)
    ) and not bool(re.search(r"(播放|接触|推进|会面|条目|本期|期)", t))
    calendar_heavy = ("日历" in t or "忙碌" in t) and t.count("–") + t.count("-") >= 2 and len(t) < 600
    return {
        "used_ask": used_ask,
        "used_crm": used_crm,
        "used_feishu": used_feishu,
        "used_ask_or_crm": used_ask or used_crm,
        "used_work_tools": used_ask or used_crm or used_feishu,
        "not_pure_speak": bool(tools),
        "mentions_self": actor_name in t or (len(actor_name) >= 2 and actor_name[-2:] in t),
        "not_calendar_only": not calendar_heavy,
        "not_false_empty": not false_empty,
    }


def _pass(need: list[str], checks: dict[str, bool]) -> bool:
    return all(checks.get(k, False) for k in need)


def run_case(con, actor: dict, dim: dict) -> CaseResult:
    oid = actor["open_id"]
    name = actor["name"]
    env = AgentEnvelope(text="", channel="feishu_dm", feishu_open_id=oid)
    ident = idmod.resolve_identity(con, env)
    perm = permmod.decide_permission(ident, explicit_team=None, chat_team="")
    ctx = ctxmod.assemble_context(con, env, ident, perm)
    session = sstore.SessionContextState(session_key=f"probe:matrix:{oid}:{dim['dim']}")
    if ident.primary_team:
        session.active_team = str(ident.primary_team)

    q = dim["q"]
    t0 = time.time()
    out = handle_turn(
        con=con,
        user_text=q,
        identity=ident,
        permission=perm,
        context=ctx,
        session=session,
        invoke_tool=toolsmod.invoke_tool,
        render_tool_result=_render,
    )
    ms = int((time.time() - t0) * 1000)
    text = out.text or ""
    tools = list(out.tools_called or [])
    checks = _checks(name, text, tools)
    return CaseResult(
        actor=name,
        team=str(ident.primary_team or ""),
        dim=dim["dim"],
        q=q,
        tools=tools,
        intent=str(out.intent or ""),
        chars=len(text),
        ms=ms,
        checks=checks,
        pass_ok=_pass(dim["need"], checks),
        excerpt=text[:400].replace("\n", " / "),
    )


def main() -> int:
    print("supervisor_enabled", supervisor_enabled())
    con = db.connect()
    results: list[CaseResult] = []
    # Full matrix: each actor × all capability dimensions
    actors = ACTORS
    for actor in actors:
        print("\n## actor", actor["name"], actor["open_id"])
        for dim in DIMENSIONS:
            try:
                r = run_case(con, actor, dim)
            except Exception as e:
                r = CaseResult(
                    actor=actor["name"],
                    team="?",
                    dim=dim["dim"],
                    q=dim["q"],
                    pass_ok=False,
                    excerpt=f"ERR {e}",
                    checks={},
                )
            results.append(r)
            mark = "PASS" if r.pass_ok else "FAIL"
            print(f"  [{mark}] {r.dim} team={r.team} tools={r.tools} ms={r.ms}")
            print(f"         checks={json.dumps(r.checks, ensure_ascii=False)}")
            print(f"         {r.excerpt[:220]}")

    # rollup
    by_dim: dict[str, list[bool]] = {}
    for r in results:
        by_dim.setdefault(r.dim, []).append(r.pass_ok)
    print("\n## ROLLUP")
    for dim, xs in by_dim.items():
        ok = sum(1 for x in xs if x)
        print(f"  {dim}: {ok}/{len(xs)}")
    n_ok = sum(1 for r in results if r.pass_ok)
    print(f"TOTAL {n_ok}/{len(results)}")
    out_path = "eval/reports/COLLEAGUE_MATRIX_PROBE.json"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
        print("wrote", out_path)
    except Exception as e:
        print("write_fail", e)
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2)[:8000])
    con.close()
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
