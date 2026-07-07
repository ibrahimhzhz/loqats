#!/bin/sh
# Web service entrypoint. Writes the Vertex AI key from a base64 env var (so it
# never lives in git), then launches the API. See DEPLOYMENT.md §3.3.
if [ -n "$GCP_SA_KEY_B64" ]; then
  echo "$GCP_SA_KEY_B64" | base64 -d > /app/vertex-key.json
fi
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
