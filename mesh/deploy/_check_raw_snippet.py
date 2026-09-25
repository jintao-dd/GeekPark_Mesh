from app import db

con = db.connect()
rows = con.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name='items' AND column_name='raw_snippet'"
).fetchall()
print('raw_snippet exists:', bool(rows))

if rows:
    r = con.execute(
        "SELECT id, text, raw_snippet FROM items WHERE raw_snippet IS NOT NULL "
        "AND raw_snippet != text ORDER BY id DESC LIMIT 3"
    ).fetchone()
    if r:
        print('sample id', r['id'])
        print('text len', len(r['text'] or ''), 'raw_snippet len', len(r['raw_snippet'] or ''))
        print('text:', (r['text'] or '')[:100])
        print('raw_snippet:', (r['raw_snippet'] or '')[:200])
