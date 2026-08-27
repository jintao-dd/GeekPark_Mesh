#!/bin/bash
docker exec geekpark-mesh grep -E 'askStream|askstream-on' /srv/mesh/app/templates/base.html | head -5
grep -n 'ask/stream\|proxy_buffering' /www/server/panel/vhost/nginx/mesh.geekpark.ai.conf
curl -sS http://127.0.0.1:8090/healthz; echo
