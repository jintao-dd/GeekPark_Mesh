# Full Regression Report

- Time: 2026-09-02T03:19:10.991434+00:00
- Overall: **FAIL**
- Report: `full_regression_20260902_111940.json`

## Checks

- **pytest**: PASS — `{"ok": true, "exit_code": 0, "tail": "  C:\\Users\\86152\\Desktop\\geekpark_mesh\\mesh\\app\\auth.py:19: RuntimeWarning: MESH_SECRET 未设置或过弱：会话可被伪造。生产环境必须设置强随机串。\n    warnings.warn(msg, RuntimeWarning,`
- **ask_25**: PASS — `{"ok": true, "exit_code": 0, "summary": "检索: 25/25 pass"}`
- **relation_pytest**: PASS — `{"ok": true, "tail": "............................................                             [100%]\n44 passed in 1.23s"}`
- **relation_integrity_published**: FAIL — `{"ok": false, "n_relations": 14, "n_ok": 0, "strong_no_evidence": 10, "team_mismatch": 24, "orphan_sources": 25, "blocked_leaks": 0, "bad_titles": ["破壳创智", "面壁智能 · 詹杨帆", "深圳 Physical AI 近场研究", "阿里千问",`
- **relation_integrity_draft**: FAIL — `{"ok": false, "n_relations": 14, "n_ok": 0, "strong_no_evidence": 10, "team_mismatch": 24, "orphan_sources": 25, "blocked_leaks": 0, "bad_titles": ["破壳创智", "面壁智能 · 詹杨帆", "深圳 Physical AI 近场研究", "阿里千问",`
- **new_fields**: FAIL — `{"ok": false, "n_draft_relations": 14, "n_reader_visible": 0, "n_draft_backlog": 14, "decision_tier_counts": {"null": 14}, "missing_decision_tier": ["破壳创智", "面壁智能 · 詹杨帆", "深圳 Physical AI 近场研究", "阿里千问"`
- **relation_25**: FAIL — `{"ok": false, "published_relations": 14, "published_ok": 0, "evidence_100": false, "team_mismatch": 24, "blocked_leakage": 0}`

## Failures

- relation: strong_no_ev=10 team_mis=24 blocked=0
- new_fields: missing decision_tier on ['破壳创智', '面壁智能 · 詹杨帆', '深圳 Physical AI 近场研究', '阿里千问', '追觅', 'Flowtica · 成旭然、陈洁茹', 'memopin · 叶志伟', '李源 · 具身智能账号矩阵', '众筹与出海调研', '字节豆包 · Agent 巨头战役']
