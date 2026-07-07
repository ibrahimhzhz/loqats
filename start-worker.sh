#!/bin/sh
# Worker service entrypoint. Same credential bootstrap as the web service, then
# runs the Celery worker that performs résumé screening. See DEPLOYMENT.md §4.2.
if [ -n "$GCP_SA_KEY_B64" ]; then
  echo "$GCP_SA_KEY_B64" | base64 -d > /app/vertex-key.json
fi
exec celery -A core.celery_app worker --loglevel=info --concurrency=2
