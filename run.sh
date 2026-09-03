#!/bin/bash
set -e

# One image, several roles, chosen by SERVICE_TYPE. Only `web` exists in stage 0;
# `digest` and `ingest` arrive in stage 4 (SPEC.md §7). Keeping the switch here from
# day one means adding a role later is one line, not a second Dockerfile.
case "${SERVICE_TYPE:-web}" in
  web)
    exec uvicorn sveta.core.app:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  *)
    echo "run.sh: unknown SERVICE_TYPE '${SERVICE_TYPE}' (stage 0 knows only 'web')" >&2
    exit 64
    ;;
esac
