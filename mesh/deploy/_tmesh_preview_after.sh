#!/bin/bash
set -e
docker cp /opt/geekpark-tmesh/deploy/_tmesh_start_preview.py geekpark-tmesh:/srv/mesh/deploy/
docker cp /opt/geekpark-tmesh/deploy/_tmesh_wait_job.py geekpark-tmesh:/srv/mesh/deploy/
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_start_preview.py
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_wait_job.py preview
echo PREVIEW_DONE
