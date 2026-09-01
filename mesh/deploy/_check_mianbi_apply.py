import app.db as d
c = d.connect()
for iid in (1119, 1120, 1121):
    r = c.execute(
        "SELECT id, owner_team, owner_provenance, source_label FROM items WHERE id=?",
        (iid,),
    ).fetchone()
    print(dict(r))
