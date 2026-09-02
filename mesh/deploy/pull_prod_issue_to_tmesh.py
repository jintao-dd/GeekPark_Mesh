#!/usr/bin/env python3
"""Copy one issue (sources/items/cards/issue row) from prod PG into tmesh PG.

Does NOT touch prod. Overwrites the same slug on tmesh.
Then optionally start tmesh preview.
"""
from __future__ import annotations

import json
import subprocess
import sys


SLUG = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
START_PREVIEW = "--preview" in sys.argv


def _sh(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def _dump_prod() -> dict:
    py = r"""
import json
from app import db
slug = %r
con = db.connect()
issue = con.execute('SELECT * FROM issues WHERE slug=?', (slug,)).fetchone()
if not issue:
    raise SystemExit('prod issue missing')
issue = dict(issue)
iid = int(issue['id'])
sources = [dict(x) for x in con.execute('SELECT * FROM sources WHERE issue_id=?', (iid,))]
items = [dict(x) for x in con.execute('SELECT * FROM items WHERE issue_id=?', (iid,))]
cards = [dict(x) for x in con.execute('SELECT * FROM cards WHERE issue_id=?', (iid,))]
con.close()
# drop identity / serialize
for row in sources + items + cards + [issue]:
    for k, v in list(row.items()):
        if hasattr(v, 'isoformat'):
            row[k] = v.isoformat()
print(json.dumps({'issue': issue, 'sources': sources, 'items': items, 'cards': cards}, ensure_ascii=False))
""" % SLUG
    raw = _sh(["docker", "exec", "-w", "/srv/mesh", "geekpark-mesh", "env", "PYTHONPATH=/srv/mesh", "python", "-c", py])
    # strip mesh banner lines
    lines = [ln for ln in raw.splitlines() if not ln.startswith("[mesh]")]
    return json.loads("\n".join(lines))


def _restore_tmesh(payload: dict) -> dict:
    py = r"""
import json
from app import db
payload = json.loads('''%s''')
slug = payload['issue']['slug']
con = db.connect()
old = con.execute('SELECT id FROM issues WHERE slug=?', (slug,)).fetchone()
if old:
    oid = int(old['id'])
    con.execute('DELETE FROM cards WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM items WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM sources WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM issues WHERE id=?', (oid,))
# insert issue without id
iss = dict(payload['issue'])
iss.pop('id', None)
cols = list(iss.keys())
con.execute(
    f"INSERT INTO issues({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
    [iss[c] for c in cols],
)
new_id = int(con.execute('SELECT id FROM issues WHERE slug=?', (slug,)).fetchone()['id'])
# sources
sid_map = {}
for s in payload['sources']:
    old_sid = s.get('id')
    row = dict(s)
    row.pop('id', None)
    row['issue_id'] = new_id
    cols = list(row.keys())
    con.execute(
        f"INSERT INTO sources({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
        [row[c] for c in cols],
    )
    new_sid = int(con.execute('SELECT id FROM sources WHERE issue_id=? ORDER BY id DESC LIMIT 1', (new_id,)).fetchone()['id'])
    if old_sid is not None:
        sid_map[int(old_sid)] = new_sid
# items
iid_map = {}
for it in payload['items']:
    old_iid = it.get('id')
    row = dict(it)
    row.pop('id', None)
    row['issue_id'] = new_id
    if row.get('source_id') is not None:
        row['source_id'] = sid_map.get(int(row['source_id']), row['source_id'])
    if row.get('merged_into') is not None:
        # remap later
        pass
    cols = list(row.keys())
    con.execute(
        f"INSERT INTO items({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
        [row[c] for c in cols],
    )
    new_iid = int(con.execute('SELECT id FROM items WHERE issue_id=? ORDER BY id DESC LIMIT 1', (new_id,)).fetchone()['id'])
    if old_iid is not None:
        iid_map[int(old_iid)] = new_iid
# fix merged_into
for old_iid, new_iid in iid_map.items():
    # find original merged_into from payload
    pass
for it in payload['items']:
    old_iid = it.get('id')
    mi = it.get('merged_into')
    if old_iid is None or mi is None:
        continue
    if int(old_iid) in iid_map and int(mi) in iid_map:
        con.execute('UPDATE items SET merged_into=? WHERE id=?', (iid_map[int(mi)], iid_map[int(old_iid)]))
# cards
for c in payload['cards']:
    row = dict(c)
    row.pop('id', None)
    row['issue_id'] = new_id
    cols = list(row.keys())
    con.execute(
        f"INSERT INTO cards({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
        [row[c] for c in cols],
    )
db.reindex_issue(con, new_id)
con.commit()
n_items = con.execute('SELECT count(*) AS c FROM items WHERE issue_id=? AND blocked=0', (new_id,)).fetchone()['c']
n_cards = con.execute("SELECT count(*) AS c FROM cards WHERE issue_id=? AND status='approved'", (new_id,)).fetchone()['c']
print(json.dumps({'slug': slug, 'issue_id': new_id, 'n_items': n_items, 'n_cards': n_cards, 'n_sources': len(payload['sources'])}, ensure_ascii=False))
""" % json.dumps(payload, ensure_ascii=False).replace("\\", "\\\\").replace("'", "\\'")
    # embedding huge JSON in -c is fragile; write temp file instead
    raise RuntimeError("use file-based restore")


def main() -> None:
    payload = _dump_prod()
    path = f"/tmp/prod_issue_{SLUG}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    # copy into tmesh and restore
    subprocess.check_call(["docker", "cp", path, f"geekpark-tmesh:{path}"])
    restore_py = f"""
import json
from app import db
payload = json.load(open({path!r}, encoding='utf-8'))
slug = payload['issue']['slug']
con = db.connect()
old = con.execute('SELECT id FROM issues WHERE slug=?', (slug,)).fetchone()
if old:
    oid = int(old['id'])
    con.execute('DELETE FROM cards WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM items WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM sources WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM issues WHERE id=?', (oid,))
iss = dict(payload['issue'])
iss.pop('id', None)
cols = list(iss.keys())
con.execute('INSERT INTO issues(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [iss[c] for c in cols])
new_id = int(con.execute('SELECT id FROM issues WHERE slug=?', (slug,)).fetchone()['id'])
sid_map = {{}}
for s in payload['sources']:
    old_sid = s.get('id')
    row = dict(s); row.pop('id', None); row['issue_id'] = new_id
    cols = list(row.keys())
    con.execute('INSERT INTO sources(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [row[c] for c in cols])
    new_sid = int(con.execute('SELECT id FROM sources WHERE issue_id=? ORDER BY id DESC LIMIT 1', (new_id,)).fetchone()['id'])
    if old_sid is not None:
        sid_map[int(old_sid)] = new_sid
iid_map = {{}}
pending_merge = []
for it in payload['items']:
    old_iid = it.get('id')
    row = dict(it); row.pop('id', None); row['issue_id'] = new_id
    if row.get('source_id') is not None:
        row['source_id'] = sid_map.get(int(row['source_id']), row['source_id'])
    mi = row.pop('merged_into', None)
    cols = list(row.keys())
    con.execute('INSERT INTO items(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [row[c] for c in cols])
    new_iid = int(con.execute('SELECT id FROM items WHERE issue_id=? ORDER BY id DESC LIMIT 1', (new_id,)).fetchone()['id'])
    if old_iid is not None:
        iid_map[int(old_iid)] = new_iid
    if mi is not None and old_iid is not None:
        pending_merge.append((int(old_iid), int(mi)))
for old_iid, mi in pending_merge:
    if old_iid in iid_map and mi in iid_map:
        con.execute('UPDATE items SET merged_into=? WHERE id=?', (iid_map[mi], iid_map[old_iid]))
for c in payload['cards']:
    row = dict(c); row.pop('id', None); row['issue_id'] = new_id
    cols = list(row.keys())
    con.execute('INSERT INTO cards(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [row[c] for c in cols])
db.reindex_issue(con, new_id)
con.commit()
n_items = con.execute('SELECT count(*) AS c FROM items WHERE issue_id=? AND blocked=0', (new_id,)).fetchone()['c']
n_cards = con.execute(\"SELECT count(*) AS c FROM cards WHERE issue_id=? AND status='approved'\", (new_id,)).fetchone()['c']
print(json.dumps({{'ok': True, 'slug': slug, 'issue_id': new_id, 'n_items': n_items, 'n_cards': n_cards, 'n_sources': len(payload['sources']), 'n_draft_rel': len(json.loads(payload['issue'].get('draft_json') or '{{}}').get('relations') or [])}}, ensure_ascii=False))
"""
    # Write restore script to host then docker cp - cleaner
    restore_path = "/tmp/_restore_issue_tmesh.py"
    open(restore_path, "w", encoding="utf-8").write(
        "import json\nfrom app import db\n"
        f"payload = json.load(open({path!r}, encoding='utf-8'))\n"
        + """
slug = payload['issue']['slug']
con = db.connect()
old = con.execute('SELECT id FROM issues WHERE slug=?', (slug,)).fetchone()
if old:
    oid = int(old['id'])
    con.execute('DELETE FROM cards WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM items WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM sources WHERE issue_id=?', (oid,))
    con.execute('DELETE FROM issues WHERE id=?', (oid,))
iss = dict(payload['issue']); iss.pop('id', None)
cols = list(iss.keys())
con.execute('INSERT INTO issues(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [iss[c] for c in cols])
new_id = int(con.execute('SELECT id FROM issues WHERE slug=?', (slug,)).fetchone()['id'])
sid_map = {}
for s in payload['sources']:
    old_sid = s.get('id')
    row = dict(s); row.pop('id', None); row['issue_id'] = new_id
    cols = list(row.keys())
    con.execute('INSERT INTO sources(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [row[c] for c in cols])
    new_sid = int(con.execute('SELECT id FROM sources WHERE issue_id=? ORDER BY id DESC LIMIT 1', (new_id,)).fetchone()['id'])
    if old_sid is not None:
        sid_map[int(old_sid)] = new_sid
iid_map = {}
pending_merge = []
for it in payload['items']:
    old_iid = it.get('id')
    row = dict(it); row.pop('id', None); row['issue_id'] = new_id
    if row.get('source_id') is not None:
        row['source_id'] = sid_map.get(int(row['source_id']), row['source_id'])
    mi = row.pop('merged_into', None)
    cols = list(row.keys())
    con.execute('INSERT INTO items(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [row[c] for c in cols])
    new_iid = int(con.execute('SELECT id FROM items WHERE issue_id=? ORDER BY id DESC LIMIT 1', (new_id,)).fetchone()['id'])
    if old_iid is not None:
        iid_map[int(old_iid)] = new_iid
    if mi is not None and old_iid is not None:
        pending_merge.append((int(old_iid), int(mi)))
for old_iid, mi in pending_merge:
    if old_iid in iid_map and mi in iid_map:
        con.execute('UPDATE items SET merged_into=? WHERE id=?', (iid_map[mi], iid_map[old_iid]))
for c in payload['cards']:
    row = dict(c); row.pop('id', None); row['issue_id'] = new_id
    cols = list(row.keys())
    con.execute('INSERT INTO cards(' + ','.join(cols) + ') VALUES (' + ','.join(['?']*len(cols)) + ')', [row[c] for c in cols])
db.reindex_issue(con, new_id)
con.commit()
import json as _json
n_items = con.execute('SELECT count(*) AS c FROM items WHERE issue_id=? AND blocked=0', (new_id,)).fetchone()['c']
n_cards = con.execute("SELECT count(*) AS c FROM cards WHERE issue_id=? AND status='approved'", (new_id,)).fetchone()['c']
print(_json.dumps({'ok': True, 'slug': slug, 'issue_id': new_id, 'n_items': n_items, 'n_cards': n_cards, 'n_sources': len(payload['sources'])}, ensure_ascii=False))
"""
    )
    subprocess.check_call(["docker", "cp", restore_path, "geekpark-tmesh:/tmp/_restore_issue_tmesh.py"])
    out = _sh(["docker", "exec", "-w", "/srv/mesh", "geekpark-tmesh", "env", "PYTHONPATH=/srv/mesh", "python", "/tmp/_restore_issue_tmesh.py"])
    print(out.strip())
    if START_PREVIEW:
        out2 = _sh(["docker", "exec", "-w", "/srv/mesh", "geekpark-tmesh", "env", "PYTHONPATH=/srv/mesh", "python", "deploy/_tmesh_start_preview_slug.py", SLUG])
        print(out2.strip())


if __name__ == "__main__":
    main()
