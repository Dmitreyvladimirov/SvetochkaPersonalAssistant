#!/bin/bash
set -e

# One image, several roles, chosen by SERVICE_TYPE (SPEC.md §7): `web` is the
# webhook + agent + reminder tick; `digest` and `ingest` are Railway cron
# services (stage 4) that run one pass and exit.
case "${SERVICE_TYPE:-web}" in
  web)
    exec uvicorn sveta.core.app:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  digest)
    exec python -m sveta.jobs.digest
    ;;
  ingest)
    exec python -m sveta.jobs.ingest
    ;;
  *)
    echo "run.sh: unknown SERVICE_TYPE '${SERVICE_TYPE}' (web | digest | ingest)" >&2
    exit 64
    ;;
esac
