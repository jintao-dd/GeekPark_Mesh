"""对比不同 LLM 模型在 Ask 25 E2E 上的表现。"""
import argparse, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app import db, db_conn
from app import ask_engine, ask_context
from app.ask_scope import AskScope
from eval.run_acceptance import _seed_golden_db
from eval.eval_lib import load_cases, evaluate_e2e, evaluate_retrieval


def run_for_model(model: str, cases: list, con) -> dict:
    os.environ["MESH_LLM_MODEL"] = model
    os.environ["MESH_LLM_MODEL_ANSWER"] = model
    os.environ["MESH_LLM_MODEL_SEMANTIC"] = model
    os.environ["MESH_LLM_MODEL_SENSITIVE"] = model
    # 强制刷新 model cache
    import app.llm as _llm_mod
    _llm_mod._MODEL_CACHE.clear()
    results = []
    for case in cases:
        scope = AskScope(channel="web", user_id=1, role="viewer")
        if case.get("scope"):
            for k, v in case["scope"].items():
                setattr(scope, k, v)
        route = ask_context.route(case["q"], [])
        prepared = ask_engine.prepare(con, case["q"], scope, search_q=route.get("search_q") or case["q"])
        row = evaluate_e2e(con, case, evaluate_retrieval(con, case))
        results.append({
            "id": case["id"],
            "pass": row.get("pass"),
            "failures": [(c["check"], c.get("detail")) for c in row.get("checks", []) if not c.get("ok")],
            "verify": row.get("verify") or {},
        })
    passed = sum(1 for r in results if r["pass"])
    total_rejected = sum((r["verify"].get("rejected") or 0) for r in results)
    return {
        "model": model,
        "passed": passed,
        "total": len(results),
        "total_rejected": total_rejected,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="anthropic/claude-4.8-opus,deepseek/deepseek-v3.1-terminus,qwen3-235b-a22b")
    parser.add_argument("--ids", default="", help="逗号分隔 case id，例如 e02,e03,e06")
    args = parser.parse_args()

    td = tempfile.mkdtemp(prefix="mesh_eval_")
    db_path = Path(td) / "eval.db"
    old_path, old_url = db_conn.DB_PATH, db_conn.MESH_DB_URL
    db_conn.DB_PATH, db_conn.MESH_DB_URL = str(db_path), ""
    db.DB_PATH = str(db_path)
    con = db.connect()
    db.init_db(seed=True)
    _seed_golden_db(con)

    all_cases = load_cases(ROOT / "eval" / "ask_eval_v1.jsonl")
    # 先跑重点 case，再跑全量；可用 --ids 指定
    if args.ids:
        cases = [c for c in all_cases if c["id"] in args.ids.split(",")]
    else:
        cases = all_cases
    out = []
    for model in args.models.split(","):
        print(f"Running {model} ...", flush=True)
        out.append(run_for_model(model.strip(), cases, con))
        print(f"  {out[-1]['passed']}/{out[-1]['total']} pass, rejected={out[-1]['total_rejected']}")

    db_conn.DB_PATH, db_conn.MESH_DB_URL = old_path, old_url
    report = ROOT / "eval" / "reports" / "model_compare.json"
    report.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {report}")


if __name__ == "__main__":
    main()
