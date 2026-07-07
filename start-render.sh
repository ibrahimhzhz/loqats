#!/bin/sh
# Render's free tier only allows one service, so the Celery worker runs in
# the background (&) and the API runs in the foreground as the main process.
#
# Known limitation: if the worker crashes independently (the web process
# stays healthy), Render won't notice or restart it since only the foreground
# process is health-checked. At real client volume, split these into Render's
# paid Background Worker ($7/mo) or move to a VM — see RENDER_DEPLOYMENT.md.
celery -A core.celery_app worker --loglevel=info --concurrency=2 &
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
