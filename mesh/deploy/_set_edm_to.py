import sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
c.execute(
    "INSERT INTO settings(key,value) VALUES('edm_default_to',?) "
    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
    ("blog@geekpark.net",),
)
c.commit()
print("default_to_updated", c.execute("SELECT value FROM settings WHERE key='edm_default_to'").fetchone()[0])
