# Full Regression Report

- Time: 2026-09-25T09:13:49.457855+00:00
- Overall: **FAIL**
- Report: `full_regression_20260925_171437.json`

## Checks

- **pytest**: FAIL — `{"ok": false, "exit_code": 1, "tail": "FAILED tests/test_relation_filter_quality.py::test_filter_ungrounded_drops_card_without_evidence\nFAILED tests/test_relation_gold_smoke.py::test_dispatch_keep - `
- **relation_pytest**: FAIL — `{"ok": false, "tail": "FAILED tests/test_relation_candidates.py::test_merge_strips_hallucinated_relation\nFAILED tests/test_relation_candidates.py::test_merge_preserves_evidence_assets\n2 failed, 68 p`
- **relation_integrity_published**: FAIL — `{"ok": false, "n_relations": 14, "n_ok": 0, "strong_no_evidence": 10, "team_mismatch": 24, "orphan_sources": 25, "blocked_leaks": 0, "bad_titles": ["破壳创智", "面壁智能 · 詹杨帆", "深圳 Physical AI 近场研究", "阿里千问",`
- **relation_integrity_draft**: FAIL — `{"ok": false, "n_relations": 14, "n_ok": 0, "strong_no_evidence": 10, "team_mismatch": 24, "orphan_sources": 25, "blocked_leaks": 0, "bad_titles": ["破壳创智", "面壁智能 · 詹杨帆", "深圳 Physical AI 近场研究", "阿里千问",`
- **new_fields**: FAIL — `{"ok": false, "n_draft_relations": 14, "n_reader_visible": 0, "n_draft_backlog": 14, "decision_tier_counts": {"null": 14}, "missing_decision_tier": ["破壳创智", "面壁智能 · 詹杨帆", "深圳 Physical AI 近场研究", "阿里千问"`
- **relation_25**: FAIL — `{"ok": false, "published_relations": 14, "published_ok": 0, "evidence_100": false, "team_mismatch": 24, "blocked_leakage": 0}`

## Failures

- pytest: FAILED tests/test_relation_filter_quality.py::test_filter_ungrounded_drops_card_without_evidence
FAILED tests/test_relation_gold_smoke.py::test_dispatch_keep - AssertionError...
FAILED tests/test_relation_merge_a_plus.py::test_teams_and_sources_from_evidence_not_llm
FAILED tests/test_relation_merge_a_plus.py::test_evidence_team_source_consistency
21 failed, 755 passed, 4 skipped, 1 warning in 41.46s
- relation: strong_no_ev=10 team_mis=24 blocked=0
- new_fields: missing decision_tier on ['破壳创智', '面壁智能 · 詹杨帆', '深圳 Physical AI 近场研究', '阿里千问', '追觅', 'Flowtica · 成旭然、陈洁茹', 'memopin · 叶志伟', '李源 · 具身智能账号矩阵', '众筹与出海调研', '字节豆包 · Agent 巨头战役']
