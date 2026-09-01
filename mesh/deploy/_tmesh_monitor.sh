#!/bin/bash
set -e
docker cp /opt/geekpark-tmesh/deploy/_tmesh_check_attribution.py geekpark-tmesh:/srv/mesh/deploy/
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_check_attribution.py
