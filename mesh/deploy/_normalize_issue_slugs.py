"""一次性迁移：把所有 issue_slug 规范化为 YYYY-MM-DD。

影响表（按 issues.slug 级联）：
- issues（slug 主键）
- sources
- items
- search_fts
- entity_team_facts
- item_facts
- item_entity_facts
- chunk_index
- crm_cross_anchor
- preview_job_state.issue_slug（如有）

冲突处理：若旧 slug 规范化后与另一旧 slug 相同，保留 id 较大的（更晚创建）。
"""
from __future__ import annotations

from app.issue_period import normalize_issue_slug
from app import db, db_conn


ISSUE_SLUG_TABLES = [
    ("sources", "issue_slug"),
    ("items", "issue_slug"),
    ("search_fts", "issue_slug"),
    ("entity_team_facts", "issue_slug"),
    ("item_facts", "issue_slug"),
    ("item_entity_facts", "issue_slug"),
    ("chunk_index", "issue_slug"),
    ("crm_cross_anchor", "issue_slug"),
    ("preview_job_state", "issue_slug"),
]


def _tables_exist(con) -> list[tuple[str, str]]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    existing = {r["name"] for r in rows}
    return [(t, c) for t, c in ISSUE_SLUG_TABLES if t in existing]


def _pg_tables_exist(con) -> list[tuple[str, str]]:
    rows = con.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'"
    ).fetchall()
    existing = {(r["table_name"], r["column_name"]) for r in rows}
    return [(t, c) for t, c in ISSUE_SLUG_TABLES if (t, c) in existing]


def migrate() -> dict:
    con = db.connect()
    try:
        is_pg = db_conn.is_postgres()
        existing = _pg_tables_exist(con) if is_pg else _tables_exist(con)

        # 1) 收集需要变更的 issues
        rows = con.execute("SELECT id, slug FROM issues ORDER BY id").fetchall()
        mappings: list[tuple[int, str, str]] = []
        collisions: dict[str, list[tuple[int, str]]] = {}
        for r in rows:
            old = (r["slug"] or "").strip()
            new = normalize_issue_slug(old) or old
            if new and new != old:
                mappings.append((int(r["id"]), old, new))
                collisions.setdefault(new, []).append((int(r["id"]), old))

        if not mappings:
            return {"changed": 0, "tables": 0, "details": []}

        # 2) 处理冲突：同一个新 slug 被多个旧 slug 映射到时，只保留 id 最大的
        # 3) 删除被合并的 issue（连带数据由应用层 delete_issue 清理）
        drop_ids: set[int] = set()
        for new, olds in collisions.items():
            if len(olds) > 1:
                olds_sorted = sorted(olds, key=lambda x: x[0])
                drop_ids |= {oid for oid, _ in olds_sorted[:-1]}
        for iid in sorted(drop_ids):
            row = con.execute("SELECT slug FROM issues WHERE id=?", (iid,)).fetchone()
            if row:
                db.delete_issue(con, row["slug"])

        # 4) 重新计算映射（去掉已删除的）
        rows = con.execute("SELECT id, slug FROM issues ORDER BY id").fetchall()
        final_mappings: list[tuple[str, str]] = []
        for r in rows:
            old = (r["slug"] or "").strip()
            new = normalize_issue_slug(old) or old
            if new and new != old:
                final_mappings.append((old, new))

        # 5) 级联更新各表
        updated_tables = 0
        for table, col in existing:
            for old, new in final_mappings:
                con.execute(
                    f"UPDATE {table} SET {col}=? WHERE {col}=?",
                    (new, old),
                )
            updated_tables += 1

        # 6) 更新 issues 主键
        for old, new in final_mappings:
            con.execute(
                "UPDATE issues SET slug=? WHERE slug=?",
                (new, old),
            )

        con.commit()
        return {
            "changed": len(final_mappings),
            "merged": len(drop_ids),
            "tables": updated_tables,
            "details": final_mappings,
        }
    finally:
        con.close()


if __name__ == "__main__":
    print(migrate())
