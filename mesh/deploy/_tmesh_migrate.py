import app.db as db

c = db.connect()
db.migrate(c)
c.commit()
print("migrate_ok", db.SCHEMA_VERSION)
