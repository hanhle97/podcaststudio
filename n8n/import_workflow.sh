#!/bin/sh
# Import (or re-import) the workflow into the running n8n container and publish it.
# Telegram / YouTube credentials you created in n8n are attached automatically
# (only their id, name and type are read, never the secrets).
# Usage (repo root):  sh n8n/import_workflow.sh
set -e
CREDS=$(docker compose exec -T n8n sh -c '
  n8n export:credentials --all --output=/tmp/creds.json >/dev/null 2>&1 || echo "[]" > /tmp/creds.json
  node -e "const c=require(\"/tmp/creds.json\");console.log(JSON.stringify(c.map(x=>({id:x.id,name:x.name,type:x.type}))))"
  rm -f /tmp/creds.json')
python n8n/build_workflow.py --credentials "$CREDS"
docker compose exec -T n8n n8n import:workflow --input=/workflows/podcast_pipeline.local.json
docker compose exec -T n8n n8n publish:workflow --id=PodcastPipeline1
docker compose restart n8n
