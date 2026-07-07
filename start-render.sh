#!/bin/sh
# Render's free tier only allows one service AND caps the container at
# 512MB total RAM. Two things had to change from a "normal" combined setup:
#
# 1. Celery runs with --pool=solo (no forked child processes). The default
#    prefork pool forks extra worker processes, each duplicating the entire
#    loaded Python heap (FastAPI, SQLAlchemy, the Gemini SDK, PyMuPDF...) —
#    that's what blew past 512MB and crashed the deploy. Solo pool means one
#    process, no forking, at the cost of processing resumes one at a time
#    instead of in parallel. Fine for demo/pilot volume.
#
# 2. Known limitation carried over: if the worker crashes independently (the
#    web process stays healthy), Render won't notice or restart it since only
#    the foreground process is health-checked. At real client volume, split
#    these into Render's paid Background Worker ($7/mo) or move to a VM —
#    see RENDER_DEPLOYMENT.md.
celery -A core.celery_app worker --loglevel=info --pool=solo &
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}