#!/bin/bash
set -e
docker exec geekpark-tmesh python -c "
import app.attribution as a
import app.db as db
c = db.connect()
db.migrate(c)
c.commit()
print('attribution_ok', a.PROVENANCE_MANUAL)
"

docker exec geekpark-tmesh python -c "
from app import pipeline
pipeline.start('2026-8-17', force=True)
print('pipeline_started')
"
