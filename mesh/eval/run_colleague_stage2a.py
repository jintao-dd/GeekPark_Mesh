"""Stage 2A Gate A — mock Conversation Core（可复现；不扩 Gold）。

真人盲测指标见 colleague_stage2a_blind.md；本脚本只保证：
- response_mode 预算接线
- session feedback / relationship 更新
- 无客服禁词硬伤（启发式）
- 不调用 Controller / 不进 Retrieval
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import colleague_chat
from app.agent import colleague_core as ccore
from app.agent import session_state as sstore

SCENARIOS = ROOT / "eval" / "colleague_stage2a.jsonl"
OUT_JSON = ROOT / "eval" / "reports" / "COLLEAGUE_STAGE2A_GATE_A.json"
OUT_MD = ROOT / "eval" / "reports" / "COLLEAGUE_STAGE2A_GATE_A.md"

_ROBOT = re.compile(
    r"(很抱歉给您带来不便|还有什么可以帮您|感谢您的反馈|作为AI|作为人工智能|"
    r"我是一个?检索|Query Scope|claim_strength|Published-only)",
    re.I,
)
_SELF_INTRO = re.compile(r"(我是\s*Mesh|我叫\s*Mesh|我是极客公园)", re.I)


def _load() -> list[dict]:
    rows = []
    for line in SCENARIOS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _mock_reply(system: str, user: str, max_tokens: int = 400, **kwargs) -> str:
    # 根据 system 里的 response_mode 给可区分的假回复
    if "response_mode：rewrite" in system or "response_mode: rewrite" in system:
        return "我们会继续盯这个方向的变化。——这样口语一点。"
    if "response_mode：opinion" in system:
        return "我觉得先别急着下结论；如果是我，会先看谁在推进、证据够不够。"
    if "response_mode：clarify" in system:
        return "你具体指哪一块？"
    if "机械" in user or "傻子" in user:
        return "说得对，刚才那下是偏机械了。我按同事聊天来。"
    if "同事" in user:
        return "行，我就按内部同事这个位置来聊。"
    if "忙" in user:
        return "懂，这种日子是挺磨人。"
    if "谢谢" in user:
        return "不客气。"
    return "嗯，我在听。你接着说。"


def _score_text(text: str, user: str, *, allow_intro: bool = False) -> dict:
    robotic = 1 if _ROBOT.search(text or "") else 0
    unnecessary = 0
    if not allow_intro and _SELF_INTRO.search(text or "") and "谁" not in user:
        unnecessary = 1
    repetition = 1 if user and user.strip() and user.strip() in (text or "") and len(user) > 8 else 0
    length_fit = 1 if 2 <= len((text or "").strip()) <= 800 else 0
    return {
        "robotic_score": robotic,
        "unnecessary_explanation": unnecessary,
        "repetition": repetition,
        "response_length_fit": length_fit,
        "naturalness_heuristic": 1 if robotic == 0 and unnecessary == 0 else 0,
    }


def run_gate_a() -> dict:
    sstore.reset_for_tests()
    cases = []
    fails = []
    with mock.patch("app.llm.call", side_effect=_mock_reply):
        with mock.patch("app.llm.model_for_task", return_value="mock-answer"):
            for row in _load():
                st = sstore.SessionContextState()
                turns = row.get("turns") or [row["user"]]
                mode = row.get("expect_mode") or "conversational"
                last_text = ""
                last_meta: dict = {}
                for t in turns:
                    last_text, last_meta = colleague_chat.reply_colleague(
                        t, st, response_mode=mode
                    )
                scores = _score_text(
                    last_text,
                    turns[-1],
                    allow_intro=("谁" in turns[-1] or "能干" in turns[-1]),
                )
                ok = (
                    last_meta.get("llm_used") is True
                    and last_meta.get("response_mode") == mode
                    and last_meta.get("max_tokens") == ccore.RESPONSE_BUDGETS[mode][0]
                    and scores["robotic_score"] == 0
                    and scores["unnecessary_explanation"] == 0
                    and scores["repetition"] == 0
                    and scores["response_length_fit"] == 1
                )
                # chain：关系偏好应留下
                if row.get("bucket") == "chain":
                    if st.colleague_relationship != "familiar-colleague":
                        ok = False
                        fails.append(f"{row['id']}: relationship not updated")
                if not ok:
                    fails.append(row["id"])
                cases.append(
                    {
                        "id": row["id"],
                        "bucket": row.get("bucket"),
                        "ok": ok,
                        "text": last_text,
                        "meta": last_meta,
                        "scores": scores,
                        "relationship": st.colleague_relationship,
                        "pref": st.colleague_pref,
                    }
                )
    passed = sum(1 for c in cases if c["ok"])
    report = {
        "gate": "A",
        "pass": passed == len(cases) and not fails,
        "passed": passed,
        "total": len(cases),
        "fails": fails,
        "cases": cases,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Colleague Stage 2A · Gate A",
        "",
        f"pass={report['pass']}  {passed}/{len(cases)}",
        "",
        "| id | bucket | ok | mode | robotic |",
        "|----|--------|----|------|---------|",
    ]
    for c in cases:
        lines.append(
            f"| {c['id']} | {c['bucket']} | {c['ok']} | {c['meta'].get('response_mode')} | {c['scores']['robotic_score']} |"
        )
    if fails:
        lines += ["", "## fails", *[f"- {f}" for f in fails]]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default="A", choices=["A"])
    args = ap.parse_args()
    if args.gate != "A":
        raise SystemExit("only Gate A (mock) in this runner; blind test is human/tmesh")
    rep = run_gate_a()
    print(json.dumps({"pass": rep["pass"], "passed": rep["passed"], "total": rep["total"], "fails": rep["fails"]}, ensure_ascii=False))
    raise SystemExit(0 if rep["pass"] else 1)


if __name__ == "__main__":
    main()
